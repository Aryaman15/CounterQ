"""Shared versioned CounterMap graph contract for timeline and future graph views."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

COUNTERMAP_SCHEMA_VERSION = "countermap.graph.v1"
COUNTERMAP_GENERATION_POLICY_VERSION = "countermap-projector.v2"

CounterMapNodeType = Literal[
    "CLAIM",
    "REASONING",
    "CODE",
    "TEST",
    "QUESTION",
    "RESPONSE",
    "EVIDENCE",
    "BREAKPOINT",
    "ASSISTANCE",
    "MUTATION",
]
CounterMapRelationship = Literal[
    "TRIGGERED",
    "ANSWERED_BY",
    "LED_TO",
    "SUPPORTED",
    "EXPOSED",
    "CORRECTED_BY",
    "ASSISTED",
]
CanonicalSourceType = Literal[
    "SESSION_EVENT",
    "CANDIDATE_TRANSCRIPT",
    "DELIVERED_PROMPT",
    "CANDIDATE_CLAIM",
    "CANDIDATE_RESPONSE",
    "CODE_SNAPSHOT",
    "CODE_DIFF",
    "EXECUTION",
    "EVIDENCE",
    "BREAKPOINT",
]
RelationshipSourceType = Literal[
    "PROMPT_TARGET",
    "RESPONSE_LINK",
    "EVIDENCE_SOURCE",
    "BREAKPOINT_EVIDENCE",
    "ASSISTANCE_TARGET",
    "CORRECTION_EVIDENCE",
    "EVENT_CAUSATION",
]
IndependenceLevel = Literal[
    "INDEPENDENT",
    "AFTER_PROBE",
    "AFTER_LIGHT_GUIDANCE",
    "AFTER_STRONG_HINT",
    "DIRECTLY_TAUGHT",
]


class CounterMapContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CanonicalSourceReference(CounterMapContractModel):
    source_type: CanonicalSourceType
    source_id: UUID
    interview_session_id: UUID
    server_sequence: int | None = Field(default=None, ge=1)
    version: int | None = Field(default=None, ge=1)
    content_hash: str | None = None


class CanonicalRelationshipSource(CounterMapContractModel):
    source_type: RelationshipSourceType
    source_id: UUID
    related_source_id: UUID | None = None
    interview_session_id: UUID
    detail: str = Field(min_length=1, max_length=96)


class CounterMapEventRange(CounterMapContractModel):
    start_sequence: int = Field(ge=1)
    end_sequence: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered(self) -> CounterMapEventRange:
        if self.end_sequence < self.start_sequence:
            raise ValueError("CounterMap event range must be ordered")
        return self


class CounterMapAvailableAction(CounterMapContractModel):
    action: Literal["VIEW_SOURCE", "DISPUTE_ASSESSMENT", "COUNTERQ_ME_AGAIN"]
    label: str = Field(min_length=1, max_length=80)
    availability: Literal["AVAILABLE", "DEFERRED", "UNAVAILABLE"]
    reason: str | None = Field(default=None, max_length=180)


class CounterMapDisplayMetadata(CounterMapContractModel):
    exact_quote: bool = False
    delivery_state: Literal["DELIVERED", "PARTIALLY_DELIVERED", "INTERRUPTED"] | None = None
    why: str | None = Field(default=None, max_length=360)
    polarity: Literal["POSITIVE", "NEGATIVE", "MIXED"] | None = None
    strength: Literal["WEAK", "MODERATE", "STRONG"] | None = None
    independence_level: IndependenceLevel | None = None
    breakpoint_status: str | None = Field(default=None, max_length=32)
    breakpoint_severity: str | None = Field(default=None, max_length=32)
    breakpoint_relationships: list[
        Literal["CREATED", "REINFORCED", "CONTRADICTED", "RESOLUTION_SUPPORT"]
    ] = Field(default_factory=list)
    assistance_label: str | None = Field(default=None, max_length=80)
    code_snapshot_id: UUID | None = None
    code_version: int | None = Field(default=None, ge=1)
    content_hash: str | None = None
    language: str | None = Field(default=None, max_length=64)
    execution_status: str | None = Field(default=None, max_length=64)
    visible_passed: int | None = Field(default=None, ge=0)
    visible_failed: int | None = Field(default=None, ge=0)


class CounterMapNode(CounterMapContractModel):
    node_id: str = Field(pattern=r"^cmn_[0-9a-f]{24}$")
    node_type: CounterMapNodeType
    subtype: str = Field(min_length=1, max_length=96)
    canonical_sources: list[CanonicalSourceReference] = Field(min_length=1)
    title: str = Field(min_length=1, max_length=140)
    summary: str = Field(min_length=1, max_length=700)
    causal_rank: int = Field(ge=0)
    stage: str | None = Field(default=None, max_length=64)
    event_range: CounterMapEventRange | None = None
    display_metadata: CounterMapDisplayMetadata = Field(default_factory=CounterMapDisplayMetadata)
    available_actions: list[CounterMapAvailableAction] = Field(default_factory=list)


class CounterMapEdge(CounterMapContractModel):
    edge_id: str = Field(pattern=r"^cme_[0-9a-f]{24}$")
    from_node_id: str = Field(pattern=r"^cmn_[0-9a-f]{24}$")
    to_node_id: str = Field(pattern=r"^cmn_[0-9a-f]{24}$")
    relationship: CounterMapRelationship
    canonical_relationship_sources: list[CanonicalRelationshipSource] = Field(min_length=1)


class CounterMapSummary(CounterMapContractModel):
    title: str = Field(min_length=1, max_length=140)
    overview: str = Field(min_length=1, max_length=360)
    node_counts: dict[str, int]
    relationship_counts: dict[str, int]


class CounterMapGraph(CounterMapContractModel):
    schema_version: Literal["countermap.graph.v1"]
    generation_policy_version: Literal["countermap-projector.v2"]
    interview_session_id: UUID
    source_watermark: int = Field(ge=0)
    nodes: list[CounterMapNode]
    edges: list[CounterMapEdge]
    summary: CounterMapSummary

    def semantic_identity(self) -> str:
        payload = self.model_dump(
            mode="json", exclude={"summary": {"node_counts", "relationship_counts"}}
        )
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def stable_node_id(node_type: CounterMapNodeType, *source_ids: UUID) -> str:
    identity = f"{node_type}:" + ":".join(sorted(str(value) for value in source_ids))
    return "cmn_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def stable_edge_id(
    from_node_id: str,
    to_node_id: str,
    relationship: CounterMapRelationship,
    *source_ids: UUID,
) -> str:
    identity = ":".join(
        [from_node_id, to_node_id, relationship, *sorted(str(value) for value in source_ids)]
    )
    return "cme_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
