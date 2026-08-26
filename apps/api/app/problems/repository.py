from sqlalchemy.ext.asyncio import AsyncSession

from app.problems.content import canonical_hash
from app.problems.models import InterviewPackVersion, Problem, ProblemVersion


class ProblemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_problem(
        self,
        *,
        source_type: str,
        slug: str | None,
        status: str,
    ) -> Problem:
        problem = Problem(source_type=source_type, slug=slug, status=status)
        self._session.add(problem)
        await self._session.flush()
        return problem

    async def add_problem_version(
        self,
        *,
        problem: Problem,
        version: str,
        title: str,
        statement: str,
        content_hash: str,
        schema_version: str,
    ) -> ProblemVersion:
        problem_version = ProblemVersion(
            problem_id=problem.id,
            version=version,
            title=title,
            statement=statement,
            content_hash=content_hash,
            schema_version=schema_version,
        )
        self._session.add(problem_version)
        await self._session.flush()
        return problem_version

    async def add_interview_pack_version(
        self,
        *,
        problem_version: ProblemVersion,
        schema_version: str,
        pack_json: dict[str, object],
        review_status: str,
        preparation_policy_key: str | None = None,
        authored_version: str | None = None,
    ) -> InterviewPackVersion:
        content_hash = canonical_hash(pack_json)
        effective_authored_version = authored_version or f"legacy:{content_hash[7:39]}"
        pack_version = InterviewPackVersion(
            problem_version_id=problem_version.id,
            schema_version=schema_version,
            authored_version=effective_authored_version,
            content_hash=content_hash,
            pack_json=pack_json,
            review_status=review_status,
            preparation_policy_key=preparation_policy_key,
        )
        self._session.add(pack_version)
        await self._session.flush()
        return pack_version
