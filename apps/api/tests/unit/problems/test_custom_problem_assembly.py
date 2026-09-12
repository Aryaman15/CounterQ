from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from app.execution.harness import execution_request_for_problem
from app.execution.provider import ExecutionRequest
from app.problems.content import ProblemConceptDefinition, ProblemContent, load_curated_content
from app.problems.custom_pack_validation import (
    PackArtifactValidationError,
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


def test_pack_phase_validates_and_converts_typed_private_cases() -> None:
    problem = _problem()
    entry = next(item for item in load_curated_content() if item.problem.slug == "two-sum")
    pack, private_cases = validate_prepared_pack_artifacts(
        pack_json=entry.interview_pack.model_dump_json(),
        private_cases=[_private_case()],
        problem=problem,
        active_concept_keys={item.canonical_key for item in problem.problem_concepts},
    )

    assert pack.schema_version == "interview-pack.v1"
    assert [item.model_dump(mode="json") for item in private_cases] == [
        {
            "arguments": {"nums": [1, 1, 1, 1], "target": 2},
            "expected_output": 6,
        }
    ]


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("bad_json", "PACK_JSON_INVALID"),
        ("bad_schema", "PACK_SCHEMA_INVALID"),
        ("bad_concept", "PACK_CONCEPT_INVALID"),
        ("argument_mismatch", "PRIVATE_CASE_ARGUMENT_MISMATCH"),
        ("argument_type", "PRIVATE_CASE_VALUE_TYPE_INVALID"),
        ("output_type", "PRIVATE_CASE_EXPECTED_OUTPUT_TYPE_INVALID"),
    ],
)
def test_pack_and_private_failures_have_bounded_diagnostics(
    mutation: str, expected_code: str
) -> None:
    problem = _problem()
    entry = next(item for item in load_curated_content() if item.problem.slug == "two-sum")
    pack: object = entry.interview_pack.model_dump(mode="json")
    private = _private_case()
    active = {item.canonical_key for item in problem.problem_concepts}
    if mutation == "bad_json":
        pack = "{"
    elif mutation == "bad_schema":
        assert isinstance(pack, dict)
        del pack["expected_approaches"]
    elif mutation == "bad_concept":
        active = set()
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
            pack_json=pack if isinstance(pack, str) else __import__("json").dumps(pack),
            private_cases=[private],
            problem=problem,
            active_concept_keys=active,
        )

    assert expected_code in {issue.code for issue in caught.value.issues}
    assert 1 <= len(caught.value.issues) <= 8
    assert all(issue.field for issue in caught.value.issues)
