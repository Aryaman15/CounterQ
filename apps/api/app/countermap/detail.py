"""Candidate-safe CounterMap node detail resolved from canonical session sources."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.countermap.models import CounterMapProjection
from app.countermap.schema import (
    CanonicalSourceReference,
    CounterMapGraph,
    CounterMapNode,
    CounterMapNodeType,
)
from app.countermap.source import CounterMapSourceBuilder, CounterMapSourceBundle
from app.execution.models import ExecutionRun, TestResult
from app.observation.models import CodeDiff, CodeSnapshot


class CounterMapDetailModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateTextDetail(CounterMapDetailModel):
    text: str
    exact_quote: bool


class CandidateDeliveredPromptDetail(CounterMapDetailModel):
    text: str
    delivery_state: Literal["DELIVERED", "PARTIALLY_DELIVERED", "INTERRUPTED"]
    why: str | None = None
    assistance_label: str | None = None
    concepts: list[str]
    skills: list[str]


class CandidateCodeDiffDetail(CounterMapDetailModel):
    from_version: int
    to_version: int
    diff_format: str
    diff_content: str
    change_summary: str | None = None


class CandidateCodeSnapshotDetail(CounterMapDetailModel):
    snapshot_id: UUID
    version: int
    language: str
    source_code: str
    context: str = "This is the code CounterQ was reacting to."
    diff: CandidateCodeDiffDetail | None = None


class CandidateVisibleTestDetail(CounterMapDetailModel):
    test_identifier: str
    status: str
    input: dict[str, object]
    expected_output: str | None = None
    actual_output: str | None = None
    duration_ms: int | None = None


class CandidateExecutionDetail(CounterMapDetailModel):
    run_id: UUID
    status: str
    language: str
    code_snapshot_version: int
    visible_passed: int
    visible_failed: int
    visible_tests: list[CandidateVisibleTestDetail]


class CandidateEvidenceDetail(CounterMapDetailModel):
    finding: str
    polarity: Literal["POSITIVE", "NEGATIVE", "MIXED"]
    strength: Literal["WEAK", "MODERATE", "STRONG"]
    independence_level: Literal[
        "INDEPENDENT",
        "AFTER_PROBE",
        "AFTER_LIGHT_GUIDANCE",
        "AFTER_STRONG_HINT",
        "DIRECTLY_TAUGHT",
    ]
    concepts: list[str]
    skills: list[str]
    supporting_moments: list[int]


class CandidateBreakpointEvidenceDetail(CounterMapDetailModel):
    relationship: Literal["CREATED", "REINFORCED", "CONTRADICTED", "RESOLUTION_SUPPORT"]
    finding: str
    polarity: Literal["POSITIVE", "NEGATIVE", "MIXED"]
    independence_level: str


class CandidateBreakpointDetail(CounterMapDetailModel):
    summary: str
    status: str
    severity: str
    concept: str
    skill: str
    independent_verification_required: bool
    evidence: list[CandidateBreakpointEvidenceDetail]


class CandidateCounterMapNodeDetailResponse(CounterMapDetailModel):
    node_id: str
    node_type: CounterMapNodeType
    title: str
    summary: str
    stage: str | None
    source_status: Literal["AVAILABLE", "UNAVAILABLE"]
    message: str | None = None
    statement: CandidateTextDetail | None = None
    delivered_prompt: CandidateDeliveredPromptDetail | None = None
    code: CandidateCodeSnapshotDetail | None = None
    execution: CandidateExecutionDetail | None = None
    evidence: CandidateEvidenceDetail | None = None
    breakpoint: CandidateBreakpointDetail | None = None


class CounterMapNodeNotFound(LookupError):
    pass


class CounterMapNodeDetailResolver:
    """Resolve only the source represented by a persisted CounterMap node."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(
        self,
        *,
        session_id: UUID,
        projection: CounterMapProjection,
        graph: CounterMapGraph,
        node_id: str,
    ) -> CandidateCounterMapNodeDetailResponse:
        node = _node_for_session(graph, session_id, node_id)
        bundle = await CounterMapSourceBuilder(self._session).build(session_id)
        if (
            bundle.source_identity != projection.source_identity
            or bundle.source_watermark != projection.source_watermark
        ):
            return _unavailable(
                node,
                "This source is being revalidated against updated interview evidence.",
            )
        detail = assemble_candidate_detail(node=node, bundle=bundle)
        if detail.source_status == "UNAVAILABLE":
            return detail
        if node.node_type == "CODE":
            return await self._with_code(detail=detail, node=node, session_id=session_id)
        if node.node_type == "TEST":
            return await self._with_execution(detail=detail, node=node, session_id=session_id)
        return detail

    async def _with_code(
        self,
        *,
        detail: CandidateCounterMapNodeDetailResponse,
        node: CounterMapNode,
        session_id: UUID,
    ) -> CandidateCounterMapNodeDetailResponse:
        reference = next(
            (item for item in node.canonical_sources if item.source_type == "CODE_SNAPSHOT"),
            None,
        )
        metadata = node.display_metadata
        if (
            reference is None
            or metadata.code_snapshot_id != reference.source_id
            or metadata.code_version != reference.version
            or metadata.content_hash != reference.content_hash
        ):
            return _unavailable(node, _exact_source_unavailable())
        snapshot = await self._session.scalar(
            select(CodeSnapshot).where(
                CodeSnapshot.id == reference.source_id,
                CodeSnapshot.interview_session_id == session_id,
            )
        )
        if snapshot is None or not _snapshot_matches_projection(snapshot, reference):
            return _unavailable(node, _exact_source_unavailable())
        diff = await self._code_diff(session_id=session_id, snapshot=snapshot)
        return detail.model_copy(
            update={
                "code": CandidateCodeSnapshotDetail(
                    snapshot_id=snapshot.id,
                    version=snapshot.version_number,
                    language=snapshot.language,
                    source_code=snapshot.source_code,
                    diff=diff,
                )
            }
        )

    async def _code_diff(
        self,
        *,
        session_id: UUID,
        snapshot: CodeSnapshot,
    ) -> CandidateCodeDiffDetail | None:
        row = await self._session.scalar(
            select(CodeDiff)
            .where(
                CodeDiff.interview_session_id == session_id,
                CodeDiff.to_snapshot_id == snapshot.id,
            )
            .order_by(CodeDiff.created_at.desc(), CodeDiff.id)
            .limit(1)
        )
        if row is None:
            return None
        previous = await self._session.scalar(
            select(CodeSnapshot).where(
                CodeSnapshot.id == row.from_snapshot_id,
                CodeSnapshot.interview_session_id == session_id,
            )
        )
        if previous is None:
            return None
        return CandidateCodeDiffDetail(
            from_version=previous.version_number,
            to_version=snapshot.version_number,
            diff_format=row.diff_format,
            diff_content=row.diff_content,
            change_summary=row.change_summary,
        )

    async def _with_execution(
        self,
        *,
        detail: CandidateCounterMapNodeDetailResponse,
        node: CounterMapNode,
        session_id: UUID,
    ) -> CandidateCounterMapNodeDetailResponse:
        reference = next(
            (item for item in node.canonical_sources if item.source_type == "EXECUTION"),
            None,
        )
        if reference is None:
            return _unavailable(node, "The exact visible run is unavailable.")
        run = await self._session.scalar(
            select(ExecutionRun).where(
                ExecutionRun.id == reference.source_id,
                ExecutionRun.interview_session_id == session_id,
            )
        )
        if run is None:
            return _unavailable(node, "The exact visible run is unavailable.")
        snapshot = await self._session.scalar(
            select(CodeSnapshot).where(
                CodeSnapshot.id == run.code_snapshot_id,
                CodeSnapshot.interview_session_id == session_id,
            )
        )
        if snapshot is None:
            return _unavailable(node, "The code version for this run is unavailable.")
        rows = list(
            await self._session.scalars(
                select(TestResult)
                .where(
                    TestResult.execution_run_id == run.id,
                    TestResult.is_visible.is_(True),
                )
                .order_by(TestResult.test_identifier, TestResult.id)
            )
        )
        counts = Counter(item.status for item in rows)
        metadata = node.display_metadata
        if (
            metadata.execution_status != run.status
            or metadata.language != run.language
            or metadata.visible_passed != counts["PASSED"]
            or metadata.visible_failed != counts["FAILED"]
        ):
            return _unavailable(node, "The exact visible run is being revalidated.")
        return detail.model_copy(
            update={
                "execution": CandidateExecutionDetail(
                    run_id=run.id,
                    status=run.status,
                    language=run.language,
                    code_snapshot_version=snapshot.version_number,
                    visible_passed=counts["PASSED"],
                    visible_failed=counts["FAILED"],
                    visible_tests=[
                        CandidateVisibleTestDetail(
                            test_identifier=item.test_identifier,
                            status=item.status,
                            input=item.input_json,
                            expected_output=item.expected_output,
                            actual_output=item.actual_output,
                            duration_ms=item.duration_ms,
                        )
                        for item in rows
                    ],
                )
            }
        )


