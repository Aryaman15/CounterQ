from __future__ import annotations

from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.problems.custom import CustomPreparationView, custom_preparation_retryable
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


class CreateCustomProblemPreparationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_text: str = Field(min_length=1, max_length=20_000)
    idempotency_key: str = Field(min_length=1, max_length=128)


class CustomProblemPreparationResponse(BaseModel):
    preparation_id: UUID
    operational_status: Literal["PENDING", "PROCESSING", "FAILED", "COMPLETED"]
    quality_outcome: Literal["READY", "NEEDS_CORRECTION", "REJECTED"] | None
    retryable: bool
    title: str | None
    statement_preview: str | None
    constraints: list[str]
    examples: list[dict[str, str]]
    supported_languages: list[CandidateLanguage]
    concept_labels: list[str]
    problem_version_id: UUID | None
    message: str


def custom_preparation_response(
    view: CustomPreparationView,
    *,
    now: datetime | None = None,
) -> CustomProblemPreparationResponse:
    preparation = view.preparation
    version = view.problem_version
    languages: list[CandidateLanguage] = []
    constraints: list[str] = []
    examples: list[dict[str, str]] = []
    if version is not None:
        raw_languages = cast(dict[str, object], version.io_schema_json.get("languages", {}))
        languages = [item for item in SUPPORTED_CANDIDATE_LANGUAGES if item in raw_languages]
        constraints = cast(list[str], version.constraints_json.get("items", []))
        examples = cast(list[dict[str, str]], version.examples_json)
    outcome = cast(
        Literal["READY", "NEEDS_CORRECTION", "REJECTED"] | None,
        preparation.quality_outcome,
    )
    status = cast(
        Literal["PENDING", "PROCESSING", "FAILED", "COMPLETED"],
        preparation.operational_status,
    )
    return CustomProblemPreparationResponse(
        preparation_id=preparation.id,
        operational_status=status,
        quality_outcome=outcome,
        retryable=custom_preparation_retryable(preparation, now=now),
        title=version.title if version is not None else None,
        statement_preview=(version.statement[:600] if version is not None else None),
        constraints=constraints,
        examples=examples,
        supported_languages=languages,
        concept_labels=list(view.concept_labels),
        problem_version_id=preparation.prepared_problem_version_id,
        message=preparation.candidate_message or "Custom problem preparation is pending.",
    )


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
