"""Deterministic canonical source bundles for the Stage 6B report gate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid5

from app.reports.schema import (
    SESSION_REPORT_INPUT_CONTRACT_VERSION,
    SESSION_REPORT_OUTPUT_CONTRACT_VERSION,
    CanonicalTarget,
    CoachAssistanceFinding,
    DeliveredAssistanceSource,
    ObservedSourceReference,
    ReportBreakpointFinding,
    ReportBreakpointSource,
    ReportEvidenceSource,
    ReportFinding,
    ReportNextAction,
    ReportSection,
    ReportSessionFacts,
    SessionReportSourceBundle,
    SessionReportSynthesis,
    candidate_assistance_label,
)

CORPUS_PATH = Path(__file__).parent / "fixtures" / "stage6b_report_corpus.json"
CORPUS_NAMESPACE = UUID("55ce1b78-f83d-4f54-8256-2dd0831e6200")


@dataclass(frozen=True)
class ReportCorpusFixture:
    fixture_id: str
    bundle: SessionReportSourceBundle
    report: SessionReportSynthesis
    expected_active_evidence: int
    expected_delivered_assistance: int
    expected_report_version_behavior: str


def load_report_corpus() -> tuple[ReportCorpusFixture, ...]:
    document = cast(dict[str, Any], json.loads(CORPUS_PATH.read_text(encoding="utf-8")))
    if document.get("input_contract") != SESSION_REPORT_INPUT_CONTRACT_VERSION:
        raise ValueError("Stage 6B corpus input contract drifted")
    if document.get("output_contract") != SESSION_REPORT_OUTPUT_CONTRACT_VERSION:
        raise ValueError("Stage 6B corpus output contract drifted")
    return tuple(_build_fixture(cast(dict[str, str], item)) for item in document["fixtures"])


def _build_fixture(case: dict[str, str]) -> ReportCorpusFixture:
    fixture_id = case["id"]
    mode = case["mode"]
    evidence_kind = case["evidence"]
    report_kind = case["report"]
    session_id = _id(fixture_id, "session")
    concept_id = _id(fixture_id, "concept")
    skill_id = _id(fixture_id, "skill")
    evidence_id = _id(fixture_id, "evidence")
    before_help_evidence_id = _id(fixture_id, "before-help-evidence")
    breakpoint_id = _id(fixture_id, "breakpoint")
    delivery_id = _id(fixture_id, "delivery")
    target = CanonicalTarget(
        id=concept_id,
        canonical_key="hash_table_complexity",
        display_name="Hash Table Complexity",
    )
    skill = CanonicalTarget(
        id=skill_id,
        canonical_key="complexity_reasoning",
        display_name="Complexity Reasoning",
    )
    active = evidence_kind not in {"INVALIDATED", "STARTER_BASELINE"}
    independence = {
        "POSITIVE_INDEPENDENT": "INDEPENDENT",
        "NEGATIVE_INDEPENDENT": "INDEPENDENT",
        "MIXED_INDEPENDENT": "INDEPENDENT",
        "MIXED_AFTER_PROBE": "AFTER_PROBE",
        "POSITIVE_AFTER_LIGHT_GUIDANCE": "AFTER_LIGHT_GUIDANCE",
        "POSITIVE_AFTER_STRONG_HINT": "AFTER_STRONG_HINT",
        "POSITIVE_DIRECTLY_TAUGHT": "DIRECTLY_TAUGHT",
    }.get(evidence_kind, "INDEPENDENT")
    polarity = (
        "NEGATIVE"
        if evidence_kind == "NEGATIVE_INDEPENDENT"
        else ("MIXED" if evidence_kind.startswith("MIXED") else "POSITIVE")
    )
    assisted = mode == "COACH" and ("ASSIST" in report_kind or report_kind == "TAUGHT_NOT_VERIFIED")
    evidence = (
        []
        if not active
        else [
            ReportEvidenceSource(
                id=evidence_id,
                finding=(
                    "The candidate gave session-specific reasoning about the exact "
                    "complexity boundary."
                ),
                polarity=cast(Any, polarity),
                strength="MODERATE",
                independence_level=cast(Any, independence),
                concept_targets=[target],
                skill_targets=[skill],
                sources=[
                    ObservedSourceReference(
                        event_id=_id(fixture_id, "event"),
                        server_sequence=8,
                        event_type="TRANSCRIPT_FINALIZED",
                        source_kind="CANDIDATE_TRANSCRIPT",
                        candidate_safe_excerpt=(
                            "Average-case lookup is constant, but the worst case can degrade."
                        ),
                    )
                ],
            )
        ]
    )
    if assisted and active:
        evidence.insert(
            0,
            ReportEvidenceSource(
                id=before_help_evidence_id,
                finding="The initial complexity claim omitted the worst-case boundary.",
                polarity="NEGATIVE",
                strength="MODERATE",
                independence_level="INDEPENDENT",
                concept_targets=[target],
                skill_targets=[skill],
                sources=[
                    ObservedSourceReference(
                        event_id=_id(fixture_id, "before-help-event"),
                        server_sequence=4,
                        event_type="TRANSCRIPT_FINALIZED",
                        source_kind="CANDIDATE_TRANSCRIPT",
                        candidate_safe_excerpt="Hash lookup is always constant time.",
                    )
                ],
            ),
        )
    needs_breakpoint = report_kind in {"OPEN_BREAKPOINT", "ASSISTED_OPEN_BREAKPOINT"}
    breakpoint_support_id = (
        before_help_evidence_id if report_kind == "ASSISTED_OPEN_BREAKPOINT" else evidence_id
    )
    breakpoints = (
        []
        if not needs_breakpoint or not active
        else [
            ReportBreakpointSource(
                id=breakpoint_id,
                status="OPEN",
                severity="MEDIUM",
                summary="Worst-case complexity still needs independent defense.",
                concept_target=target,
                skill_target=skill,
                supporting_evidence_ids=[breakpoint_support_id],
                resolution_support_evidence_ids=(
                    [evidence_id] if report_kind == "ASSISTED_OPEN_BREAKPOINT" else []
                ),
            )
        ]
    )
    assistance_type = (
        "DIRECT_TEACHING"
        if independence == "DIRECTLY_TAUGHT"
        else "STRUCTURAL_HINT"
        if independence == "AFTER_STRONG_HINT"
        else "CONCEPTUAL_HINT"
    )
    assistance = (
        []
        if not assisted
        else [
            DeliveredAssistanceSource(
                prompt_id=_id(fixture_id, "prompt"),
                delivery_id=delivery_id,
                assistance_type=cast(Any, assistance_type),
                hint_level=cast(Any, assistance_type),
                actual_text="What assumption does expected hash lookup make?",
                delivery_state="DELIVERED",
                delivered_server_sequence=5,
                target_concept_id=concept_id,
                target_skill_dimension_id=skill_id,
            )
        ]
    )
    finding = ReportFinding(
        title="Complexity reasoning was session-specific",
        finding="You distinguished expected lookup behavior from its worst-case boundary.",
        evidence_ids=[evidence_id] if active else [],
        breakpoint_id=(
            breakpoint_id
            if breakpoints and report_kind != "ASSISTED_OPEN_BREAKPOINT"
            else None
        ),
        independence_level=cast(Any, independence) if active else None,
        based_on_insufficient_evidence=False,
    )
    insufficient = ReportSection(
        status="INSUFFICIENT_EVIDENCE",
        items=[],
        insufficient_evidence_message="Not enough evidence from this session.",
    )
    supported = ReportSection(
        status="SUPPORTED",
        items=[finding],
        insufficient_evidence_message=None,
    )
    coach_findings = (
        []
        if not assistance
        else [
            CoachAssistanceFinding(
                title="The complexity explanation changed after help",
                explanation=(
                    "After the delivered guidance, you revised the complexity claim; "
                    "independent verification is still needed."
                ),
                delivery_ids=[delivery_id],
                assistance_type=cast(Any, assistance_type),
                hint_level=cast(Any, assistance_type),
                assistance_label=candidate_assistance_label(
                    cast(Any, assistance_type), cast(Any, assistance_type)
                ),
                before_help_evidence_ids=[before_help_evidence_id],
                after_help_evidence_ids=[evidence_id],
                later_independence_level=cast(Any, independence),
                independent_verification_missing=independence != "INDEPENDENT",
            )
        ]
    )
    report = SessionReportSynthesis(
        contract_version=SESSION_REPORT_OUTPUT_CONTRACT_VERSION,
        summary=[finding]
        if active
        else [
            ReportFinding(
                title="This session produced limited evaluative evidence",
                finding=(
                    "CounterQ did not observe enough active candidate work for a "
                    "detailed conclusion."
                ),
                evidence_ids=[],
                breakpoint_id=None,
                independence_level=None,
                based_on_insufficient_evidence=True,
            )
        ],
        strengths=[finding]
        if active and polarity in {"POSITIVE", "MIXED"} and independence == "INDEPENDENT"
        else [],
        breakpoints=[
            ReportBreakpointFinding(
                breakpoint_id=breakpoint_id,
                concept_id=concept_id,
                skill_dimension_id=skill_id,
                concept_label=target.display_name,
                skill_label=skill.display_name,
                title="Worst-case complexity remained unresolved",
                explanation=(
                    "The session did not establish the worst-case lookup bound independently."
                ),
                status="OPEN",
                severity="MEDIUM",
                evidence_ids=[breakpoint_support_id],
            )
        ]
        if breakpoints
        else [],
        claim_defense=supported if active else insufficient,
        correctness_implementation=supported if active else insufficient,
        complexity=supported if active else insufficient,
        edge_cases=insufficient,
        debugging=supported if report_kind == "DEBUGGING_STRENGTH" else insufficient,
        adaptability=supported
        if report_kind in {"PROBED_CORRECTION", "ASSISTED_IMPROVEMENT"}
        else insufficient,
        coach_assistance=coach_findings,
        next_actions=[
            ReportNextAction(
                action="Practice defending expected and worst-case lookup behavior separately.",
                evidence_ids=[evidence_id] if active else [],
                breakpoint_ids=[breakpoint_id] if breakpoints else [],
                based_on_insufficient_evidence=not active,
            )
        ],
    )
    return ReportCorpusFixture(
        fixture_id=fixture_id,
        bundle=SessionReportSourceBundle(
            input_contract_version=SESSION_REPORT_INPUT_CONTRACT_VERSION,
            session=ReportSessionFacts(
                interview_session_id=session_id,
                mode=cast(Any, mode),
                level="NEW_GRAD",
                language="python",
                problem_version_id=_id(fixture_id, "problem"),
                problem_title="Two Sum",
                started_at="2026-09-04T10:00:00+00:00",
                completed_at="2026-09-04T10:30:00+00:00",
                duration_seconds=1800,
                source_watermark=12,
            ),
            evidence=evidence,
            breakpoints=breakpoints,
            delivered_prompts=[],
            delivered_assistance=assistance,
            candidate_claims=[],
            candidate_responses=[],
            executions=[],
        ),
        report=report,
        expected_active_evidence=len(evidence),
        expected_delivered_assistance=len(assistance),
        expected_report_version_behavior=(
            "IDEMPOTENT_THEN_NEW_VERSION"
            if report_kind == "VERSIONED_REGENERATION"
            else "NOT_APPLICABLE"
        ),
    )


def _id(fixture_id: str, role: str) -> UUID:
    return uuid5(CORPUS_NAMESPACE, f"{fixture_id}:{role}")
