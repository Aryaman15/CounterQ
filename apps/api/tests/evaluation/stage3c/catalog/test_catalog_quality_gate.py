from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.repository import UserRepository
from app.db.ids import uuid7
from app.db.session import get_session
from app.interviews.repository import InterviewRepository
from app.main import create_app
from app.problems.content import CuratedContent, load_curated_content, load_ontology
from app.problems.models import Problem, ProblemVersion
from app.problems.pack_service import InterviewPackService
from app.problems.service import CuratedProblemService

EXPECTED_CATALOG = {
    "two-sum": 1,
    "contains-duplicate": 2,
    "valid-anagram": 3,
    "product-of-array-except-self": 4,
    "top-k-frequent-elements": 5,
    "longest-substring-without-repeating-characters": 6,
    "minimum-size-subarray-sum": 7,
    "valid-palindrome": 8,
    "container-with-most-water": 9,
    "valid-parentheses": 10,
    "daily-temperatures": 11,
    "binary-search": 12,
    "search-in-rotated-sorted-array": 13,
    "kth-largest-element": 14,
    "merge-intervals": 15,
    "maximum-subarray": 16,
    "house-robber": 17,
    "coin-change": 18,
    "number-of-islands": 19,
    "course-schedule": 20,
}
UNORDERED_RESULT_SLUGS = {"two-sum", "top-k-frequent-elements"}
STRING_INPUT_SLUGS = {
    "valid-anagram",
    "longest-substring-without-repeating-characters",
    "valid-palindrome",
    "valid-parentheses",
    "number-of-islands",
}
FORBIDDEN_CANDIDATE_TERMS = (
    "reviewed",
    "reviewed cases",
    "reviewed execution cases",
    "reference solution",
    "interview pack",
    "validation solution",
    "primary reviewed approach",
    "counterq expects",
)
FORBIDDEN_PACK_FIELDS = {
    "expected_approaches",
    "alternative_approaches",
    "reference_solutions",
    "reference_reasoning",
    "common_followups",
    "common_misconceptions",
    "failure_modes",
    "invariants",
    "complexity_expectations",
    "counterexamples",
    "probe_opportunities",
    "constraint_mutations",
    "level_considerations",
}
INT32_MAX = 2_147_483_647
INT32_MIN = -2_147_483_648


def _entries_by_slug() -> dict[str, CuratedContent]:
    return {entry.problem.slug: entry for entry in load_curated_content()}


def _candidate_copy(entry: CuratedContent) -> str:
    problem = entry.problem
    fields = [problem.title, problem.statement, *problem.constraints]
    for example in problem.examples:
        fields.extend((example.input, example.output, example.explanation))
    return "\n".join(fields).casefold()


def _has_visible_case(entry: CuratedContent, expected_output: object, **arguments: object) -> bool:
    return any(
        case.arguments == arguments and case.expected_output == expected_output
        for case in entry.problem.execution.visible_cases
    )


def _assert_int32_values(value: object) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        assert INT32_MIN <= value <= INT32_MAX
    elif isinstance(value, list):
        for item in value:
            _assert_int32_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            _assert_int32_values(item)


def test_authored_catalog_shape_and_candidate_boundary() -> None:
    entries = load_curated_content()
    assert len(entries) == 20
    assert [entry.problem.catalog_order for entry in entries] == list(range(1, 21))
    assert {entry.problem.slug: entry.problem.catalog_order for entry in entries} == (
        EXPECTED_CATALOG
    )
    assert len({entry.problem.slug for entry in entries}) == 20

    for entry in entries:
        problem = entry.problem
        assert problem.review_status == "REVIEWED"
        assert entry.interview_pack.review_status == "REVIEWED"
        assert set(problem.languages) == {"cpp", "python", "java"}
        assert all(language.starter_code for language in problem.languages.values())
        assert len(problem.examples) >= 2
        assert len(problem.execution.visible_cases) >= 2
        expected_comparator = (
            "UNORDERED_LIST" if problem.slug in UNORDERED_RESULT_SLUGS else "EXACT"
        )
        assert problem.execution.comparator == expected_comparator
        for case in problem.execution.visible_cases:
            _assert_int32_values(case.arguments)
            _assert_int32_values(case.expected_output)

        candidate_copy = _candidate_copy(entry)
        for term in FORBIDDEN_CANDIDATE_TERMS:
            assert re.search(rf"\b{re.escape(term)}\b", candidate_copy) is None
        if problem.slug in STRING_INPUT_SLUGS:
            assert all('"' in example.input for example in problem.examples)


