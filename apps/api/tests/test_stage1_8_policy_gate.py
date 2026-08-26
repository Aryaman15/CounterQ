from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_stage1_1a_persistence import Stage1PersistenceGraph, create_stage1_graph
from test_stage1_1b_causal_persistence import add_snapshot, create_ai_context

from app.examiner.models import ExaminerDecision
from app.examiner.repository import ExaminerRepository
from app.interviews.models import InterviewerPrompt, InterviewerPromptDelivery, SessionBudget
from app.interviews.prompt_authorization import (
    PromptAuthorizationService,
    PromptGateRuntimeState,
)
from app.observation.models import CodeSnapshot
from app.realtime.control_protocol import (
    CounterQDeliveryCompletedMessage,
    CounterQDeliveryInterruptedMessage,
    CounterQDeliveryStartedMessage,
)
from app.realtime.control_service import RealtimeControlService

pytestmark = pytest.mark.asyncio


async def proposed_decision(
    db_session: AsyncSession,
    *,
    action: str = "PROBE",
    strategy: str | None = "PROVE",
    confidence: Decimal = Decimal("0.91"),
    source_sequence: int = 1,
    stage: str = "IMPLEMENTATION",
    decision_deadline_at: datetime | None = None,
    session_deadline_at: datetime | None = None,
    now: datetime | None = None,
) -> tuple[Stage1PersistenceGraph, ExaminerDecision, CodeSnapshot]:
    graph = await create_stage1_graph(db_session, now=now)
    graph.interview_session.current_stage = stage
    graph.interview_session.state_version = 0
    graph.interview_session.last_server_sequence = source_sequence
    if session_deadline_at is not None:
        graph.interview_session.deadline_at = session_deadline_at
    ai = await create_ai_context(db_session, graph, purpose="LIVE_EXAMINER", now=now)
    event, snapshot = await add_snapshot(
        db_session,
        graph,
        server_sequence=source_sequence,
        version_number=1,
        source_code="class Solution { int left = 0; };",
        now=now,
    )
    decision = await ExaminerRepository(db_session).add_examiner_decision(
        interview_session_id=graph.interview_session.id,
        action=action,
        target_event_id=event.id,
        target_code_snapshot_id=snapshot.id if action == "PROBE" else None,
        proposed_probe_strategy=strategy,
        technical_rationale="The implementation needs an invariant check.",
        confidence=confidence,
        priority=4,
        urgency=3,
        source_event_watermark=event.server_sequence,
        source_state_version=graph.interview_session.state_version,
        deadline_at=decision_deadline_at or (now or datetime.now(UTC)) + timedelta(seconds=60),
        expiry_policy="stage1_live_examiner_short_lived",
        status="PROPOSED",
        ai_invocation_id=ai.invocation.id,
        ai_policy_version_id=ai.policy.id,
    )
    return graph, decision, snapshot


def client_base(sequence: int = 1) -> dict[str, object]:
    return {
        "client_event_id": f"client-{sequence}",
        "client_instance_id": "stage1-8-test-client",
        "client_sequence": sequence,
    }


async def test_probe_decision_authorizes_prompt_without_consuming_budget(
    db_session: AsyncSession,
) -> None:
    _graph, decision, _snapshot = await proposed_decision(db_session)

    result = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )

    prompt = await db_session.get(InterviewerPrompt, result.prompt_id)
    budget = await db_session.get(SessionBudget, decision.interview_session_id)
    assert result.disposition == "AUTHORIZED"
    assert prompt is not None
    assert prompt.origin == "EXAMINER_DECISION"
    assert prompt.kind == "PROBE"
    assert prompt.probe_strategy == "PROVE"
    assert prompt.status == "AUTHORIZED"
    assert result.candidate_safe_text == prompt.intent
    assert budget is not None
    assert budget.probes_used == 0


async def test_assumption_challenge_uses_concise_candidate_safe_claim_wording(
    db_session: AsyncSession,
) -> None:
    graph, decision, snapshot = await proposed_decision(
        db_session,
        strategy="ASSUMPTION_CHALLENGE",
    )
    claim = await ExaminerRepository(db_session).add_candidate_claim(
        interview_session_id=decision.interview_session_id,
        origin_kind="CODE",
        normalized_claim="lookup is always guaranteed O(1).",
        claim_type="COMPLEXITY",
        extraction_confidence=Decimal("0.91"),
        status="ACCEPTED_AS_INTERPRETATION",
        ai_invocation_id=decision.ai_invocation_id,
        ai_policy_version_id=decision.ai_policy_version_id,
        source_event_id=decision.target_event_id,
        source_code_snapshot_id=snapshot.id,
    )
    decision.target_claim_id = claim.id
    gate = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )

    assert gate.candidate_safe_text == (
        "You said lookup is always guaranteed O(1). Is that actually guaranteed?"
    )


