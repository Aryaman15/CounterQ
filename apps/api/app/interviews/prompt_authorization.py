from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.examiner.models import CandidateClaim, ExaminerDecision
from app.interviews.budget_policy import probe_budget_snapshot
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.models import (
    InterviewerPrompt,
    InterviewerPromptDelivery,
    InterviewSession,
    SessionBudget,
)
from app.observation.models import CodeSnapshot, InterviewEvent
from app.observation.repository import ObservationRepository

logger = structlog.get_logger(__name__)

MIN_PROMPT_GATE_CONFIDENCE = Decimal("0.75")
IMPLEMENTATION_PROBE_MIN_CONFIDENCE = Decimal("0.80")
CONSEQUENTIAL_CLAIM_CHALLENGE_MIN_CONFIDENCE = Decimal("0.80")
CONSEQUENTIAL_CLAIM_CHALLENGE_STRATEGIES = frozenset(
    {
        "PROVE",
        "ASSUMPTION_CHALLENGE",
        "COUNTEREXAMPLE",
        "COMPLEXITY",
        "FAILURE_MODE",
    }
)
MIN_REMAINING_PROMPT_SECONDS = 8
PROBE_ALLOWED_STAGES = {
    "PROBLEM_UNDERSTANDING",
    "APPROACH_DISCOVERY",
    "APPROACH_DEFENSE",
    "IMPLEMENTATION",
    "TESTING_DEBUGGING",
    "COMPLEXITY_EDGE_CASES",
    "CONSTRAINT_MUTATION",
    "FINAL_DEFENSE",
}
ASK_ALLOWED_STAGES = PROBE_ALLOWED_STAGES | {"INTRODUCTION", "WRAP_UP"}
AUTHORIZED_PROMPT_DELIVERY_WINDOW_SECONDS = 12.0


class PromptAuthorizationError(ValueError):
    pass


class PromptDeliveryPermitError(PromptAuthorizationError):
    pass


@dataclass(frozen=True)
class PromptGateRuntimeState:
    candidate_speaking: bool = False
    candidate_code_active: bool = False


@dataclass(frozen=True)
class PromptGateResult:
    decision_id: UUID
    disposition: str
    decision_status: str
    policy_gate_outcome: str | None
    reason: str
    prompt_id: UUID | None = None
    prompt_kind: str | None = None
    probe_strategy: str | None = None
    candidate_safe_text: str | None = None


@dataclass(frozen=True)
class PromptDeliveryPermit:
    prompt_id: UUID
    status: str
    reason: str
    text: str | None = None
    kind: str | None = None
    origin: str | None = None


class PromptAuthorizationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] | None = None,
        authorized_prompt_delivery_window_seconds: float = (
            AUTHORIZED_PROMPT_DELIVERY_WINDOW_SECONDS
        ),
    ) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))
        self._authorized_prompt_delivery_window = timedelta(
            seconds=authorized_prompt_delivery_window_seconds,
        )

    async def evaluate_examiner_decision(
        self,
        *,
        session_id: UUID,
        decision_id: UUID,
        runtime_state: PromptGateRuntimeState | None = None,
    ) -> PromptGateResult:
        runtime_state = runtime_state or PromptGateRuntimeState()
        decision = await self._lock_decision(session_id, decision_id)
        interview = await self._lock_session(session_id)

        existing_prompt = await self._prompt_for_decision(session_id, decision_id)
        if existing_prompt is not None:
            return PromptGateResult(
                decision_id=decision.id,
                disposition="AUTHORIZED",
                decision_status=decision.status,
                policy_gate_outcome=decision.policy_gate_outcome,
                reason="ExaminerDecision already has an authorized prompt.",
                prompt_id=existing_prompt.id,
                prompt_kind=existing_prompt.kind,
                probe_strategy=existing_prompt.probe_strategy,
                candidate_safe_text=existing_prompt.intent,
            )

        if decision.status != "PROPOSED":
            return self._non_proposed_result(decision)

        if decision.action in {"WAIT", "OBSERVE"}:
            return await self._accept_silence(decision)

        deferred_reason = await self._defer_reason(session_id, runtime_state)
        if deferred_reason is not None:
            return PromptGateResult(
                decision_id=decision.id,
                disposition="DEFERRED",
                decision_status=decision.status,
                policy_gate_outcome=None,
                reason=deferred_reason,
            )

        reject_outcome = await self._durable_rejection_outcome(interview, decision)
        if reject_outcome is not None:
            outcome, reason = reject_outcome
            self._mark_decision(decision, outcome=outcome, reason=reason)
            await self._session.flush()
            logger.info(
                "examiner_decision_policy_rejected",
                session_id=str(session_id),
                decision_id=str(decision.id),
                outcome=outcome,
            )
            return PromptGateResult(
                decision_id=decision.id,
                disposition=outcome,
                decision_status=decision.status,
                policy_gate_outcome=decision.policy_gate_outcome,
                reason=reason,
            )

        prompt_kind = "CLARIFICATION" if decision.action == "ASK" else "PROBE"
        probe_strategy = decision.proposed_probe_strategy if prompt_kind == "PROBE" else None
        text = await self._compose_candidate_safe_text(decision)
        prompt = await InterviewInteractionRepository(self._session).add_prompt(
            interview_session_id=session_id,
            origin="EXAMINER_DECISION",
            kind=prompt_kind,
            examiner_decision_id=decision.id,
            probe_strategy=probe_strategy,
            target_claim_id=decision.target_claim_id,
            intent=text,
            status="AUTHORIZED",
            authorized_at=self._clock(),
        )
        decision.status = "AUTHORIZED"
        decision.policy_gate_outcome = "AUTHORIZED"
        decision.policy_gate_reason = "Policy gate authorized candidate-safe prompt intent."
        await self._session.flush()
        logger.info(
            "examiner_decision_authorized",
            session_id=str(session_id),
            decision_id=str(decision.id),
            prompt_id=str(prompt.id),
            action=decision.action,
        )
        return PromptGateResult(
            decision_id=decision.id,
            disposition="AUTHORIZED",
            decision_status=decision.status,
            policy_gate_outcome=decision.policy_gate_outcome,
            reason=decision.policy_gate_reason,
            prompt_id=prompt.id,
            prompt_kind=prompt.kind,
            probe_strategy=prompt.probe_strategy,
            candidate_safe_text=prompt.intent,
        )

    async def permit_delivery(
        self,
        *,
        session_id: UUID,
        prompt_id: UUID,
        runtime_state: PromptGateRuntimeState | None = None,
    ) -> PromptDeliveryPermit:
        runtime_state = runtime_state or PromptGateRuntimeState()
        prompt = await self._prompt_for_session(session_id, prompt_id)
        if prompt.status != "AUTHORIZED":
            return PromptDeliveryPermit(
                prompt_id=prompt.id,
                status="REJECTED",
                reason="Prompt is not authorized for delivery.",
            )
        if runtime_state.candidate_speaking:
            return PromptDeliveryPermit(
                prompt_id=prompt.id,
                status="DEFERRED",
                reason="Candidate is speaking.",
            )
        if runtime_state.candidate_code_active:
            return PromptDeliveryPermit(
                prompt_id=prompt.id,
                status="DEFERRED",
                reason="Candidate is actively editing.",
            )
        if await self._active_delivery_exists(session_id):
            return PromptDeliveryPermit(
                prompt_id=prompt.id,
                status="DEFERRED",
                reason="A prompt delivery is already active.",
            )
        if prompt.authorized_at is None:
            prompt.status = "REJECTED"
            await self._session.flush()
            return PromptDeliveryPermit(
                prompt_id=prompt.id,
                status="REJECTED",
                reason="Prompt authorization timestamp is unavailable.",
            )
        if self._clock() > prompt.authorized_at + self._authorized_prompt_delivery_window:
            prompt.status = "EXPIRED"
            await self._session.flush()
            return PromptDeliveryPermit(
                prompt_id=prompt.id,
                status="EXPIRED",
                reason="Authorized prompt delivery window expired.",
            )

        if prompt.origin == "EXAMINER_DECISION":
            decision = await self._session.get(ExaminerDecision, prompt.examiner_decision_id)
            if decision is None or decision.status != "AUTHORIZED":
                prompt.status = "REJECTED"
                await self._session.flush()
                return PromptDeliveryPermit(
                    prompt_id=prompt.id,
                    status="REJECTED",
                    reason="Examiner prompt no longer has authorization.",
                )
            rejection = await self._delivery_rejection_outcome(
                await self._lock_session(session_id),
                decision,
            )
            if rejection is not None:
                outcome, reason = rejection
                prompt.status = (
                    outcome if outcome in {"STALE", "EXPIRED", "SUPERSEDED"} else "REJECTED"
                )
                await self._session.flush()
                return PromptDeliveryPermit(
                    prompt_id=prompt.id,
                    status=outcome if outcome in {"STALE", "EXPIRED", "SUPERSEDED"} else "REJECTED",
                    reason=reason,
                )

        return PromptDeliveryPermit(
            prompt_id=prompt.id,
            status="PERMITTED",
            reason="Authorized prompt is valid for delivery.",
            text=prompt.intent,
            kind=prompt.kind,
            origin=prompt.origin,
        )

    async def consume_probe_budget_for_delivered_prompt(self, prompt: InterviewerPrompt) -> None:
        if prompt.kind != "PROBE" or prompt.status == "DELIVERED":
            return
        budget = await self._session.scalar(
            select(SessionBudget)
            .where(SessionBudget.session_id == prompt.interview_session_id)
            .with_for_update(),
        )
        if budget is None:
            return
        budget.probes_used += 1

    async def _durable_rejection_outcome(
        self,
        interview: InterviewSession,
        decision: ExaminerDecision,
    ) -> tuple[str, str] | None:
        now = self._clock()
        if interview.status != "ACTIVE":
            return ("STALE", "InterviewSession is no longer active.")
        if now >= interview.deadline_at:
            return ("EXPIRED", "InterviewSession deadline has been reached.")
        if decision.deadline_at is not None and now >= decision.deadline_at:
            return ("EXPIRED", "ExaminerDecision usefulness deadline expired.")
        if interview.state_version != decision.source_state_version:
            return ("STALE", "Interview state version changed after ExaminerDecision.")
        if decision.target_event_id is None:
            return ("STALE", "ExaminerDecision has no source event target.")
        source_event = await self._session.get(InterviewEvent, decision.target_event_id)
        if source_event is None or source_event.interview_session_id != interview.id:
            return ("STALE", "ExaminerDecision source event is unavailable.")
        if source_event.server_sequence != decision.source_event_watermark:
            return ("STALE", "ExaminerDecision source watermark does not match source event.")
        if decision.action == "PROBE" and interview.current_stage not in PROBE_ALLOWED_STAGES:
            return ("STAGE_INVALID", "Probe is not legal in the current interview stage.")
        if decision.action == "ASK" and interview.current_stage not in ASK_ALLOWED_STAGES:
            return ("STAGE_INVALID", "Clarification is not legal in the current interview stage.")
        min_confidence = (
            IMPLEMENTATION_PROBE_MIN_CONFIDENCE
            if decision.action == "PROBE" and interview.current_stage == "IMPLEMENTATION"
            else MIN_PROMPT_GATE_CONFIDENCE
        )
        if decision.confidence is None or decision.confidence < min_confidence:
            return ("LOW_CONFIDENCE", "ExaminerDecision confidence is below policy threshold.")
        claim_confidence_rejection = await self._claim_confidence_rejection(decision)
        if claim_confidence_rejection is not None:
            return claim_confidence_rejection
        remaining_seconds = (interview.deadline_at - now).total_seconds()
        if remaining_seconds < MIN_REMAINING_PROMPT_SECONDS:
            return ("EXPIRED", "Insufficient session time remains for a new prompt.")
        if decision.target_code_snapshot_id is not None:
            latest = await ObservationRepository(self._session).latest_code_snapshot(interview.id)
            target_snapshot = await self._session.get(
                CodeSnapshot,
                decision.target_code_snapshot_id,
            )
            if target_snapshot is None or target_snapshot.interview_session_id != interview.id:
                return ("STALE", "Target CodeSnapshot is unavailable.")
            if latest is not None and latest.version_number > target_snapshot.version_number:
                return ("STALE", "A newer CodeSnapshot superseded the target.")
        newer_candidate_event = await self._session.scalar(
            select(InterviewEvent)
            .where(InterviewEvent.interview_session_id == interview.id)
            .where(InterviewEvent.server_sequence > decision.source_event_watermark)
            .where(InterviewEvent.source.in_(["CANDIDATE_VOICE", "NATIVE_EDITOR"]))
            .order_by(InterviewEvent.server_sequence.asc())
            .limit(1),
        )
        if newer_candidate_event is not None:
            return ("SUPERSEDED", "Newer candidate behavior arrived after the source event.")
        if decision.action == "PROBE":
            duplicate_result = await self._duplicate_probe_result(decision)
            if duplicate_result is not None:
                return duplicate_result
            budget_result = await self._probe_budget_result(interview.id)
            if budget_result is not None:
                return budget_result
        return None

    async def _delivery_rejection_outcome(
        self,
        interview: InterviewSession,
        decision: ExaminerDecision,
    ) -> tuple[str, str] | None:
        now = self._clock()
        if interview.status != "ACTIVE":
            return ("STALE", "InterviewSession is no longer active.")
        if now >= interview.deadline_at:
            return ("EXPIRED", "InterviewSession deadline has been reached.")
        if interview.state_version != decision.source_state_version:
            return ("STALE", "Interview state version changed after prompt authorization.")
        if decision.target_event_id is None:
            return ("STALE", "ExaminerDecision has no source event target.")
        source_event = await self._session.get(InterviewEvent, decision.target_event_id)
        if source_event is None or source_event.interview_session_id != interview.id:
            return ("STALE", "ExaminerDecision source event is unavailable.")
        if source_event.server_sequence != decision.source_event_watermark:
            return ("STALE", "ExaminerDecision source watermark does not match source event.")
        if decision.action == "PROBE" and interview.current_stage not in PROBE_ALLOWED_STAGES:
            return ("STALE", "Probe is no longer legal in the current interview stage.")
        if decision.action == "ASK" and interview.current_stage not in ASK_ALLOWED_STAGES:
            return ("STALE", "Clarification is no longer legal in the current interview stage.")
        remaining_seconds = (interview.deadline_at - now).total_seconds()
        if remaining_seconds < MIN_REMAINING_PROMPT_SECONDS:
            return ("EXPIRED", "Insufficient session time remains for prompt delivery.")
        if decision.target_code_snapshot_id is not None:
            latest = await ObservationRepository(self._session).latest_code_snapshot(interview.id)
            target_snapshot = await self._session.get(
                CodeSnapshot,
                decision.target_code_snapshot_id,
            )
            if target_snapshot is None or target_snapshot.interview_session_id != interview.id:
                return ("STALE", "Target CodeSnapshot is unavailable.")
            if latest is not None and latest.version_number > target_snapshot.version_number:
                return ("STALE", "Target code changed after prompt authorization.")
        newer_candidate_event = await self._session.scalar(
            select(InterviewEvent)
            .where(InterviewEvent.interview_session_id == interview.id)
            .where(InterviewEvent.server_sequence > decision.source_event_watermark)
            .where(InterviewEvent.source.in_(["CANDIDATE_VOICE", "NATIVE_EDITOR"]))
            .order_by(InterviewEvent.server_sequence.asc())
            .limit(1),
        )
        if newer_candidate_event is not None:
            return ("STALE", "Newer candidate behavior arrived after prompt authorization.")
        if decision.action == "PROBE":
            duplicate_result = await self._duplicate_probe_result(decision)
            if duplicate_result is not None:
                return duplicate_result
        return None

    async def _probe_budget_result(self, session_id: UUID) -> tuple[str, str] | None:
        budget = await probe_budget_snapshot(self._session, session_id, for_update=True)
        if budget is None:
            return ("BUDGET_DENIED", "SessionBudget is unavailable.")
        if budget.remaining_probes == 0:
            return ("BUDGET_DENIED", "Probe budget is exhausted or already reserved.")
        return None

    async def _claim_confidence_rejection(
        self,
        decision: ExaminerDecision,
    ) -> tuple[str, str] | None:
        if (
            decision.action != "PROBE"
            or decision.target_claim_id is None
            or decision.proposed_probe_strategy
            not in CONSEQUENTIAL_CLAIM_CHALLENGE_STRATEGIES
        ):
            return None
        claim = await self._session.get(CandidateClaim, decision.target_claim_id)
        if claim is None:
            return ("STALE", "Target CandidateClaim is unavailable.")
        if claim.extraction_confidence < CONSEQUENTIAL_CLAIM_CHALLENGE_MIN_CONFIDENCE:
            return (
                "LOW_CONFIDENCE",
                "Consequential claim challenge requires trustworthy claim extraction.",
            )
        return None

    async def _duplicate_probe_result(
        self,
        decision: ExaminerDecision,
    ) -> tuple[str, str] | None:
        if decision.proposed_probe_strategy is None:
            return None
        current_claim = (
            await self._session.get(CandidateClaim, decision.target_claim_id)
            if decision.target_claim_id
            else None
        )
        rows = (
            await self._session.execute(
                select(InterviewerPrompt, ExaminerDecision, CandidateClaim)
                .join(
                    InterviewerPromptDelivery,
                    InterviewerPromptDelivery.interviewer_prompt_id == InterviewerPrompt.id,
                )
                .outerjoin(
                    ExaminerDecision,
                    InterviewerPrompt.examiner_decision_id == ExaminerDecision.id,
                )
                .outerjoin(CandidateClaim, InterviewerPrompt.target_claim_id == CandidateClaim.id)
                .where(InterviewerPrompt.interview_session_id == decision.interview_session_id)
                .where(InterviewerPrompt.kind == "PROBE")
                .where(InterviewerPrompt.probe_strategy == decision.proposed_probe_strategy)
                .where(InterviewerPromptDelivery.delivery_state == "DELIVERED")
            )
        ).all()
        for _prompt, previous_decision, previous_claim in rows:
            same_claim = (
                current_claim is not None
                and previous_claim is not None
                and current_claim.claim_type == previous_claim.claim_type
                and _normalized_claim_identity(current_claim.normalized_claim)
                == _normalized_claim_identity(previous_claim.normalized_claim)
            )
            same_code_source = (
                decision.target_code_snapshot_id is not None
                and previous_decision is not None
                and previous_decision.target_code_snapshot_id
                == decision.target_code_snapshot_id
                and previous_decision.target_event_id == decision.target_event_id
            )
            if same_claim or same_code_source:
                return (
                    "REJECTED",
                    "A candidate-visible probe already covered the same structured "
                    "target and strategy.",
                )
        return None

    async def _defer_reason(
        self,
        session_id: UUID,
        runtime_state: PromptGateRuntimeState,
    ) -> str | None:
        if runtime_state.candidate_speaking:
            return "Candidate currently owns the conversation floor."
        if runtime_state.candidate_code_active:
            return "Candidate is actively editing."
        active = await self._session.scalar(
            select(InterviewerPromptDelivery.id)
            .where(InterviewerPromptDelivery.interview_session_id == session_id)
            .where(InterviewerPromptDelivery.delivery_state == "STARTED")
            .limit(1),
        )
        if active is not None:
            return "A prompt delivery is already active."
        return None

    async def _accept_silence(self, decision: ExaminerDecision) -> PromptGateResult:
        decision.status = "AUTHORIZED"
        decision.policy_gate_outcome = "AUTHORIZED"
        decision.policy_gate_reason = "Policy gate accepted Examiner silence; no prompt authorized."
        await self._session.flush()
        return PromptGateResult(
            decision_id=decision.id,
            disposition="AUTHORIZED",
            decision_status=decision.status,
            policy_gate_outcome=decision.policy_gate_outcome,
            reason=decision.policy_gate_reason,
        )

    def _mark_decision(self, decision: ExaminerDecision, *, outcome: str, reason: str) -> None:
        status_by_outcome = {
            "STALE": "STALE",
            "EXPIRED": "EXPIRED",
            "SUPERSEDED": "SUPERSEDED",
            "BUDGET_DENIED": "REJECTED",
            "STAGE_INVALID": "REJECTED",
            "LOW_CONFIDENCE": "REJECTED",
            "REJECTED": "REJECTED",
        }
        decision.status = status_by_outcome.get(outcome, "REJECTED")
        decision.policy_gate_outcome = outcome
        decision.policy_gate_reason = reason

    def _non_proposed_result(self, decision: ExaminerDecision) -> PromptGateResult:
        if decision.status == "AUTHORIZED":
            disposition = "AUTHORIZED"
        else:
            disposition = decision.policy_gate_outcome or decision.status
        return PromptGateResult(
            decision_id=decision.id,
            disposition=disposition,
            decision_status=decision.status,
            policy_gate_outcome=decision.policy_gate_outcome,
            reason=decision.policy_gate_reason or "ExaminerDecision is no longer proposed.",
        )

    async def _compose_candidate_safe_text(self, decision: ExaminerDecision) -> str:
        claim: CandidateClaim | None = None
        if decision.target_claim_id is not None:
            claim = await self._session.get(CandidateClaim, decision.target_claim_id)
        return compose_candidate_safe_prompt(
            action=decision.action,
            strategy=decision.proposed_probe_strategy,
            normalized_claim=claim.normalized_claim if claim else None,
        )

    async def _lock_decision(self, session_id: UUID, decision_id: UUID) -> ExaminerDecision:
        decision = await self._session.scalar(
            select(ExaminerDecision)
            .where(ExaminerDecision.interview_session_id == session_id)
            .where(ExaminerDecision.id == decision_id)
            .with_for_update(),
        )
        if decision is None:
            raise PromptAuthorizationError("ExaminerDecision not found for session.")
        return decision

    async def _lock_session(self, session_id: UUID) -> InterviewSession:
        interview = await self._session.scalar(
            select(InterviewSession).where(InterviewSession.id == session_id).with_for_update(),
        )
        if interview is None:
            raise PromptAuthorizationError("InterviewSession not found.")
        return interview

    async def _prompt_for_decision(
        self,
        session_id: UUID,
        decision_id: UUID,
    ) -> InterviewerPrompt | None:
        prompt = await self._session.scalar(
            select(InterviewerPrompt)
            .where(InterviewerPrompt.interview_session_id == session_id)
            .where(InterviewerPrompt.examiner_decision_id == decision_id)
            .limit(1),
        )
        return cast(InterviewerPrompt | None, prompt)

    async def _prompt_for_session(self, session_id: UUID, prompt_id: UUID) -> InterviewerPrompt:
        prompt = await self._session.scalar(
            select(InterviewerPrompt)
            .where(InterviewerPrompt.interview_session_id == session_id)
            .where(InterviewerPrompt.id == prompt_id),
        )
        if prompt is None:
            raise PromptDeliveryPermitError("Prompt is unavailable.")
        return prompt

    async def _ensure_no_active_delivery(self, session_id: UUID) -> None:
        active = await self._active_delivery_exists(session_id)
        if active:
            raise PromptDeliveryPermitError("A prompt delivery is already active.")

    async def _active_delivery_exists(self, session_id: UUID) -> bool:
        active = await self._session.scalar(
            select(InterviewerPromptDelivery.id)
            .where(InterviewerPromptDelivery.interview_session_id == session_id)
            .where(InterviewerPromptDelivery.delivery_state == "STARTED")
            .limit(1),
        )
        return active is not None


