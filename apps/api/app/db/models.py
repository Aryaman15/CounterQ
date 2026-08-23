"""Import all ORM models so Alembic sees complete metadata."""

from app.ai_gateway.models import AIInvocation, AIPolicyVersion
from app.auth.models import User
from app.examiner.models import CandidateClaim, ExaminerDecision
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
from app.problems.models import InterviewPackVersion, Problem, ProblemVersion

__all__ = [
    "AIPolicyVersion",
    "AIInvocation",
    "CandidateClaim",
    "CandidateResponse",
    "CandidateResponseSource",
    "CodeDiff",
    "CodeSnapshot",
    "ExaminerDecision",
    "InterviewConfiguration",
    "InterviewEvent",
    "InterviewPackVersion",
    "InterviewStageTransition",
    "InterviewSession",
    "InterviewerPrompt",
    "InterviewerPromptDelivery",
    "Problem",
    "ProblemVersion",
    "SessionBudget",
    "TranscriptSegment",
    "User",
]