async def test_wait_decision_accepts_silence_without_prompt(
    db_session: AsyncSession,
) -> None:
    _graph, decision, _snapshot = await proposed_decision(
        db_session,
        action="WAIT",
        strategy=None,
        confidence=Decimal("0.95"),
    )

    result = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )

    prompt_count = await db_session.scalar(
        select(InterviewerPrompt).where(InterviewerPrompt.examiner_decision_id == decision.id),
    )
    assert result.disposition == "AUTHORIZED"
    assert result.prompt_id is None
    assert prompt_count is None


@pytest.mark.parametrize("action", ["WAIT", "OBSERVE"])
async def test_silent_examiner_actions_authorize_without_prompt_delivery_or_probe_budget(
    db_session: AsyncSession,
    action: str,
) -> None:
    _graph, decision, _snapshot = await proposed_decision(
        db_session,
        action=action,
        strategy=None,
        confidence=Decimal("0.95"),
    )
    result = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )
    prompt_count = await db_session.scalar(
        select(func.count())
        .select_from(InterviewerPrompt)
        .where(InterviewerPrompt.examiner_decision_id == decision.id)
    )
    delivery_count = await db_session.scalar(
        select(func.count())
        .select_from(InterviewerPromptDelivery)
        .where(InterviewerPromptDelivery.interview_session_id == decision.interview_session_id)
    )
    budget = await db_session.get(SessionBudget, decision.interview_session_id)

    assert result.disposition == "AUTHORIZED"
    assert result.prompt_id is None
    assert prompt_count == 0
    assert delivery_count == 0
    assert budget is not None
    assert budget.probes_used == 0


async def test_candidate_speaking_or_active_editing_defers_without_persisting_outcome(
    db_session: AsyncSession,
) -> None:
    _graph, decision, _snapshot = await proposed_decision(db_session)

    result = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
        runtime_state=PromptGateRuntimeState(candidate_code_active=True),
    )

    refreshed = await db_session.get(ExaminerDecision, decision.id)
    assert result.disposition == "DEFERRED"
    assert refreshed is not None
    assert refreshed.status == "PROPOSED"
    assert refreshed.policy_gate_outcome is None


async def test_newer_code_snapshot_marks_decision_stale(
    db_session: AsyncSession,
) -> None:
    graph, decision, first_snapshot = await proposed_decision(db_session, source_sequence=1)

    # Add a real newer code snapshot in the same session through the accepted test repository path.
    await add_snapshot(
        db_session,
        graph,
        server_sequence=2,
        version_number=2,
        parent_snapshot_id=first_snapshot.id,
        source_code="class Solution { int left = 1; };",
    )

    result = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )

    assert result.disposition == "STALE"
    assert result.policy_gate_outcome == "STALE"
    refreshed = await db_session.get(ExaminerDecision, decision.id)
    assert refreshed is not None
    assert refreshed.status == "STALE"


async def test_authorized_prompt_delivery_window_survives_original_decision_deadline(
    db_session: AsyncSession,
) -> None:
    t0 = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    _graph, decision, _snapshot = await proposed_decision(
        db_session,
        decision_deadline_at=t0 + timedelta(seconds=8),
        session_deadline_at=t0 + timedelta(minutes=30),
        now=t0,
    )
    current_time = t0 + timedelta(seconds=6)
    service = PromptAuthorizationService(
        db_session,
        clock=lambda: current_time,
        authorized_prompt_delivery_window_seconds=12,
    )

    gate = await service.evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )
    assert gate.disposition == "AUTHORIZED"
    assert gate.prompt_id is not None

    current_time = t0 + timedelta(seconds=9)
    permit = await service.permit_delivery(
        session_id=decision.interview_session_id,
        prompt_id=gate.prompt_id,
    )

    assert permit.status == "PERMITTED"
    assert permit.text == gate.candidate_safe_text


async def test_decision_expired_before_policy_gate_creates_no_prompt(
    db_session: AsyncSession,
) -> None:
    t0 = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    _graph, decision, _snapshot = await proposed_decision(
        db_session,
        decision_deadline_at=t0 + timedelta(seconds=8),
        session_deadline_at=t0 + timedelta(minutes=30),
        now=t0,
    )

    result = await PromptAuthorizationService(
        db_session,
        clock=lambda: t0 + timedelta(seconds=9),
    ).evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )

    assert result.disposition == "EXPIRED"
    prompt = await db_session.scalar(
        select(InterviewerPrompt).where(InterviewerPrompt.examiner_decision_id == decision.id),
    )
    assert prompt is None


