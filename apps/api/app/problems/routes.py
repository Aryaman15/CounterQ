from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.problems.service import CuratedProblemError, CuratedProblemService

router = APIRouter(prefix="/api/problems/curated", tags=["curated-problems"])


class CuratedCatalogItem(BaseModel):
    problem_version_id: UUID
    slug: str
    title: str
    supported_languages: list[Literal["cpp", "python", "java"]]
    catalog_order: int


class CandidateProblemDetail(CuratedCatalogItem):
    statement: str
    constraints: list[str]
    examples: list[dict[str, str]]
    selected_language: Literal["cpp", "python", "java"]
    display_signature: str
    starter_code: str
    argument_schema: list[dict[str, object]]
    return_type: str
    comparator: str
    custom_test_supported: bool


@router.get("", response_model=list[CuratedCatalogItem])
async def list_curated_problems(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CuratedCatalogItem]:
    versions = await CuratedProblemService(session).list_candidate_catalog()
    return [_catalog_item(version) for version in versions]


@router.get("/{problem_version_id}", response_model=CandidateProblemDetail)
async def curated_problem_detail(
    problem_version_id: UUID,
    language: Literal["cpp", "python", "java"],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateProblemDetail:
    try:
        version = await CuratedProblemService(session).candidate_problem(problem_version_id)
    except CuratedProblemError as exc:
        raise HTTPException(status_code=404, detail="Curated problem is not available") from exc
    schema = version.io_schema_json
    execution = cast(dict[str, object], schema["execution"])
    languages = cast(dict[str, dict[str, object]], schema["languages"])
    if language not in languages:
        raise HTTPException(status_code=404, detail="Requested language is not available")
    language_definition = languages[language]
    return CandidateProblemDetail(
        **_catalog_item(version).model_dump(),
        statement=version.statement,
        constraints=cast(list[str], version.constraints_json["items"]),
        examples=cast(list[dict[str, str]], version.examples_json),
        selected_language=language,
        display_signature=cast(str, language_definition["display_signature"]),
        starter_code=cast(str, language_definition["starter_code"]),
        argument_schema=cast(list[dict[str, object]], execution["arguments"]),
        return_type=cast(str, execution["return_type"]),
        comparator=cast(str, execution["comparator"]),
        custom_test_supported=bool(execution["custom_test_supported"]),
    )


def _catalog_item(version: object) -> CuratedCatalogItem:
    from app.problems.models import ProblemVersion

    assert isinstance(version, ProblemVersion)
    languages = cast(dict[str, object], version.io_schema_json["languages"])
    return CuratedCatalogItem(
        problem_version_id=version.id,
        slug=version.problem.slug or "",
        title=version.title,
        supported_languages=[
            language for language in ("cpp", "python", "java") if language in languages
        ],
        catalog_order=cast(int, version.io_schema_json["catalog_order"]),
    )
