from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.ai_gateway.structured_output import StrictReasoningOutputModel
from app.db.constants import CLAIM_TYPES, EXAMINER_ACTIONS, PROBE_STRATEGIES

ExaminerAction = Literal["WAIT", "OBSERVE", "ASK", "PROBE"]
ExaminerTargetKind = Literal["NONE", "CLAIM", "EVENT", "CODE_SNAPSHOT"]
ExaminerClaimType = Literal[
    "ALGORITHM_CHOICE",
    "COMPLEXITY",
    "CORRECTNESS",
    "INVARIANT",
    "DATA_STRUCTURE",
    "ASSUMPTION",
    "EDGE_CASE",
    "IMPLEMENTATION",
    "TRADE_OFF",
]
ExaminerProbeStrategy = Literal[
    "WHY",
    "PROVE",
    "ASSUMPTION_CHALLENGE",
    "COUNTEREXAMPLE",
    "COMPLEXITY",
    "EDGE_CASE",
    "TRADE_OFF",
    "ALTERNATIVE",
    "IMPLEMENTATION_CHOICE",
    "CONSTRAINT_MUTATION",
    "FAILURE_MODE",
    "TRANSFER",
]
ExaminerFactorLevel = Literal["LOW", "MEDIUM", "HIGH"]
ExaminerVerificationReason = Literal[
    "NONE",
    "TRANSCRIPTION_AMBIGUITY",
    "UNUSUAL_VALID_APPROACH",
    "DIFFICULT_CODE_SEMANTICS",
    "VERIFIED_PACK_DISAGREEMENT",
    "CONSEQUENTIAL_LOW_CONFIDENCE",
]
EXAMINER_OUTPUT_CONTRACT_VERSION = "v2"


class ExaminerTargetRankingOutput(StrictReasoningOutputModel):
    """Bounded diagnostic metadata, never hidden chain-of-thought."""

    technical_importance: ExaminerFactorLevel
    interpretation_confidence: ExaminerFactorLevel
    diagnostic_value: ExaminerFactorLevel
    current_evidence_gap: ExaminerFactorLevel
    candidate_commitment: ExaminerFactorLevel
    context_relevance: ExaminerFactorLevel
    freshness: ExaminerFactorLevel
    self_correction_likelihood: ExaminerFactorLevel
    interruption_cost: ExaminerFactorLevel
    duplicate_evidence: ExaminerFactorLevel
    time_pressure: ExaminerFactorLevel
    probe_fatigue: ExaminerFactorLevel
    staleness_risk: ExaminerFactorLevel


class ExaminerVerificationOutput(StrictReasoningOutputModel):
    required: bool
    reason: ExaminerVerificationReason

    @model_validator(mode="after")
    def validate_reason(self) -> ExaminerVerificationOutput:
        if self.required and self.reason == "NONE":
            raise ValueError("Required verification needs a specific reason")
        if not self.required and self.reason != "NONE":
            raise ValueError("Non-required verification must use NONE")
        return self


class ExaminerClaimOutput(StrictReasoningOutputModel):
    normalized_claim: str = Field(max_length=500)
    claim_type: ExaminerClaimType
    verbatim_excerpt: str | None = Field(max_length=500)
    confidence: float = Field(ge=0, le=1)


class ExaminerDecisionOutput(StrictReasoningOutputModel):
    target_kind: ExaminerTargetKind = Field(
        description=(
            "Primary diagnostic target: CLAIM for an extracted candidate claim; "
            "CODE_SNAPSHOT for implementation behavior; EVENT only when neither "
            "claim nor code snapshot is the better target; NONE for WAIT or OBSERVE."
        )
    )
    target_claim_index: int | None = Field(
        ge=0,
        description=(
            "Required only when target_kind is CLAIM: the zero-based index of one "
            "returned claim. For NONE, EVENT, and CODE_SNAPSHOT this must be JSON null."
        ),
    )
    technical_rationale: str = Field(max_length=900)
    confidence: float = Field(ge=0, le=1)
    priority: int = Field(ge=0, le=5)
    urgency: int = Field(ge=0, le=5)
    target_ranking: ExaminerTargetRankingOutput
    verification: ExaminerVerificationOutput


class ExaminerWaitDecisionOutput(ExaminerDecisionOutput):
    action: Literal["WAIT"]
    proposed_probe_strategy: None


class ExaminerObserveDecisionOutput(ExaminerDecisionOutput):
    action: Literal["OBSERVE"]
    proposed_probe_strategy: None


class ExaminerAskDecisionOutput(ExaminerDecisionOutput):
    action: Literal["ASK"]
    proposed_probe_strategy: None


class ExaminerProbeDecisionOutput(ExaminerDecisionOutput):
    action: Literal["PROBE"]
    proposed_probe_strategy: ExaminerProbeStrategy


ExaminerActionSpecificDecisionOutput = (
    ExaminerWaitDecisionOutput
    | ExaminerObserveDecisionOutput
    | ExaminerAskDecisionOutput
    | ExaminerProbeDecisionOutput
)


class ExaminerAnalysisResult(StrictReasoningOutputModel):
    claims: list[ExaminerClaimOutput] = Field(max_length=4)
    decision: ExaminerActionSpecificDecisionOutput

    @model_validator(mode="after")
    def validate_decision_links(self) -> ExaminerAnalysisResult:
        decision = self.decision
        if decision.action == "PROBE" and decision.proposed_probe_strategy is None:
            raise ValueError("PROBE decisions require one ProbeStrategy")
        if decision.action != "PROBE" and decision.proposed_probe_strategy is not None:
            raise ValueError("Only PROBE decisions may carry a ProbeStrategy")
        if decision.target_kind == "CLAIM":
            if decision.target_claim_index is None:
                raise ValueError("CLAIM targets require target_claim_index")
            if decision.target_claim_index >= len(self.claims):
                raise ValueError("target_claim_index must reference a returned claim")
        elif decision.target_claim_index is not None:
            raise ValueError("Only CLAIM targets may carry target_claim_index")
        if decision.action not in EXAMINER_ACTIONS:
            raise ValueError("Unknown Examiner action")
        if decision.proposed_probe_strategy is not None and (
            decision.proposed_probe_strategy not in PROBE_STRATEGIES
        ):
            raise ValueError("Unknown ProbeStrategy")
        for claim in self.claims:
            if claim.claim_type not in CLAIM_TYPES:
                raise ValueError("Unknown CandidateClaim type")
        return self