async def test_authorized_prompt_delivery_window_expiry_expires_prompt(
    db_session: AsyncSession,
) -> None:
    t0 = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    _graph, decision, _snapshot = await proposed_decision(
        db_session,
        decision_deadline_at=t0 + timedelta(seconds=30),
        session_deadline_at=t0 + timedelta(minutes=30),
        now=t0,
    )
    current_time = t0 + timedelta(seconds=6)
    service = PromptAuthorizationService(
        db_session,
        clock=lambda: current_time,
        authorized_prompt_delivery_window_seconds=12,
    )
    gate = await service.evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )
    assert gate.prompt_id is not None

    current_time = t0 + timedelta(seconds=19)
    permit = await service.permit_delivery(
        session_id=decision.interview_session_id,
        prompt_id=gate.prompt_id,
    )
    prompt = await db_session.get(InterviewerPrompt, gate.prompt_id)

    assert permit.status == "EXPIRED"
    assert permit.reason == "Authorized prompt delivery window expired."
    assert prompt is not None
    assert prompt.status == "EXPIRED"


async def test_authorized_prompt_stales_if_target_code_changes_before_delivery(
    db_session: AsyncSession,
) -> None:
    graph, decision, first_snapshot = await proposed_decision(db_session, source_sequence=1)
    gate = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )
    assert gate.prompt_id is not None
    await add_snapshot(
        db_session,
        graph,
        server_sequence=2,
        version_number=2,
        parent_snapshot_id=first_snapshot.id,
        source_code="class Solution { int left = 2; };",
    )

    permit = await PromptAuthorizationService(db_session).permit_delivery(
        session_id=decision.interview_session_id,
        prompt_id=gate.prompt_id,
    )

    assert permit.status == "STALE"
    assert permit.reason == "Target code changed after prompt authorization."
    prompt = await db_session.get(InterviewerPrompt, gate.prompt_id)
    refreshed_decision = await db_session.get(ExaminerDecision, decision.id)
    assert prompt is not None
    assert prompt.status == "STALE"
    assert refreshed_decision is not None
    assert refreshed_decision.status == "AUTHORIZED"


async def test_standalone_policy_gate_reports_deferred_then_stale_for_old_code_decision(
    db_session: AsyncSession,
) -> None:
    graph, decision, first_snapshot = await proposed_decision(db_session, source_sequence=1)
    await add_snapshot(
        db_session,
        graph,
        server_sequence=2,
        version_number=2,
        parent_snapshot_id=first_snapshot.id,
        source_code="class Solution { int left = 2; };",
    )
    service = PromptAuthorizationService(db_session)

    deferred = await service.evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
        runtime_state=PromptGateRuntimeState(candidate_code_active=True),
    )
    stale = await service.evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )
    prompt_count = await db_session.scalar(
        select(func.count())
        .select_from(InterviewerPrompt)
        .where(InterviewerPrompt.examiner_decision_id == decision.id)
    )
    budget = await db_session.get(SessionBudget, decision.interview_session_id)

    assert deferred.disposition == "DEFERRED"
    assert deferred.reason == "Candidate is actively editing."
    assert stale.disposition == "STALE"
    assert stale.policy_gate_outcome == "STALE"
    assert prompt_count == 0
    assert budget is not None
    assert budget.probes_used == 0


async def test_authorized_prompt_deferred_by_candidate_floor_or_active_editing(
    db_session: AsyncSession,
) -> None:
    _graph, decision, _snapshot = await proposed_decision(db_session)
    service = PromptAuthorizationService(db_session)
    gate = await service.evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )
    assert gate.prompt_id is not None

    speaking = await service.permit_delivery(
        session_id=decision.interview_session_id,
        prompt_id=gate.prompt_id,
        runtime_state=PromptGateRuntimeState(candidate_speaking=True),
    )
    editing = await service.permit_delivery(
        session_id=decision.interview_session_id,
        prompt_id=gate.prompt_id,
        runtime_state=PromptGateRuntimeState(candidate_code_active=True),
    )

    prompt = await db_session.get(InterviewerPrompt, gate.prompt_id)
    assert speaking.status == "DEFERRED"
    assert speaking.reason == "Candidate is speaking."
    assert editing.status == "DEFERRED"
    assert editing.reason == "Candidate is actively editing."
    assert prompt is not None
    assert prompt.status == "AUTHORIZED"


