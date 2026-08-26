from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.problems.content import (
    ConceptOntology,
    CuratedContent,
    InterviewPackContent,
    ProblemContent,
    canonical_hash,
    load_curated_content,
    load_ontology,
    validate_authored_content,
)
from app.problems.models import (
    Concept,
    ConceptAlias,
    ConceptRelationship,
    InterviewPackVersion,
    Problem,
    ProblemConcept,
    ProblemVersion,
)
from app.problems.service import CuratedProblemError, CuratedProblemService


def test_repository_authored_content_validates() -> None:
    ontology, entries = validate_authored_content()
    assert len(entries) == 20
    assert all(entry.problem.review_status == "REVIEWED" for entry in entries)
    assert len(ontology.concepts) >= 25


def test_batch_one_curated_catalog_has_reviewed_pack_depth() -> None:
    _, entries = validate_authored_content()
    by_slug = {entry.problem.slug: entry for entry in entries}
    expected_orders = {
        "two-sum": 1,
        "contains-duplicate": 2,
        "valid-anagram": 3,
        "product-of-array-except-self": 4,
        "valid-palindrome": 8,
        "valid-parentheses": 10,
    }

    actual_orders = {slug: by_slug[slug].problem.catalog_order for slug in expected_orders}
    assert actual_orders == expected_orders
    for slug in expected_orders:
        entry = by_slug[slug]
        pack = entry.interview_pack
        assert pack.review_status == "REVIEWED"
        assert len(pack.reference_solutions) >= 3
        primary = pack.expected_approaches[0].approach_id
        languages = {
            item.language for item in pack.reference_solutions if item.approach_id == primary
        }
        assert languages == {"cpp", "python", "java"}
        assert len(pack.common_followups) >= 3
        assert len(pack.counterexamples) >= 2

    assert by_slug["two-sum"].problem.execution.comparator == "UNORDERED_LIST"


def test_batch_two_curated_catalog_has_reviewed_pack_depth() -> None:
    _, entries = validate_authored_content()
    by_slug = {entry.problem.slug: entry for entry in entries}
    expected_orders = {
        "top-k-frequent-elements": 5,
        "minimum-size-subarray-sum": 7,
        "container-with-most-water": 9,
        "daily-temperatures": 11,
        "search-in-rotated-sorted-array": 13,
        "kth-largest-element": 14,
    }
    actual_orders = {slug: by_slug[slug].problem.catalog_order for slug in expected_orders}
    assert actual_orders == expected_orders
    for slug in expected_orders:
        entry = by_slug[slug]
        pack = entry.interview_pack
        primary = pack.expected_approaches[0].approach_id
        assert entry.problem.review_status == "REVIEWED"
        assert all(
            entry.problem.languages[language].starter_code
            for language in ("cpp", "python", "java")
        )
        languages = {
            item.language for item in pack.reference_solutions if item.approach_id == primary
        }
        assert languages == {"cpp", "python", "java"}
        assert len(pack.common_followups) >= 3
        assert len(pack.counterexamples) >= 2

    assert by_slug["top-k-frequent-elements"].problem.execution.comparator == "UNORDERED_LIST"


def test_final_batch_has_reviewed_content_and_execution_shapes() -> None:
    _, entries = validate_authored_content()
    by_slug = {entry.problem.slug: entry for entry in entries}
    expected = {
        "merge-intervals": (15, "int[][]", "int[][]"),
        "maximum-subarray": (16, "int[]", "int"),
        "house-robber": (17, "int[]", "int"),
        "coin-change": (18, "int[]", "int"),
        "number-of-islands": (19, "string[]", "int"),
        "course-schedule": (20, "int", "bool"),
    }
    for slug, (order, first_argument, return_type) in expected.items():
        entry = by_slug[slug]
        pack = entry.interview_pack
        assert entry.problem.catalog_order == order
        assert entry.problem.review_status == pack.review_status == "REVIEWED"
        assert entry.problem.execution.arguments[0].type == first_argument
        assert entry.problem.execution.return_type == return_type
        assert len(pack.common_followups) >= 3
        assert len(pack.counterexamples) >= 2
        primary = pack.expected_approaches[0].approach_id
        languages = {
            item.language for item in pack.reference_solutions if item.approach_id == primary
        }
        assert languages == {"cpp", "python", "java"}


def test_canonical_hash_ignores_order_and_line_endings() -> None:
    assert canonical_hash({"b": "one\r\ntwo", "a": 1}) == canonical_hash({"a": 1, "b": "one\ntwo"})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["concepts"].append(deepcopy(data["concepts"][0])),
        lambda data: data["concepts"][0].update(parent_concept_key="missing"),
        lambda data: data["concepts"][0].update(parent_concept_key="arrays"),
        lambda data: data["aliases"].append(
            {"concept_key": "missing", "alias": "unknown", "alias_type": "TERM"}
        ),
        lambda data: data["aliases"].append(
            {"concept_key": "dynamic_programming", "alias": "dP", "alias_type": "TERM"}
        ),
        lambda data: data["relationships"].append(
            {"from_concept_key": "arrays", "to_concept_key": "missing", "relationship_type": "USES"}
        ),
        lambda data: data["relationships"].append(deepcopy(data["relationships"][0])),
        lambda data: data["relationships"].append(
            {
                "from_concept_key": "arrays",
                "to_concept_key": "hash_map",
                "relationship_type": "INVALID",
            }
        ),
    ],
)
def test_ontology_rejects_invalid_graph(mutate: object) -> None:
    payload = load_ontology().model_dump(mode="json")
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(ValidationError):
        ConceptOntology.model_validate(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["execution"]["visible_cases"][1]["arguments"].pop("target"),
        lambda data: data["execution"]["visible_cases"][0]["arguments"].update(nums=[1, "2"]),
        lambda data: data["execution"]["visible_cases"][0].update(expected_output="four"),
    ],
)
def test_problem_rejects_invalid_semantic_cases(mutate: object) -> None:
    entry = next(entry for entry in load_curated_content() if entry.problem.slug == "binary-search")
    payload = entry.problem.model_dump(mode="json")
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(ValidationError):
        ProblemContent.model_validate(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["expected_approaches"][0]["concept_keys"].append("missing"),
        lambda data: data["reference_solutions"][0].update(approach_id="missing"),
        lambda data: data["common_followups"][0]["target_concepts"].append("missing"),
        lambda data: data["common_followups"][0].update(counterexample_id="missing"),
        lambda data: data["common_followups"][0]["relevant_strategies"].append("INVALID"),
    ],
)
def test_pack_rejects_dangling_or_invalid_references(mutate: object) -> None:
    payload = load_curated_content()[1].interview_pack.model_dump(mode="json")
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(ValidationError):
        InterviewPackContent.model_validate(payload)


