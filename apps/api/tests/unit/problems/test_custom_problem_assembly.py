from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.ai_gateway.structured_output import validate_strict_reasoning_schema
from app.execution.harness import execution_request_for_problem
from app.execution.provider import ExecutionRequest
from app.problems.content import ProblemConceptDefinition, ProblemContent, load_curated_content
from app.problems.custom_pack_validation import (
    PackArtifactValidationError,
    PreparedApproachReference,
    PreparedPackOutput,
    PreparedPrivateCase,
    validate_prepared_pack_artifacts,
)
from app.problems.custom_problem_assembly import (
    CollectionComparator,
    CustomProblemAssemblyNeedsCorrection,
    CustomProblemAssemblyValidationError,
    build_custom_problem_content,
    fallback_title_from_method,
)
from app.problems.custom_source_evidence import derive_custom_problem_source_evidence

PREPARATION_ID = UUID("01995f29-fab1-7b49-99ba-629725836cb0")
SOURCE = """Given an array of integers nums and an integer target, return the number of
pairs of indices (i, j) such that i < j and nums[i] + nums[j] == target.

Function signature:
int countPairs(vector<int> nums, int target)

Constraints:
1 <= nums.length <= 100000
-100000 <= nums[i] <= 100000
-200000 <= target <= 200000

Example 1:
Input: nums = [1, 2, 3, 4], target = 5
Output: 2
Explanation: The valid pairs are (1,4) and (2,3).

Example 2:
Input: nums = [1, 1, 1], target = 2
Output: 3
"""
ORDERED_COLLECTION_SOURCE = """Return the values in their original order.

Function signature:
vector<int> solve(vector<int> nums)

Constraints:
1 <= nums.length <= 100

Example:
Input: nums = [3, 1, 2]
Output: [3, 1, 2]
"""
UNORDERED_COLLECTION_SOURCE = """Return the duplicate values in any order.

Function signature:
vector<int> findDuplicates(vector<int> nums)

Constraints:
1 <= nums.length <= 100

Example:
Input: nums = [1, 2, 2, 3, 3]
Output: [2, 3]
"""
UNORDERED_MATRIX_SOURCE = """Return the rows in any order while preserving values within each row.

Function signature:
vector<vector<int>> reorderRows(vector<vector<int>> grid)

Constraints:
1 <= grid.length <= 100

Example:
Input: grid = [[1, 2], [3, 4]]
Output: [[3, 4], [1, 2]]
"""


def _concepts() -> list[ProblemConceptDefinition]:
    entry = next(item for item in load_curated_content() if item.problem.slug == "two-sum")
    return list(entry.problem.problem_concepts)


def _problem(*, title: str | None = "Count Pairs") -> ProblemContent:
    concepts = _concepts()
    return build_custom_problem_content(
        preparation_id=PREPARATION_ID,
        source_evidence=derive_custom_problem_source_evidence(SOURCE),
        title=title,
        normalized_statement=None,
        normalized_constraints=[],
        collection_comparator=None,
        problem_concepts=concepts,
        active_concept_keys={item.canonical_key for item in concepts},
    )


def _collection_problem(
    source: str,
    comparator: CollectionComparator | None,
) -> ProblemContent:
    concepts = _concepts()
    return build_custom_problem_content(
        preparation_id=PREPARATION_ID,
        source_evidence=derive_custom_problem_source_evidence(source),
        title=None,
        normalized_statement=None,
        normalized_constraints=[],
        collection_comparator=comparator,
        problem_concepts=concepts,
        active_concept_keys={item.canonical_key for item in concepts},
    )


def _execution_request(problem: ProblemContent) -> ExecutionRequest:
    return execution_request_for_problem(
        io_schema={"execution": problem.execution.model_dump(mode="json")},
        language="python",
        source_code=problem.languages["python"].starter_code,
        compile_timeout_seconds=5,
        run_timeout_seconds=5,
        memory_limit_mb=256,
        output_limit_bytes=65_536,
    )


