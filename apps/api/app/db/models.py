"""Import all ORM models so Alembic sees complete metadata."""

from app.ai_gateway.models import AIInvocation, AIPolicyVersion
from app.auth.models import User
from app.countermap.models import CounterMapProjection
from app.evidence.models import (
    Assessment,
    AssessmentSource,
    AssessmentUnitEvaluation,
    Breakpoint,
    BreakpointEvidence,
    Evidence,
    EvidenceConcept,
    EvidenceSkill,
    EvidenceSource,
    SkillDimension,
)
from app.examiner.models import CandidateClaim, ExaminerDecision
from app.execution.models import ExecutionRun, TestResult
from app.interviews.models import (
    CandidateResponse,
    CandidateResponseSource,
    InterviewConfiguration,
    InterviewerPrompt,
    InterviewerPromptDelivery,
    InterviewSession,
    InterviewStageTransition,
    SessionBudget,
)
from app.mastery.models import (
    ConceptMastery,
    ConceptMasteryEvidence,
    MasteryTransition,
    MasteryTransitionEvidence,
    RetestAttempt,
    RetestAttemptEvidence,
    RetestRecommendation,
    SkillMastery,
    SkillMasteryEvidence,
)
from app.observation.models import CodeDiff, CodeSnapshot, InterviewEvent, TranscriptSegment
from app.outbox.models import OutboxEvent
from app.problems.models import (
    Concept,
    ConceptAlias,
    ConceptRelationship,
    InterviewPackVersion,
    Problem,
    ProblemConcept,
    ProblemVersion,
)
from app.reports.models import SessionReport

__all__ = [
    "AIPolicyVersion",
    "AIInvocation",
    "Assessment",
    "AssessmentSource",
    "AssessmentUnitEvaluation",
    "Breakpoint",
    "BreakpointEvidence",
    "CandidateClaim",
    "CandidateResponse",
    "CandidateResponseSource",
    "CodeDiff",
    "CodeSnapshot",
    "ConceptMastery",
    "ConceptMasteryEvidence",
    "CounterMapProjection",
    "Concept",
    "ConceptAlias",
    "ConceptRelationship",
    "ExaminerDecision",
    "Evidence",
    "EvidenceConcept",
    "EvidenceSkill",
    "EvidenceSource",
    "ExecutionRun",
    "InterviewConfiguration",
    "InterviewEvent",
    "InterviewPackVersion",
    "InterviewStageTransition",
    "InterviewSession",
    "InterviewerPrompt",
    "InterviewerPromptDelivery",
    "MasteryTransition",
    "MasteryTransitionEvidence",
    "OutboxEvent",
    "Problem",
    "ProblemConcept",
    "ProblemVersion",
    "RetestAttempt",
    "RetestAttemptEvidence",
    "RetestRecommendation",
    "SessionBudget",
    "SessionReport",
    "SkillDimension",
    "SkillMastery",
    "SkillMasteryEvidence",
    "TranscriptSegment",
    "TestResult",
    "User",
]
