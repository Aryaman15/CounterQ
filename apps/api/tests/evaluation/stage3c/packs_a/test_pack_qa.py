from __future__ import annotations

from app.problems.content import SUPPORTED_PROBE_STRATEGIES, CuratedContent, load_curated_content

QA_SLUGS = (
    "two-sum",
    "contains-duplicate",
    "valid-anagram",
    "product-of-array-except-self",
    "top-k-frequent-elements",
    "longest-substring-without-repeating-characters",
    "minimum-size-subarray-sum",
    "valid-palindrome",
    "container-with-most-water",
    "valid-parentheses",
)

EXPECTED_PACK_VERSIONS = {
    "two-sum": "v1",
    "contains-duplicate": "v2",
    "valid-anagram": "v3",
    "product-of-array-except-self": "v2",
    "top-k-frequent-elements": "v2",
    "longest-substring-without-repeating-characters": "v2",
    "minimum-size-subarray-sum": "v2",
    "valid-palindrome": "v2",
    "container-with-most-water": "v2",
    "valid-parentheses": "v2",
}


def _qa_entries() -> dict[str, CuratedContent]:
    entries = load_curated_content()
    assert tuple(entry.problem.slug for entry in entries[:10]) == QA_SLUGS
    return {entry.problem.slug: entry for entry in entries[:10]}


def test_packs_a_parse_with_reviewed_primary_language_references() -> None:
    entries = _qa_entries()
    assert {slug: entry.interview_pack.version for slug, entry in entries.items()} == (
        EXPECTED_PACK_VERSIONS
    )

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

        for opportunity in pack.probe_opportunities:
            assert set(opportunity.relevant_strategies).issubset(SUPPORTED_PROBE_STRATEGIES)
        for followup in pack.common_followups:
            assert set(followup.relevant_strategies).issubset(SUPPORTED_PROBE_STRATEGIES)


def test_known_complexity_precision_regressions() -> None:
    entries = _qa_entries()

    contains = entries["contains-duplicate"].interview_pack
    sorting = next(
        item
        for item in contains.alternative_approaches
        if item.approach_id == "sort_adjacent_values"
    )
    assert "in-place" in sorting.space_complexity
    assert "O(n) for an input copy" in sorting.space_complexity

    anagram = entries["valid-anagram"].interview_pack.expected_approaches[0]
    assert "O(1)" in anagram.space_complexity
    assert "26-letter" in anagram.space_complexity

    top_k = entries["top-k-frequent-elements"].interview_pack
    heap = next(
        item
        for item in top_k.expected_approaches
        if item.approach_id == "frequency_bounded_min_heap"
    )
    buckets = next(
        item for item in top_k.expected_approaches if item.approach_id == "frequency_buckets"
    )
    assert "expected hash counting" in heap.time_complexity
    assert "d log k" in heap.time_complexity
    assert "d <= n" in buckets.time_complexity

    longest = entries[
        "longest-substring-without-repeating-characters"
    ].interview_pack.expected_approaches[0]
    assert "expected time with hash-based" in longest.time_complexity
    assert "fixed ASCII" in longest.time_complexity
    assert "O(1) under the printable-ASCII contract" in longest.space_complexity


def test_prefix_binary_search_uses_latest_valid_prior_prefix() -> None:
    pack = _qa_entries()["minimum-size-subarray-sum"].interview_pack
    approach = next(
        item
        for item in pack.alternative_approaches
        if item.approach_id == "prefix_sum_binary_search"
    )
    assert "latest prior prefix index" in approach.summary
    assert "prefix[j] <= prefix[r] - target" in approach.summary
    assert "earliest qualifying prefix" not in approach.summary

    failure = next(item for item in pack.failure_modes if item.id == "earliest_prefix_not_shortest")
    assert failure.approach_id == approach.approach_id
    assert failure.counterexample_id == "multiple_shrinks"


def test_corrected_counterexamples_and_followup_strategy_targets() -> None:
    entries = _qa_entries()
    container = entries["container-with-most-water"].interview_pack
    counterexamples = {item.id: item for item in container.counterexamples}
    assert counterexamples["taller_move"].input == [1, 2, 4, 3]
    assert counterexamples["asymmetric_width"].input == [1, 10, 1, 1]

    parentheses = entries["valid-parentheses"].interview_pack
    end_state = next(item for item in parentheses.common_followups if item.id == "end_state")
    assert end_state.target_concepts == ["stack"]
    assert end_state.relevant_strategies == ["PROVE"]
    assert end_state.applicable_stages == ["TESTING_DEBUGGING"]
