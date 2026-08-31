from __future__ import annotations

from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel

from app.problems.models import ProblemVersion

CandidateLanguage = Literal["cpp", "python", "java"]
SUPPORTED_CANDIDATE_LANGUAGES: tuple[CandidateLanguage, ...] = ("cpp", "python", "java")


class CuratedCatalogItem(BaseModel):
    problem_version_id: UUID
    slug: str
    title: str
    supported_languages: list[CandidateLanguage]
    catalog_order: int


class CandidateProblemDetail(CuratedCatalogItem):
    statement: str
    constraints: list[str]
    examples: list[dict[str, str]]
    selected_language: CandidateLanguage
    display_signature: str
    starter_code: str
    argument_schema: list[dict[str, object]]
    return_type: str
    comparator: str
    custom_test_supported: bool


def curated_catalog_item(version: ProblemVersion) -> CuratedCatalogItem:
    languages = cast(dict[str, object], version.io_schema_json["languages"])
    return CuratedCatalogItem(
        problem_version_id=version.id,
        slug=version.problem.slug or "",
        title=version.title,
        supported_languages=[
            language for language in SUPPORTED_CANDIDATE_LANGUAGES if language in languages
        ],
        catalog_order=cast(int, version.io_schema_json["catalog_order"]),
    )


def candidate_problem_detail(
    version: ProblemVersion,
    language: CandidateLanguage,
) -> CandidateProblemDetail:
    schema = version.io_schema_json
    execution = cast(dict[str, object], schema["execution"])
    languages = cast(dict[str, dict[str, object]], schema["languages"])
    if language not in languages:
        raise ValueError("Requested language is not available")
    language_definition = languages[language]
    return CandidateProblemDetail(
        **curated_catalog_item(version).model_dump(),
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