def test_software_assembles_the_complete_strict_problem_from_source() -> None:
    problem = _problem()

    assert problem.schema_version == "problem.v1"
    assert problem.slug == f"custom-{PREPARATION_ID}"
    assert problem.version == "v1"
    assert problem.catalog_order == 1
    assert problem.review_status == "REVIEWED"
    assert problem.statement == (
        "Given an array of integers nums and an integer target, return the number of\n"
        "pairs of indices (i, j) such that i < j and nums[i] + nums[j] == target."
    )
    assert problem.constraints == [
        "1 <= nums.length <= 100000",
        "-100000 <= nums[i] <= 100000",
        "-200000 <= target <= 200000",
    ]
    assert [item.model_dump(mode="json") for item in problem.examples] == [
        {
            "input": "nums = [1, 2, 3, 4], target = 5",
            "output": "2",
            "explanation": "The valid pairs are (1,4) and (2,3).",
        },
        {
            "input": "nums = [1, 1, 1], target = 2",
            "output": "3",
            "explanation": "",
        },
    ]
    assert problem.execution.model_dump(mode="json") == {
        "method_name": "countPairs",
        "arguments": [
            {"name": "nums", "type": "int[]"},
            {"name": "target", "type": "int"},
        ],
        "return_type": "int",
        "comparator": "EXACT",
        "visible_cases": [
            {"arguments": {"nums": [1, 2, 3, 4], "target": 5}, "expected_output": 2},
            {"arguments": {"nums": [1, 1, 1], "target": 2}, "expected_output": 3},
        ],
        "custom_test_supported": True,
    }
    assert set(problem.languages) == {"cpp", "python", "java"}
    assert all("Not implemented" in item.starter_code for item in problem.languages.values())


def test_scalar_comparator_is_software_owned_exact() -> None:
    concepts = _concepts()
    problem = build_custom_problem_content(
        preparation_id=PREPARATION_ID,
        source_evidence=derive_custom_problem_source_evidence(SOURCE),
        title=None,
        normalized_statement=None,
        normalized_constraints=[],
        collection_comparator="UNORDERED_LIST",
        problem_concepts=concepts,
        active_concept_keys={item.canonical_key for item in concepts},
    )

    assert problem.execution.return_type == "int"
    assert problem.execution.comparator == "EXACT"


def test_ordered_collection_uses_exact_comparison() -> None:
    problem = _collection_problem(ORDERED_COLLECTION_SOURCE, "EXACT")

    assert problem.execution.return_type == "int[]"
    assert problem.execution.comparator == "EXACT"
    assert {case.comparator for case in _execution_request(problem).cases} == {"EXACT"}


def test_unordered_collection_reaches_execution_request() -> None:
    problem = _collection_problem(UNORDERED_COLLECTION_SOURCE, "UNORDERED_LIST")

    assert problem.execution.return_type == "int[]"
    assert problem.execution.comparator == "UNORDERED_LIST"
    assert {case.comparator for case in _execution_request(problem).cases} == {
        "UNORDERED_LIST"
    }


def test_unordered_matrix_uses_existing_outer_collection_contract() -> None:
    problem = _collection_problem(UNORDERED_MATRIX_SOURCE, "UNORDERED_LIST")

    assert problem.execution.return_type == "int[][]"
    assert problem.execution.comparator == "UNORDERED_LIST"
    assert {case.comparator for case in _execution_request(problem).cases} == {
        "UNORDERED_LIST"
    }


def test_collection_comparator_is_required_and_rejects_arbitrary_values() -> None:
    with pytest.raises(CustomProblemAssemblyValidationError):
        _collection_problem(ORDERED_COLLECTION_SOURCE, None)
    with pytest.raises(CustomProblemAssemblyValidationError):
        _collection_problem(
            UNORDERED_COLLECTION_SOURCE,
            cast(CollectionComparator, "COUNT"),
        )


def test_title_falls_back_from_camel_case_method_name() -> None:
    assert fallback_title_from_method("countPairs") == "Count Pairs"
    assert _problem(title=None).title == "Count Pairs"
    assert _problem(title="   ").title == "Count Pairs"


def test_source_text_wins_and_normalized_text_is_only_a_fallback() -> None:
    concepts = _concepts()
    problem = build_custom_problem_content(
        preparation_id=PREPARATION_ID,
        source_evidence=derive_custom_problem_source_evidence(SOURCE),
        title="Count Pairs",
        normalized_statement="Model replacement statement.",
        normalized_constraints=["Model replacement constraint."],
        collection_comparator=None,
        problem_concepts=concepts,
        active_concept_keys={item.canonical_key for item in concepts},
    )

    assert problem.statement.startswith("Given an array")
    assert problem.constraints[0] == "1 <= nums.length <= 100000"


