from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ExternalPrincipal:
    provider: str
    subject: str
    session_id: str | None = None


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    status: str