def test_critical_candidate_contracts_and_integer_safety() -> None:
    by_slug = _entries_by_slug()

    two_sum = by_slug["two-sum"]
    assert "distinct elements" in two_sum.problem.statement
    assert "either order" in two_sum.problem.statement
    assert "exactly one valid pair" in " ".join(two_sum.problem.constraints).casefold()
    assert 2_000_000_000 <= INT32_MAX

    contains_duplicate = by_slug["contains-duplicate"]
    assert "0 <= nums.length" in contains_duplicate.problem.constraints[0]
    assert "otherwise return false" in contains_duplicate.problem.statement.casefold()

    valid_anagram = by_slug["valid-anagram"]
    assert "lowercase english letters" in " ".join(valid_anagram.problem.constraints).casefold()

    product = by_slug["product-of-array-except-self"]
    product_contract = " ".join(
        [product.problem.statement, *product.problem.constraints]
    ).casefold()
    assert "without using division" in product_contract
    assert "prefix product" in product_contract and "suffix product" in product_contract
    assert "signed 32-bit" in product_contract
    assert _has_visible_case(product, [0, 0, 0], nums=[0, 0, 2])

    top_k = by_slug["top-k-frequent-elements"]
    top_k_contract = " ".join([top_k.problem.statement, *top_k.problem.constraints]).casefold()
    assert "unique set of k" in top_k_contract
    assert "any order" in top_k_contract

    longest = by_slug["longest-substring-without-repeating-characters"]
    assert "contiguous substring" in longest.problem.statement.casefold()
    assert "printable ascii" in " ".join(longest.problem.constraints).casefold()

    minimum = by_slug["minimum-size-subarray-sum"]
    minimum_contract = " ".join(
        [minimum.problem.statement, *minimum.problem.constraints]
    ).casefold()
    assert "positive integers" in minimum_contract
    assert "return 0" in minimum_contract
    assert 100_000 * 10_000 <= INT32_MAX

    palindrome = by_slug["valid-palindrome"]
    assert "ascii" in " ".join(palindrome.problem.constraints).casefold()

    container = by_slug["container-with-most-water"]
    assert "100000" in " ".join(container.problem.constraints)
    assert "10000" in " ".join(container.problem.constraints)
    assert (100_000 - 1) * 10_000 <= INT32_MAX

    parentheses = by_slug["valid-parentheses"]
    assert "only the six bracket characters" in " ".join(
        parentheses.problem.constraints
    ).casefold()

    daily = by_slug["daily-temperatures"]
    assert "strictly warmer" in daily.problem.statement.casefold()
    assert _has_visible_case(daily, [0, 0, 0], temperatures=[70, 70, 70])

    binary = by_slug["binary-search"]
    binary_contract = " ".join([binary.problem.statement, *binary.problem.constraints]).casefold()
    assert "strictly increasing" in binary_contract
    assert "-1 when it is absent" in binary_contract
    assert _has_visible_case(binary, -1, nums=[-1, 0, 3, 5, 9, 12], target=2)

    rotated = by_slug["search-in-rotated-sorted-array"]
    assert "distinct values" in " ".join(rotated.problem.constraints).casefold()

    kth = by_slug["kth-largest-element"]
    assert "equal values occupy separate positions" in kth.problem.statement.casefold()
    assert _has_visible_case(kth, 7, nums=[7, 7, 7], k=3)

    merge = by_slug["merge-intervals"]
    merge_contract = " ".join([merge.problem.statement, *merge.problem.constraints]).casefold()
    assert "closed" in merge_contract and "touch" in merge_contract
    assert "ordered by ascending start" in merge_contract
    assert _has_visible_case(merge, [[1, 5]], intervals=[[1, 4], [4, 5]])

    maximum = by_slug["maximum-subarray"]
    maximum_contract = " ".join(
        [maximum.problem.statement, *maximum.problem.constraints]
    ).casefold()
    assert "non-empty" in maximum_contract
    assert "-10000 <= nums[i] <= 10000" in maximum.problem.constraints
    assert 100_000 * 10_000 <= INT32_MAX
    assert _has_visible_case(maximum, -2, nums=[-5, -2, -7])

    robber = by_slug["house-robber"]
    robber_contract = " ".join([robber.problem.statement, *robber.problem.constraints]).casefold()
    assert "non-negative" in robber_contract
    assert "line, not a circle" in robber_contract
    assert _has_visible_case(robber, 0, nums=[])
    assert 50_000 * 10_000 <= INT32_MAX

    coin_change = by_slug["coin-change"]
    coin_contract = " ".join(
        [coin_change.problem.statement, *coin_change.problem.constraints]
    ).casefold()
    assert "positive coin denominations" in coin_contract
    assert "any number of times" in coin_contract
    assert "0 <= amount" in coin_contract
    assert "return -1" in coin_contract
    assert 10_000 + 1 <= INT32_MAX

    islands = by_slug["number-of-islands"]
    islands_contract = " ".join(
        [islands.problem.statement, *islands.problem.constraints]
    ).casefold()
    assert islands.problem.execution.arguments[0].type == "string[]"
    assert "all rows have equal length" in islands_contract
    assert '"0" or "1"' in islands_contract
    assert "up, down, left, and right" in islands_contract
    assert 300 * 300 <= INT32_MAX

    course = by_slug["course-schedule"]
    course_contract = " ".join([course.problem.statement, *course.problem.constraints]).casefold()
    assert "[course, prerequisite]" in course_contract
    assert "between 0 and numcourses - 1" in course_contract
    assert "every course can be completed" in course_contract