def test_normalized_text_is_used_only_when_source_sections_are_absent() -> None:
    concepts = _concepts()
    source = """int echo(int value)
Example:
Input: value = 7
Output: 7
"""
    problem = build_custom_problem_content(
        preparation_id=PREPARATION_ID,
        source_evidence=derive_custom_problem_source_evidence(source),
        title=None,
        normalized_statement="Return the supplied integer.",
        normalized_constraints=["-100 <= value <= 100"],
        collection_comparator=None,
        problem_concepts=concepts,
        active_concept_keys={item.canonical_key for item in concepts},
    )

    assert problem.title == "Echo"
    assert problem.statement == "Return the supplied integer."
    assert problem.constraints == ["-100 <= value <= 100"]


def test_missing_source_and_fallback_content_produces_bounded_correction() -> None:
    concepts = _concepts()
    with pytest.raises(CustomProblemAssemblyNeedsCorrection) as caught:
        build_custom_problem_content(
            preparation_id=PREPARATION_ID,
            source_evidence=derive_custom_problem_source_evidence(
                "int echo(int value)\nExample:\nInput: value = 7\nOutput: 7\n"
            ),
            title=None,
            normalized_statement=None,
            normalized_constraints=[],
            collection_comparator=None,
            problem_concepts=concepts,
            active_concept_keys={item.canonical_key for item in concepts},
        )

    assert caught.value.finding_codes == ("MISSING_PROBLEM_TEXT", "MISSING_CONSTRAINTS")


def test_concept_selection_must_be_unique_and_ontology_verified() -> None:
    concepts = _concepts()
    with pytest.raises(CustomProblemAssemblyValidationError):
        build_custom_problem_content(
            preparation_id=PREPARATION_ID,
            source_evidence=derive_custom_problem_source_evidence(SOURCE),
            title=None,
            normalized_statement=None,
            normalized_constraints=[],
            collection_comparator=None,
            problem_concepts=[concepts[0], concepts[0]],
            active_concept_keys={item.canonical_key for item in concepts},
        )
    invented = concepts[0].model_copy(update={"canonical_key": "model_invented_concept"})
    with pytest.raises(CustomProblemAssemblyValidationError):
        build_custom_problem_content(
            preparation_id=PREPARATION_ID,
            source_evidence=derive_custom_problem_source_evidence(SOURCE),
            title=None,
            normalized_statement=None,
            normalized_constraints=[],
            collection_comparator=None,
            problem_concepts=[invented],
            active_concept_keys={item.canonical_key for item in concepts},
        )


def _private_case(arguments: list[dict[str, object]] | None = None) -> PreparedPrivateCase:
    return PreparedPrivateCase.model_validate(
        {
            "arguments": arguments
            or [
                {"name": "nums", "value": [1, 1, 1, 1]},
                {"name": "target", "value": 2},
            ],
            "expected_output": 6,
        }
    )


def _approach(summary: str, concept_key: str) -> dict[str, object]:
    return {
        "summary": summary,
        "concept_keys": [concept_key],
        "applicability": "Use for the supplied Count Pairs constraints.",
        "assumptions": [],
        "key_invariants": ["Seen counts cover only prior values."],
        "time_complexity": "O(n)",
        "space_complexity": "O(n)",
        "tradeoffs": [],
        "common_implementation_variants": [],
        "common_failure_modes": [],
    }


def _technical(
    concept_key: str,
    *,
    goal: str,
    approach_kind: str | None = None,
    approach_index: int = 0,
    counterexample_index: int | None = None,
) -> dict[str, object]:
    return {
        "concept_keys": [concept_key],
        "diagnostic_goal": goal,
        "counterexample_index": counterexample_index,
        "approach_reference": (
            {
                "approach_kind": approach_kind,
                "approach_index": approach_index,
            }
            if approach_kind is not None
            else None
        ),
    }


