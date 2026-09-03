"""Deterministic admission gate for AI-synthesized Session Reports."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from app.reports.schema import (
    CoachAssistanceFinding,
    ReportFinding,
    ReportNextAction,
    ReportSection,
    SessionReportSourceBundle,
    SessionReportSynthesis,
    candidate_assistance_label,
)


@dataclass(frozen=True)
class ReportValidationIssue:
    category: str
    path: str


class SessionReportValidationError(ValueError):
    def __init__(self, issues: list[ReportValidationIssue]) -> None:
        self.issues = tuple(issues)
        categories = ", ".join(sorted({issue.category for issue in issues}))
        super().__init__(f"Session Report failed deterministic validation: {categories}")


class SessionReportValidator:
    """Reject unsupported IDs, attribution and forbidden candidate claims."""

    def validate(
        self,
        *,
        bundle: SessionReportSourceBundle,
        report: SessionReportSynthesis,
    ) -> None:
        issues: list[ReportValidationIssue] = []
        evidence = {item.id: item for item in bundle.evidence}
        breakpoints = {item.id: item for item in bundle.breakpoints}
        assistance = {item.delivery_id: item for item in bundle.delivered_assistance}

        for path, item in _material_findings(report):
            if not item.evidence_ids:
                if not item.based_on_insufficient_evidence:
                    issues.append(ReportValidationIssue("UNSUPPORTED_MATERIAL_CLAIM", path))
                continue
            if item.based_on_insufficient_evidence:
                issues.append(ReportValidationIssue("AMBIGUOUS_MATERIAL_SUPPORT", path))
            cited = []
            for evidence_id in item.evidence_ids:
                source = evidence.get(evidence_id)
                if source is None:
                    issues.append(ReportValidationIssue("INVALID_EVIDENCE_REFERENCE", path))
                else:
                    cited.append(source)
            if item.breakpoint_id is not None:
                breakpoint = breakpoints.get(item.breakpoint_id)
                if breakpoint is None:
                    issues.append(ReportValidationIssue("INVALID_BREAKPOINT_REFERENCE", path))
                elif not set(item.evidence_ids).intersection(breakpoint.supporting_evidence_ids):
                    issues.append(ReportValidationIssue("BREAKPOINT_EVIDENCE_MISMATCH", path))
            if item.independence_level is not None and cited:
                if any(source.independence_level != item.independence_level for source in cited):
                    issues.append(ReportValidationIssue("INDEPENDENCE_OVERSTATEMENT", path))
            text = f"{item.title} {item.finding}".lower()
            if "independent" in text and any(
                source.independence_level != "INDEPENDENT" for source in cited
            ):
                issues.append(ReportValidationIssue("INDEPENDENCE_OVERSTATEMENT", path))
            if "hint" in text and any(
                source.independence_level == "AFTER_PROBE" for source in cited
            ):
                issues.append(ReportValidationIssue("PROBE_MISLABELLED_AS_HINT", path))

        for index, item in enumerate(report.strengths):
            for evidence_id in item.evidence_ids:
                source = evidence.get(evidence_id)
                if source is not None and source.polarity not in {"POSITIVE", "MIXED"}:
                    issues.append(
                        ReportValidationIssue(
                            "STRENGTH_WITHOUT_POSITIVE_EVIDENCE", f"strengths[{index}]"
                        )
                    )
                if source is not None and source.independence_level != "INDEPENDENT":
                    issues.append(
                        ReportValidationIssue(
                            "ASSISTED_EVIDENCE_IN_INDEPENDENT_STRENGTH",
                            f"strengths[{index}]",
                        )
                    )

        for index, breakpoint_item in enumerate(report.breakpoints):
            path = f"breakpoints[{index}]"
            breakpoint_source = breakpoints.get(breakpoint_item.breakpoint_id)
            if breakpoint_source is None:
                issues.append(ReportValidationIssue("INVALID_BREAKPOINT_REFERENCE", path))
                continue
            if (
                breakpoint_source.concept_target.id != breakpoint_item.concept_id
                or breakpoint_source.skill_target.id != breakpoint_item.skill_dimension_id
                or breakpoint_source.concept_target.display_name != breakpoint_item.concept_label
                or breakpoint_source.skill_target.display_name != breakpoint_item.skill_label
                or breakpoint_source.status != breakpoint_item.status
                or breakpoint_source.severity != breakpoint_item.severity
            ):
                issues.append(ReportValidationIssue("BREAKPOINT_TARGET_MISMATCH", path))
            if not set(breakpoint_item.evidence_ids).issubset(
                breakpoint_source.supporting_evidence_ids
            ):
                issues.append(ReportValidationIssue("BREAKPOINT_EVIDENCE_MISMATCH", path))

        if bundle.session.mode == "SIMULATION" and report.coach_assistance:
            issues.append(ReportValidationIssue("SIMULATION_ASSISTANCE_CLAIM", "coach_assistance"))
        for index, assistance_item in enumerate(report.coach_assistance):
            self._validate_assistance(
                item=assistance_item,
                path=f"coach_assistance[{index}]",
                evidence=evidence,
                assistance=assistance,
                issues=issues,
            )

        for index, next_action in enumerate(report.next_actions):
            self._validate_next_action(
                item=next_action,
                path=f"next_actions[{index}]",
                evidence_ids=set(evidence),
                breakpoint_ids=set(breakpoints),
                issues=issues,
            )

        all_text = " ".join(_report_text(report))
        if _has_numeric_score(all_text):
            issues.append(ReportValidationIssue("NUMERIC_SCORE", "$"))
        if _contains_any(all_text, ("personality", "introvert", "extrovert", "neurotic")):
            issues.append(ReportValidationIssue("PERSONALITY_JUDGMENT", "$"))
        if _contains_any(
            all_text,
            ("hire probability", "hiring outcome", "should be hired", "hire recommendation"),
        ):
            issues.append(ReportValidationIssue("HIRING_PREDICTION", "$"))
        canonical_ids = [str(identifier) for identifier in (*evidence, *breakpoints, *assistance)]
        if any(identifier in all_text for identifier in canonical_ids):
            issues.append(ReportValidationIssue("RAW_INTERNAL_ID_IN_COPY", "$"))

        if issues:
            raise SessionReportValidationError(issues)

    def _validate_assistance(
        self,
        *,
        item: CoachAssistanceFinding,
        path: str,
        evidence: Mapping[UUID, object],
        assistance: Mapping[UUID, object],
        issues: list[ReportValidationIssue],
    ) -> None:
        from app.reports.schema import DeliveredAssistanceSource, ReportEvidenceSource

        deliveries: list[DeliveredAssistanceSource] = []
        for delivery_id in item.delivery_ids:
            source = assistance.get(delivery_id)
            if not isinstance(source, DeliveredAssistanceSource):
                issues.append(ReportValidationIssue("UNDELIVERED_ASSISTANCE_CLAIM", path))
            else:
                deliveries.append(source)
        before: list[ReportEvidenceSource] = []
        after: list[ReportEvidenceSource] = []
        for evidence_id in item.before_help_evidence_ids:
            source = evidence.get(evidence_id)
            if not isinstance(source, ReportEvidenceSource):
                issues.append(ReportValidationIssue("INVALID_EVIDENCE_REFERENCE", path))
            else:
                before.append(source)
        for evidence_id in item.after_help_evidence_ids:
            source = evidence.get(evidence_id)
            if not isinstance(source, ReportEvidenceSource):
                issues.append(ReportValidationIssue("INVALID_EVIDENCE_REFERENCE", path))
            else:
                after.append(source)
        if deliveries:
            if any(
                source.assistance_type != item.assistance_type
                or source.hint_level != item.hint_level
                for source in deliveries
            ):
                issues.append(ReportValidationIssue("ASSISTANCE_TYPE_MISMATCH", path))
            expected_label = candidate_assistance_label(
                item.assistance_type,
                item.hint_level,
            )
            if item.assistance_label != expected_label:
                issues.append(ReportValidationIssue("ASSISTANCE_LABEL_MISMATCH", path))
            assistance_sequence = min(item.delivered_server_sequence for item in deliveries)
            if any(_max_source_sequence(source) >= assistance_sequence for source in before):
                issues.append(ReportValidationIssue("BEFORE_HELP_ORDER_INVALID", path))
            if any(_max_source_sequence(source) <= assistance_sequence for source in after):
                issues.append(ReportValidationIssue("AFTER_HELP_ORDER_INVALID", path))
        if item.later_independence_level is not None and any(
            source.independence_level != item.later_independence_level for source in after
        ):
            issues.append(ReportValidationIssue("ASSISTANCE_ATTRIBUTION_MISMATCH", path))
        if item.later_independence_level == "AFTER_PROBE":
            issues.append(ReportValidationIssue("PROBE_MISLABELLED_AS_ASSISTANCE", path))
        if after and item.later_independence_level is None:
            issues.append(ReportValidationIssue("ASSISTANCE_ATTRIBUTION_MISSING", path))
        if item.later_independence_level is not None and (
            item.independent_verification_missing
            != (item.later_independence_level != "INDEPENDENT")
        ):
            issues.append(ReportValidationIssue("ASSISTANCE_VERIFICATION_MISMATCH", path))
        for source in (*before, *after):
            if deliveries and not any(
                _assistance_targets_evidence(delivery, source) for delivery in deliveries
            ):
                issues.append(ReportValidationIssue("ASSISTANCE_TARGET_MISMATCH", path))

    @staticmethod
    def _validate_next_action(
        *,
        item: ReportNextAction,
        path: str,
        evidence_ids: set[UUID],
        breakpoint_ids: set[UUID],
        issues: list[ReportValidationIssue],
    ) -> None:
        if not set(item.evidence_ids).issubset(evidence_ids):
            issues.append(ReportValidationIssue("INVALID_EVIDENCE_REFERENCE", path))
        if not set(item.breakpoint_ids).issubset(breakpoint_ids):
            issues.append(ReportValidationIssue("INVALID_BREAKPOINT_REFERENCE", path))
        if (
            not item.evidence_ids
            and not item.breakpoint_ids
            and not item.based_on_insufficient_evidence
        ):
            issues.append(ReportValidationIssue("UNSUPPORTED_RECOMMENDATION", path))
        if item.based_on_insufficient_evidence and (item.evidence_ids or item.breakpoint_ids):
            issues.append(ReportValidationIssue("AMBIGUOUS_RECOMMENDATION_SUPPORT", path))


def _material_findings(
    report: SessionReportSynthesis,
) -> list[tuple[str, ReportFinding]]:
    result = [(f"summary[{index}]", item) for index, item in enumerate(report.summary)]
    result.extend((f"strengths[{index}]", item) for index, item in enumerate(report.strengths))
    sections: tuple[tuple[str, ReportSection], ...] = (
        ("claim_defense", report.claim_defense),
        ("correctness_implementation", report.correctness_implementation),
        ("complexity", report.complexity),
        ("edge_cases", report.edge_cases),
        ("debugging", report.debugging),
        ("adaptability", report.adaptability),
    )
    for name, section in sections:
        result.extend((f"{name}.items[{index}]", item) for index, item in enumerate(section.items))
    return result


def _max_source_sequence(source: object) -> int:
    from app.reports.schema import ReportEvidenceSource

    if not isinstance(source, ReportEvidenceSource) or not source.sources:
        return -1
    return max(item.server_sequence for item in source.sources)


def _assistance_targets_evidence(delivery: object, evidence: object) -> bool:
    from app.reports.schema import DeliveredAssistanceSource, ReportEvidenceSource

    if not isinstance(delivery, DeliveredAssistanceSource) or not isinstance(
        evidence, ReportEvidenceSource
    ):
        return False
    concept_ids = {target.id for target in evidence.concept_targets}
    skill_ids = {target.id for target in evidence.skill_targets}
    return (delivery.target_concept_id is None or delivery.target_concept_id in concept_ids) and (
        delivery.target_skill_dimension_id is None
        or delivery.target_skill_dimension_id in skill_ids
    )


def _report_text(report: SessionReportSynthesis) -> list[str]:
    value = report.model_dump(mode="json")
    text: list[str] = []

    non_copy_fields = {
        "contract_version",
        "status",
        "severity",
        "independence_level",
        "later_independence_level",
        "evidence_ids",
        "breakpoint_ids",
        "breakpoint_id",
        "concept_id",
        "skill_dimension_id",
        "delivery_ids",
        "before_help_evidence_ids",
        "after_help_evidence_ids",
        "assistance_type",
        "hint_level",
    }

    def visit(item: object, *, field_name: str | None = None) -> None:
        if field_name in non_copy_fields:
            return
        if isinstance(item, str):
            text.append(item.lower())
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for key, child in item.items():
                visit(child, field_name=key)

    visit(value)
    return text


def _has_numeric_score(value: str) -> bool:
    return bool(
        re.search(r"\b\d+(?:\.\d+)?\s*(?:/\s*10|%|percent\b)", value, re.IGNORECASE)
        or re.search(r"\b(?:score|readiness)\s*(?:of|is|:)\s*\d", value, re.IGNORECASE)
    )


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    lowered = value.lower()
    return any(needle in lowered for needle in needles)
