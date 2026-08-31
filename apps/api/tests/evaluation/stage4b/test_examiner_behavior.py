from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_stage1_7_live_examiner import (
    CODE_STABLE_INVARIANT_BUG,
    FakeExaminerProvider,
    add_code,
    add_transcript,
    code_probe_output,
    decision_metadata,
    dev_context,
    settings,
    transcript_probe_output,
    wait_output,
)
from test_stage1_8_policy_gate import proposed_decision

from app.ai_gateway.models import AIInvocation
from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningEffort,
    ReasoningRequest,
    ReasoningUsage,
)
from app.db.base import Base
from app.examiner.context import (
    RECENT_CLAIM_LIMIT,
    RECENT_DELIVERED_PROMPT_LIMIT,
    RECENT_TRANSCRIPT_LIMIT,
    ExaminerContextBuilder,
)
from app.examiner.coordinator import LiveExaminerCoordinator, LiveExaminerTaskRegistry
from app.examiner.models import CandidateClaim, ExaminerDecision
from app.examiner.policy import (
    CANDIDATE_LEVEL_DEPTH_POLICY,
    LIVE_EXAMINER_POLICY_VERSION,
    PROBE_STRATEGY_POLICY,
)
from app.examiner.repository import ExaminerRepository
from app.execution.models import ExecutionRun
from app.interviews.models import (
    InterviewerPrompt,
    InterviewerPromptDelivery,
    InterviewSession,
    SessionBudget,
)
from app.interviews.prompt_authorization import PromptAuthorizationService
from app.interviews.repository import InterviewRepository
from app.observation.models import CodeSnapshot