def assemble_candidate_detail(
    *,
    node: CounterMapNode,
    bundle: CounterMapSourceBundle,
) -> CandidateCounterMapNodeDetailResponse:
    if any(
        reference.interview_session_id != bundle.interview_session_id
        for reference in node.canonical_sources
    ):
        raise CounterMapNodeNotFound("CounterMap source is outside this interview")
    base = CandidateCounterMapNodeDetailResponse(
        node_id=node.node_id,
        node_type=node.node_type,
        title=node.title,
        summary=node.summary,
        stage=node.stage,
        source_status="AVAILABLE",
    )
    refs = {item.source_type: item.source_id for item in node.canonical_sources}
    if node.node_type == "CLAIM":
        claim = next(
            (item for item in bundle.claims if item.id == refs.get("CANDIDATE_CLAIM")), None
        )
        if claim is None:
            return _unavailable(node, "The exact statement is unavailable.")
        exact = bool(node.display_metadata.exact_quote and claim.verbatim_excerpt)
        return base.model_copy(
            update={
                "statement": CandidateTextDetail(
                    text=claim.verbatim_excerpt if exact else claim.normalized_claim,
                    exact_quote=exact,
                )
            }
        )
    if node.node_type == "REASONING":
        transcript = next(
            (item for item in bundle.transcripts if item.id == refs.get("CANDIDATE_TRANSCRIPT")),
            None,
        )
        if transcript is None or transcript.speaker != "CANDIDATE":
            return _unavailable(node, "The exact reasoning source is unavailable.")
        return base.model_copy(
            update={"statement": CandidateTextDetail(text=transcript.text, exact_quote=True)}
        )
    if node.node_type == "RESPONSE":
        transcript_ids = {
            item.source_id
            for item in node.canonical_sources
            if item.source_type == "CANDIDATE_TRANSCRIPT"
        }
        transcripts = sorted(
            (
                item
                for item in bundle.transcripts
                if item.id in transcript_ids and item.speaker == "CANDIDATE"
            ),
            key=lambda item: (item.server_sequence, str(item.id)),
        )
        if transcripts:
            return base.model_copy(
                update={
                    "statement": CandidateTextDetail(
                        text=" ".join(item.text for item in transcripts),
                        exact_quote=bool(node.display_metadata.exact_quote),
                    )
                }
            )
        response = next(
            (item for item in bundle.responses if item.id == refs.get("CANDIDATE_RESPONSE")),
            None,
        )
        if response is None or not response.summary:
            return _unavailable(node, "The response source is unavailable.")
        return base.model_copy(
            update={"statement": CandidateTextDetail(text=response.summary, exact_quote=False)}
        )
    if node.node_type in {"QUESTION", "MUTATION", "ASSISTANCE"}:
        delivery = next(
            (item for item in bundle.deliveries if item.id == refs.get("DELIVERED_PROMPT")),
            None,
        )
        if delivery is None or delivery.actual_text != node.summary:
            return _unavailable(node, "The delivered wording is unavailable.")
        concepts_by_id = {
            target.id: target.display_name
            for evidence in bundle.evidence
            for target in evidence.concept_targets
        }
        skills_by_id = {
            target.id: target.display_name
            for evidence in bundle.evidence
            for target in evidence.skill_targets
        }
        return base.model_copy(
            update={
                "delivered_prompt": CandidateDeliveredPromptDetail(
                    text=delivery.actual_text,
                    delivery_state=delivery.delivery_state,
                    why=node.display_metadata.why,
                    assistance_label=node.display_metadata.assistance_label,
                    concepts=(
                        [concepts_by_id[delivery.target_concept_id]]
                        if delivery.target_concept_id in concepts_by_id
                        else []
                    ),
                    skills=(
                        [skills_by_id[delivery.target_skill_dimension_id]]
                        if delivery.target_skill_dimension_id in skills_by_id
                        else []
                    ),
                )
            }
        )
    if node.node_type == "EVIDENCE":
        evidence = next((item for item in bundle.evidence if item.id == refs.get("EVIDENCE")), None)
        if evidence is None:
            return _unavailable(node, "The supporting evidence is unavailable.")
        return base.model_copy(
            update={
                "evidence": CandidateEvidenceDetail(
                    finding=evidence.finding,
                    polarity=evidence.polarity,
                    strength=evidence.strength,
                    independence_level=evidence.independence_level,
                    concepts=[item.display_name for item in evidence.concept_targets],
                    skills=[item.display_name for item in evidence.skill_targets],
                    supporting_moments=sorted(
                        {item.server_sequence for item in evidence.source_links}
                    ),
                )
            }
        )
    if node.node_type == "BREAKPOINT":
        breakpoint = next(
            (item for item in bundle.breakpoints if item.id == refs.get("BREAKPOINT")),
            None,
        )
        if breakpoint is None:
            return _unavailable(node, "The supporting breakpoint detail is unavailable.")
        evidence_by_id = {item.id: item for item in bundle.evidence}
        linked = [
            CandidateBreakpointEvidenceDetail(
                relationship=item.relationship,
                finding=evidence_by_id[item.evidence_id].finding,
                polarity=evidence_by_id[item.evidence_id].polarity,
                independence_level=evidence_by_id[item.evidence_id].independence_level,
            )
            for item in breakpoint.evidence_links
            if item.evidence_id in evidence_by_id
        ]
        return base.model_copy(
            update={
                "breakpoint": CandidateBreakpointDetail(
                    summary=breakpoint.summary,
                    status=breakpoint.status,
                    severity=breakpoint.severity,
                    concept=breakpoint.concept_target.display_name,
                    skill=breakpoint.skill_target.display_name,
                    independent_verification_required=(
                        breakpoint.status in {"OPEN", "IMPROVING", "RETEST_PENDING"}
                        and not any(
                            item.relationship == "RESOLUTION_SUPPORT"
                            and evidence_by_id.get(item.evidence_id) is not None
                            and evidence_by_id[item.evidence_id].independence_level == "INDEPENDENT"
                            for item in breakpoint.evidence_links
                        )
                    ),
                    evidence=linked,
                )
            }
        )
    if node.node_type in {"CODE", "TEST"}:
        return base
    return _unavailable(node, "Source detail is unavailable for this moment.")