def test_reviewed_problem_requires_reviewed_pack() -> None:
    entry = load_curated_content()[0]
    pack = entry.interview_pack.model_copy(update={"review_status": "DRAFT"})
    with pytest.raises(ValidationError):
        CuratedContent(problem=entry.problem, interview_pack=pack)


@pytest.mark.asyncio
async def test_seed_is_idempotent_across_all_foundation_rows(db_session: AsyncSession) -> None:
    ontology, entries = validate_authored_content()
    service = CuratedProblemService(db_session)
    await service.seed_ontology(ontology)
    for entry in entries:
        await service.seed_problem(entry)
    await db_session.flush()
    first = await _row_counts(db_session)

    second_ontology = await service.seed_ontology(ontology)
    second_problems = [await service.seed_problem(entry) for entry in entries]
    await db_session.flush()

    assert second_ontology == type(second_ontology)()
    assert all(counts == type(counts)() for counts in second_problems)
    assert await _row_counts(db_session) == first


@pytest.mark.asyncio
async def test_ontology_seed_rejects_conflicting_authored_meaning(
    db_session: AsyncSession,
) -> None:
    ontology = load_ontology()
    service = CuratedProblemService(db_session)
    await service.seed_ontology(ontology)

    changed = ontology.model_copy(deep=True)
    changed.concepts[0].description += " Changed without a canonical identity change."

    with pytest.raises(CuratedProblemError, match="immutable authored meaning"):
        await service.seed_ontology(changed)


@pytest.mark.asyncio
async def test_problem_and_pack_versions_are_immutable(db_session: AsyncSession) -> None:
    ontology, entries = validate_authored_content()
    service = CuratedProblemService(db_session)
    await service.seed_ontology(ontology)
    entry = entries[0]
    await service.seed_problem(entry)
    original_problem_hash = canonical_hash(entry.problem.model_dump(mode="json"))
    original_pack_hash = canonical_hash(entry.interview_pack.model_dump(mode="json"))

    changed_problem = entry.model_copy(deep=True)
    changed_problem.problem.statement += " Changed without a version bump."
    with pytest.raises(CuratedProblemError, match="increment problem version"):
        await service.seed_problem(changed_problem)

    changed_pack = entry.model_copy(deep=True)
    changed_pack.interview_pack.reference_reasoning += " Changed without a version bump."
    with pytest.raises(CuratedProblemError, match="increment pack version"):
        await service.seed_problem(changed_pack)

    next_problem = entry.model_copy(deep=True)
    next_problem.problem.version = "v99"
    next_problem.interview_pack.version = "v99"
    created = await service.seed_problem(next_problem)
    assert created.problem_versions == 1
    assert created.interview_pack_versions == 1

    next_pack = entry.model_copy(deep=True)
    next_pack.interview_pack.version = "v98"
    created_pack = await service.seed_problem(next_pack)
    assert created_pack.problem_versions == 0
    assert created_pack.interview_pack_versions == 1

    problem_row = await db_session.scalar(
        select(ProblemVersion).where(ProblemVersion.content_hash == original_problem_hash)
    )
    pack_row = await db_session.scalar(
        select(InterviewPackVersion).where(InterviewPackVersion.content_hash == original_pack_hash)
    )
    assert problem_row is not None and problem_row.statement == entry.problem.statement
    assert (
        pack_row is not None
        and pack_row.pack_json["reference_reasoning"] == entry.interview_pack.reference_reasoning
    )


@pytest.mark.asyncio
async def test_problem_concept_seed_rejects_conflicting_mapping(
    db_session: AsyncSession,
) -> None:
    ontology, entries = validate_authored_content()
    service = CuratedProblemService(db_session)
    await service.seed_ontology(ontology)
    await service.seed_problem(entries[0])
    mapping = await db_session.scalar(select(ProblemConcept))
    assert mapping is not None
    mapping.role = "OPTIONAL" if mapping.role != "OPTIONAL" else "PRIMARY"
    await db_session.flush()

    with pytest.raises(CuratedProblemError, match="immutable ProblemConcept mapping"):
        await service.seed_problem(entries[0])


async def _row_counts(session: AsyncSession) -> tuple[int, ...]:
    models = (
        Concept,
        ConceptAlias,
        ConceptRelationship,
        Problem,
        ProblemVersion,
        InterviewPackVersion,
        ProblemConcept,
    )
    counts: list[int] = []
    for model in models:
        counts.append(int(await session.scalar(select(func.count()).select_from(model)) or 0))
    return tuple(counts)