class SequencedReasoningProvider:
    provider_name = "stage4b-fake"

    def __init__(
        self,
        outputs: list[dict[str, Any]],
        *,
        after_call: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        self.outputs = outputs
        self.after_call = after_call
        self.calls: list[tuple[ReasoningRequest, str, ReasoningEffort]] = []

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        call_number = len(self.calls) + 1
        self.calls.append((request, model, reasoning_effort))
        output = self.outputs[min(call_number - 1, len(self.outputs) - 1)]
        if self.after_call is not None:
            await self.after_call(call_number)
        return ProviderReasoningResult(
            output_data=output,
            provider=self.provider_name,
            model=model,
            provider_model_version=f"{model}-stage4b",
            provider_request_id=f"stage4b-{call_number}",
            usage=ReasoningUsage(input_tokens=100, cached_input_tokens=0, output_tokens=50),
            latency_ms=2,
            retry_count=0,
            estimated_cost=Decimal("0"),
            currency="USD",
        )


def verification_output(*, final: bool = False) -> dict[str, Any]:
    value = copy.deepcopy(transcript_probe_output())
    value["claims"][0]["normalized_claim"] = (
        "final verified claim" if final else "preliminary unverified claim"
    )
    value["decision"].update(
        decision_metadata(
            verification_required=not final,
            verification_reason=("DIFFICULT_CODE_SEMANTICS" if not final else "NONE"),
        )
    )
    return value


async def _coordinator(
    tmp_path: Path,
    maker: async_sessionmaker[AsyncSession],
    provider: Any,
) -> LiveExaminerCoordinator:
    return LiveExaminerCoordinator(
        settings=settings(tmp_path),
        sessionmaker=maker,
        provider=provider,
        registry=LiveExaminerTaskRegistry(),
    )


async def test_production_context_budget_claim_delivery_bounds_and_watermark(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        session_id = dev.interview_session.id
        first = await add_transcript(maker, session_id, sequence=1)
        first_result = await (
            await _coordinator(tmp_path, maker, FakeExaminerProvider())
        ).analyze_latest(session_id)
        assert first_result.decision is not None

        async with maker() as session:
            async with session.begin():
                budget = await session.get(SessionBudget, session_id)
                assert budget is not None
                budget.max_probes = 3
                budget.probes_used = 1
                gate = await PromptAuthorizationService(session).evaluate_examiner_decision(
                    session_id=session_id,
                    decision_id=first_result.decision.id,
                )
                assert gate.prompt_id is not None

        async with maker() as session:
            authorized_context = await ExaminerContextBuilder(session).build_for_event(
                first.event_id
            )
        diagnostic = authorized_context.context_json["diagnostic_context"]
        assert isinstance(diagnostic, dict)
        assert diagnostic["remaining_probe_budget"] == 1
        assert diagnostic["recent_delivered_prompt_intents"] == []

        async with maker() as session:
            async with session.begin():
                prompt = await session.get(InterviewerPrompt, gate.prompt_id)
                assert prompt is not None
                prompt.status = "DELIVERED"
                session.add(
                    InterviewerPromptDelivery(
                        interview_session_id=session_id,
                        interviewer_prompt_id=prompt.id,
                        delivery_attempt=1,
                        intended_text=prompt.intent,
                        delivery_state="PARTIALLY_DELIVERED",
                        started_at=datetime.now(UTC),
                        interrupted_at=datetime.now(UTC),
                    )
                )
                budget = await session.get(SessionBudget, session_id)
                assert budget is not None
                budget.probes_used = 2

        second = await add_transcript(
            maker,
            session_id,
            transcript="I am now making a newer complexity claim.",
            sequence=2,
        )
        await (
            await _coordinator(tmp_path, maker, FakeExaminerProvider())
        ).analyze_latest(session_id)

        async with maker() as session:
            old_context = await ExaminerContextBuilder(session).build_for_event(first.event_id)
            current_context = await ExaminerContextBuilder(session).build_for_event(second.event_id)
        old_diagnostic = old_context.context_json["diagnostic_context"]
        current_diagnostic = current_context.context_json["diagnostic_context"]
        assert isinstance(old_diagnostic, dict) and isinstance(current_diagnostic, dict)
        assert len(old_diagnostic["recent_claims"]) == 1
        assert len(current_diagnostic["recent_claims"]) == 2
        assert old_diagnostic["recent_delivered_prompt_intents"] == []
        delivered = current_diagnostic["recent_delivered_prompt_intents"]
        assert len(delivered) == 1
        assert delivered[0]["delivery_state"] == "PARTIALLY_DELIVERED"
        assert delivered[0]["strategy"] == "ASSUMPTION_CHALLENGE"
        assert current_diagnostic["remaining_probe_budget"] == 1
        assert len(current_diagnostic["recent_claims"]) <= RECENT_CLAIM_LIMIT
        assert len(delivered) <= RECENT_DELIVERED_PROMPT_LIMIT

        for sequence in range(3, 11):
            await add_transcript(
                maker,
                session_id,
                transcript=f"bounded recent candidate transcript {sequence}",
                sequence=sequence,
            )
        async with maker() as session:
            latest_event = await ExaminerContextBuilder(session).latest_eligible_event_id(
                session_id
            )
            assert latest_event is not None
            compact = await ExaminerContextBuilder(session).build_for_event(latest_event)
        compact_diagnostic = compact.context_json["diagnostic_context"]
        assert isinstance(compact_diagnostic, dict)
        assert len(compact_diagnostic["recent_transcript"]) == RECENT_TRANSCRIPT_LIMIT
        serialized = str(compact.context_json)
        assert "bounded recent candidate transcript 3" not in serialized


async def _add_execution_run(
    maker: async_sessionmaker[AsyncSession],
    *,
    session_id: UUID,
    snapshot_id: UUID,
    status: str,
) -> ExecutionRun:
    async with maker() as session:
        async with session.begin():
            interview = await session.get(InterviewSession, session_id, with_for_update=True)
            snapshot = await session.get(CodeSnapshot, snapshot_id)
            assert interview is not None and snapshot is not None
            interview.last_server_sequence += 1
            now = datetime.now(UTC)
            event = await InterviewRepository(session).add_event(
                session_id=session_id,
                user_id=interview.user_id,
                event_type="RUN_CLICKED",
                source="NATIVE_RUNNER",
                occurred_at=now,
                received_at=now,
                server_sequence=interview.last_server_sequence,
                interview_state_version=interview.state_version,
                schema_version="interview.event.v1",
            )
            run = ExecutionRun(
                interview_session_id=session_id,
                run_event_id=event.id,
                code_snapshot_id=snapshot_id,
                problem_version_id=interview.problem_version_id,
                language="cpp",
                status=status,
                started_at=now,
                completed_at=now,
                execution_provider="fake",
                schema_version="execution.run.v1",
                idempotency_key=f"stage4b-run-{event.server_sequence}",
                stdout="bounded stdout",
                stderr="bounded stderr",
                compiler_output="",
            )
            session.add(run)
            await session.flush()
            return run


async def test_execution_context_is_watermarked_and_marks_old_code_contextual() -> None:
    async with dev_context() as (maker, dev):
        session_id = dev.interview_session.id
        first_code = await add_code(
            maker,
            session_id,
            source="int main() { return 1; }",
            sequence=1,
            key="stage4b-code-1",
        )
        before_run_source = await add_transcript(maker, session_id, sequence=2)
        old_run = await _add_execution_run(
            maker,
            session_id=session_id,
            snapshot_id=first_code.snapshot_id,
            status="RUNTIME_ERROR",
        )
        second_code = await add_code(
            maker,
            session_id,
            source="int main() { return 0; }",
            sequence=3,
            key="stage4b-code-2",
        )

        async with maker() as session:
            before_run = await ExaminerContextBuilder(session).build_for_event(
                before_run_source.event_id
            )
            after_edit = await ExaminerContextBuilder(session).build_for_event(second_code.event_id)
        before_execution = before_run.context_json["diagnostic_context"]
        stale_execution = after_edit.context_json["diagnostic_context"]
        assert isinstance(before_execution, dict) and isinstance(stale_execution, dict)
        assert "execution_context" not in before_execution
        assert stale_execution["execution_context"]["execution_run_id"] == str(old_run.id)
        assert stale_execution["execution_context"]["matches_current_code"] is False
        assert stale_execution["execution_context"]["contextual_only"] is True

        current_run = await _add_execution_run(
            maker,
            session_id=session_id,
            snapshot_id=second_code.snapshot_id,
            status="SUCCEEDED",
        )
        transcript = await add_transcript(maker, session_id, sequence=3)
        async with maker() as session:
            current = await ExaminerContextBuilder(session).build_for_event(transcript.event_id)
        execution = current.context_json["diagnostic_context"]
        assert isinstance(execution, dict)
        assert execution["execution_context"]["execution_run_id"] == str(current_run.id)
        assert execution["execution_context"]["matches_current_code"] is True
        assert execution["execution_context"]["contextual_only"] is False


async def _clone_decision(
    session: AsyncSession,
    decision: ExaminerDecision,
    *,
    strategy: str,
) -> ExaminerDecision:
    return await ExaminerRepository(session).add_examiner_decision(
        interview_session_id=decision.interview_session_id,
        action="PROBE",
        target_claim_id=decision.target_claim_id,
        target_event_id=decision.target_event_id,
        target_code_snapshot_id=decision.target_code_snapshot_id,
        proposed_probe_strategy=strategy,
        technical_rationale="A deterministic Stage 4B duplicate-control fixture.",
        confidence=Decimal("0.95"),
        priority=4,
        urgency=2,
        source_event_watermark=decision.source_event_watermark,
        source_state_version=decision.source_state_version,
        deadline_at=datetime.now(UTC) + timedelta(seconds=60),
        expiry_policy="stage4b-test",
        status="PROPOSED",
        ai_invocation_id=decision.ai_invocation_id,
        ai_policy_version_id=decision.ai_policy_version_id,
    )


async def test_duplicate_guard_uses_delivery_truth_and_allows_distinct_strategy(
    db_session: AsyncSession,
) -> None:
    _graph, first, snapshot = await proposed_decision(
        db_session,
        strategy="ASSUMPTION_CHALLENGE",
    )
    claim = await ExaminerRepository(db_session).add_candidate_claim(
        interview_session_id=first.interview_session_id,
        origin_kind="CODE",
        normalized_claim="lookup is always guaranteed O(1)",
        claim_type="COMPLEXITY",
        extraction_confidence=Decimal("0.95"),
        status="ACCEPTED_AS_INTERPRETATION",
        ai_invocation_id=first.ai_invocation_id,
        ai_policy_version_id=first.ai_policy_version_id,
        source_event_id=first.target_event_id,
        source_code_snapshot_id=snapshot.id,
    )
    first.target_claim_id = claim.id
    first.target_code_snapshot_id = None
    first_gate = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=first.interview_session_id,
        decision_id=first.id,
    )
    assert first_gate.prompt_id is not None

    undelivered = await _clone_decision(
        db_session,
        first,
        strategy="ASSUMPTION_CHALLENGE",
    )
    undelivered_gate = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=first.interview_session_id,
        decision_id=undelivered.id,
    )
    assert undelivered_gate.disposition == "AUTHORIZED"

    prompt = await db_session.get(InterviewerPrompt, first_gate.prompt_id)
    assert prompt is not None
    prompt.status = "DELIVERED"
    db_session.add(
        InterviewerPromptDelivery(
            interview_session_id=first.interview_session_id,
            interviewer_prompt_id=prompt.id,
            delivery_attempt=1,
            intended_text=prompt.intent,
            delivery_state="DELIVERED",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    duplicate = await _clone_decision(
        db_session,
        first,
        strategy="ASSUMPTION_CHALLENGE",
    )
    duplicate_gate = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=first.interview_session_id,
        decision_id=duplicate.id,
    )
    assert duplicate_gate.disposition == "REJECTED"
    assert "same structured target and strategy" in duplicate_gate.reason

    deeper = await _clone_decision(db_session, first, strategy="COMPLEXITY")
    deeper_gate = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=first.interview_session_id,
        decision_id=deeper.id,
    )
    assert deeper_gate.disposition == "AUTHORIZED"


