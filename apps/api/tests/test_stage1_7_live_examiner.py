from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_gateway.models import AIInvocation
from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningEffort,
    ReasoningRequest,
    ReasoningUsage,
)
from app.ai_gateway.routes import get_reasoning_provider_builder
from app.config.settings import Settings, create_settings, get_settings
from app.db.session import build_engine, dispose_engine
from app.examiner.analysis_schema import ExaminerAnalysisResult
from app.examiner.coordinator import LiveExaminerCoordinator, LiveExaminerTaskRegistry
from app.examiner.development_workflow import DevelopmentAnalyzeAndAuthorizeWorkflow
from app.examiner.models import CandidateClaim, ExaminerDecision
from app.examiner.policy import LIVE_EXAMINER_INSTRUCTIONS, live_examiner_policy_descriptor
from app.examiner.routes import get_live_examiner_coordinator_builder
from app.interviews.dev_factory import DevelopmentInterview, create_development_interview
from app.interviews.models import (
    InterviewerPrompt,
    InterviewerPromptDelivery,
    SessionBudget,
)
from app.interviews.prompt_authorization import PromptAuthorizationService
from app.main import create_app
from app.realtime.control_protocol import (
    CandidateCodeSnapshotMessage,
    CandidateTranscriptFinalizedMessage,
)
from app.realtime.control_service import RealtimeControlService

