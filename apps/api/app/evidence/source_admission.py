from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EvidenceSourceCategory(StrEnum):
    CANDIDATE_DEMONSTRATION = "CANDIDATE_DEMONSTRATION"
    CONTEXT_ONLY = "CONTEXT_ONLY"
    NON_EVIDENTIARY = "NON_EVIDENTIARY"


@dataclass(frozen=True)
class EvidenceSourceAdmission:
    category: EvidenceSourceCategory
    admitted: bool
    counts_as_candidate_demonstration: bool
    reason: str


CANDIDATE_DEMONSTRATION_EVENT_KINDS = frozenset(
    {
        ("TRANSCRIPT_FINALIZED", "CANDIDATE_VOICE"),
        ("CODE_SNAPSHOT_CREATED", "NATIVE_EDITOR"),
        ("MEANINGFUL_CODE_CHANGE", "NATIVE_EDITOR"),
        ("RUN_CLICKED", "NATIVE_RUNNER"),
        ("COMPILE_COMPLETED", "NATIVE_RUNNER"),
        ("TEST_COMPLETED", "NATIVE_RUNNER"),
    }
)

CONTEXT_ONLY_EVENT_KINDS = frozenset(
    {
        ("COUNTERQ_UTTERANCE_DELIVERED", "COUNTERQ_VOICE"),
    }
)


def evidence_source_admission(
    *,
    event_type: str,
    event_source: str,
    source_role: str,
) -> EvidenceSourceAdmission:
    """Classify whether an observed event may support candidate Evidence."""

    event_kind = (event_type, event_source)
    if event_kind in CANDIDATE_DEMONSTRATION_EVENT_KINDS:
        return EvidenceSourceAdmission(
            category=EvidenceSourceCategory.CANDIDATE_DEMONSTRATION,
            admitted=True,
            counts_as_candidate_demonstration=True,
            reason="CANDIDATE_DEMONSTRATION_ADMITTED",
        )
    if event_kind in CONTEXT_ONLY_EVENT_KINDS:
        if source_role == "CONTEXT":
            return EvidenceSourceAdmission(
                category=EvidenceSourceCategory.CONTEXT_ONLY,
                admitted=True,
                counts_as_candidate_demonstration=False,
                reason="CONTEXT_SOURCE_ADMITTED",
            )
        return EvidenceSourceAdmission(
            category=EvidenceSourceCategory.CONTEXT_ONLY,
            admitted=False,
            counts_as_candidate_demonstration=False,
            reason="CONTEXT_SOURCE_ROLE_REQUIRED",
        )
    return EvidenceSourceAdmission(
        category=EvidenceSourceCategory.NON_EVIDENTIARY,
        admitted=False,
        counts_as_candidate_demonstration=False,
        reason="NON_EVIDENTIARY_SOURCE",
    )