async def test_delivery_completion_consumes_probe_budget_once(
    db_session: AsyncSession,
) -> None:
    _graph, decision, _snapshot = await proposed_decision(db_session)
    gate = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )
    assert gate.prompt_id is not None

    # Mirror the WebSocket's fresh durable-message session.  Without the
    # selectinload in _prompt_for_session, start_delivery would lazily load the
    # decision here and AsyncSession would raise MissingGreenlet.
    db_session.expunge_all()

    service = RealtimeControlService(db_session)
    start = await service.start_delivery(
        session_id=decision.interview_session_id,
        message=CounterQDeliveryStartedMessage(
            **client_base(1),
            type="counterq_delivery_started",
            interviewer_prompt_id=gate.prompt_id,
            intended_text="browser tried to alter this text",
            provider_response_id="response-policy-gate-1",
        ),
    )
    delivery = await db_session.get(InterviewerPromptDelivery, start.delivery_id)
    assert delivery is not None
    assert delivery.intended_text == gate.candidate_safe_text

    completed = await service.complete_delivery(
        session_id=decision.interview_session_id,
        message=CounterQDeliveryCompletedMessage(
            **client_base(2),
            type="counterq_delivery_completed",
            interviewer_prompt_id=gate.prompt_id,
            prompt_delivery_id=start.delivery_id,
            provider_response_id="response-policy-gate-1",
            transcript=gate.candidate_safe_text or "What invariant holds?",
            idempotency_key=f"counterq-delivered:{start.delivery_id}:response-policy-gate-1",
        ),
    )
    assert completed.delivery_state == "DELIVERED"
    assert completed.transcript_segment_id is not None
    delivery = await db_session.get(InterviewerPromptDelivery, start.delivery_id)
    assert delivery is not None
    assert delivery.delivery_state == "DELIVERED"
    assert delivery.actual_transcript_segment_id == completed.transcript_segment_id
    budget = await db_session.get(SessionBudget, decision.interview_session_id)
    assert budget is not None
    assert budget.probes_used == 1

    await service.complete_delivery(
        session_id=decision.interview_session_id,
        message=CounterQDeliveryCompletedMessage(
            **client_base(3),
            type="counterq_delivery_completed",
            interviewer_prompt_id=gate.prompt_id,
            prompt_delivery_id=start.delivery_id,
            provider_response_id="response-policy-gate-1",
            transcript=gate.candidate_safe_text or "What invariant holds?",
            idempotency_key=f"counterq-delivered:{start.delivery_id}:response-policy-gate-1",
        ),
    )
    assert budget.probes_used == 1


async def test_probe_barge_in_interrupts_without_actual_transcript_or_budget_consumption(
    db_session: AsyncSession,
) -> None:
    _graph, decision, _snapshot = await proposed_decision(db_session)
    gate = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )
    assert gate.prompt_id is not None
    service = RealtimeControlService(db_session)
    started = await service.start_delivery(
        session_id=decision.interview_session_id,
        message=CounterQDeliveryStartedMessage(
            **client_base(10),
            type="counterq_delivery_started",
            interviewer_prompt_id=gate.prompt_id,
            intended_text="untrusted browser wording",
            provider_response_id="response-interrupted-probe",
        ),
    )
    interrupted = await service.interrupt_delivery(
        session_id=decision.interview_session_id,
        message=CounterQDeliveryInterruptedMessage(
            **client_base(11),
            type="counterq_delivery_interrupted",
            interviewer_prompt_id=gate.prompt_id,
            prompt_delivery_id=started.delivery_id,
            provider_response_id="response-interrupted-probe",
            confirmed_by="candidate_speech",
        ),
    )
    retry = await service.interrupt_delivery(
        session_id=decision.interview_session_id,
        message=CounterQDeliveryInterruptedMessage(
            **client_base(12),
            type="counterq_delivery_interrupted",
            interviewer_prompt_id=gate.prompt_id,
            prompt_delivery_id=started.delivery_id,
            provider_response_id="response-interrupted-probe",
            confirmed_by="candidate_speech",
        ),
    )
    delivery = await db_session.get(InterviewerPromptDelivery, started.delivery_id)
    budget = await db_session.get(SessionBudget, decision.interview_session_id)

    assert interrupted.delivery_state == "INTERRUPTED"
    assert retry.created is False
    assert retry.event_id == interrupted.event_id
    assert delivery is not None
    assert delivery.delivery_state == "INTERRUPTED"
    assert delivery.actual_transcript_segment_id is None
    assert service.floor.state == "CANDIDATE_SPEAKING"
    assert budget is not None
    assert budget.probes_used == 0
