from __future__ import annotations

import ast

from app.problems.content import (
    SUPPORTED_PROBE_STRATEGIES,
    Approach,
    CuratedContent,
    InterviewPackContent,
    load_curated_content,
)

QA_SLUGS = (
    "daily-temperatures",
    "binary-search",
    "search-in-rotated-sorted-array",
    "kth-largest-element",
    "merge-intervals",
    "maximum-subarray",
    "house-robber",
    "coin-change",
    "number-of-islands",
    "course-schedule",
)

EXPECTED_PACK_VERSIONS = dict.fromkeys(QA_SLUGS, "v2")


def _qa_entries() -> dict[str, CuratedContent]:
    entries = load_curated_content()
    assert tuple(entry.problem.slug for entry in entries[10:20]) == QA_SLUGS
    return {entry.problem.slug: entry for entry in entries[10:20]}


def _approach(pack: InterviewPackContent, approach_id: str) -> Approach:
    return next(
        approach
        for approach in [*pack.expected_approaches, *pack.alternative_approaches]
        if approach.approach_id == approach_id
    )


def _counterexamples(entry: CuratedContent) -> dict[str, object]:
    return {item.id: item.input for item in entry.interview_pack.counterexamples}


def _primary_source(entry: CuratedContent, language: str) -> str:
    primary_id = entry.interview_pack.expected_approaches[0].approach_id
    return next(
        reference.source_code
        for reference in entry.interview_pack.reference_solutions
        if reference.approach_id == primary_id and reference.language == language
    )


def _has_direct_recursion(source: str) -> bool:
    tree = ast.parse(source)
    for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == function.name
            ):
                return True
    return False


def test_packs_b_parse_with_reviewed_primary_language_references() -> None:
    entries = _qa_entries()
    assert {slug: entry.interview_pack.version for slug, entry in entries.items()} == (
        EXPECTED_PACK_VERSIONS
    )

    primary_reference_count = 0
    for entry in entries.values():
        pack = entry.interview_pack
        assert pack.review_status == "REVIEWED"
        primary_id = pack.expected_approaches[0].approach_id
        primary_references = [
            reference
            for reference in pack.reference_solutions
            if reference.approach_id == primary_id
        ]
        assert {reference.language for reference in primary_references} == {
            "cpp",
            "python",
            "java",
        }
        assert all(
            entry.problem.execution.method_name in reference.source_code
            for reference in primary_references
        )
        primary_reference_count += len(primary_references)

        for opportunity in pack.probe_opportunities:
            assert set(opportunity.relevant_strategies).issubset(SUPPORTED_PROBE_STRATEGIES)
        for followup in pack.common_followups:
            assert set(followup.relevant_strategies).issubset(SUPPORTED_PROBE_STRATEGIES)

    assert primary_reference_count == 30


def test_search_stack_heap_and_interval_regressions() -> None:
    entries = _qa_entries()

    daily = entries["daily-temperatures"]
    bounded = _approach(daily.interview_pack, "bounded_domain_reverse_scan")
    assert "D = 71" in bounded.time_complexity
    assert "D = 71" in bounded.space_complexity
    assert _counterexamples(daily)["equal_temperatures"] == [70, 70, 70]
    assert "> temperatures[stack[-1]]" in _primary_source(daily, "python")

    binary = _approach(entries["binary-search"].interview_pack, "closed_interval_binary_search")
    assert "inclusive interval [left, right]" in " ".join(binary.key_invariants)
    assert "strictly shrinks" in " ".join(binary.key_invariants)

    rotated = entries["search-in-rotated-sorted-array"].interview_pack
    duplicate_mutation = next(
        item for item in rotated.constraint_mutations if item.id == "duplicates_allowed"
    )
    assert "ambiguous" in duplicate_mutation.diagnostic_goal
    assert "O(n)" in duplicate_mutation.diagnostic_goal
    assert "no longer holds" in duplicate_mutation.diagnostic_goal

    kth = entries["kth-largest-element"]
    heap = _approach(kth.interview_pack, "size_k_min_heap")
    quickselect = _approach(kth.interview_pack, "quickselect_partition")
    assert "ranked occurrences" in " ".join(heap.key_invariants)
    assert "n - k" in " ".join(quickselect.key_invariants)
    assert "expected" in quickselect.time_complexity
    assert "O(n^2) worst case" in quickselect.time_complexity
    assert _counterexamples(kth)["k_one"] == {"nums": [3, 1, 2], "k": 1}
    assert _counterexamples(kth)["k_boundaries"] == {"nums": [3, 1, 2], "k": 3}

    merge = entries["merge-intervals"]
    merge_approach = _approach(merge.interview_pack, "sort_and_linear_merge")
    assert merge.problem.execution.comparator == "EXACT"
    assert "returned output" in merge_approach.space_complexity
    assert "copying the input" in merge_approach.space_complexity
    assert "max(out.back()[1],interval[1])" in _primary_source(merge, "cpp")


def test_dynamic_programming_regressions() -> None:
    entries = _qa_entries()

    maximum = entries["maximum-subarray"]
    prefix = _approach(maximum.interview_pack, "prefix_minimum")
    assert "strictly earlier" in prefix.summary
    assert "non-empty subarray" in " ".join(prefix.key_invariants)
    assert _counterexamples(maximum)["all_negative"] == [-5, -2, -7]

    robber = entries["house-robber"]
    rolling = _approach(robber.interview_pack, "rolling_prefix_dp")
    assert "prev1" in " ".join(rolling.key_invariants)
    assert "prev2" in " ".join(rolling.key_invariants)
    assert "before shifting" in " ".join(rolling.key_invariants)
    assert _counterexamples(robber)["greedy_failure"] == [2, 3, 2]

    coin = entries["coin-change"]
    bottom_up = _approach(coin.interview_pack, "bottom_up_amount_dp")
    top_down = _approach(coin.interview_pack, "top_down_memoization")
    assert bottom_up.time_complexity == "O(amount * coins.length)"
    assert "amount + 1 sentinel" in " ".join(bottom_up.key_invariants)
    assert "O(amount / min_coin)" in top_down.space_complexity
    assert _counterexamples(coin)["greedy_fails"] == {
        "coins": [1, 3, 4],
        "amount": 6,
    }
    assert _counterexamples(coin)["impossible"] == {"coins": [2], "amount": 3}


def test_graph_regressions_and_iterative_python_flood_fill() -> None:
    entries = _qa_entries()

    islands = entries["number-of-islands"]
    python_source = _primary_source(islands, "python")
    assert "stack =" in python_source
    assert "while stack" in python_source
    assert not _has_direct_recursion(python_source)
    assert "def visit" not in python_source
    assert _counterexamples(islands)["diagonal"] == ["10", "01"]

    for language in ("cpp", "python", "java"):
        source = _primary_source(islands, language)
        assert "stack" in source

    course = entries["course-schedule"]
    kahn = _approach(course.interview_pack, "kahn_indegree_processing")
    dfs = _approach(course.interview_pack, "dfs_active_path_cycle_detection")
    assert "O(V + E)" in kahn.time_complexity
    assert "O(V + E)" in kahn.space_complexity
    assert "ACTIVE" in " ".join(dfs.key_invariants)
    assert "FINISHED" in " ".join(dfs.key_invariants)
    assert "O(V) worst-case recursion stack" in dfs.space_complexity
    assert _counterexamples(course)["self_cycle"] == {
        "numCourses": 1,
        "prerequisites": [[0, 0]],
    }
    assert _counterexamples(course)["disconnected_cycle"] == {
        "numCourses": 4,
        "prerequisites": [[1, 0], [2, 3], [3, 2]],
    }
