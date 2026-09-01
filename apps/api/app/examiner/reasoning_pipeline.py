from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from app.ai_gateway.provider import ReasoningCapability, ReasoningEffort
from app.examiner.analysis_schema import ExaminerAnalysisResult, ExaminerVerificationReason

ExaminerReasoningTier = Literal["FAST", "MEDIUM", "STRONG"]
FAST_REASONING_EFFORT: ReasoningEffort = "low"
STRONG_ESCALATION_MIN_REMAINING_SECONDS = 2.0
ALLOWED_STRONG_VERIFICATION_REASONS = frozenset(
    {
        "TRANSCRIPTION_AMBIGUITY",
        "UNUSUAL_VALID_APPROACH",
        "DIFFICULT_CODE_SEMANTICS",
        "VERIFIED_PACK_DISAGREEMENT",
        "CONSEQUENTIAL_LOW_CONFIDENCE",
    }
)
_ALLOWED_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})


class ExaminerReasoningPipelineError(ValueError):
    pass


@dataclass(frozen=True)
class ExaminerReasoningRoute:
    tier: ExaminerReasoningTier
    capability: ReasoningCapability
    purpose: str
    reasoning_effort: ReasoningEffort


def initial_reasoning_tier(context_json: dict[str, object]) -> ExaminerReasoningTier:
    interview = cast(dict[str, object], context_json["interview"])
    source = cast(dict[str, object], context_json["source_observation"])
    diagnostic = cast(dict[str, object], context_json.get("diagnostic_context", {}))
    straightforward_stage = interview.get("current_stage") in {
        "INTRODUCTION",
        "PROBLEM_UNDERSTANDING",
        "APPROACH_DISCOVERY",
    }
    transcript_only = (
        source.get("kind") == "CANDIDATE_TRANSCRIPT_FINALIZED"
        and "code_context_at_watermark" not in source
        and source.get("code") is None
    )
    if straightforward_stage and transcript_only and diagnostic.get("execution_context") is None:
        return "FAST"
    return "MEDIUM"


def reasoning_route_for_tier(
    tier: ExaminerReasoningTier,
    *,
    standard_effort: str,
    strong_effort: str,
) -> ExaminerReasoningRoute:
    if tier == "FAST":
        return ExaminerReasoningRoute(
            tier=tier,
            capability="STANDARD_REASONING",
            purpose="live_examiner",
            reasoning_effort=FAST_REASONING_EFFORT,
        )
    if tier == "MEDIUM":
        return ExaminerReasoningRoute(
            tier=tier,
            capability="STANDARD_REASONING",
            purpose="live_examiner",
            reasoning_effort=_validate_reasoning_effort(standard_effort),
        )
    return ExaminerReasoningRoute(
        tier=tier,
        capability="STRONG_REASONING",
        purpose="live_examiner_strong_verification",
        reasoning_effort=_validate_reasoning_effort(strong_effort),
    )


def build_reasoning_input_payload(
    *,
    context_json: dict[str, object],
    tier: ExaminerReasoningTier,
    required_verification_reason: ExaminerVerificationReason | None = None,
    preliminary_analysis: ExaminerAnalysisResult | None = None,
) -> dict[str, object]:
    payload = dict(context_json)
    runtime_control: dict[str, object] = {
        "reasoning_tier": tier,
        "verification_pass": "NONE",
        "this_is_single_verification_pass": False,
    }
    if tier == "STRONG":
        if required_verification_reason in {None, "NONE"} or preliminary_analysis is None:
            raise ExaminerReasoningPipelineError(
                "Strong verification requires a preliminary result and verification reason"
            )
        preliminary = preliminary_analysis.decision
        runtime_control.update(
            {
                "verification_pass": "ONE_AND_ONLY",
                "this_is_single_verification_pass": True,
                "verification_reason": required_verification_reason,
                "preliminary_recommendation": {
                    "action": preliminary.action,
                    "target": _preliminary_target(context_json, preliminary_analysis),
                    "strategy": preliminary.proposed_probe_strategy,
                },
                "verification_requirements": {
                    "resolve_uncertainty_independently_using_original_context": True,
                    "do_not_escalate_again": True,
                    "if_unresolved": {
                        "prefer_safe_neutral_action": ["WAIT", "OBSERVE"],
                        "do_not_make_consequential_accusation": True,
                    },
                },
            }
        )
    payload["trusted_runtime_control"] = runtime_control
    return payload


def next_strong_verification_reason(
    current_tier: ExaminerReasoningTier,
    result: ExaminerAnalysisResult,
) -> ExaminerVerificationReason | None:
    """Return one allowed escalation reason, never an escalation from STRONG."""
    verification = result.decision.verification
    if (
        current_tier != "STRONG"
        and verification.required
        and verification.reason in ALLOWED_STRONG_VERIFICATION_REASONS
    ):
        return verification.reason
    return None


def unresolved_consequential_challenge(result: ExaminerAnalysisResult) -> bool:
    decision = result.decision
    return decision.action == "PROBE" and decision.verification.required


def _preliminary_target(
    context_json: dict[str, object],
    analysis: ExaminerAnalysisResult,
) -> dict[str, object]:
    decision = analysis.decision
    target: dict[str, object] = {"kind": decision.target_kind}
    if decision.target_kind == "CLAIM" and decision.target_claim_index is not None:
        claim = analysis.claims[decision.target_claim_index]
        target.update(
            {
                "claim_type": claim.claim_type,
                "normalized_claim": claim.normalized_claim,
            }
        )
    elif decision.target_kind == "CODE_SNAPSHOT":
        source = cast(dict[str, object], context_json["source_observation"])
        code = _source_code_reference(source)
        target.update(
            {
                "code_snapshot_id": code.get("code_snapshot_id") if code else None,
                "code_snapshot_version": code.get("code_snapshot_version") if code else None,
            }
        )
    elif decision.target_kind == "EVENT":
        source = cast(dict[str, object], context_json["source_observation"])
        target["source_event_id"] = source.get("source_event_id")
    return target


def _source_code_reference(source: dict[str, object]) -> dict[str, object] | None:
    for key in ("code", "code_context_at_watermark"):
        value = source.get(key)
        if isinstance(value, dict):
            return cast(dict[str, object], value)
    transcript = source.get("transcript")
    if isinstance(transcript, dict):
        typed_transcript = cast(dict[str, object], transcript)
        if typed_transcript.get("associated_code_snapshot_id") is not None:
            return {
                "code_snapshot_id": typed_transcript["associated_code_snapshot_id"],
                "code_snapshot_version": typed_transcript.get(
                    "associated_code_snapshot_version"
                ),
            }
    return None


def _validate_reasoning_effort(value: str) -> ReasoningEffort:
    if value not in _ALLOWED_REASONING_EFFORTS:
        raise ExaminerReasoningPipelineError("Configured reasoning effort is unsupported")
    return cast(ReasoningEffort, value)