def _pack_output() -> PreparedPackOutput:
    concept_key = _concepts()[0].canonical_key
    duplicate_invariant = _technical(
        concept_key,
        goal="Explain why every pair is counted exactly once.",
        approach_kind="EXPECTED",
    )
    return PreparedPackOutput.model_validate(
        {
            "expected_approaches": [
                _approach("Hash Map Frequency", concept_key),
                _approach("Sort and scan", concept_key),
            ],
            "alternative_approaches": [
                _approach("Brute force", concept_key),
                _approach("Binary search", concept_key),
            ],
            "primary_reference_solutions": {
                "cpp": {"source_code": "class Solution {};", "implementation_notes": None},
                "python": {"source_code": "class Solution: pass", "implementation_notes": None},
                "java": {"source_code": "class Solution {}", "implementation_notes": None},
            },
            "invariants": [duplicate_invariant, duplicate_invariant],
            "complexity_expectations": [
                _technical(
                    concept_key,
                    goal="Defend the sorting complexity.",
                    approach_kind="EXPECTED",
                    approach_index=1,
                )
            ],
            "common_misconceptions": [
                _technical(
                    concept_key,
                    goal="Contrast the brute-force alternative.",
                    approach_kind="ALTERNATIVE",
                )
            ],
            "failure_modes": [],
            "edge_cases": [
                _technical(
                    concept_key,
                    goal="Handle repeated equal values.",
                    counterexample_index=0,
                )
            ],
            "counterexamples": [
                {
                    "input": "nums = [1, 1, 1], target = 2",
                    "purpose": "Expose multiplicity mistakes.",
                }
            ],
            "constraint_mutations": [],
            "probe_opportunities": [
                {
                    **_technical(
                        concept_key,
                        goal="Prove the one-pass invariant.",
                        approach_kind="EXPECTED",
                    ),
                    "relevant_strategies": ["PROVE"],
                }
            ],
            "common_followups": [
                {
                    "target_concepts": [concept_key],
                    "approach_reference": {
                        "approach_kind": "ALTERNATIVE",
                        "approach_index": 1,
                    },
                    "trigger_cues": ["Candidate proposes sorting."],
                    "diagnostic_goal": "Compare alternatives.",
                    "relevant_strategies": ["TRADE_OFF"],
                    "expected_good_signals": ["Explains time-space tradeoff."],
                    "weak_or_misconception_signals": [],
                    "counterexample_index": 0,
                    "applicable_levels": ["NEW_GRAD"],
                    "applicable_stages": ["APPROACH_DEFENSE"],
                    "sample_phrasings": ["What changes if memory is constrained?"],
                }
            ],
            "level_considerations": [
                {"level": "NEW_GRAD", "guidance": "Expect complexity justification."}
            ],
            "reference_reasoning": "Count complements already observed in the prefix.",
            "private_cases": [_private_case().model_dump(mode="json")],
        }
    )


def test_software_assembles_deterministic_pack_ids_and_references() -> None:
    problem = _problem()
    output = _pack_output()
    pack, private_cases = validate_prepared_pack_artifacts(
        semantic_pack=output,
        private_cases=output.private_cases,
        problem=problem,
    )

    assert pack.schema_version == "interview-pack.v1"
    assert pack.version == "v1"
    assert pack.review_status == "REVIEWED"
    assert pack.concepts == [_concepts()[0].canonical_key]
    assert [item.approach_id for item in pack.expected_approaches] == [
        "expected_approach_1",
        "expected_approach_2",
    ]
    assert [item.approach_id for item in pack.alternative_approaches] == [
        "alternative_approach_1",
        "alternative_approach_2",
    ]
    assert {item.approach_id for item in pack.reference_solutions} == {
        "expected_approach_1"
    }
    assert {item.language for item in pack.reference_solutions} == {"cpp", "python", "java"}
    assert [item.id for item in pack.invariants] == ["invariant_1", "invariant_2"]
    assert pack.invariants[0].diagnostic_goal == pack.invariants[1].diagnostic_goal
    assert pack.complexity_expectations[0].id == "complexity_1"
    assert pack.complexity_expectations[0].approach_id == "expected_approach_2"
    assert pack.common_misconceptions[0].id == "misconception_1"
    assert pack.common_misconceptions[0].approach_id == "alternative_approach_1"
    assert pack.edge_cases[0].id == "edge_case_1"
    assert pack.edge_cases[0].counterexample_id == "counterexample_1"
    assert pack.probe_opportunities[0].id == "probe_1"
    assert pack.counterexamples[0].id == "counterexample_1"
    assert pack.common_followups[0].id == "followup_1"
    assert pack.common_followups[0].target_approach_id == "alternative_approach_2"
    assert pack.common_followups[0].counterexample_id == "counterexample_1"
    assert "Hash Map Frequency" not in {
        item.approach_id for item in pack.expected_approaches
    }
    assert [item.model_dump(mode="json") for item in private_cases] == [
        {
            "arguments": {"nums": [1, 1, 1, 1], "target": 2},
            "expected_output": 6,
        }
    ]


