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


class ExaminerClaimOutput(StrictReasoningOutputModel):
    normalized_claim: str = Field(max_length=500)
    claim_type: ExaminerClaimType
    verbatim_excerpt: str | None = Field(max_length=500)
    confidence: float = Field(ge=0, le=1)


class ExaminerDecisionOutput(StrictReasoningOutputModel):
    action: ExaminerAction
    target_kind: ExaminerTargetKind
    target_claim_index: int | None = Field(ge=0)
    proposed_probe_strategy: ExaminerProbeStrategy | None
    technical_rationale: str = Field(max_length=900)
    confidence: float = Field(ge=0, le=1)
    priority: int = Field(ge=0, le=5)
    urgency: int = Field(ge=0, le=5)


class ExaminerAnalysisResult(StrictReasoningOutputModel):
    claims: list[ExaminerClaimOutput] = Field(max_length=4)
    decision: ExaminerDecisionOutput

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