async def test_low_confidence_claim_cannot_authorize_consequential_challenge(
    db_session: AsyncSession,
) -> None:
    _graph, decision, snapshot = await proposed_decision(
        db_session,
        strategy="ASSUMPTION_CHALLENGE",
        confidence=Decimal("0.99"),
    )
    claim = await ExaminerRepository(db_session).add_candidate_claim(
        interview_session_id=decision.interview_session_id,
        origin_kind="CODE",
        normalized_claim="possibly guaranteed constant time",
        claim_type="COMPLEXITY",
        extraction_confidence=Decimal("0.31"),
        status="ACCEPTED_AS_INTERPRETATION",
        ai_invocation_id=decision.ai_invocation_id,
        ai_policy_version_id=decision.ai_policy_version_id,
        source_event_id=decision.target_event_id,
        source_code_snapshot_id=snapshot.id,
    )
    decision.target_claim_id = claim.id
    decision.target_code_snapshot_id = None
    result = await PromptAuthorizationService(db_session).evaluate_examiner_decision(
        session_id=decision.interview_session_id,
        decision_id=decision.id,
    )
    assert result.disposition == "LOW_CONFIDENCE"
    assert "trustworthy claim extraction" in result.reason


async def test_fast_medium_and_single_strong_escalation_preserve_provenance(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        session_id = dev.interview_session.id
        async with maker() as session:
            async with session.begin():
                interview = await session.get(InterviewSession, session_id)
                assert interview is not None
                interview.current_stage = "PROBLEM_UNDERSTANDING"
        await add_transcript(maker, session_id, sequence=1)
        fast = SequencedReasoningProvider([wait_output()])
        result = await (await _coordinator(tmp_path, maker, fast)).analyze_latest(session_id)
        assert result.reasoning_tier == "FAST"
        assert fast.calls[0][2] == "low"

    async with dev_context() as (maker, dev):
        session_id = dev.interview_session.id
        await add_code(
            maker,
            session_id,
            source="class Solution { public: int f() { return 0; } };",
            sequence=1,
            key="stage4b-medium-initial-code",
        )
        await add_code(
            maker,
            session_id,
            source=CODE_STABLE_INVARIANT_BUG,
            sequence=2,
            key="stage4b-medium-code",
        )
        medium = SequencedReasoningProvider([code_probe_output()])
        result = await (await _coordinator(tmp_path, maker, medium)).analyze_latest(session_id)
        assert result.reasoning_tier == "MEDIUM"
        assert medium.calls[0][2] == "medium"

    async with dev_context() as (maker, dev):
        session_id = dev.interview_session.id
        await add_transcript(maker, session_id, sequence=1)
        strong = SequencedReasoningProvider(
            [verification_output(), verification_output(final=True)]
        )
        result = await (await _coordinator(tmp_path, maker, strong)).analyze_latest(session_id)
        assert result.status == "PROPOSED"
        assert result.reasoning_tier == "STRONG"
        assert result.preliminary_ai_invocation_id is not None
        assert len(strong.calls) == 2
        assert strong.calls[0][0].capability == "STANDARD_REASONING"
        assert strong.calls[1][0].capability == "STRONG_REASONING"
        assert strong.calls[1][2] == "high"

        async with maker() as session:
            invocations = list(
                await session.scalars(
                    select(AIInvocation)
                    .where(AIInvocation.interview_session_id == session_id)
                    .order_by(AIInvocation.started_at)
                )
            )
            claims = list(
                await session.scalars(
                    select(CandidateClaim).where(CandidateClaim.interview_session_id == session_id)
                )
            )
            decisions = list(
                await session.scalars(
                    select(ExaminerDecision).where(
                        ExaminerDecision.interview_session_id == session_id
                    )
                )
            )
        assert [item.capability for item in invocations] == [
            "STANDARD_REASONING",
            "STRONG_REASONING",
        ]
        assert all(
            item.ai_policy_version_id == invocations[0].ai_policy_version_id
            for item in invocations
        )
        assert [claim.normalized_claim for claim in claims] == ["final verified claim"]
        assert len(decisions) == 1
        assert decisions[0].ai_invocation_id == invocations[1].id


async def test_strong_budget_exhaustion_and_between_call_staleness_suppress_persistence(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        session_id = dev.interview_session.id
        await add_transcript(maker, session_id, sequence=1)
        async with maker() as session:
            async with session.begin():
                budget = await session.get(SessionBudget, session_id)
                assert budget is not None
                budget.max_strong_reasoning_calls = 0
        provider = SequencedReasoningProvider([verification_output()])
        result = await (await _coordinator(tmp_path, maker, provider)).analyze_latest(session_id)
        assert result.status == "SUPPRESSED"
        assert len(provider.calls) == 1
        async with maker() as session:
            assert await session.scalar(
                select(func.count())
                .select_from(CandidateClaim)
                .where(CandidateClaim.interview_session_id == session_id)
            ) == 0
            assert await session.scalar(
                select(func.count())
                .select_from(ExaminerDecision)
                .where(ExaminerDecision.interview_session_id == session_id)
            ) == 0

    async with dev_context() as (maker, dev):
        session_id = dev.interview_session.id
        await add_transcript(maker, session_id, sequence=1)

        async def make_stale(call_number: int) -> None:
            if call_number == 1:
                await add_transcript(
                    maker,
                    session_id,
                    transcript="Newer candidate self-correction.",
                    sequence=2,
                )

        provider = SequencedReasoningProvider(
            [verification_output(), verification_output(final=True)],
            after_call=make_stale,
        )
        result = await (await _coordinator(tmp_path, maker, provider)).analyze_latest(session_id)
        assert result.status == "STALE"
        assert len(provider.calls) == 1
        async with maker() as session:
            claim_count = await session.scalar(
                select(func.count())
                .select_from(CandidateClaim)
                .where(CandidateClaim.interview_session_id == session_id)
            )
            decision_count = await session.scalar(
                select(func.count())
                .select_from(ExaminerDecision)
                .where(ExaminerDecision.interview_session_id == session_id)
            )
        assert claim_count == 0 and decision_count == 0


async def test_strong_verification_never_escalates_more_than_once(tmp_path: Path) -> None:
    async with dev_context() as (maker, dev):
        session_id = dev.interview_session.id
        await add_transcript(maker, session_id, sequence=1)
        provider = SequencedReasoningProvider(
            [verification_output(), verification_output()]
        )
        result = await (await _coordinator(tmp_path, maker, provider)).analyze_latest(session_id)
        assert result.status == "SUPPRESSED"
        assert result.reasoning_tier == "STRONG"
        assert len(provider.calls) == 2
        async with maker() as session:
            decision_count = await session.scalar(
                select(func.count())
                .select_from(ExaminerDecision)
                .where(ExaminerDecision.interview_session_id == session_id)
            )
        assert decision_count == 0


def test_stage4b_policy_contract_has_all_frozen_strategies_levels_and_no_stage5_tables() -> None:
    assert LIVE_EXAMINER_POLICY_VERSION == "v5"
    assert len(PROBE_STRATEGY_POLICY) == 12
    assert set(CANDIDATE_LEVEL_DEPTH_POLICY) == {"INTERN", "NEW_GRAD", "EARLY_CAREER"}
    assert "evidence" not in Base.metadata.tables
    assert "breakpoints" not in Base.metadata.tables
