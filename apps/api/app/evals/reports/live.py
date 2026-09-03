"""Explicitly guarded live Stage 6B Session Report quality harness."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from collections.abc import Sequence
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
    ReportBreakpointSource,
    ReportEvidenceSource,
    ReportFinding,
    SessionReportSynthesis,
)
from app.reports.source import SessionReportSourceBuilder
from app.reports.validator import SessionReportValidationError, SessionReportValidator

LIVE_OPT_IN = "COUNTERQ_STAGE6B_LIVE_EVAL"
LIVE_SESSION_ID = "COUNTERQ_STAGE6B_SESSION_ID"


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
            "metrics": _metrics(bundle.evidence, bundle.breakpoints, result.parsed, issues),
            "validator_issues": issues,
        }
    finally:
        await engine.dispose()


def _metrics(
    evidence: Sequence[ReportEvidenceSource],
    breakpoints: Sequence[ReportBreakpointSource],
    report: SessionReportSynthesis,
    issues: tuple[str, ...],
) -> dict[str, float | int]:
    findings = _findings(report)
    recommendations = report.next_actions
    supported_findings = sum(bool(item.evidence_ids) for item in findings)
    restrained_findings = sum(item.based_on_insufficient_evidence for item in findings)
    recommendation_support = sum(
        bool(item.evidence_ids or item.breakpoint_ids or item.based_on_insufficient_evidence)
        for item in recommendations
    )
    evidence_references = sum(len(item.evidence_ids) for item in findings)
    evidence_ids = {str(item.id) for item in evidence}
    breakpoint_ids = {str(item.id) for item in breakpoints}
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
        "unsupported_material_claim_rate": _rate(
            len(findings) - supported_findings - restrained_findings, len(findings)
        ),
        "evidence_reference_validity": _rate(valid_evidence_references, evidence_references),
        "breakpoint_reference_validity": _rate(valid_breakpoints, len(report_breakpoints)),
        "independence_overstatement_rate": int("INDEPENDENCE_OVERSTATEMENT" in issue_set),
        "assistance_attribution_correctness": int(
            not any("ASSISTANCE" in issue for issue in issue_set)
        ),
        "insufficient_evidence_restraint": _rate(restrained_findings, max(1, restrained_findings)),
        "recommendation_traceability": _rate(recommendation_support, len(recommendations)),
        "numeric_score_violation": int("NUMERIC_SCORE" in issue_set),
        "personality_judgment_violation": int("PERSONALITY_JUDGMENT" in issue_set),
        "hiring_prediction_violation": int("HIRING_PREDICTION" in issue_set),
        "technical_correctness": _rate(valid_evidence_references, evidence_references),
        "candidate_specificity": _rate(supported_findings, len(findings)),
        "concision_readability": int(average_words <= 120),
    }


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
