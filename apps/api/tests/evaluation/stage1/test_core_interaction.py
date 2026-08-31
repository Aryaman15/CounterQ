"""Deterministic acceptance scenarios for the Stage 1 Core Interaction Spike.

These scenarios deliberately exercise the production-shaped persistence,
observation, Examiner, authorization, and delivery services.  Only external
reasoning is faked, so the suite never consumes provider credit.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_gateway.gateway import StructuredOutputValidationFailure
from app.ai_gateway.models import AIInvocation
from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningEffort,
    ReasoningRequest,
    ReasoningUsage,
)
from app.config.settings import Settings, create_settings
from app.examiner.coordinator import (
    LiveExaminerCoordinator,
    LiveExaminerDebugResult,
    LiveExaminerTaskRegistry,
)
from app.examiner.models import CandidateClaim, ExaminerDecision
from app.interviews.dev_factory import DevelopmentInterview, create_development_interview
from app.interviews.models import InterviewerPrompt, InterviewerPromptDelivery, SessionBudget
from app.interviews.prompt_authorization import PromptAuthorizationService, PromptGateResult
from app.observation.models import TranscriptSegment
from app.realtime.control_protocol import (
    CandidateCodeSnapshotMessage,
    CandidateTranscriptFinalizedMessage,
    CounterQDeliveryCompletedMessage,
    CounterQDeliveryInterruptedMessage,
    CounterQDeliveryStartedMessage,
)
from app.realtime.control_service import (
    CodeSnapshotPersistenceResult,
    DeliveryPersistenceResult,
    RealtimeControlService,
    TranscriptPersistenceResult,
)

CODE_INITIAL = "class Solution { public: int lengthOfLongestSubstring(string s) { return 0; } };"
CODE_INVARIANT_BUG = """
class Solution {
public:
    int lengthOfLongestSubstring(string s) {
        unordered_map<char, int> last;
        int left = 0;
        int ans = 0;
        for (int right = 0; right < s.size(); right++) {
            if (last.count(s[right])) {
                left = last[s[right]] + 1;
            }
            last[s[right]] = right;
            ans = max(ans, right - left + 1);
        }
        return ans;
    }
};
""".strip()
CODE_CORRECTED = CODE_INVARIANT_BUG.replace(
    "left = last[s[right]] + 1;", "left = max(left, last[s[right]] + 1);"
)
CODE_INCOMPLETE = """
class Solution {
public:
    int lengthOfLongestSubstring(string s) {
        int left = 0;
        unordered_map<char, int> last;
    }
};
""".strip()

pytestmark = pytest.mark.asyncio


class FakeReasoningProvider:
    provider_name = "stage1-evaluation-fake"

    def __init__(self, output_data: dict[str, Any], *, pause: bool = False) -> None:
        self.output_data = output_data
        self.pause = pause
        self.calls = 0
        self.called = asyncio.Event()
        self.release = asyncio.Event()

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        del request, reasoning_effort
        self.calls += 1
        self.called.set()
        if self.pause:
            await self.release.wait()
        return ProviderReasoningResult(
            output_data=self.output_data,
            provider=self.provider_name,
            model=model,
            provider_model_version="stage1-fixture",
            provider_request_id=f"stage1-eval-{self.calls}",
            usage=ReasoningUsage(input_tokens=100, cached_input_tokens=0, output_tokens=40),
            latency_ms=1,
            retry_count=0,
            estimated_cost=Decimal("0"),
            currency="USD",
        )


def decision_metadata() -> dict[str, object]:
    return {
        "target_ranking": {
            "technical_importance": "HIGH",
            "interpretation_confidence": "HIGH",
            "diagnostic_value": "HIGH",
            "current_evidence_gap": "HIGH",
            "candidate_commitment": "MEDIUM",
            "context_relevance": "HIGH",
            "freshness": "HIGH",
            "self_correction_likelihood": "LOW",
            "interruption_cost": "LOW",
            "duplicate_evidence": "LOW",
            "time_pressure": "LOW",
            "probe_fatigue": "LOW",
            "staleness_risk": "LOW",
        },
        "verification": {"required": False, "reason": "NONE"},
    }


def evaluation_settings(tmp_path: Path) -> Settings:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "COUNTERQ_APP_ENV=local\n"
        "COUNTERQ_LIVE_EXAMINER_AUTOSTART=false\n"
        "COUNTERQ_LIVE_EXAMINER_USEFULNESS_SECONDS=8\n"
    )
    return create_settings(env_file=env_file)


@asynccontextmanager
async def evaluation_context(db_session: AsyncSession) -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], DevelopmentInterview]
]:
    # Share pytest's outer transaction so this acceptance suite cannot leak
    # committed fixture data into the rest of the repository test run.
    maker = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    async with maker() as session:
        async with session.begin():
            development_interview = await create_development_interview(
                session,
                initial_stage="IMPLEMENTATION",
            )
    yield maker, development_interview


def client_fields(sequence: int) -> dict[str, object]:
    return {
        "client_event_id": f"stage1-evaluation-client-event-{sequence}",
        "client_instance_id": "stage1-evaluation-client",
        "client_sequence": sequence,
    }


async def persist_transcript(
    maker: async_sessionmaker[AsyncSession],
    session_id: UUID,
    *,
    sequence: int,
    text: str,
) -> TranscriptPersistenceResult:
    async with maker() as session:
        async with session.begin():
            return await RealtimeControlService(session).persist_candidate_transcript(
                session_id=session_id,
                message=CandidateTranscriptFinalizedMessage(
                    **client_fields(sequence),
                    type="candidate_transcript_finalized",
                    provider_item_id=f"candidate-transcript-{sequence}",
                    transcript=text,
                    ended_at=datetime.now(UTC),
                ),
            )


async def persist_code(
    maker: async_sessionmaker[AsyncSession],
    session_id: UUID,
    *,
    sequence: int,
    source: str,
) -> CodeSnapshotPersistenceResult:
    async with maker() as session:
        async with session.begin():
            return await RealtimeControlService(session).persist_candidate_code_snapshot(
                session_id=session_id,
                message=CandidateCodeSnapshotMessage(
                    **client_fields(sequence),
                    type="candidate_code_snapshot",
                    source_code=source,
                    language="cpp",
                    trigger="EDIT_BURST",
                    idempotency_key=f"stage1-evaluation-code-{sequence}",
                ),
            )


def speech_probe_output() -> dict[str, Any]:
    return {
        "claims": [
            {
                "normalized_claim": "unordered_map lookup is always guaranteed O(1)",
                "claim_type": "COMPLEXITY",
                "verbatim_excerpt": "unordered_map lookup is always guaranteed O(1)",
                "confidence": 0.92,
            }
        ],
        "decision": {
            "action": "PROBE",
            "target_kind": "CLAIM",
            "target_claim_index": 0,
            "proposed_probe_strategy": "ASSUMPTION_CHALLENGE",
            "technical_rationale": "The guarantee needs diagnostic scrutiny.",
            "confidence": 0.9,
            "priority": 4,
            "urgency": 3,
            **decision_metadata(),
        },
    }


def code_probe_output() -> dict[str, Any]:
    return {
        "claims": [],
        "decision": {
            "action": "PROBE",
            "target_kind": "CODE_SNAPSHOT",
            "target_claim_index": None,
            "proposed_probe_strategy": "PROVE",
            "technical_rationale": "The left-boundary invariant needs defense.",
            "confidence": 0.9,
            "priority": 4,
            "urgency": 3,
            **decision_metadata(),
        },
    }


def observe_output() -> dict[str, Any]:
    return {
        "claims": [],
        "decision": {
            "action": "OBSERVE",
            "target_kind": "CODE_SNAPSHOT",
            "target_claim_index": None,
            "proposed_probe_strategy": None,
            "technical_rationale": "The implementation is incomplete.",
            "confidence": 0.8,
            "priority": 1,
            "urgency": 0,
            **decision_metadata(),
        },
    }


async def analyze(
    maker: async_sessionmaker[AsyncSession],
    session_id: UUID,
    provider: FakeReasoningProvider,
    local_settings: Settings,
) -> LiveExaminerDebugResult:
    return await LiveExaminerCoordinator(
        settings=local_settings,
        sessionmaker=maker,
        provider=provider,
        registry=LiveExaminerTaskRegistry(),
    ).analyze_latest(session_id)


async def authorize(
    maker: async_sessionmaker[AsyncSession], session_id: UUID, decision_id: UUID
) -> PromptGateResult:
    async with maker() as session:
        async with session.begin():
            return await PromptAuthorizationService(session).evaluate_examiner_decision(
                session_id=session_id,
                decision_id=decision_id,
            )


async def deliver(
    maker: async_sessionmaker[AsyncSession],
    session_id: UUID,
    prompt_id: UUID,
    *,
    sequence: int,
    actual_text: str,
) -> DeliveryPersistenceResult:
    async with maker() as session:
        async with session.begin():
            service = RealtimeControlService(session)
            started = await service.start_delivery(
                session_id=session_id,
                message=CounterQDeliveryStartedMessage(
                    **client_fields(sequence),
                    type="counterq_delivery_started",
                    interviewer_prompt_id=prompt_id,
                    intended_text="browser-controlled text is not authoritative",
                    provider_response_id=f"stage1-evaluation-response-{sequence}",
                ),
            )
            return await service.complete_delivery(
                session_id=session_id,
                message=CounterQDeliveryCompletedMessage(
                    **client_fields(sequence + 1),
                    type="counterq_delivery_completed",
                    interviewer_prompt_id=prompt_id,
                    prompt_delivery_id=started.delivery_id,
                    provider_response_id=f"stage1-evaluation-response-{sequence}",
                    provider_item_id=f"stage1-evaluation-item-{sequence}",
                    transcript=actual_text,
                ),
            )


async def test_speech_misconception_reaches_candidate_safe_delivered_truth(
    tmp_path: Path,
    db_session: AsyncSession,
) -> None:
    async with evaluation_context(db_session) as (maker, development_interview):
        candidate = await persist_transcript(
            maker,
            development_interview.interview_session.id,
            sequence=1,
            text="In C++, unordered_map lookup is always guaranteed O(1), including worst case.",
        )
        result = await analyze(
            maker,
            development_interview.interview_session.id,
            FakeReasoningProvider(speech_probe_output()),
            evaluation_settings(tmp_path),
        )
        assert result.decision is not None
        gate = await authorize(
            maker, development_interview.interview_session.id, result.decision.id
        )
        assert gate.prompt_id is not None
        actual_text = (
            "You said unordered_map lookup is always guaranteed O(1). Is that actually guaranteed?"
        )
        delivered = await deliver(
            maker,
            development_interview.interview_session.id,
            gate.prompt_id,
            sequence=2,
            actual_text=actual_text,
        )

        async with maker() as session:
            claim = await session.get(CandidateClaim, result.claims[0].id)
            decision = await session.get(ExaminerDecision, result.decision.id)
            prompt = await session.get(InterviewerPrompt, gate.prompt_id)
            delivery = await session.get(InterviewerPromptDelivery, delivered.delivery_id)
            transcript = await session.get(TranscriptSegment, delivered.transcript_segment_id)
            budget = await session.get(SessionBudget, development_interview.interview_session.id)

        assert claim is not None and claim.source_event_id == candidate.event_id
        assert claim.source_transcript_segment_id == candidate.transcript_segment_id
        assert decision is not None and decision.source_event_watermark == candidate.server_sequence
        assert decision.proposed_probe_strategy == "ASSUMPTION_CHALLENGE"
        assert prompt is not None and prompt.intent == actual_text
        assert "guarantee needs diagnostic scrutiny" not in prompt.intent
        assert transcript is not None
        assert delivery is not None and delivery.actual_transcript_segment_id == transcript.id
        assert transcript.text == actual_text
        assert budget is not None and budget.probes_used == 1


async def test_code_invariant_reaches_delivered_probe_with_exact_snapshot_provenance(
    tmp_path: Path,
    db_session: AsyncSession,
) -> None:
    async with evaluation_context(db_session) as (maker, development_interview):
        await persist_code(
            maker, development_interview.interview_session.id, sequence=1, source=CODE_INITIAL
        )
        code = await persist_code(
            maker,
            development_interview.interview_session.id,
            sequence=2,
            source=CODE_INVARIANT_BUG,
        )
        result = await analyze(
            maker,
            development_interview.interview_session.id,
            FakeReasoningProvider(code_probe_output()),
            evaluation_settings(tmp_path),
        )
        assert result.decision is not None
        gate = await authorize(
            maker, development_interview.interview_session.id, result.decision.id
        )
        assert gate.prompt_id is not None
        delivered = await deliver(
            maker,
            development_interview.interview_session.id,
            gate.prompt_id,
            sequence=3,
            actual_text="What invariant are you relying on here, and what guarantees it holds?",
        )

        async with maker() as session:
            decision = await session.get(ExaminerDecision, result.decision.id)
            delivery = await session.get(InterviewerPromptDelivery, delivered.delivery_id)
            budget = await session.get(SessionBudget, development_interview.interview_session.id)

        assert decision is not None and decision.target_code_snapshot_id == code.snapshot_id
        assert decision.source_event_watermark == code.server_sequence
        assert decision.proposed_probe_strategy == "PROVE"
        assert delivery is not None and delivery.delivery_state == "DELIVERED"
        assert "max(left" not in delivery.intended_text
        assert budget is not None and budget.probes_used == 1


async def test_self_correction_supersedes_old_code_decision_without_delivery_or_budget(
    tmp_path: Path,
    db_session: AsyncSession,
) -> None:
    async with evaluation_context(db_session) as (maker, development_interview):
        await persist_code(
            maker, development_interview.interview_session.id, sequence=1, source=CODE_INITIAL
        )
        old_code = await persist_code(
            maker,
            development_interview.interview_session.id,
            sequence=2,
            source=CODE_INVARIANT_BUG,
        )
        old_result = await analyze(
            maker,
            development_interview.interview_session.id,
            FakeReasoningProvider(code_probe_output()),
            evaluation_settings(tmp_path),
        )
        assert old_result.decision is not None
        corrected = await persist_code(
            maker,
            development_interview.interview_session.id,
            sequence=3,
            source=CODE_CORRECTED,
        )
        stale_gate = await authorize(
            maker, development_interview.interview_session.id, old_result.decision.id
        )
        fresh_result = await analyze(
            maker,
            development_interview.interview_session.id,
            FakeReasoningProvider(observe_output()),
            evaluation_settings(tmp_path),
        )

        async with maker() as session:
            old_decision = await session.get(ExaminerDecision, old_result.decision.id)
            prompts = await session.scalar(
                select(func.count())
                .select_from(InterviewerPrompt)
                .where(
                    InterviewerPrompt.interview_session_id
                    == development_interview.interview_session.id
                )
            )
            deliveries = await session.scalar(
                select(func.count())
                .select_from(InterviewerPromptDelivery)
                .where(
                    InterviewerPromptDelivery.interview_session_id
                    == development_interview.interview_session.id
                )
            )
            budget = await session.get(SessionBudget, development_interview.interview_session.id)

        assert stale_gate.disposition == "STALE"
        assert old_decision is not None and old_decision.status == "STALE"
        assert prompts == 0 and deliveries == 0
        assert budget is not None and budget.probes_used == 0
        assert fresh_result.source_event_id == corrected.event_id
        assert old_code.snapshot_id != corrected.snapshot_id


async def test_in_flight_stale_reasoning_never_reaches_candidate(
    tmp_path: Path,
    db_session: AsyncSession,
) -> None:
    async with evaluation_context(db_session) as (maker, development_interview):
        await persist_code(
            maker, development_interview.interview_session.id, sequence=1, source=CODE_INITIAL
        )
        source = await persist_code(
            maker,
            development_interview.interview_session.id,
            sequence=2,
            source=CODE_INVARIANT_BUG,
        )
        provider = FakeReasoningProvider(code_probe_output(), pause=True)
        coordinator = LiveExaminerCoordinator(
            settings=evaluation_settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )
        assert source.event_id is not None
        task = coordinator.submit(
            interview_session_id=development_interview.interview_session.id,
            source_event_id=source.event_id,
        )
        await asyncio.wait_for(provider.called.wait(), timeout=1)
        latest = await persist_code(
            maker,
            development_interview.interview_session.id,
            sequence=3,
            source=CODE_CORRECTED,
        )
        provider.release.set()
        result = await task

        async with maker() as session:
            assert result.decision is not None
            decision = await session.get(ExaminerDecision, result.decision.id)
            prompt_count = await session.scalar(
                select(func.count())
                .select_from(InterviewerPrompt)
                .where(
                    InterviewerPrompt.interview_session_id
                    == development_interview.interview_session.id
                )
            )
            budget = await session.get(SessionBudget, development_interview.interview_session.id)

        assert result.status == "STALE"
        assert decision is not None and decision.status == "STALE"
        assert decision.target_event_id == source.event_id
        assert latest.version_number > source.version_number
        assert prompt_count == 0
        assert budget is not None and budget.probes_used == 0


async def test_barge_in_interrupts_delivery_without_unheard_candidate_visible_text(
    tmp_path: Path,
    db_session: AsyncSession,
) -> None:
    async with evaluation_context(db_session) as (maker, development_interview):
        await persist_transcript(
            maker,
            development_interview.interview_session.id,
            sequence=1,
            text="unordered_map lookup is always guaranteed O(1).",
        )
        result = await analyze(
            maker,
            development_interview.interview_session.id,
            FakeReasoningProvider(speech_probe_output()),
            evaluation_settings(tmp_path),
        )
        assert result.decision is not None
        gate = await authorize(
            maker, development_interview.interview_session.id, result.decision.id
        )
        assert gate.prompt_id is not None

        async with maker() as session:
            async with session.begin():
                service = RealtimeControlService(session)
                started = await service.start_delivery(
                    session_id=development_interview.interview_session.id,
                    message=CounterQDeliveryStartedMessage(
                        **client_fields(2),
                        type="counterq_delivery_started",
                        interviewer_prompt_id=gate.prompt_id,
                        intended_text="untrusted browser text",
                        provider_response_id="stage1-barge-in",
                    ),
                )
                interrupted = await service.interrupt_delivery(
                    session_id=development_interview.interview_session.id,
                    message=CounterQDeliveryInterruptedMessage(
                        **client_fields(3),
                        type="counterq_delivery_interrupted",
                        interviewer_prompt_id=gate.prompt_id,
                        prompt_delivery_id=started.delivery_id,
                        provider_response_id="stage1-barge-in",
                        confirmed_by="candidate_speech",
                    ),
                )
                retry = await service.interrupt_delivery(
                    session_id=development_interview.interview_session.id,
                    message=CounterQDeliveryInterruptedMessage(
                        **client_fields(4),
                        type="counterq_delivery_interrupted",
                        interviewer_prompt_id=gate.prompt_id,
                        prompt_delivery_id=started.delivery_id,
                        provider_response_id="stage1-barge-in",
                        confirmed_by="candidate_speech",
                    ),
                )
                delivery = await session.get(InterviewerPromptDelivery, started.delivery_id)
                budget = await session.get(
                    SessionBudget, development_interview.interview_session.id
                )

        assert interrupted.delivery_state == "INTERRUPTED"
        assert retry.created is False and retry.event_id == interrupted.event_id
        assert delivery is not None and delivery.actual_transcript_segment_id is None
        assert service.floor.state == "CANDIDATE_SPEAKING"
        assert budget is not None and budget.probes_used == 0


async def test_incomplete_implementation_remains_silent(
    tmp_path: Path,
    db_session: AsyncSession,
) -> None:
    async with evaluation_context(db_session) as (maker, development_interview):
        await persist_code(
            maker, development_interview.interview_session.id, sequence=1, source=CODE_INITIAL
        )
        await persist_code(
            maker,
            development_interview.interview_session.id,
            sequence=2,
            source=CODE_INCOMPLETE,
        )
        result = await analyze(
            maker,
            development_interview.interview_session.id,
            FakeReasoningProvider(observe_output()),
            evaluation_settings(tmp_path),
        )
        assert result.decision is not None
        gate = await authorize(
            maker, development_interview.interview_session.id, result.decision.id
        )
        async with maker() as session:
            budget = await session.get(SessionBudget, development_interview.interview_session.id)
        assert gate.prompt_id is None
        assert gate.disposition == "AUTHORIZED"
        assert budget is not None and budget.probes_used == 0


async def test_invalid_structured_output_is_isolated_and_following_analysis_can_succeed(
    tmp_path: Path,
    db_session: AsyncSession,
) -> None:
    async with evaluation_context(db_session) as (maker, development_interview):
        await persist_transcript(
            maker,
            development_interview.interview_session.id,
            sequence=1,
            text="unordered_map lookup is always guaranteed O(1).",
        )
        invalid = code_probe_output()
        invalid["decision"]["target_claim_index"] = 0
        with pytest.raises(
            StructuredOutputValidationFailure,
            match="Reasoning provider output failed schema validation",
        ):
            await analyze(
                maker,
                development_interview.interview_session.id,
                FakeReasoningProvider(invalid),
                evaluation_settings(tmp_path),
            )
        recovered = await analyze(
            maker,
            development_interview.interview_session.id,
            FakeReasoningProvider(speech_probe_output()),
            evaluation_settings(tmp_path),
        )
        async with maker() as session:
            claims = await session.scalar(
                select(func.count())
                .select_from(CandidateClaim)
                .where(
                    CandidateClaim.interview_session_id
                    == development_interview.interview_session.id
                )
            )
            invocations = await session.scalar(
                select(func.count())
                .select_from(AIInvocation)
                .where(
                    AIInvocation.interview_session_id == development_interview.interview_session.id
                )
            )
        assert recovered.status == "PROPOSED"
        assert claims == 1
        assert invocations == 2
