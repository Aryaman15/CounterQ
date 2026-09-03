"""Import all ORM models so Alembic sees complete metadata."""

from app.ai_gateway.models import AIInvocation, AIPolicyVersion
from app.auth.models import User
from app.evidence.models import (
    Assessment,
    AssessmentSource,
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
    "Breakpoint",
    "BreakpointEvidence",
    "CandidateClaim",
    "CandidateResponse",
    "CandidateResponseSource",
    "CodeDiff",
    "CodeSnapshot",
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
    "OutboxEvent",
    "Problem",
    "ProblemConcept",
    "ProblemVersion",
    "SessionBudget",
    "SessionReport",
    "SkillDimension",
    "TranscriptSegment",
    "TestResult",
    "User",
]
