from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.repository import UserRepository
from app.db.ids import uuid7
from app.interviews.models import InterviewConfiguration, InterviewSession, SessionBudget
from app.interviews.repository import InterviewRepository
from app.observation.models import CodeSnapshot, InterviewEvent, TranscriptSegment
from app.observation.repository import ObservationRepository
from app.problems.models import InterviewPackVersion, Problem, ProblemVersion
from app.problems.repository import ProblemRepository


@dataclass(frozen=True)
class Stage1PersistenceGraph:
    user: User
    problem: Problem
    problem_version: ProblemVersion
    pack_version: InterviewPackVersion
    configuration: InterviewConfiguration
    interview_session: InterviewSession
    budget: SessionBudget


async def create_stage1_graph(
    db_session: AsyncSession,
    *,
    now: datetime | None = None,
) -> Stage1PersistenceGraph:
    created_at = now or datetime.now(UTC)
    user = await UserRepository(db_session).add(
        external_auth_provider="dev",
        external_auth_subject=f"candidate-{uuid7()}",
    )
    problems = ProblemRepository(db_session)
    problem = await problems.add_problem(
        source_type="CURATED",
        slug=f"longest-substring-{uuid7()}",
        status="ACTIVE",
    )
    problem_version = await problems.add_problem_version(
        problem=problem,
        version="v1",
        title="Longest Substring Without Repeating Characters",
        statement="Find the length of the longest substring without repeating characters.",
        content_hash=f"sha256:{uuid7()}",
        schema_version="problem.v1",
    )
    pack_version = await problems.add_interview_pack_version(
        problem_version=problem_version,
        schema_version="interview-pack.v1",
        pack_json={"expected_approaches": ["sliding_window"], "invariants": ["monotonic_left"]},
        review_status="REVIEWED",
        preparation_policy_key="manual_review",
    )

    interviews = InterviewRepository(db_session)
    configuration = await interviews.add_configuration(
        mode="SIMULATION",
        level="NEW_GRAD",
        language="cpp",
        configured_duration_seconds=1_800,
        problem_source="CURATED",
    )
    interview_session = await interviews.add_session(
        user_id=user.id,
        configuration_id=configuration.id,
        problem_version_id=problem_version.id,
        interview_pack_version_id=pack_version.id,
        current_stage="SETUP",
        state_version=0,
        status="ACTIVE",
        started_at=created_at,
        deadline_at=created_at + timedelta(minutes=30),
    )
    budget = await interviews.add_budget(
        session_id=interview_session.id,
        max_duration_seconds=1_800,
        max_probes=5,
        max_deep_reasoning_calls=8,
        max_strong_reasoning_calls=1,
        max_vision_calls=0,
        soft_monetary_budget=Decimal("2.5000"),
        hard_monetary_budget=Decimal("5.0000"),
        realtime_reserved_budget=Decimal("1.2500"),
    )
    return Stage1PersistenceGraph(
        user=user,
        problem=problem,
        problem_version=problem_version,
        pack_version=pack_version,
        configuration=configuration,
        interview_session=interview_session,
        budget=budget,
    )


async def add_event(
    db_session: AsyncSession,
    graph: Stage1PersistenceGraph,
    *,
    server_sequence: int,
    event_type: str = "TRANSCRIPT_FINALIZED",
    source: str = "CANDIDATE_VOICE",
    now: datetime | None = None,
) -> InterviewEvent:
    occurred_at = now or datetime.now(UTC)
    return await InterviewRepository(db_session).add_event(
        session_id=graph.interview_session.id,
        user_id=graph.user.id,
        event_type=event_type,
        source=source,
        occurred_at=occurred_at,
        received_at=occurred_at,
        server_sequence=server_sequence,
        interview_state_version=graph.interview_session.state_version,
        schema_version="interview.event.v1",
        idempotency_key=f"event-{server_sequence}-{uuid7()}",
    )


async def test_user_to_interview_session_relationship_is_valid(db_session: AsyncSession) -> None:
    graph = await create_stage1_graph(db_session)

    result = await db_session.scalar(
        select(InterviewSession).where(InterviewSession.user_id == graph.user.id),
    )

    assert result is not None
    assert result.id == graph.interview_session.id


