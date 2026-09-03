"""Strict Session Report input/output contracts and candidate document shape."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai_gateway.structured_output import StrictReasoningOutputModel

SESSION_REPORT_INPUT_CONTRACT_VERSION = "session-report-input.v1"
SESSION_REPORT_OUTPUT_CONTRACT_VERSION = "session-report-output.v1"

IndependenceLevel = Literal[
    "INDEPENDENT",
    "AFTER_PROBE",
    "AFTER_LIGHT_GUIDANCE",
    "AFTER_STRONG_HINT",
    "DIRECTLY_TAUGHT",
]
AssistanceType = Literal[
    "METACOGNITIVE",
    "PROBLEM_NARROWING",
    "CONCEPTUAL_HINT",
    "STRUCTURAL_HINT",
    "DIRECT_TEACHING",
    "DEBUGGING_HINT",
    "CORRECTNESS_FEEDBACK",
]
HintLevel = Literal[
    "METACOGNITIVE",
    "PROBLEM_NARROWING",
    "CONCEPTUAL_HINT",
    "STRUCTURAL_HINT",
    "DIRECT_TEACHING",
]


class ReportSourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CanonicalTarget(ReportSourceModel):
    id: UUID
    canonical_key: str
    display_name: str


class ObservedSourceReference(ReportSourceModel):
    event_id: UUID
    server_sequence: int
    event_type: str
    source_kind: str
    candidate_safe_excerpt: str | None


class ReportEvidenceSource(ReportSourceModel):
    id: UUID
    finding: str
    polarity: Literal["POSITIVE", "NEGATIVE", "MIXED"]
    strength: Literal["WEAK", "MODERATE", "STRONG"]
    independence_level: IndependenceLevel
    concept_targets: list[CanonicalTarget]
    skill_targets: list[CanonicalTarget]
    sources: list[ObservedSourceReference]


class ReportBreakpointSource(ReportSourceModel):
    id: UUID
    status: Literal["OPEN", "RETEST_PENDING", "IMPROVING", "RESOLVED", "DISMISSED"]
    severity: str
    summary: str
    concept_target: CanonicalTarget
    skill_target: CanonicalTarget
    supporting_evidence_ids: list[UUID]
    contradicting_evidence_ids: list[UUID] = Field(default_factory=list)
    resolution_support_evidence_ids: list[UUID] = Field(default_factory=list)


class DeliveredPromptSource(ReportSourceModel):
    prompt_id: UUID
    delivery_id: UUID
    kind: str
    probe_strategy: str | None
    actual_text: str
    delivery_state: str
    delivered_server_sequence: int


class DeliveredAssistanceSource(ReportSourceModel):
    prompt_id: UUID
    delivery_id: UUID
    assistance_type: AssistanceType
    hint_level: HintLevel
    actual_text: str
    delivery_state: str
    delivered_server_sequence: int
    target_concept_id: UUID | None
    target_skill_dimension_id: UUID | None


class CandidateClaimSource(ReportSourceModel):
    id: UUID
    claim_type: str
    normalized_claim: str
    source_event_id: UUID
    source_server_sequence: int


class CandidateResponseSource(ReportSourceModel):
    id: UUID
    prompt_id: UUID | None
    summary: str | None
    source_event_ids: list[UUID]


class ExecutionSource(ReportSourceModel):
    id: UUID
    code_snapshot_id: UUID
    language: str
    status: str
    visible_passed: int
    visible_failed: int
    completed_at: str | None


class ReportSessionFacts(ReportSourceModel):
    interview_session_id: UUID
    mode: Literal["COACH", "SIMULATION"]
    level: str
    language: str
    problem_version_id: UUID
    problem_title: str
    started_at: str
    completed_at: str
    duration_seconds: int
    source_watermark: int


class SessionReportSourceBundle(ReportSourceModel):
    input_contract_version: Literal["session-report-input.v1"]
    session: ReportSessionFacts
    evidence: list[ReportEvidenceSource]
    breakpoints: list[ReportBreakpointSource]
    delivered_prompts: list[DeliveredPromptSource]
    delivered_assistance: list[DeliveredAssistanceSource]
    candidate_claims: list[CandidateClaimSource]
    candidate_responses: list[CandidateResponseSource]
    executions: list[ExecutionSource]

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def source_identity(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def serialize_for_ai(self) -> str:
        value = self.model_dump(mode="json")
        candidate_interpretations = value.pop("candidate_claims")
        candidate_responses = value.pop("candidate_responses")
        candidate_content_by_event_id: dict[str, dict[str, object]] = {}
        for evidence in value["evidence"]:
            for source in evidence["sources"]:
                excerpt = source.get("candidate_safe_excerpt")
                if source.get("source_kind") != "CANDIDATE_TRANSCRIPT" or not excerpt:
                    continue
                event_id = str(source["event_id"])
                candidate_content_by_event_id[event_id] = {
                    "event_id": event_id,
                    "server_sequence": source["server_sequence"],
                    "event_type": source["event_type"],
                    "source_kind": source["source_kind"],
                    "candidate_safe_excerpt": excerpt,
                }
                source["candidate_safe_excerpt"] = None
        return json.dumps(
            {
                "input_contract_version": SESSION_REPORT_INPUT_CONTRACT_VERSION,
                "trusted_canonical_context": value,
                "untrusted_interpretation_and_candidate_context": {
                    "authority": "CONTEXT_ONLY",
                    "candidate_content_by_event_id": candidate_content_by_event_id,
                    "candidate_claims": candidate_interpretations,
                    "candidate_responses": candidate_responses,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )


class ReportFinding(StrictReasoningOutputModel):
    title: str = Field(min_length=1, max_length=140)
    finding: str = Field(min_length=1, max_length=700)
    evidence_ids: list[UUID] = Field(max_length=8)
    breakpoint_id: UUID | None
    independence_level: IndependenceLevel | None
    based_on_insufficient_evidence: bool

    @model_validator(mode="after")
    def validate_support_shape(self) -> ReportFinding:
        if self.based_on_insufficient_evidence and (
            self.evidence_ids
            or self.breakpoint_id is not None
            or self.independence_level is not None
        ):
            raise ValueError("An insufficient-evidence finding cannot cite canonical support")
        if self.evidence_ids and self.independence_level is None:
            raise ValueError("An Evidence-backed finding must preserve independence attribution")
        return self


class ReportSection(StrictReasoningOutputModel):
    status: Literal["SUPPORTED", "INSUFFICIENT_EVIDENCE"]
    items: list[ReportFinding] = Field(max_length=6)
    insufficient_evidence_message: str | None

    @model_validator(mode="after")
    def validate_section_shape(self) -> ReportSection:
        if self.status == "SUPPORTED" and not self.items:
            raise ValueError("A supported section requires at least one finding")
        if self.status == "INSUFFICIENT_EVIDENCE" and self.items:
            raise ValueError("An insufficient-evidence section cannot contain findings")
        if self.status == "INSUFFICIENT_EVIDENCE" and not self.insufficient_evidence_message:
            raise ValueError("An insufficient-evidence section requires candidate-safe copy")
        if self.status == "SUPPORTED" and self.insufficient_evidence_message is not None:
            raise ValueError("A supported section cannot include insufficient-evidence copy")
        return self


class ReportBreakpointFinding(StrictReasoningOutputModel):
    breakpoint_id: UUID
    concept_id: UUID
    skill_dimension_id: UUID
    concept_label: str = Field(min_length=1, max_length=140)
    skill_label: str = Field(min_length=1, max_length=140)
    title: str = Field(min_length=1, max_length=140)
    explanation: str = Field(min_length=1, max_length=800)
    status: str = Field(min_length=1, max_length=32)
    severity: str = Field(min_length=1, max_length=32)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=8)


class CoachAssistanceFinding(StrictReasoningOutputModel):
    title: str = Field(min_length=1, max_length=140)
    explanation: str = Field(min_length=1, max_length=800)
    delivery_ids: list[UUID] = Field(min_length=1, max_length=6)
    assistance_type: AssistanceType
    hint_level: HintLevel
    assistance_label: str = Field(min_length=1, max_length=160)
    before_help_evidence_ids: list[UUID] = Field(max_length=8)
    after_help_evidence_ids: list[UUID] = Field(max_length=8)
    later_independence_level: IndependenceLevel | None
    independent_verification_missing: bool


class ReportNextAction(StrictReasoningOutputModel):
    action: str = Field(min_length=1, max_length=360)
    evidence_ids: list[UUID] = Field(max_length=8)
    breakpoint_ids: list[UUID] = Field(max_length=4)
    based_on_insufficient_evidence: bool


class SessionReportSynthesis(StrictReasoningOutputModel):
    contract_version: Literal["session-report-output.v1"]
    summary: list[ReportFinding] = Field(min_length=1, max_length=4)
    strengths: list[ReportFinding] = Field(max_length=6)
    breakpoints: list[ReportBreakpointFinding] = Field(max_length=6)
    claim_defense: ReportSection
    correctness_implementation: ReportSection
    complexity: ReportSection
    edge_cases: ReportSection
    debugging: ReportSection
    adaptability: ReportSection
    coach_assistance: list[CoachAssistanceFinding] = Field(max_length=6)
    next_actions: list[ReportNextAction] = Field(max_length=6)


class CandidateSourceDetail(ReportSourceModel):
    evidence_id: UUID
    finding: str
    attribution: str
    source_label: str
    source_excerpt: str | None


class SessionReportDocument(ReportSourceModel):
    contract_version: Literal["session-report-output.v1"]
    metadata: ReportSessionFacts
    summary: list[ReportFinding]
    strengths: list[ReportFinding]
    breakpoints: list[ReportBreakpointFinding]
    claim_defense: ReportSection
    correctness_implementation: ReportSection
    complexity: ReportSection
    edge_cases: ReportSection
    debugging: ReportSection
    adaptability: ReportSection
    coach_assistance: list[CoachAssistanceFinding]
    next_actions: list[ReportNextAction]
    source_details: list[CandidateSourceDetail]


def build_candidate_document(
    bundle: SessionReportSourceBundle,
    synthesis: SessionReportSynthesis,
) -> SessionReportDocument:
    details = [
        CandidateSourceDetail(
            evidence_id=item.id,
            finding=item.finding,
            attribution=candidate_attribution(item.independence_level),
            source_label=_source_label(item),
            source_excerpt=next(
                (
                    source.candidate_safe_excerpt
                    for source in item.sources
                    if source.candidate_safe_excerpt
                ),
                None,
            ),
        )
        for item in bundle.evidence
    ]
    return SessionReportDocument(
        metadata=bundle.session,
        source_details=details,
        **synthesis.model_dump(),
    )


def candidate_attribution(level: IndependenceLevel) -> str:
    return {
        "INDEPENDENT": "Independently demonstrated",
        "AFTER_PROBE": "Demonstrated after interviewer challenge",
        "AFTER_LIGHT_GUIDANCE": "Demonstrated after light guidance",
        "AFTER_STRONG_HINT": "Demonstrated after a strong hint",
        "DIRECTLY_TAUGHT": "Demonstrated after explanation",
    }[level]


def candidate_assistance_label(
    assistance_type: AssistanceType,
    hint_level: HintLevel,
) -> str:
    labels = {
        "METACOGNITIVE": "Reflection prompt",
        "PROBLEM_NARROWING": "Problem-narrowing guidance",
        "CONCEPTUAL_HINT": "Conceptual hint",
        "STRUCTURAL_HINT": "Structural hint",
        "DIRECT_TEACHING": "Direct explanation",
        "DEBUGGING_HINT": "Debugging hint",
        "CORRECTNESS_FEEDBACK": "Correctness feedback",
    }
    type_label = labels[assistance_type]
    level_label = labels[hint_level]
    return type_label if type_label == level_label else f"{type_label} · {level_label}"


def _source_label(evidence: ReportEvidenceSource) -> str:
    kinds = {source.event_type for source in evidence.sources}
    if any("CODE" in kind for kind in kinds):
        return "Code from this interview"
    if any(kind in {"RUN_CLICKED", "COMPILE_COMPLETED", "TEST_COMPLETED"} for kind in kinds):
        return "Execution from this interview"
    return "Conversation from this interview"
