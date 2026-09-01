from __future__ import annotations

from typing import cast

LIVE_EXAMINER_CONTEXT_PROJECTION_KEY = "live_examiner_context"
LIVE_EXAMINER_CONTEXT_PROJECTION_VERSION = "v1"

_APPROACH_FIELDS = (
    "approach_id",
    "summary",
    "concept_keys",
    "applicability",
    "assumptions",
    "key_invariants",
    "time_complexity",
    "space_complexity",
    "tradeoffs",
    "common_implementation_variants",
    "common_failure_modes",
)
_DIAGNOSTIC_FIELDS: dict[str, tuple[str, ...]] = {
    "invariants": (
        "id",
        "concept_keys",
        "diagnostic_goal",
        "counterexample_id",
        "approach_id",
    ),
    "complexity_expectations": (
        "id",
        "concept_keys",
        "diagnostic_goal",
        "approach_id",
    ),
    "common_misconceptions": (
        "id",
        "concept_keys",
        "diagnostic_goal",
        "counterexample_id",
        "approach_id",
    ),
    "failure_modes": (
        "id",
        "concept_keys",
        "diagnostic_goal",
        "counterexample_id",
        "approach_id",
    ),
    "edge_cases": (
        "id",
        "concept_keys",
        "diagnostic_goal",
        "counterexample_id",
        "approach_id",
    ),
    "counterexamples": ("id", "purpose"),
    "constraint_mutations": (
        "id",
        "concept_keys",
        "diagnostic_goal",
        "counterexample_id",
        "approach_id",
    ),
    "probe_opportunities": (
        "id",
        "concept_keys",
        "diagnostic_goal",
        "counterexample_id",
        "approach_id",
        "relevant_strategies",
    ),
}
_FOLLOWUP_FIELDS = (
    "id",
    "target_concepts",
    "target_approach_id",
    "trigger_cues",
    "diagnostic_goal",
    "relevant_strategies",
    "expected_good_signals",
    "weak_or_misconception_signals",
    "counterexample_id",
    "applicable_levels",
    "applicable_stages",
)


def project_problem_context(
    problem: dict[str, object],
    *,
    language: str,
) -> dict[str, object]:
    """Keep the problem contract needed for diagnosis without execution bulk."""
    projected = _select(
        problem,
        ("problem_version_id", "title", "statement", "constraints", "examples"),
    )
    io_schema = problem.get("io_schema")
    if not isinstance(io_schema, dict):
        return projected

    typed_io = cast(dict[str, object], io_schema)
    execution = typed_io.get("execution")
    if isinstance(execution, dict):
        projected["io_contract"] = _select(
            cast(dict[str, object], execution),
            ("method_name", "arguments", "return_type", "comparator"),
        )

    languages = typed_io.get("languages")
    if isinstance(languages, dict):
        active = cast(dict[str, object], languages).get(language)
        if isinstance(active, dict):
            projected["active_language_contract"] = {
                "language": language,
                **_select(cast(dict[str, object], active), ("display_signature",)),
            }
    return projected


def project_interview_pack(
    interview_pack: dict[str, object],
    *,
    candidate_level: str,
    interview_stage: str,
) -> dict[str, object]:
    """Project reviewed diagnostic priors; never expose reference solution source."""
    projected = _select(
        interview_pack,
        ("interview_pack_version_id", "schema_version", "review_status"),
    )
    raw_pack = interview_pack.get("pack")
    if not isinstance(raw_pack, dict):
        projected["diagnostic_pack"] = {}
        return projected

    pack = cast(dict[str, object], raw_pack)
    diagnostic: dict[str, object] = {}
    if "version" in pack:
        diagnostic["version"] = pack["version"]
    for key in ("expected_approaches", "alternative_approaches"):
        items = _project_items(pack.get(key), _APPROACH_FIELDS)
        if items:
            diagnostic[key] = items
    if isinstance(pack.get("concepts"), list):
        diagnostic["concepts"] = pack["concepts"]
    for key, fields in _DIAGNOSTIC_FIELDS.items():
        items = _project_items(pack.get(key), fields)
        if items:
            diagnostic[key] = items

    followups = [
        item
        for item in _project_items(pack.get("common_followups"), _FOLLOWUP_FIELDS)
        if _applies(item.get("applicable_levels"), candidate_level)
        and _applies(item.get("applicable_stages"), interview_stage)
    ]
    if followups:
        diagnostic["relevant_followups"] = followups

    level_considerations = [
        item
        for item in _project_items(pack.get("level_considerations"), ("level", "guidance"))
        if item.get("level") == candidate_level
    ]
    if level_considerations:
        diagnostic["level_considerations"] = level_considerations

    projected["diagnostic_pack"] = diagnostic
    return projected


def _select(value: dict[str, object], fields: tuple[str, ...]) -> dict[str, object]:
    return {field: value[field] for field in fields if field in value}


def _project_items(value: object, fields: tuple[str, ...]) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        _select(cast(dict[str, object], item), fields)
        for item in value
        if isinstance(item, dict)
    ]


def _applies(value: object, expected: str) -> bool:
    return not isinstance(value, list) or not value or expected in value