def attach_development_source(
    *,
    detail: CandidateCounterMapNodeDetailResponse,
    node: CounterMapNode,
    bundle: CounterMapSourceBundle,
    source_code_for_version: Callable[[int], str | None],
) -> CandidateCounterMapNodeDetailResponse:
    """Add deterministic development-only bodies without bypassing production projection."""

    if node.node_type == "CODE":
        reference = next(
            (item for item in node.canonical_sources if item.source_type == "CODE_SNAPSHOT"),
            None,
        )
        snapshot = next(
            (
                item
                for item in bundle.code_snapshots
                if reference is not None and item.id == reference.source_id
            ),
            None,
        )
        if (
            reference is None
            or snapshot is None
            or node.display_metadata.code_snapshot_id != reference.source_id
            or node.display_metadata.code_version != reference.version
            or node.display_metadata.content_hash != reference.content_hash
        ):
            return _unavailable(node, _exact_source_unavailable())
        source_code = source_code_for_version(snapshot.version)
        if source_code is None:
            return _unavailable(node, _exact_source_unavailable())
        digest = hashlib.sha256(source_code.encode("utf-8")).hexdigest()
        if snapshot.content_hash not in {digest, f"sha256:{digest}"}:
            return _unavailable(node, _exact_source_unavailable())
        return detail.model_copy(
            update={
                "code": CandidateCodeSnapshotDetail(
                    snapshot_id=snapshot.id,
                    version=snapshot.version,
                    language=snapshot.language,
                    source_code=source_code,
                )
            }
        )
    if node.node_type == "TEST":
        reference = next(
            (item for item in node.canonical_sources if item.source_type == "EXECUTION"),
            None,
        )
        execution = next(
            (
                item
                for item in bundle.executions
                if reference is not None and item.id == reference.source_id
            ),
            None,
        )
        snapshot = next(
            (
                item
                for item in bundle.code_snapshots
                if execution is not None and item.id == execution.code_snapshot_id
            ),
            None,
        )
        if execution is None or snapshot is None:
            return _unavailable(node, "The exact visible run is unavailable.")
        return detail.model_copy(
            update={
                "execution": CandidateExecutionDetail(
                    run_id=execution.id,
                    status=execution.status,
                    language=execution.language,
                    code_snapshot_version=snapshot.version,
                    visible_passed=execution.visible_passed,
                    visible_failed=execution.visible_failed,
                    visible_tests=[],
                )
            }
        )
    return detail


