"""Import all ORM models so Alembic sees complete metadata."""

from app.ai_gateway.models import AIInvocation, AIPolicyVersion
from app.auth.models import User
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
from app.problems.models import Concept, ConceptAlias, ConceptRelationship, InterviewPackVersion, Problem, ProblemConcept, ProblemVersion

__all__ = [
    "AIPolicyVersion",
    "AIInvocation",
    "CandidateClaim",
    "CandidateResponse",
    "CandidateResponseSource",
    "CodeDiff",
    "CodeSnapshot",
    "Concept",
    "ConceptAlias",
    "ConceptRelationship",
    "ExaminerDecision",
    "ExecutionRun",
    "InterviewConfiguration",
    "InterviewEvent",
    "InterviewPackVersion",
    "InterviewStageTransition",
    "InterviewSession",
    "InterviewerPrompt",
    "InterviewerPromptDelivery",
    "Problem",
    "ProblemConcept",
    "ProblemVersion",
    "SessionBudget",
    "TranscriptSegment",
    "TestResult",
    "User",
]
