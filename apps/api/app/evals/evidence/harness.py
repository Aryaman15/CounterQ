from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from app.evals.evidence.schema import (
    EvidenceEvaluationFixture,
    EvidenceSemanticScore,
)
from app.evidence.assessment_schema import AssessmentAnalysisResult

FIXTURE_PATH = Path(__file__).with_name("fixtures.json")


def load_fixtures(path: Path = FIXTURE_PATH) -> list[EvidenceEvaluationFixture]:
    values = json.loads(path.read_text(encoding="utf-8"))
    return [EvidenceEvaluationFixture.model_validate(value) for value in values]


def score_output(
    fixture: EvidenceEvaluationFixture, output: AssessmentAnalysisResult
) -> EvidenceSemanticScore:
    expected = fixture.expected
    findings = output.findings
    source_allowlist = {
        str(source["alias"])
        for source in cast(
            list[dict[str, object]], fixture.model_input["assessment_unit"]["source_allowlist"]
        )
    }
    independence = cast(
        dict[str, object], fixture.model_input["assessment_unit"]["independence"]
    ).get("level")
    no_findings_ok = bool(not findings and expected.allow_no_findings)
    supported = (
        no_findings_ok
        or bool(findings)
        and all(finding.assessment_dimension in expected.dimensions for finding in findings)
    )
    false_positive_free = not findings if expected.allow_no_findings else True
    breakpoint_findings = [
        finding for finding in findings if finding.breakpoint_effect == "WEAKNESS"
    ]
    return EvidenceSemanticScore(
        supported_finding_correctness=supported,
        unsupported_finding_false_positive=false_positive_free,
        polarity_appropriate=no_findings_ok
        or all(finding.polarity in expected.polarities for finding in findings),
        concept_target_correct=no_findings_ok
        or all(set(finding.concept_keys).issubset(expected.concept_keys) for finding in findings),
        skill_target_correct=no_findings_ok
        or all(
            set(finding.skill_dimension_keys).issubset(expected.skill_keys) for finding in findings
        ),
        source_provenance_correct=all(
            set(finding.source_aliases).issubset(source_allowlist) for finding in findings
        ),
        independence_correct=independence == expected.independence,
        strength_appropriate=no_findings_ok
        or all(finding.proposed_strength in expected.strengths for finding in findings),
        trivial_error_breakpoint_suppressed=(
            not breakpoint_findings if not expected.breakpoint else True
        ),
        meaningful_breakpoint_detected=(bool(breakpoint_findings) if expected.breakpoint else True),
    )


def empty_failure_score(fixture: EvidenceEvaluationFixture) -> EvidenceSemanticScore:
    return EvidenceSemanticScore(
        supported_finding_correctness=False,
        unsupported_finding_false_positive=fixture.expected.allow_no_findings,
        polarity_appropriate=False,
        concept_target_correct=False,
        skill_target_correct=False,
        source_provenance_correct=False,
        independence_correct=False,
        strength_appropriate=False,
        trivial_error_breakpoint_suppressed=not fixture.expected.breakpoint,
        meaningful_breakpoint_detected=False,
    )


def aggregate(results: list[dict[str, object]]) -> dict[str, object]:
    metric_names = tuple(EvidenceSemanticScore.model_fields)
    scored = len(results)
    return {
        "fixture_count": scored,
        "provider_failures": sum(result["provider_status"] != "SUCCEEDED" for result in results),
        "metrics": {
            metric: (
                sum(bool(cast(dict[str, object], result["score"])[metric]) for result in results)
                / scored
                if scored
                else 0.0
            )
            for metric in metric_names
        },
    }
