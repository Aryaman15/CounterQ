"""Explicitly guarded live Stage 6B Session Report quality harness."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai_gateway.gateway import AIGateway
from app.ai_gateway.provider_factory import build_reasoning_provider
from app.config.settings import get_settings
from app.db.registry import register_orm_models
from app.db.session import build_engine
from app.evals.reports.corpus import CORPUS_PATH
from app.reports.policy import (
    SESSION_REPORT_INSTRUCTIONS,
    SESSION_REPORT_POLICY_KEY,
    SESSION_REPORT_POLICY_VERSION,
    SESSION_REPORT_PURPOSE,
    session_report_policy_descriptor,
)
from app.reports.schema import (
    SESSION_REPORT_INPUT_CONTRACT_VERSION,
    SESSION_REPORT_OUTPUT_CONTRACT_VERSION,
    ReportFinding,
    SessionReportSourceBundle,
    SessionReportSynthesis,
    build_candidate_document,
)
from app.reports.source import SessionReportSourceBuilder
from app.reports.validator import SessionReportValidationError, SessionReportValidator

LIVE_OPT_IN = "COUNTERQ_STAGE6B_LIVE_EVAL"
LIVE_SESSION_ID = "COUNTERQ_STAGE6B_SESSION_ID"
ASSISTANCE_VALIDATION_ISSUES = frozenset(
    {
        "SIMULATION_ASSISTANCE_CLAIM",
        "UNDELIVERED_ASSISTANCE_CLAIM",
        "ASSISTANCE_TYPE_MISMATCH",
        "ASSISTANCE_LABEL_MISMATCH",
        "BEFORE_HELP_ORDER_INVALID",
        "AFTER_HELP_ORDER_INVALID",
        "ASSISTANCE_ATTRIBUTION_MISMATCH",
        "PROBE_MISLABELLED_AS_ASSISTANCE",
        "ASSISTANCE_ATTRIBUTION_MISSING",
        "ASSISTANCE_VERIFICATION_MISMATCH",
        "ASSISTANCE_TARGET_MISMATCH",
    }
)


def assert_live_enabled() -> None:
    if os.getenv(LIVE_OPT_IN) != "1":
        raise SystemExit(
            f"Refusing live report evaluation. Set {LIVE_OPT_IN}=1 explicitly to opt in."
        )


async def run_live() -> dict[str, object]:
    assert_live_enabled()
    raw_session_id = os.getenv(LIVE_SESSION_ID)
    if not raw_session_id:
        raise SystemExit(f"{LIVE_SESSION_ID} must name one completed local interview")
    session_id = UUID(raw_session_id)
    register_orm_models()
    settings = get_settings()
    engine = build_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            bundle = await SessionReportSourceBuilder(session).build(session_id)
        gateway = AIGateway(
            settings=settings,
            sessionmaker=sessions,
            provider=build_reasoning_provider(settings),
        )
        result = await gateway.reason_structured(
            interview_session_id=session_id,
            capability="STANDARD_REASONING",
            purpose=SESSION_REPORT_PURPOSE,
            policy=session_report_policy_descriptor(),
            instructions=SESSION_REPORT_INSTRUCTIONS,
            input_content=bundle.serialize_for_ai(),
            output_model=SessionReportSynthesis,
            correlation_id=f"stage6b-live:{session_id}",
            metadata={"harness": "stage6b-live", "source_identity": bundle.source_identity},
        )
        issues: tuple[str, ...] = ()
        try:
            SessionReportValidator().validate(bundle=bundle, report=result.parsed)
        except SessionReportValidationError as exc:
            issues = tuple(sorted({issue.category for issue in exc.issues}))
        quality = build_review_payload(bundle, result.parsed, issues)
        return {
            "pins": {
                "git_revision": _git_revision(),
                "fixture_digest": "sha256:" + hashlib.sha256(CORPUS_PATH.read_bytes()).hexdigest(),
                "policy": f"{SESSION_REPORT_POLICY_KEY}/{SESSION_REPORT_POLICY_VERSION}",
                "input_contract": SESSION_REPORT_INPUT_CONTRACT_VERSION,
                "output_contract": SESSION_REPORT_OUTPUT_CONTRACT_VERSION,
                "model": result.model,
                "reasoning_effort": settings.reasoning_standard_effort,
                "input_tokens": result.usage.input_tokens,
                "cached_input_tokens": result.usage.cached_input_tokens,
                "output_tokens": result.usage.output_tokens,
                "latency_ms": result.latency_ms,
                "estimated_cost": (
                    str(result.estimated_cost) if result.estimated_cost is not None else None
                ),
                "currency": result.currency,
            },
            **quality,
            "validator_issues": issues,
        }
    finally:
        await engine.dispose()


def build_review_payload(
    bundle: SessionReportSourceBundle,
    report: SessionReportSynthesis,
    issues: tuple[str, ...],
) -> dict[str, object]:
    findings = _findings(report)
    recommendations = report.next_actions
    supported_findings = sum(bool(item.evidence_ids) for item in findings)
    restrained_findings = sum(item.based_on_insufficient_evidence for item in findings)
    recommendation_support = sum(
        bool(item.evidence_ids or item.breakpoint_ids or item.based_on_insufficient_evidence)
        for item in recommendations
    )
    evidence_references = sum(len(item.evidence_ids) for item in findings)
    evidence_ids = {str(item.id) for item in bundle.evidence}
    breakpoint_ids = {str(item.id) for item in bundle.breakpoints}
    valid_evidence_references = sum(
        str(identifier) in evidence_ids for item in findings for identifier in item.evidence_ids
    )
    report_breakpoints = report.breakpoints
    valid_breakpoints = sum(
        str(item.breakpoint_id) in breakpoint_ids for item in report_breakpoints
    )
    average_words = (
        sum(len(f"{item.title} {item.finding}".split()) for item in findings) / len(findings)
        if findings
        else 0.0
    )
    issue_set = set(issues)
    return {
        "automated_hard_checks": {
            "unsupported_material_claim_rate": _rate(
                len(findings) - supported_findings - restrained_findings,
                len(findings),
            ),
            "evidence_reference_validity": _rate(
                valid_evidence_references,
                evidence_references,
            ),
            "breakpoint_reference_validity": _rate(
                valid_breakpoints,
                len(report_breakpoints),
            ),
            "independence_overstatement_rate": int(
                "INDEPENDENCE_OVERSTATEMENT" in issue_set
            ),
            "assistance_attribution_correctness": int(
                not issue_set.intersection(ASSISTANCE_VALIDATION_ISSUES)
            ),
            "recommendation_traceability": _rate(
                recommendation_support,
                len(recommendations),
            ),
            "numeric_score_violation": int("NUMERIC_SCORE" in issue_set),
            "personality_judgment_violation": int("PERSONALITY_JUDGMENT" in issue_set),
            "hiring_prediction_violation": int("HIRING_PREDICTION" in issue_set),
            "raw_internal_id_leakage": int("RAW_INTERNAL_ID_IN_COPY" in issue_set),
            "report_validator_issue_count": len(issues),
            "approximate_average_material_finding_words": round(average_words, 1),
        },
        "manual_review_required": {
            "technical_correctness": "MANUAL_REVIEW_REQUIRED",
            "candidate_specificity": "MANUAL_REVIEW_REQUIRED",
            "insufficient_evidence_restraint": "MANUAL_REVIEW_REQUIRED",
        },
        "candidate_facing_report": build_candidate_document(bundle, report).model_dump(
            mode="json"
        ),
        "grounding_view": _grounding_view(bundle, report),
    }


def _grounding_view(
    bundle: SessionReportSourceBundle,
    report: SessionReportSynthesis,
) -> list[dict[str, object]]:
    evidence = {item.id: item for item in bundle.evidence}
    breakpoints = {item.id: item for item in bundle.breakpoints}
    result: list[dict[str, object]] = []
    for path, finding in _material_findings(report):
        breakpoint = (
            breakpoints.get(finding.breakpoint_id)
            if finding.breakpoint_id is not None
            else None
        )
        result.append(
            {
                "path": path,
                "title": finding.title,
                "finding": finding.finding,
                "based_on_insufficient_evidence": finding.based_on_insufficient_evidence,
                "cited_evidence": [
                    {
                        "finding": source.finding,
                        "polarity": source.polarity,
                        "independence_level": source.independence_level,
                        "concept_labels": [
                            target.display_name for target in source.concept_targets
                        ],
                        "skill_labels": [target.display_name for target in source.skill_targets],
                    }
                    for identifier in finding.evidence_ids
                    if (source := evidence.get(identifier)) is not None
                ],
                "cited_breakpoint": (
                    {"status": breakpoint.status, "severity": breakpoint.severity}
                    if breakpoint is not None
                    else None
                ),
            }
        )
    for index, item in enumerate(report.breakpoints):
        breakpoint = breakpoints.get(item.breakpoint_id)
        result.append(
            {
                "path": f"breakpoints[{index}]",
                "title": item.title,
                "finding": item.explanation,
                "based_on_insufficient_evidence": False,
                "cited_evidence": [
                    {
                        "finding": source.finding,
                        "polarity": source.polarity,
                        "independence_level": source.independence_level,
                        "concept_labels": [
                            target.display_name for target in source.concept_targets
                        ],
                        "skill_labels": [target.display_name for target in source.skill_targets],
                    }
                    for identifier in item.evidence_ids
                    if (source := evidence.get(identifier)) is not None
                ],
                "cited_breakpoint": (
                    {"status": breakpoint.status, "severity": breakpoint.severity}
                    if breakpoint is not None
                    else None
                ),
            }
        )
    return result


def _findings(report: SessionReportSynthesis) -> list[ReportFinding]:
    result = [*report.summary, *report.strengths]
    for section in (
        report.claim_defense,
        report.correctness_implementation,
        report.complexity,
        report.edge_cases,
        report.debugging,
        report.adaptability,
    ):
        result.extend(section.items)
    return result


def _material_findings(
    report: SessionReportSynthesis,
) -> list[tuple[str, ReportFinding]]:
    result = [(f"summary[{index}]", item) for index, item in enumerate(report.summary)]
    result.extend((f"strengths[{index}]", item) for index, item in enumerate(report.strengths))
    for section_name, section in (
        ("claim_defense", report.claim_defense),
        ("correctness_implementation", report.correctness_implementation),
        ("complexity", report.complexity),
        ("edge_cases", report.edge_cases),
        ("debugging", report.debugging),
        ("adaptability", report.adaptability),
    ):
        result.extend(
            (f"{section_name}.items[{index}]", item)
            for index, item in enumerate(section.items)
        )
    return result


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def _git_revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[5],
    ).stdout.strip()


def main() -> None:
    print(json.dumps(asyncio.run(run_live()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
