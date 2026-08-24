from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
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
) -> tuple[Stage1PersistenceGraph, ExaminerDecision, CodeSnapshot]:
    graph = await create_stage1_graph(db_session)
    graph.interview_session.current_stage = stage
    graph.interview_session.state_version = 0
    graph.interview_session.last_server_sequence = source_sequence
    ai = await create_ai_context(db_session, graph, purpose="LIVE_EXAMINER")
    event, snapshot = await add_snapshot(
        db_session,
        graph,
        server_sequence=source_sequence,
        version_number=1,
        source_code="class Solution { int left = 0; };",
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
        deadline_at=datetime.now(UTC) + timedelta(seconds=60),
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


async def test_delivery_completion_consumes_probe_budget_once(
    db_session: AsyncSession,
) -> None:
    _graph, decision, _snapshot = await proposed_decision(db_session)
    gate = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )
    assert gate.prompt_id is not None
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

    await service.complete_delivery(
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