def test_pack_output_is_a_provider_strict_semantic_schema_without_storage_ids() -> None:
    schema = PreparedPackOutput.model_json_schema()
    validate_strict_reasoning_schema(schema)
    serialized = str(schema)

    assert "pack_json" not in schema["properties"]
    assert "expected_approaches" in schema["properties"]
    assert "primary_reference_solutions" in schema["properties"]
    assert "private_cases" in schema["properties"]
    assert "concepts" not in schema["properties"]
    assert "'concept_keys'" in serialized
    assert "'approach_id'" not in serialized
    assert "'counterexample_id'" not in serialized
    assert "'target_approach_id'" not in serialized
    assert "'review_status'" not in serialized
    assert "'schema_version'" not in serialized


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("strategy", "MODEL_INVENTED_STRATEGY"),
        ("level", "STAFF"),
        ("stage", "SYSTEM_DESIGN"),
        ("language", "ruby"),
    ],
)
def test_pack_semantic_enums_are_rejected_by_the_typed_output_boundary(
    field: str,
    invalid_value: str,
) -> None:
    payload = _pack_output().model_dump(mode="json")
    if field == "strategy":
        payload["probe_opportunities"][0]["relevant_strategies"] = [invalid_value]
    elif field == "level":
        payload["common_followups"][0]["applicable_levels"] = [invalid_value]
    elif field == "stage":
        payload["common_followups"][0]["applicable_stages"] = [invalid_value]
    else:
        payload["primary_reference_solutions"][invalid_value] = payload[
            "primary_reference_solutions"
        ]["python"]

    with pytest.raises(ValidationError):
        PreparedPackOutput.model_validate(payload)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("invalid_approach_index", "PACK_SCHEMA_INVALID"),
        ("invalid_counterexample_index", "PACK_SCHEMA_INVALID"),
        ("bad_nested_concept", "PACK_CONCEPT_INVALID"),
        ("argument_mismatch", "PRIVATE_CASE_ARGUMENT_MISMATCH"),
        ("argument_type", "PRIVATE_CASE_VALUE_TYPE_INVALID"),
        ("output_type", "PRIVATE_CASE_EXPECTED_OUTPUT_TYPE_INVALID"),
    ],
)
def test_pack_and_private_failures_have_bounded_diagnostics(
    mutation: str, expected_code: str
) -> None:
    problem = _problem()
    output = _pack_output().model_copy(deep=True)
    private = _private_case()
    if mutation == "invalid_approach_index":
        output.invariants[0].approach_reference = PreparedApproachReference(
            approach_kind="EXPECTED",
            approach_index=3,
        )
    elif mutation == "invalid_counterexample_index":
        output.edge_cases[0].counterexample_index = 1
    elif mutation == "bad_nested_concept":
        output.expected_approaches[0].concept_keys = ["not_in_pack"]
    elif mutation == "argument_mismatch":
        private = _private_case(
            [
                {"name": "values", "value": [1, 1, 1, 1]},
                {"name": "target", "value": 2},
            ]
        )
    elif mutation == "argument_type":
        private = _private_case(
            [
                {"name": "nums", "value": [1, 1, 1, 1]},
                {"name": "target", "value": "two"},
            ]
        )
    elif mutation == "output_type":
        private = private.model_copy(update={"expected_output": "six"})

    with pytest.raises(PackArtifactValidationError) as caught:
        validate_prepared_pack_artifacts(
            semantic_pack=output,
            private_cases=[private],
            problem=problem,
        )

    assert expected_code in {issue.code for issue in caught.value.issues}
    if mutation == "bad_nested_concept":
        assert caught.value.issues[0].field == (
            "pack.expected_approaches[0].concept_keys"
        )
    assert 1 <= len(caught.value.issues) <= 8
    assert all(issue.field for issue in caught.value.issues)
