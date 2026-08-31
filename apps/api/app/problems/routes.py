from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.problems.contracts import (
    CandidateLanguage,
    CandidateProblemDetail,
    CuratedCatalogItem,
    candidate_problem_detail,
    curated_catalog_item,
)
from app.problems.service import CuratedProblemError, CuratedProblemService

router = APIRouter(prefix="/api/problems/curated", tags=["curated-problems"])


@router.get("", response_model=list[CuratedCatalogItem])
async def list_curated_problems(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CuratedCatalogItem]:
    versions = await CuratedProblemService(session).list_candidate_catalog()
    return [curated_catalog_item(version) for version in versions]


@router.get("/{problem_version_id}", response_model=CandidateProblemDetail)
async def curated_problem_detail(
    problem_version_id: UUID,
    language: CandidateLanguage,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateProblemDetail:
    try:
        version = await CuratedProblemService(session).candidate_problem(problem_version_id)
    except CuratedProblemError as exc:
        raise HTTPException(status_code=404, detail="Curated problem is not available") from exc
    try:
        return candidate_problem_detail(version, language)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail="Requested language is not available"
        ) from exc