def _node_for_session(
    graph: CounterMapGraph,
    session_id: UUID,
    node_id: str,
) -> CounterMapNode:
    if graph.interview_session_id != session_id:
        raise CounterMapNodeNotFound("CounterMap is outside this interview")
    node = next((item for item in graph.nodes if item.node_id == node_id), None)
    if node is None or any(
        source.interview_session_id != session_id for source in node.canonical_sources
    ):
        raise CounterMapNodeNotFound("CounterMap node was not found")
    return node


def _snapshot_matches_projection(
    snapshot: CodeSnapshot,
    reference: CanonicalSourceReference,
) -> bool:
    if (
        snapshot.version_number != reference.version
        or snapshot.content_hash != reference.content_hash
    ):
        return False
    digest = hashlib.sha256(snapshot.source_code.encode("utf-8")).hexdigest()
    return snapshot.content_hash in {digest, f"sha256:{digest}"}


def _unavailable(node: CounterMapNode, message: str) -> CandidateCounterMapNodeDetailResponse:
    return CandidateCounterMapNodeDetailResponse(
        node_id=node.node_id,
        node_type=node.node_type,
        title=node.title,
        summary=node.summary,
        stage=node.stage,
        source_status="UNAVAILABLE",
        message=message,
    )


def _exact_source_unavailable() -> str:
    return "The exact historical code could not be verified, so no code is shown."
