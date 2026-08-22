"""Import all ORM models so Alembic sees complete metadata."""

from app.ai_gateway.models import AIPolicyVersion
from app.auth.models import User
from app.interviews.models import InterviewConfiguration, InterviewSession, SessionBudget
from app.observation.models import CodeSnapshot, InterviewEvent, TranscriptSegment
from app.problems.models import InterviewPackVersion, Problem, ProblemVersion

__all__ = [
    "AIPolicyVersion",
    "CodeSnapshot",
    "InterviewConfiguration",
    "InterviewEvent",
    "InterviewPackVersion",
    "InterviewSession",
    "Problem",
    "ProblemVersion",
    "SessionBudget",
    "TranscriptSegment",
    "User",
]