@pytest.mark.asyncio
async def test_current_versions_and_exact_historical_session_binding(
    db_session: AsyncSession,
) -> None:
    entries = load_curated_content()
    service = CuratedProblemService(db_session)
    await service.seed_ontology(load_ontology())
    for entry in entries:
        await service.seed_problem(entry)

    catalog = await service.list_candidate_catalog()
    authored_versions = {entry.problem.slug: entry.problem.version for entry in entries}
    actual_versions = {
        version.problem.slug: version.version
        for version in catalog
        if version.problem.slug in authored_versions
    }
    assert actual_versions == authored_versions
    for version in catalog:
        if version.problem.slug in authored_versions:
            pack = await service.reviewed_pack_for_problem(version.id)
            assert pack.problem_version_id == version.id

    template = entries[0]
    unique_slug = f"catalog-history-{uuid7()}"
    current = template.model_copy(deep=True)
    current.problem.slug = unique_slug
    current.problem.catalog_order = 1000
    current.problem.version = "v2"
    current.problem.statement += " Current authored contract."
    current.interview_pack.version = "v2"
    current.interview_pack.reference_reasoning += " SERVER_ONLY_CATALOG_SENTINEL"
    await service.seed_problem(current)

    historical = template.model_copy(deep=True)
    historical.problem.slug = unique_slug
    historical.problem.catalog_order = 1000
    historical.problem.version = "v1"
    historical.problem.statement += " Historical authored contract."
    historical.interview_pack.version = "v1"
    historical.interview_pack.reference_reasoning += " Historical pack."
    await service.seed_problem(historical)
    await db_session.flush()

    versions = list(
        (
            await db_session.scalars(
                select(ProblemVersion).join(Problem).where(Problem.slug == unique_slug)
            )
        ).all()
    )
    by_version = {version.version: version for version in versions}
    selected = next(
        version
        for version in await service.list_candidate_catalog()
        if version.problem.slug == unique_slug
    )
    assert selected.id == by_version["v2"].id
    assert by_version["v1"].created_at >= by_version["v2"].created_at

    historical_pack = await service.reviewed_pack_for_problem(by_version["v1"].id)
    now = datetime(2026, 8, 27, 0, 0, tzinfo=UTC)
    user = await UserRepository(db_session).add(
        external_auth_provider="dev",
        external_auth_subject=f"catalog-history-{uuid7()}",
    )
    interviews = InterviewRepository(db_session)
    configuration = await interviews.add_configuration(
        mode="SIMULATION",
        level="NEW_GRAD",
        language="cpp",
        configured_duration_seconds=1800,
        problem_source="CURATED",
    )
    interview = await interviews.add_session(
        user_id=user.id,
        configuration_id=configuration.id,
        problem_version_id=by_version["v1"].id,
        interview_pack_version_id=historical_pack.id,
        current_stage="IMPLEMENTATION",
        state_version=0,
        status="ACTIVE",
        started_at=now,
        deadline_at=now + timedelta(minutes=30),
    )
    await interviews.add_budget(
        session_id=interview.id,
        max_duration_seconds=1800,
        max_probes=5,
        max_deep_reasoning_calls=8,
        reserved_post_interview_deep_reasoning_calls=0,
        max_strong_reasoning_calls=1,
        max_vision_calls=0,
        soft_monetary_budget=Decimal("2.5"),
        hard_monetary_budget=Decimal("5"),
        realtime_reserved_budget=Decimal("1.25"),
    )

    bound_pack = await InterviewPackService(db_session).for_session(interview.id)
    assert interview.problem_version_id == by_version["v1"].id
    assert interview.interview_pack_version_id == historical_pack.id
    assert bound_pack.version == "v1"

    app = create_app()

    async def override_session() -> AsyncSession:
        return db_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        catalog_response = await client.get("/api/problems/curated")
        assert catalog_response.status_code == 200
        catalog_payload = catalog_response.json()
        current_payload = next(item for item in catalog_payload if item["slug"] == unique_slug)
        assert current_payload["problem_version_id"] == str(by_version["v2"].id)
        detail_response = await client.get(
            f"/api/problems/curated/{by_version['v2'].id}?language=cpp"
        )
        assert detail_response.status_code == 200
        detail_payload = detail_response.json()

    serialized = json.dumps(detail_payload).casefold()
    assert "server_only_catalog_sentinel" not in serialized
    for field in FORBIDDEN_PACK_FIELDS:
        assert f'"{field}"' not in serialized