def compose_candidate_safe_prompt(
    *, action: str, strategy: str | None, normalized_claim: str | None
) -> str:
    """Pure candidate-facing rendering shared by authorization and evaluation."""
    if action == "ASK":
        return "Can you clarify that part of your approach?"
    if action != "PROBE":
        return ""
    if strategy == "ASSUMPTION_CHALLENGE":
        if normalized_claim is not None:
            claim = _candidate_safe_claim(normalized_claim)
            if claim:
                return f"You said {_claim_excerpt(claim)}. Is that actually guaranteed?"
        return "What makes that assumption safe?"
    prompts = {
        "PROVE": "What invariant are you relying on here, and what guarantees it holds?",
        "COMPLEXITY": "Walk me through how you derived that complexity bound.",
        "COUNTEREXAMPLE": "Can you think of an input where this reasoning might break?",
        "EDGE_CASE": "Which edge case is most likely to challenge this approach?",
        "TRADE_OFF": "What trade-off are you making with this choice?",
        "ALTERNATIVE": "What alternative approach would you compare this against?",
        "IMPLEMENTATION_CHOICE": (
            "What makes this implementation choice safe for the invariant you need?"
        ),
        "CONSTRAINT_MUTATION": "How would your approach change if the constraint shifted?",
        "FAILURE_MODE": "How could this implementation fail on a valid input?",
        "TRANSFER": "Where else would this reasoning transfer?",
    }
    return prompts.get(strategy or "", "Walk me through the reasoning behind that choice.")


_CANDIDATE_META_PREFIXES = (
    re.compile(
        r"^the\s+candidate\s+(?:claims?|said|states?|argues?|believes?)\s+(?:that\s+)?",
        re.IGNORECASE,
    ),
    re.compile(r"^the\s+candidate(?:'s)?\s+claim\s+(?:is|that)\s+", re.IGNORECASE),
    re.compile(r"^candidate\s*:\s*", re.IGNORECASE),
)


def _candidate_safe_claim(claim: str) -> str:
    value = claim.strip().strip('"\'')
    previous = None
    while value != previous:
        previous = value
        for pattern in _CANDIDATE_META_PREFIXES:
            value = pattern.sub("", value, count=1).strip()
    return value


def _claim_excerpt(claim: str, *, maximum_length: int = 180) -> str:
    normalized = claim.strip().rstrip(".?! ")
    if len(normalized) <= maximum_length:
        return normalized
    return f"{normalized[: maximum_length - 3].rstrip()}..."


def _normalized_claim_identity(claim: str) -> str:
    return " ".join(claim.casefold().split()).rstrip(".?! ")
