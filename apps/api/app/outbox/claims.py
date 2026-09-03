"""Attempt-scoped durable ownership identity for one outbox delivery."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class OutboxWorkClaim:
    outbox_event_id: UUID
    attempt: int