CODE_V1 = "class Solution { public: int lengthOfLongestSubstring(string s) { return 0; } };"
CODE_V2 = "class Solution { public: int lengthOfLongestSubstring(string s) { return s.size(); } };"
CODE_INCOMPLETE = """
class Solution {
public:
    int lengthOfLongestSubstring(string s) {
        int left = 0;
        unordered_map<char, int> last;
    }
};
""".strip()
CODE_ACTIVE_CORRECTION = """
class Solution {
public:
    int lengthOfLongestSubstring(string s) {
        unordered_map<char, int> last;
        int left = 0;
        int ans = 0;
        for (int right = 0; right < s.size(); right++) {
            if (last.count(s[right])) {
                left;
            }
        }
    }
};
""".strip()
CODE_STABLE_INVARIANT_BUG = """
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


class FakeExaminerProvider:
    provider_name = "fake"

    def __init__(
        self,
        *,
        output_data: dict[str, Any] | None = None,
        delay_seconds: float = 0,
    ) -> None:
        self.output_data = output_data or transcript_probe_output()
        self.delay_seconds = delay_seconds
        self.calls = 0
        self.requests: list[ReasoningRequest] = []
        self.called_event = asyncio.Event()

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        self.calls += 1
        self.requests.append(request)
        self.called_event.set()
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return ProviderReasoningResult(
            output_data=self.output_data,
            provider="fake",
            model=model,
            provider_model_version=f"{model}-fixture",
            provider_request_id=f"fake-live-examiner-{self.calls}",
            usage=ReasoningUsage(input_tokens=120, cached_input_tokens=12, output_tokens=40),
            latency_ms=37,
            retry_count=0,
            estimated_cost=Decimal("0.000700"),
            currency="USD",
        )


def decision_metadata(
    *,
    verification_required: bool = False,
    verification_reason: str = "NONE",
) -> dict[str, object]:
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
        "verification": {
            "required": verification_required,
            "reason": verification_reason,
        },
    }


def settings(tmp_path: Path, *, autostart: bool = False) -> Settings:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "COUNTERQ_APP_ENV=local\n"
        "OPENAI_API_KEY=test-key\n"
        f"COUNTERQ_LIVE_EXAMINER_AUTOSTART={'true' if autostart else 'false'}\n"
        "COUNTERQ_LIVE_EXAMINER_USEFULNESS_SECONDS=8\n"
    )
    return create_settings(env_file=env_file)


@asynccontextmanager
async def dev_context(
    *, now: datetime | None = None
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], DevelopmentInterview]]:
    engine = build_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            async with session.begin():
                dev = await create_development_interview(
                    session,
                    initial_stage="IMPLEMENTATION",
                    now=now,
                )
        yield maker, dev
    finally:
        await engine.dispose()


def transcript_probe_output() -> dict[str, Any]:
    return {
        "claims": [
            {
                "normalized_claim": "unordered_map lookup is always guaranteed O(1)",
                "claim_type": "COMPLEXITY",
                "verbatim_excerpt": "lookup is always guaranteed O(1)",
                "confidence": 0.92,
            }
        ],
        "decision": {
            "action": "PROBE",
            "target_kind": "CLAIM",
            "target_claim_index": 0,
            "proposed_probe_strategy": "ASSUMPTION_CHALLENGE",
            "technical_rationale": "Clarifying whether the bound is expected or guaranteed.",
            "confidence": 0.9,
            "priority": 4,
            "urgency": 3,
            **decision_metadata(),
        },
    }


def complexity_derivation_probe_output() -> dict[str, Any]:
    return {
        "claims": [
            {
                "normalized_claim": "candidate states the solution is O(n log n)",
                "claim_type": "COMPLEXITY",
                "verbatim_excerpt": "My solution is O(n log n).",
                "confidence": 0.9,
            }
        ],
        "decision": {
            "action": "PROBE",
            "target_kind": "CLAIM",
            "target_claim_index": 0,
            "proposed_probe_strategy": "COMPLEXITY",
            "technical_rationale": "The diagnostic uncertainty is how the bound was derived.",
            "confidence": 0.86,
            "priority": 3,
            "urgency": 2,
            **decision_metadata(),
        },
    }


def invariant_probe_output() -> dict[str, Any]:
    return {
        "claims": [
            {
                "normalized_claim": "left boundary never moves backwards",
                "claim_type": "INVARIANT",
                "verbatim_excerpt": "left can never move backwards",
                "confidence": 0.88,
            }
        ],
        "decision": {
            "action": "PROBE",
            "target_kind": "CLAIM",
            "target_claim_index": 0,
            "proposed_probe_strategy": "PROVE",
            "technical_rationale": "The diagnostic uncertainty is whether the invariant holds.",
            "confidence": 0.87,
            "priority": 4,
            "urgency": 2,
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
            "proposed_probe_strategy": "IMPLEMENTATION_CHOICE",
            "technical_rationale": "The code may not defend the current window invariant.",
            "confidence": 0.84,
            "priority": 4,
            "urgency": 2,
            **decision_metadata(),
        },
    }


def code_invariant_prove_output() -> dict[str, Any]:
    return {
        "claims": [],
        "decision": {
            "action": "PROBE",
            "target_kind": "CODE_SNAPSHOT",
            "target_claim_index": None,
            "proposed_probe_strategy": "PROVE",
            "technical_rationale": (
                "The stable implementation leaves the left-boundary invariant unresolved."
            ),
            "confidence": 0.88,
            "priority": 4,
            "urgency": 3,
            **decision_metadata(),
        },
    }


def code_observe_output(reason: str) -> dict[str, Any]:
    return {
        "claims": [],
        "decision": {
            "action": "OBSERVE",
            "target_kind": "CODE_SNAPSHOT",
            "target_claim_index": None,
            "proposed_probe_strategy": None,
            "technical_rationale": reason,
            "confidence": 0.78,
            "priority": 2,
            "urgency": 1,
            **decision_metadata(),
        },
    }


def wait_output() -> dict[str, Any]:
    return {
        "claims": [],
        "decision": {
            "action": "WAIT",
            "target_kind": "NONE",
            "target_claim_index": None,
            "proposed_probe_strategy": None,
            "technical_rationale": "The candidate is still developing the approach productively.",
            "confidence": 0.8,
            "priority": 1,
            "urgency": 0,
            **decision_metadata(),
        },
    }


def client_base(sequence: int = 1) -> dict[str, object]:
    return {
        "client_event_id": f"client-event-{sequence}",
        "client_instance_id": "client-tab-1",
        "client_sequence": sequence,
    }


async def add_transcript(
    maker: async_sessionmaker[AsyncSession],
    session_id: Any,
    *,
    transcript: str = "I'm using an unordered map because lookup is always guaranteed O(1).",
    sequence: int = 1,
    now: datetime | None = None,
    provider_confidence: float | None = None,
) -> Any:
    occurred_at = now or datetime.now(UTC)
    async with maker() as session:
        async with session.begin():
            result = await RealtimeControlService(
                session,
                clock=lambda: occurred_at,
            ).persist_candidate_transcript(
                session_id=session_id,
                message=CandidateTranscriptFinalizedMessage(
                    **client_base(sequence),
                    type="candidate_transcript_finalized",
                    provider_item_id=f"candidate-item-{sequence}",
                    transcript=transcript,
                    provider_confidence=provider_confidence,
                    ended_at=occurred_at,
                ),
            )
            return result


async def add_code(
    maker: async_sessionmaker[AsyncSession],
    session_id: Any,
    *,
    source: str,
    sequence: int,
    key: str,
    trigger: Literal["INITIAL_EDITOR_STATE", "EDIT_BURST"] = "EDIT_BURST",
) -> Any:
    async with maker() as session:
        async with session.begin():
            result = await RealtimeControlService(session).persist_candidate_code_snapshot(
                session_id=session_id,
                message=CandidateCodeSnapshotMessage(
                    **client_base(sequence),
                    type="candidate_code_snapshot",
                    source_code=source,
                    language="cpp",
                    trigger=trigger,
                    idempotency_key=key,
                ),
            )
            return result


def test_examiner_analysis_schema_enforces_action_strategy_and_claim_target() -> None:
    parsed = ExaminerAnalysisResult.model_validate(transcript_probe_output())
    assert parsed.decision.action == "PROBE"

    invalid_probe = transcript_probe_output()
    invalid_probe["decision"]["proposed_probe_strategy"] = None
    with pytest.raises(ValidationError):
        ExaminerAnalysisResult.model_validate(invalid_probe)

    invalid_wait = wait_output()
    invalid_wait["decision"]["proposed_probe_strategy"] = "WHY"
    with pytest.raises(ValidationError):
        ExaminerAnalysisResult.model_validate(invalid_wait)

    invalid_index = transcript_probe_output()
    invalid_index["decision"]["target_claim_index"] = 4
    with pytest.raises(ValidationError):
        ExaminerAnalysisResult.model_validate(invalid_index)

    missing_claim_index = transcript_probe_output()
    missing_claim_index["decision"]["target_claim_index"] = None
    with pytest.raises(ValidationError):
        ExaminerAnalysisResult.model_validate(missing_claim_index)

    code_target = code_probe_output()
    assert ExaminerAnalysisResult.model_validate(code_target).decision.target_claim_index is None
    for target_kind in ("CODE_SNAPSHOT", "EVENT", "NONE"):
        invalid_non_claim_target = code_probe_output()
        invalid_non_claim_target["decision"]["target_kind"] = target_kind
        invalid_non_claim_target["decision"]["target_claim_index"] = 0
        with pytest.raises(ValidationError):
            ExaminerAnalysisResult.model_validate(invalid_non_claim_target)


def test_live_examiner_policy_v5_guides_ranking_strategies_depth_and_verification() -> None:
    descriptor = live_examiner_policy_descriptor()

    assert descriptor.policy_key == "live_examiner"
    assert descriptor.version == "v5"
    assert descriptor.configuration["policy_id"] == "live_examiner.v5"
    assert "primary uncertainty" in LIVE_EXAMINER_INSTRUCTIONS
    assert "not merely the topic" in LIVE_EXAMINER_INSTRUCTIONS
    assert "invalid absolute complexity guarantee" in LIVE_EXAMINER_INSTRUCTIONS
    assert "ASSUMPTION_CHALLENGE" in LIVE_EXAMINER_INSTRUCTIONS
    assert "deriving a" in LIVE_EXAMINER_INSTRUCTIONS
    assert "COMPLEXITY" in LIVE_EXAMINER_INSTRUCTIONS
    assert "defending an invariant" in LIVE_EXAMINER_INSTRUCTIONS
    assert "PROVE" in LIVE_EXAMINER_INSTRUCTIONS
    assert "Populate every target_ranking factor" in LIVE_EXAMINER_INSTRUCTIONS
    assert "STABLE_AFTER_EDIT_BURST" in LIVE_EXAMINER_INSTRUCTIONS
    assert "stable enough to analyze" in LIVE_EXAMINER_INSTRUCTIONS
    assert "Do not require Run" in LIVE_EXAMINER_INSTRUCTIONS
    assert "require target_claim_index=null" in LIVE_EXAMINER_INSTRUCTIONS
    schema = ExaminerAnalysisResult.model_json_schema()
    decision_schema = schema["$defs"]["ExaminerDecisionOutput"]["properties"]
    assert "Primary diagnostic target" in decision_schema["target_kind"]["description"]
    assert "zero-based index" in decision_schema["target_claim_index"]["description"]


async def test_live_examiner_transcript_persists_claim_and_proposed_decision(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        transcript = await add_transcript(maker, dev.interview_session.id)
        provider = FakeExaminerProvider()
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )

        result = await coordinator.analyze_latest(dev.interview_session.id)

        async with maker() as session:
            claim = await session.scalar(
                select(CandidateClaim).where(
                    CandidateClaim.interview_session_id == dev.interview_session.id
                )
            )
            decision = await session.scalar(
                select(ExaminerDecision).where(
                    ExaminerDecision.interview_session_id == dev.interview_session.id
                )
            )
            prompt_count = await session.scalar(
                select(func.count())
                .select_from(InterviewerPrompt)
                .where(InterviewerPrompt.interview_session_id == dev.interview_session.id)
            )
            budget = await session.get(SessionBudget, dev.interview_session.id)

        assert provider.calls == 1
        assert result.status == "PROPOSED"
        assert result.source_event_id == transcript.event_id
        assert claim is not None
        assert decision is not None
        assert claim.origin_kind == "TRANSCRIPT"
        assert claim.source_transcript_segment_id == transcript.transcript_segment_id
        assert claim.source_event_id == transcript.event_id
        assert claim.status == "ACCEPTED_AS_INTERPRETATION"
        assert decision.action == "PROBE"
        assert decision.status == "PROPOSED"
        assert decision.target_claim_id == claim.id
        assert decision.target_event_id == transcript.event_id
        assert claim.claim_type == "COMPLEXITY"
        assert claim.normalized_claim == "unordered_map lookup is always guaranteed O(1)"
        assert decision.proposed_probe_strategy == "ASSUMPTION_CHALLENGE"
        assert decision.policy_gate_outcome is None
        assert decision.policy_gate_reason is None
        assert prompt_count == 0
        assert budget is not None
        assert budget.probes_used == 0


async def test_live_examiner_complexity_derivation_uses_complexity_strategy(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        await add_transcript(
            maker,
            dev.interview_session.id,
            transcript="My solution is O(n log n).",
        )
        provider = FakeExaminerProvider(output_data=complexity_derivation_probe_output())
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )

        result = await coordinator.analyze_latest(dev.interview_session.id)

        async with maker() as session:
            claim = await session.scalar(
                select(CandidateClaim).where(
                    CandidateClaim.interview_session_id == dev.interview_session.id
                )
            )
            decision = await session.scalar(
                select(ExaminerDecision).where(
                    ExaminerDecision.interview_session_id == dev.interview_session.id
                )
            )

        assert result.status == "PROPOSED"
        assert claim is not None
        assert decision is not None
        assert claim.claim_type == "COMPLEXITY"
        assert decision.action == "PROBE"
        assert decision.proposed_probe_strategy == "COMPLEXITY"


async def test_live_examiner_questionable_invariant_uses_prove_strategy(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        await add_transcript(
            maker,
            dev.interview_session.id,
            transcript="After I update left, left can never move backwards.",
        )
        provider = FakeExaminerProvider(output_data=invariant_probe_output())
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )

        result = await coordinator.analyze_latest(dev.interview_session.id)

        async with maker() as session:
            claim = await session.scalar(
                select(CandidateClaim).where(
                    CandidateClaim.interview_session_id == dev.interview_session.id
                )
            )
            decision = await session.scalar(
                select(ExaminerDecision).where(
                    ExaminerDecision.interview_session_id == dev.interview_session.id
                )
            )

        assert result.status == "PROPOSED"
        assert claim is not None
        assert decision is not None
        assert claim.claim_type == "INVARIANT"
        assert decision.action == "PROBE"
        assert decision.proposed_probe_strategy == "PROVE"


async def test_live_examiner_ignores_initial_code_snapshot(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        await add_code(
            maker,
            dev.interview_session.id,
            source=CODE_V1,
            sequence=1,
            key="initial-code",
            trigger="INITIAL_EDITOR_STATE",
        )
        provider = FakeExaminerProvider(output_data=code_probe_output())
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )

        result = await coordinator.analyze_latest(dev.interview_session.id)

        assert result.status == "NO_ELIGIBLE_OBSERVATION"
        assert provider.calls == 0


async def test_live_examiner_code_path_persists_decision_without_fabricated_claim(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        await add_code(maker, dev.interview_session.id, source=CODE_V1, sequence=1, key="code-v1")
        code = await add_code(
            maker,
            dev.interview_session.id,
            source=CODE_V2,
            sequence=2,
            key="code-v2",
        )
        provider = FakeExaminerProvider(output_data=code_probe_output())
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )

        result = await coordinator.analyze_latest(dev.interview_session.id)

        async with maker() as session:
            claim_count = await session.scalar(
                select(func.count())
                .select_from(CandidateClaim)
                .where(CandidateClaim.interview_session_id == dev.interview_session.id)
            )
            decision = await session.scalar(
                select(ExaminerDecision).where(
                    ExaminerDecision.interview_session_id == dev.interview_session.id
                )
            )

        assert result.status == "PROPOSED"
        assert result.code_snapshot_id == code.snapshot_id
        assert claim_count == 0
        assert decision is not None
        assert decision.target_code_snapshot_id == code.snapshot_id
        assert decision.target_claim_id is None
        assert decision.proposed_probe_strategy == "IMPLEMENTATION_CHOICE"


async def test_live_examiner_incomplete_code_can_observe_without_strategy(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        await add_code(
            maker,
            dev.interview_session.id,
            source=CODE_V1,
            sequence=1,
            key="initial-code",
            trigger="INITIAL_EDITOR_STATE",
        )
        code = await add_code(
            maker,
            dev.interview_session.id,
            source=CODE_INCOMPLETE,
            sequence=2,
            key="incomplete-code",
        )
        provider = FakeExaminerProvider(
            output_data=code_observe_output("The implementation is structurally incomplete.")
        )
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )

        result = await coordinator.analyze_latest(dev.interview_session.id)

        async with maker() as session:
            decision = await session.scalar(
                select(ExaminerDecision).where(
                    ExaminerDecision.interview_session_id == dev.interview_session.id
                )
            )

        assert result.status == "PROPOSED"
        assert result.code_snapshot_id == code.snapshot_id
        assert decision is not None
        assert decision.action == "OBSERVE"
        assert decision.target_code_snapshot_id == code.snapshot_id
        assert decision.proposed_probe_strategy is None


async def test_live_examiner_active_correction_signal_can_observe_without_strategy(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        await add_code(
            maker,
            dev.interview_session.id,
            source=CODE_V1,
            sequence=1,
            key="initial-code",
            trigger="INITIAL_EDITOR_STATE",
        )
        code = await add_code(
            maker,
            dev.interview_session.id,
            source=CODE_ACTIVE_CORRECTION,
            sequence=2,
            key="active-correction-code",
        )
        provider = FakeExaminerProvider(
            output_data=code_observe_output(
                "The candidate appears to be actively editing the exact invariant site."
            )
        )
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )

        result = await coordinator.analyze_latest(dev.interview_session.id)

        async with maker() as session:
            decision = await session.scalar(
                select(ExaminerDecision).where(
                    ExaminerDecision.interview_session_id == dev.interview_session.id
                )
            )

        assert result.status == "PROPOSED"
        assert result.code_snapshot_id == code.snapshot_id
        assert decision is not None
        assert decision.action == "OBSERVE"
        assert decision.target_code_snapshot_id == code.snapshot_id
        assert decision.proposed_probe_strategy is None


async def test_live_examiner_stable_complete_code_can_probe_invariant_with_prove(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        await add_code(
            maker,
            dev.interview_session.id,
            source=CODE_V1,
            sequence=1,
            key="initial-code",
            trigger="INITIAL_EDITOR_STATE",
        )
        code = await add_code(
            maker,
            dev.interview_session.id,
            source=CODE_STABLE_INVARIANT_BUG,
            sequence=2,
            key="stable-invariant-code",
        )
        provider = FakeExaminerProvider(output_data=code_invariant_prove_output())
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )

        result = await coordinator.analyze_latest(dev.interview_session.id)

        async with maker() as session:
            decision = await session.scalar(
                select(ExaminerDecision).where(
                    ExaminerDecision.interview_session_id == dev.interview_session.id
                )
            )
            claim_count = await session.scalar(
                select(func.count())
                .select_from(CandidateClaim)
                .where(CandidateClaim.interview_session_id == dev.interview_session.id)
            )

        assert result.status == "PROPOSED"
        assert result.code_snapshot_id == code.snapshot_id
        assert claim_count == 0
        assert decision is not None
        assert decision.action == "PROBE"
        assert decision.target_code_snapshot_id == code.snapshot_id
        assert decision.target_claim_id is None
        assert decision.proposed_probe_strategy == "PROVE"


async def test_live_examiner_context_marks_edit_burst_code_as_stable_and_current(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        await add_code(
            maker,
            dev.interview_session.id,
            source=CODE_V1,
            sequence=1,
            key="initial-code",
            trigger="INITIAL_EDITOR_STATE",
        )
        code = await add_code(
            maker,
            dev.interview_session.id,
            source=CODE_STABLE_INVARIANT_BUG,
            sequence=2,
            key="stable-context-code",
        )
        provider = FakeExaminerProvider(output_data=code_invariant_prove_output())
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )

        result = await coordinator.analyze_latest(dev.interview_session.id)

        context = json.loads(provider.requests[0].input_content)
        source = context["source_observation"]
        freshness = context["source_freshness"]
        assert result.status == "PROPOSED"
        assert source["kind"] == "CODE_MEANINGFULLY_CHANGED"
        assert source["trigger_class"] == "CODE_EDIT_BURST"
        assert source["observation_boundary"] == "STABLE_AFTER_EDIT_BURST"
        assert "not per keystroke" in source["edit_observation_semantics"]
        assert source["code"]["code_snapshot_id"] == str(code.snapshot_id)
        assert freshness["is_latest_code_snapshot"] is True
        assert freshness["newer_code_snapshot_exists"] is False
        assert freshness["newer_candidate_transcript_exists"] is False
        assert freshness["latest_code_snapshot_version"] == code.version_number
        assert "not actively being typed" in freshness["freshness_semantics"]


async def test_live_examiner_reuses_existing_source_policy_decision_without_provider_call(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        await add_transcript(maker, dev.interview_session.id)
        provider = FakeExaminerProvider()
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )

        first = await coordinator.analyze_latest(dev.interview_session.id)
        second = await coordinator.analyze_latest(dev.interview_session.id)

        async with maker() as session:
            invocation_count = await session.scalar(
                select(func.count())
                .select_from(AIInvocation)
                .where(AIInvocation.interview_session_id == dev.interview_session.id)
            )
            decision_count = await session.scalar(
                select(func.count())
                .select_from(ExaminerDecision)
                .where(ExaminerDecision.interview_session_id == dev.interview_session.id)
            )

        assert first.status == "PROPOSED"
        assert second.status == "REUSED"
        assert provider.calls == 1
        assert invocation_count == 1
        assert decision_count == 1


async def test_development_analyze_and_authorize_gates_immediately_after_reasoning_latency(
    tmp_path: Path,
) -> None:
    t0 = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    current_time = {"value": t0}

    class AdvancingProvider(FakeExaminerProvider):
        async def reason_structured(
            self,
            request: ReasoningRequest,
            *,
            model: str,
            reasoning_effort: ReasoningEffort,
        ) -> ProviderReasoningResult:
            result = await super().reason_structured(
                request,
                model=model,
                reasoning_effort=reasoning_effort,
            )
            current_time["value"] = t0 + timedelta(seconds=4)
            return result

    async with dev_context(now=t0) as (maker, dev):
        await add_transcript(maker, dev.interview_session.id, now=t0)
        provider = AdvancingProvider()
        local_settings = settings(tmp_path)
        coordinator = LiveExaminerCoordinator(
            settings=local_settings,
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
            clock=lambda: current_time["value"],
        )
        workflow = DevelopmentAnalyzeAndAuthorizeWorkflow(
            coordinator=coordinator,
            sessionmaker=maker,
            clock=lambda: current_time["value"],
            authorized_prompt_delivery_window_seconds=12,
        )

        result = await workflow.analyze_and_authorize_latest(dev.interview_session.id)
        assert result.analysis.decision is not None

        async with maker() as session:
            decision = await session.get(ExaminerDecision, result.analysis.decision.id)
            prompt = await session.scalar(
                select(InterviewerPrompt).where(
                    InterviewerPrompt.examiner_decision_id == result.analysis.decision.id
                )
            )
            delivery_count = await session.scalar(
                select(func.count())
                .select_from(InterviewerPromptDelivery)
                .where(InterviewerPromptDelivery.interview_session_id == dev.interview_session.id)
            )
            budget = await session.get(SessionBudget, dev.interview_session.id)

        assert result.analysis.status == "PROPOSED"
        assert result.policy_gate is not None
        assert result.policy_gate.disposition == "AUTHORIZED"
        assert prompt is not None
        assert result.policy_gate.prompt_id == prompt.id
        assert result.timing.remaining_usefulness_seconds_at_analysis == 4
        assert result.timing.remaining_usefulness_seconds_at_gate == 4
        assert result.timing.delivery_window_state == "OPEN"
        assert result.timing.delivery_window_expires_at == t0 + timedelta(seconds=16)
        assert decision is not None
        assert decision.status == "AUTHORIZED"
        assert prompt.status == "AUTHORIZED"
        assert prompt.authorized_at == t0 + timedelta(seconds=4)
        assert delivery_count == 0
        assert budget is not None
        assert budget.probes_used == 0


async def test_manual_policy_gate_after_human_delay_still_expires(
    tmp_path: Path,
) -> None:
    t0 = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    current_time = {"value": t0}

    class AdvancingProvider(FakeExaminerProvider):
        async def reason_structured(
            self,
            request: ReasoningRequest,
            *,
            model: str,
            reasoning_effort: ReasoningEffort,
        ) -> ProviderReasoningResult:
            result = await super().reason_structured(
                request,
                model=model,
                reasoning_effort=reasoning_effort,
            )
            current_time["value"] = t0 + timedelta(seconds=4)
            return result

    async with dev_context(now=t0) as (maker, dev):
        await add_transcript(maker, dev.interview_session.id, now=t0)
        provider = AdvancingProvider()
        local_settings = settings(tmp_path)
        coordinator = LiveExaminerCoordinator(
            settings=local_settings,
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
            clock=lambda: current_time["value"],
        )

        analysis = await coordinator.analyze_latest(dev.interview_session.id)
        assert analysis.decision is not None
        current_time["value"] = t0 + timedelta(seconds=9)

        async with maker() as session:
            async with session.begin():
                gate = await PromptAuthorizationService(
                    session,
                    clock=lambda: current_time["value"],
                ).evaluate_examiner_decision(
                    session_id=dev.interview_session.id,
                    decision_id=analysis.decision.id,
                )
                prompt_count = await session.scalar(
                    select(func.count())
                    .select_from(InterviewerPrompt)
                    .where(InterviewerPrompt.examiner_decision_id == analysis.decision.id)
                )

        assert gate.disposition == "EXPIRED"
        assert gate.policy_gate_outcome == "EXPIRED"
        assert prompt_count == 0


@pytest.mark.parametrize("output_data", [wait_output(), code_observe_output("Continue observing.")])
async def test_development_analyze_and_authorize_silent_actions_create_no_prompt(
    tmp_path: Path,
    output_data: dict[str, Any],
) -> None:
    async with dev_context() as (maker, dev):
        await add_transcript(maker, dev.interview_session.id)
        provider = FakeExaminerProvider(output_data=output_data)
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )
        workflow = DevelopmentAnalyzeAndAuthorizeWorkflow(
            coordinator=coordinator,
            sessionmaker=maker,
        )

        result = await workflow.analyze_and_authorize_latest(dev.interview_session.id)

        async with maker() as session:
            prompt_count = await session.scalar(
                select(func.count())
                .select_from(InterviewerPrompt)
                .where(InterviewerPrompt.interview_session_id == dev.interview_session.id)
            )

        assert result.policy_gate is not None
        assert result.policy_gate.disposition == "AUTHORIZED"
        assert result.policy_gate.prompt_id is None
        assert prompt_count == 0


async def test_development_analyze_and_authorize_revalidates_stale_state_before_gate(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        await add_transcript(maker, dev.interview_session.id, sequence=1)

        async def add_newer_candidate_behavior(_: Any) -> None:
            await add_code(
                maker,
                dev.interview_session.id,
                source=CODE_V2,
                sequence=2,
                key="newer-code-before-gate",
            )

        provider = FakeExaminerProvider()
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )
        workflow = DevelopmentAnalyzeAndAuthorizeWorkflow(
            coordinator=coordinator,
            sessionmaker=maker,
            before_policy_gate=add_newer_candidate_behavior,
        )

        result = await workflow.analyze_and_authorize_latest(dev.interview_session.id)

        async with maker() as session:
            prompt_count = await session.scalar(
                select(func.count())
                .select_from(InterviewerPrompt)
                .where(InterviewerPrompt.interview_session_id == dev.interview_session.id)
            )

        assert result.analysis.status == "PROPOSED"
        assert result.policy_gate is not None
        assert result.policy_gate.disposition == "SUPERSEDED"
        assert prompt_count == 0


async def test_live_examiner_marks_code_result_stale_when_newer_code_exists(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        await add_code(maker, dev.interview_session.id, source=CODE_V1, sequence=1, key="code-v1")
        stale_source = await add_code(
            maker, dev.interview_session.id, source=CODE_V2, sequence=2, key="code-v2"
        )
        await add_code(
            maker,
            dev.interview_session.id,
            source=CODE_V1 + "\n// later",
            sequence=3,
            key="code-v3",
        )
        provider = FakeExaminerProvider(output_data=code_probe_output())
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )

        result = await coordinator.submit(
            interview_session_id=dev.interview_session.id,
            source_event_id=stale_source.event_id,
        )

        async with maker() as session:
            decision = await session.scalar(
                select(ExaminerDecision).where(
                    ExaminerDecision.interview_session_id == dev.interview_session.id
                )
            )
            claim_count = await session.scalar(
                select(func.count())
                .select_from(CandidateClaim)
                .where(CandidateClaim.interview_session_id == dev.interview_session.id)
            )

        assert result.status == "STALE"
        context = json.loads(provider.requests[0].input_content)
        freshness = context["source_freshness"]
        assert freshness["is_latest_code_snapshot"] is False
        assert freshness["newer_code_snapshot_exists"] is True
        assert decision is not None
        assert decision.status == "STALE"
        assert decision.target_event_id == stale_source.event_id
        assert claim_count == 0


async def test_live_examiner_cancellation_updates_ai_invocation_and_persists_no_decision(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        first = await add_transcript(maker, dev.interview_session.id)
        second = await add_code(
            maker,
            dev.interview_session.id,
            source=CODE_V1,
            sequence=2,
            key="code-v1",
        )
        provider = FakeExaminerProvider(delay_seconds=1)
        coordinator = LiveExaminerCoordinator(
            settings=settings(tmp_path, autostart=False),
            sessionmaker=maker,
            provider=provider,
            registry=LiveExaminerTaskRegistry(),
        )

        task = coordinator.submit(
            interview_session_id=dev.interview_session.id,
            source_event_id=first.event_id,
        )
        await asyncio.wait_for(provider.called_event.wait(), timeout=1)
        await coordinator.notify_new_observation(
            interview_session_id=dev.interview_session.id,
            source_event_id=second.event_id,
        )

        with pytest.raises(asyncio.CancelledError):
            await task

        async with maker() as session:
            invocation = await session.scalar(
                select(AIInvocation).where(
                    AIInvocation.interview_session_id == dev.interview_session.id
                )
            )
            decision_count = await session.scalar(
                select(func.count())
                .select_from(ExaminerDecision)
                .where(ExaminerDecision.interview_session_id == dev.interview_session.id)
            )

        assert invocation is not None
        assert invocation.status == "CANCELLED"
        assert invocation.error_class == "CANCELLED"
        assert decision_count == 0


async def test_live_examiner_development_endpoint_blocks_production_and_returns_safe_result(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        await add_transcript(maker, dev.interview_session.id)
        fake_provider = FakeExaminerProvider()

        local_settings = settings(tmp_path)
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: local_settings
        app.dependency_overrides[get_reasoning_provider_builder] = lambda: (
            lambda _settings: fake_provider
        )
        app.dependency_overrides[get_live_examiner_coordinator_builder] = lambda: (
            lambda _settings, _provider_builder: LiveExaminerCoordinator(
                settings=local_settings,
                sessionmaker=maker,
                provider=fake_provider,
                registry=LiveExaminerTaskRegistry(),
            )
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            result = await client.post(
                "/api/examiner/development-analyze-latest",
                json={"interview_session_id": str(dev.interview_session.id)},
            )
        assert result.status_code == 200
        body = result.json()
        assert body["status"] == "PROPOSED"
        assert body["decision"]["status"] == "PROPOSED"
        assert body["decision"]["policy_gate_outcome"] is None
        assert "OPENAI_API_KEY" not in str(body)
        assert fake_provider.calls == 1
        app.dependency_overrides.clear()

    production_settings = settings(tmp_path)
    production_settings.app_env = "production"
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: production_settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        blocked = await client.post(
            "/api/examiner/development-analyze-latest",
            json={"interview_session_id": str(dev.interview_session.id)},
        )
    assert blocked.status_code == 403
    await dispose_engine()


async def test_live_examiner_invalid_structured_output_returns_safe_failure_and_recovers(
    tmp_path: Path,
) -> None:
    async with dev_context() as (maker, dev):
        await add_transcript(maker, dev.interview_session.id)
        invalid_output = code_probe_output()
        invalid_output["decision"]["target_claim_index"] = 0
        fake_provider = FakeExaminerProvider(output_data=invalid_output)
        local_settings = settings(tmp_path)
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: local_settings
        app.dependency_overrides[get_reasoning_provider_builder] = lambda: (
            lambda _settings: fake_provider
        )
        app.dependency_overrides[get_live_examiner_coordinator_builder] = lambda: (
            lambda _settings, _provider_builder: LiveExaminerCoordinator(
                settings=local_settings,
                sessionmaker=maker,
                provider=fake_provider,
                registry=LiveExaminerTaskRegistry(),
            )
        )
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                failed = await client.post(
                    "/api/examiner/development-analyze-latest",
                    json={"interview_session_id": str(dev.interview_session.id)},
                )
                fake_provider.output_data = transcript_probe_output()
                recovered = await client.post(
                    "/api/examiner/development-analyze-latest",
                    json={"interview_session_id": str(dev.interview_session.id)},
                )
        finally:
            app.dependency_overrides.clear()

        async with maker() as session:
            claim_count = await session.scalar(
                select(func.count())
                .select_from(CandidateClaim)
                .where(CandidateClaim.interview_session_id == dev.interview_session.id)
            )
            decision_count = await session.scalar(
                select(func.count())
                .select_from(ExaminerDecision)
                .where(ExaminerDecision.interview_session_id == dev.interview_session.id)
            )
            invocations = list(
                await session.scalars(
                    select(AIInvocation)
                    .where(AIInvocation.interview_session_id == dev.interview_session.id)
                    .order_by(AIInvocation.started_at.asc())
                )
            )

        assert failed.status_code == 502
        assert failed.json()["detail"] == {
            "category": "STRUCTURED_OUTPUT_INVALID",
            "message": (
                "Examiner returned an invalid structured decision. No decision was persisted."
            ),
            "retryable": False,
        }
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "PROPOSED"
        assert claim_count == 1
        assert decision_count == 1
        assert [invocation.status for invocation in invocations] == ["FAILED", "SUCCEEDED"]
        assert invocations[0].error_class == "STRUCTURED_OUTPUT_INVALID"
        assert fake_provider.calls == 2
