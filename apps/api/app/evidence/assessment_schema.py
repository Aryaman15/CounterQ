from __future__ import annotations

from typing import Literal, get_args

from pydantic import Field, model_validator

from app.ai_gateway.structured_output import StrictReasoningOutputModel
from app.evidence.breakpoints import KNOWN_BREAKPOINT_SUBTYPES

ASSESSMENT_OUTPUT_CONTRACT_VERSION = "v2"
AssessmentDimension = Literal[
    "CORRECTNESS", "DEPTH", "INDEPENDENCE", "TRANSFER", "EXPLANATION_QUALITY"
]
EvidencePolarity = Literal["POSITIVE", "NEGATIVE", "MIXED"]
EvidenceStrength = Literal["WEAK", "MODERATE", "STRONG"]
BoundaryKind = Literal[
    "NONE",
    "MEANINGFUL_TECHNICAL_BOUNDARY",
    "SYNTAX_ERROR",
    "TRANSIENT_SLIP",
    "TRANSCRIPTION_AMBIGUITY",
    "COSMETIC_ISSUE",
]
BreakpointEffect = Literal["NONE", "WEAKNESS", "CONTRADICTED", "RESOLUTION_SUPPORT"]
BreakpointSeverity = Literal["LOW", "MEDIUM", "HIGH"]
BreakpointSubtype = Literal[
    "worst_case_complexity",
    "left_pointer_monotonicity",
    "recursive_stack_space",
]


class AssessmentFinding(StrictReasoningOutputModel):
    assessment_dimension: AssessmentDimension
    polarity: EvidencePolarity
    confidence: float = Field(ge=0, le=1)
    technical_rationale: str = Field(min_length=1, max_length=900)
    evidence_finding: str = Field(min_length=1, max_length=600)
    proposed_strength: EvidenceStrength
    source_aliases: list[str] = Field(min_length=1, max_length=8)
    concept_keys: list[str] = Field(max_length=4)
    skill_dimension_keys: list[str] = Field(max_length=4)
    boundary_kind: BoundaryKind
    breakpoint_subtype: BreakpointSubtype | None
    breakpoint_effect: BreakpointEffect
    breakpoint_severity: BreakpointSeverity | None

    @model_validator(mode="after")
    def validate_breakpoint_proposal(self) -> AssessmentFinding:
        if not self.concept_keys and not self.skill_dimension_keys:
            raise ValueError("A finding requires at least one canonical Concept or SkillDimension")
        if self.breakpoint_effect != "NONE":
            if self.boundary_kind != "MEANINGFUL_TECHNICAL_BOUNDARY":
                raise ValueError("Breakpoint effects require a meaningful technical boundary")
            if len(self.concept_keys) != 1 or len(self.skill_dimension_keys) != 1:
                raise ValueError(
                    "Breakpoint effects require exactly one Concept and one SkillDimension"
                )
        elif self.breakpoint_subtype is not None:
            raise ValueError("A breakpoint subtype requires a breakpoint effect")
        if self.breakpoint_effect == "WEAKNESS":
            if self.breakpoint_severity is None:
                raise ValueError("Breakpoint weakness requires severity")
            if self.polarity not in ("NEGATIVE", "MIXED"):
                raise ValueError("Breakpoint weakness requires negative or mixed polarity")
        elif self.breakpoint_severity is not None:
            raise ValueError("Only a weakness proposal may include severity")
        if self.breakpoint_effect in ("CONTRADICTED", "RESOLUTION_SUPPORT") and (
            self.polarity not in ("POSITIVE", "MIXED")
        ):
            raise ValueError("Rebuttal or resolution support requires positive or mixed polarity")
        return self


class AssessmentAnalysisResult(StrictReasoningOutputModel):
    findings: list[AssessmentFinding] = Field(max_length=6)


assert set(get_args(BreakpointSubtype)) == set(KNOWN_BREAKPOINT_SUBTYPES)