async def test_problem_version_belongs_to_correct_problem(db_session: AsyncSession) -> None:
    graph = await create_stage1_graph(db_session)

    problem_version = await db_session.get(ProblemVersion, graph.problem_version.id)

    assert problem_version is not None
    assert problem_version.problem_id == graph.problem.id


async def test_interview_pack_version_is_immutable_versioned_row(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    second_pack = await ProblemRepository(db_session).add_interview_pack_version(
        problem_version=graph.problem_version,
        schema_version="interview-pack.v1",
        pack_json={"expected_approaches": ["brute_force"], "invariants": []},
        review_status="REVIEWED",
        preparation_policy_key="manual_review",
    )

    assert second_pack.id != graph.pack_version.id
    assert second_pack.problem_version_id == graph.problem_version.id
    assert not hasattr(InterviewPackVersion, "updated_at")


async def test_interview_session_references_configuration_problem_and_pack(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)

    interview_session = await db_session.get(InterviewSession, graph.interview_session.id)

    assert interview_session is not None
    assert interview_session.interview_configuration_id == graph.configuration.id
    assert interview_session.problem_version_id == graph.problem_version.id
    assert interview_session.interview_pack_version_id == graph.pack_version.id


async def test_session_budget_is_one_to_one_with_session(db_session: AsyncSession) -> None:
    graph = await create_stage1_graph(db_session)

    assert graph.budget.session_id == graph.interview_session.id

    with pytest.raises(IntegrityError):
        await db_session.execute(
            insert(SessionBudget).values(
                session_id=graph.interview_session.id,
                max_duration_seconds=1_800,
                max_probes=1,
                max_deep_reasoning_calls=1,
                max_strong_reasoning_calls=0,
                max_vision_calls=0,
                soft_monetary_budget=Decimal("1.0000"),
                hard_monetary_budget=Decimal("2.0000"),
                realtime_reserved_budget=Decimal("0.5000"),
            ),
        )


async def test_interview_event_requires_valid_session_provenance(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    now = datetime.now(UTC)
    invalid_event = InterviewEvent(
        interview_session_id=uuid7(),
        user_id=graph.user.id,
        event_type="TRANSCRIPT_FINALIZED",
        source="CANDIDATE_VOICE",
        occurred_at=now,
        received_at=now,
        server_sequence=1,
        interview_state_version=0,
        schema_version="interview.event.v1",
    )
    db_session.add(invalid_event)

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_interview_event_server_sequence_is_unique_per_session(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    await add_event(db_session, graph, server_sequence=1)

    with pytest.raises(IntegrityError):
        await add_event(db_session, graph, server_sequence=1)


async def test_transcript_segment_references_session_and_event_provenance(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    event = await add_event(db_session, graph, server_sequence=1)
    now = datetime.now(UTC)

    segment = await ObservationRepository(db_session).add_transcript_segment(
        session_id=graph.interview_session.id,
        event_id=event.id,
        speaker="CANDIDATE",
        sequence=1,
        started_at=now,
        ended_at=now + timedelta(seconds=3),
        text="I will start with a sliding window.",
        provider_confidence=Decimal("0.9500"),
        interview_stage="PROBLEM_UNDERSTANDING",
        interview_state_version=0,
    )

    assert segment.interview_session_id == graph.interview_session.id
    assert segment.interview_event_id == event.id


async def test_transcript_segment_rejects_event_from_different_session(
    db_session: AsyncSession,
) -> None:
    session_a = await create_stage1_graph(db_session)
    session_b = await create_stage1_graph(db_session)
    event_from_b = await add_event(db_session, session_b, server_sequence=1)

    with pytest.raises(IntegrityError):
        await ObservationRepository(db_session).add_transcript_segment(
            session_id=session_a.interview_session.id,
            event_id=event_from_b.id,
            speaker="CANDIDATE",
            sequence=1,
            started_at=datetime.now(UTC),
            text="This segment should not cross sessions.",
            interview_stage="PROBLEM_UNDERSTANDING",
            interview_state_version=0,
        )


async def test_code_snapshot_references_session_and_event_provenance(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    event = await add_event(
        db_session,
        graph,
        server_sequence=1,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )

    snapshot = await ObservationRepository(db_session).add_code_snapshot(
        session_id=graph.interview_session.id,
        version_number=1,
        language="cpp",
        source_code="int main() { return 0; }",
        content_hash="sha256:first",
        created_from_event_id=event.id,
    )

    assert snapshot.interview_session_id == graph.interview_session.id
    assert snapshot.created_from_event_id == event.id


async def test_code_snapshot_rejects_created_from_event_from_different_session(
    db_session: AsyncSession,
) -> None:
    session_a = await create_stage1_graph(db_session)
    session_b = await create_stage1_graph(db_session)
    event_from_b = await add_event(
        db_session,
        session_b,
        server_sequence=1,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )

    with pytest.raises(IntegrityError):
        await ObservationRepository(db_session).add_code_snapshot(
            session_id=session_a.interview_session.id,
            version_number=1,
            language="cpp",
            source_code="int main() { return 0; }",
            content_hash="sha256:cross-session",
            created_from_event_id=event_from_b.id,
        )


async def test_successive_code_snapshots_are_unambiguous(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    first_event = await add_event(
        db_session,
        graph,
        server_sequence=1,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )
    second_event = await add_event(
        db_session,
        graph,
        server_sequence=2,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )
    observations = ObservationRepository(db_session)
    first_snapshot = await observations.add_code_snapshot(
        session_id=graph.interview_session.id,
        version_number=1,
        language="cpp",
        source_code="int main() { return 0; }",
        content_hash="sha256:first",
        created_from_event_id=first_event.id,
    )
    second_snapshot = await observations.add_code_snapshot(
        session_id=graph.interview_session.id,
        version_number=2,
        parent_snapshot_id=first_snapshot.id,
        language="cpp",
        source_code="int main() { return 1; }",
        content_hash="sha256:second",
        created_from_event_id=second_event.id,
    )

    assert first_snapshot.version_number == 1
    assert second_snapshot.version_number == 2
    assert second_snapshot.parent_snapshot_id == first_snapshot.id


async def test_code_snapshot_accepts_parent_from_same_session(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    parent_event = await add_event(
        db_session,
        graph,
        server_sequence=1,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )
    child_event = await add_event(
        db_session,
        graph,
        server_sequence=2,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )
    observations = ObservationRepository(db_session)
    parent = await observations.add_code_snapshot(
        session_id=graph.interview_session.id,
        version_number=1,
        language="cpp",
        source_code="int main() { return 0; }",
        content_hash="sha256:parent",
        created_from_event_id=parent_event.id,
    )
    child = await observations.add_code_snapshot(
        session_id=graph.interview_session.id,
        version_number=2,
        parent_snapshot_id=parent.id,
        language="cpp",
        source_code="int main() { return 1; }",
        content_hash="sha256:child",
        created_from_event_id=child_event.id,
    )

    assert child.interview_session_id == graph.interview_session.id
    assert child.parent_snapshot_id == parent.id


async def test_code_snapshot_rejects_parent_from_different_session(
    db_session: AsyncSession,
) -> None:
    session_a = await create_stage1_graph(db_session)
    session_b = await create_stage1_graph(db_session)
    parent_event_from_b = await add_event(
        db_session,
        session_b,
        server_sequence=1,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )
    parent_from_b = await ObservationRepository(db_session).add_code_snapshot(
        session_id=session_b.interview_session.id,
        version_number=1,
        language="cpp",
        source_code="int main() { return 0; }",
        content_hash="sha256:parent-b",
        created_from_event_id=parent_event_from_b.id,
    )
    child_event_from_a = await add_event(
        db_session,
        session_a,
        server_sequence=1,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )

    with pytest.raises(IntegrityError):
        await ObservationRepository(db_session).add_code_snapshot(
            session_id=session_a.interview_session.id,
            version_number=1,
            parent_snapshot_id=parent_from_b.id,
            language="cpp",
            source_code="int main() { return 1; }",
            content_hash="sha256:child-a",
            created_from_event_id=child_event_from_a.id,
        )


async def test_parent_snapshot_delete_nulls_child_parent_without_moving_child(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    parent_event = await add_event(
        db_session,
        graph,
        server_sequence=1,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )
    child_event = await add_event(
        db_session,
        graph,
        server_sequence=2,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )
    observations = ObservationRepository(db_session)
    parent = await observations.add_code_snapshot(
        session_id=graph.interview_session.id,
        version_number=1,
        language="cpp",
        source_code="int main() { return 0; }",
        content_hash="sha256:parent-delete",
        created_from_event_id=parent_event.id,
    )
    child = await observations.add_code_snapshot(
        session_id=graph.interview_session.id,
        version_number=2,
        parent_snapshot_id=parent.id,
        language="cpp",
        source_code="int main() { return 1; }",
        content_hash="sha256:child-delete",
        created_from_event_id=child_event.id,
    )

    await db_session.execute(delete(CodeSnapshot).where(CodeSnapshot.id == parent.id))
    await db_session.flush()
    await db_session.refresh(child)

    assert child.interview_session_id == graph.interview_session.id
    assert child.parent_snapshot_id is None


async def test_event_accepts_code_snapshot_from_same_session(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    snapshot_event = await add_event(
        db_session,
        graph,
        server_sequence=1,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )
    snapshot = await ObservationRepository(db_session).add_code_snapshot(
        session_id=graph.interview_session.id,
        version_number=1,
        language="cpp",
        source_code="int main() { return 0; }",
        content_hash="sha256:same-session",
        created_from_event_id=snapshot_event.id,
    )
    now = datetime.now(UTC)
    event = InterviewEvent(
        interview_session_id=graph.interview_session.id,
        user_id=graph.user.id,
        event_type="MEANINGFUL_CODE_CHANGE",
        source="NATIVE_EDITOR",
        occurred_at=now,
        received_at=now,
        server_sequence=2,
        interview_state_version=0,
        code_snapshot_id=snapshot.id,
        schema_version="interview.event.v1",
    )
    db_session.add(event)

    await db_session.flush()

    assert event.code_snapshot_id == snapshot.id


async def test_event_rejects_code_snapshot_from_different_session(
    db_session: AsyncSession,
) -> None:
    session_a = await create_stage1_graph(db_session)
    session_b = await create_stage1_graph(db_session)
    snapshot_event_from_b = await add_event(
        db_session,
        session_b,
        server_sequence=1,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )
    snapshot_from_b = await ObservationRepository(db_session).add_code_snapshot(
        session_id=session_b.interview_session.id,
        version_number=1,
        language="cpp",
        source_code="int main() { return 0; }",
        content_hash="sha256:session-b",
        created_from_event_id=snapshot_event_from_b.id,
    )
    now = datetime.now(UTC)
    event = InterviewEvent(
        interview_session_id=session_a.interview_session.id,
        user_id=session_a.user.id,
        event_type="MEANINGFUL_CODE_CHANGE",
        source="NATIVE_EDITOR",
        occurred_at=now,
        received_at=now,
        server_sequence=1,
        interview_state_version=0,
        code_snapshot_id=snapshot_from_b.id,
        schema_version="interview.event.v1",
    )
    db_session.add(event)

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_snapshot_delete_nulls_event_snapshot_without_moving_event(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    snapshot_event = await add_event(
        db_session,
        graph,
        server_sequence=1,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )
    snapshot = await ObservationRepository(db_session).add_code_snapshot(
        session_id=graph.interview_session.id,
        version_number=1,
        language="cpp",
        source_code="int main() { return 0; }",
        content_hash="sha256:event-delete",
        created_from_event_id=snapshot_event.id,
    )
    now = datetime.now(UTC)
    referencing_event = InterviewEvent(
        interview_session_id=graph.interview_session.id,
        user_id=graph.user.id,
        event_type="MEANINGFUL_CODE_CHANGE",
        source="NATIVE_EDITOR",
        occurred_at=now,
        received_at=now,
        server_sequence=2,
        interview_state_version=0,
        code_snapshot_id=snapshot.id,
        schema_version="interview.event.v1",
    )
    db_session.add(referencing_event)
    await db_session.flush()

    await db_session.execute(delete(CodeSnapshot).where(CodeSnapshot.id == snapshot.id))
    await db_session.flush()
    await db_session.refresh(referencing_event)

    assert referencing_event.interview_session_id == graph.interview_session.id
    assert referencing_event.user_id == graph.user.id
    assert referencing_event.code_snapshot_id is None


async def test_user_delete_cascades_session_owned_observations(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    transcript_event = await add_event(db_session, graph, server_sequence=1)
    snapshot_event = await add_event(
        db_session,
        graph,
        server_sequence=2,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )
    observations = ObservationRepository(db_session)
    await observations.add_transcript_segment(
        session_id=graph.interview_session.id,
        event_id=transcript_event.id,
        speaker="CANDIDATE",
        sequence=1,
        started_at=datetime.now(UTC),
        text="I think a window works.",
        interview_stage="PROBLEM_UNDERSTANDING",
        interview_state_version=0,
    )
    await observations.add_code_snapshot(
        session_id=graph.interview_session.id,
        version_number=1,
        language="cpp",
        source_code="int main() { return 0; }",
        content_hash="sha256:first",
        created_from_event_id=snapshot_event.id,
    )

    await db_session.execute(delete(User).where(User.id == graph.user.id))
    await db_session.flush()

    assert await count_rows(db_session, InterviewSession, graph.interview_session.id) == 0
    assert await count_rows(db_session, InterviewEvent, transcript_event.id) == 0
    assert await count_rows(db_session, TranscriptSegment) == 0
    assert await count_rows(db_session, CodeSnapshot) == 0


async def test_invalid_constrained_text_values_are_rejected(db_session: AsyncSession) -> None:
    graph = await create_stage1_graph(db_session)
    invalid_configuration = InterviewConfiguration(
        mode="TUTOR",
        level="NEW_GRAD",
        language="cpp",
        configured_duration_seconds=1_800,
        problem_source="CURATED",
    )
    db_session.add(invalid_configuration)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()
    graph = await create_stage1_graph(db_session)
    db_session.add(
        InterviewSession(
            user_id=graph.user.id,
            interview_configuration_id=graph.configuration.id,
            problem_version_id=graph.problem_version.id,
            interview_pack_version_id=graph.pack_version.id,
            current_stage="CLARIFICATION",
            state_version=0,
            status="ACTIVE",
            started_at=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(minutes=30),
        ),
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_invalid_transcript_and_code_provenance_is_rejected(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    observations = ObservationRepository(db_session)

    with pytest.raises(IntegrityError):
        await observations.add_transcript_segment(
            session_id=graph.interview_session.id,
            event_id=uuid7(),
            speaker="CANDIDATE",
            sequence=1,
            started_at=datetime.now(UTC),
            text="This event does not exist.",
            interview_stage="PROBLEM_UNDERSTANDING",
            interview_state_version=0,
        )

    await db_session.rollback()
    graph = await create_stage1_graph(db_session)
    observations = ObservationRepository(db_session)
    event = await add_event(
        db_session,
        graph,
        server_sequence=1,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
    )

    with pytest.raises(IntegrityError):
        await observations.add_code_snapshot(
            session_id=uuid7(),
            version_number=1,
            language="cpp",
            source_code="int main() { return 0; }",
            content_hash="sha256:invalid",
            created_from_event_id=event.id,
        )


async def count_rows(
    db_session: AsyncSession,
    model: (
        type[User]
        | type[InterviewSession]
        | type[InterviewEvent]
        | type[TranscriptSegment]
        | type[CodeSnapshot]
    ),
    row_id: UUID | None = None,
) -> int:
    statement = select(func.count()).select_from(model)
    if row_id is not None:
        statement = statement.where(model.id == row_id)
    count = await db_session.scalar(statement)
    return int(count or 0)
