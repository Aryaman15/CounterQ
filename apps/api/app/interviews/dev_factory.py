from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.repository import UserRepository
from app.db.ids import uuid7
from app.interviews.models import InterviewConfiguration, InterviewSession, SessionBudget
from app.interviews.repository import InterviewRepository
from app.interviews.template_policy import InterviewTemplate, template_policy
from app.problems.models import InterviewPackVersion, Problem, ProblemVersion
from app.problems.repository import ProblemRepository


@dataclass(frozen=True)
class DevelopmentInterview:
    template: InterviewTemplate
    user: User
    problem: Problem
    problem_version: ProblemVersion
    pack_version: InterviewPackVersion
    configuration: InterviewConfiguration
    interview_session: InterviewSession
    budget: SessionBudget


async def create_development_interview(
    session: AsyncSession,
    *,
    initial_stage: str = "SETUP",
    state_version: int = 0,
    template: InterviewTemplate = "STANDARD_CODING_INTERVIEW",
) -> DevelopmentInterview:
    now = datetime.now(UTC)
    policy = template_policy(template)
    if policy.configured_duration_seconds is None:
        raise ValueError("FULL_SIMULATION requires an explicit duration policy before use")
    suffix = uuid7()
    user = await UserRepository(session).add(
        external_auth_provider="dev",
        external_auth_subject=f"stage1-runtime-candidate-{suffix}",
    )
    problems = ProblemRepository(session)
    problem = await problems.add_problem(
        source_type="CURATED",
        slug=f"longest-substring-without-repeating-characters-{suffix}",
        status="ACTIVE",
    )
    problem_version = await problems.add_problem_version(
        problem=problem,
        version="v1",
        title="Longest Substring Without Repeating Characters",
        statement=(
            "Given a string s, find the length of the longest substring without "
            "repeating characters."
        ),
        content_hash=f"sha256:longest-substring-{suffix}",
        schema_version="problem.v1",
    )
    pack_version = await problems.add_interview_pack_version(
        problem_version=problem_version,
        schema_version="interview-pack.v1",
        pack_json={
            "expected_approaches": ["sliding_window"],
            "invariants": ["left pointer never moves backward"],
            "edge_cases": ["empty string", "all repeated characters", "all unique characters"],
        },
        review_status="REVIEWED",
        preparation_policy_key="stage1_runtime_fixture",
    )
    interviews = InterviewRepository(session)
    configuration = await interviews.add_configuration(
        mode="SIMULATION",
        level="NEW_GRAD",
        language="cpp",
        configured_duration_seconds=policy.configured_duration_seconds,
        problem_source="CURATED",
    )
    interview_session = await interviews.add_session(
        user_id=user.id,
        configuration_id=configuration.id,
        problem_version_id=problem_version.id,
        interview_pack_version_id=pack_version.id,
        current_stage=initial_stage,
        state_version=state_version,
        status="ACTIVE",
        started_at=now,
        deadline_at=now + timedelta(seconds=policy.configured_duration_seconds),
    )
    budget = await interviews.add_budget(
        session_id=interview_session.id,
        max_duration_seconds=policy.configured_duration_seconds,
        max_probes=policy.max_probes,
        max_deep_reasoning_calls=policy.max_deep_reasoning_calls,
        max_strong_reasoning_calls=policy.max_strong_reasoning_calls,
        max_vision_calls=0,
        soft_monetary_budget=Decimal("2.5000"),
        hard_monetary_budget=Decimal("5.0000"),
        realtime_reserved_budget=Decimal("1.2500"),
    )
    return DevelopmentInterview(
        template=template,
        user=user,
        problem=problem,
        problem_version=problem_version,
        pack_version=pack_version,
        configuration=configuration,
        interview_session=interview_session,
        budget=budget,
    )
