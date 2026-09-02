from __future__ import annotations

from typing import Final

PROACTIVE_ENRICHMENT_STAGE_ELIGIBILITY: Final[dict[str, frozenset[str]]] = {
    "TRADE_OFF": frozenset(
        {
            "APPROACH_DEFENSE",
            "COMPLEXITY_EDGE_CASES",
            "CONSTRAINT_MUTATION",
            "FINAL_DEFENSE",
        }
    ),
    "ALTERNATIVE": frozenset(
        {
            "APPROACH_DEFENSE",
            "COMPLEXITY_EDGE_CASES",
            "FINAL_DEFENSE",
        }
    ),
    "CONSTRAINT_MUTATION": frozenset(
        {
            "CONSTRAINT_MUTATION",
            "FINAL_DEFENSE",
        }
    ),
    "TRANSFER": frozenset(
        {
            "CONSTRAINT_MUTATION",
            "FINAL_DEFENSE",
        }
    ),
}


def filter_proactive_enrichment_strategies(
    strategies: list[str],
    *,
    interview_stage: str,
) -> list[str]:
    """Remove only proactive enrichment strategies unavailable at this stage."""
    return [
        strategy
        for strategy in strategies
        if strategy not in PROACTIVE_ENRICHMENT_STAGE_ELIGIBILITY
        or interview_stage in PROACTIVE_ENRICHMENT_STAGE_ELIGIBILITY[strategy]
    ]
