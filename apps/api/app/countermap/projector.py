"""Deterministic, evidence-first CounterMap materialization."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Callable
from uuid import UUID

from app.countermap.schema import (
    COUNTERMAP_GENERATION_POLICY_VERSION,
    COUNTERMAP_SCHEMA_VERSION,
    CanonicalRelationshipSource,
    CanonicalSourceReference,
    CounterMapAvailableAction,
    CounterMapDisplayMetadata,
    CounterMapEdge,
    CounterMapEventRange,
    CounterMapGraph,
    CounterMapNode,
    CounterMapNodeType,
    CounterMapRelationship,
    CounterMapSummary,
    stable_edge_id,
    stable_node_id,
)
from app.countermap.source import (
    CanonicalEvidenceSource,
    ClaimSource,
    CodeSnapshotSource,
    CounterMapSourceBundle,
    DecisionSource,
    DeliverySource,
    EventSource,
    ResponseSource,
)

ASSISTED_LEVELS = {
    "AFTER_LIGHT_GUIDANCE",
    "AFTER_STRONG_HINT",
    "DIRECTLY_TAUGHT",
}
CORRECTION_MARKERS = (
    "self-correct",
    "self correct",
    "corrected",
    "correction",
    "fixed",
    "revised",
    "debugged",
)


class CounterMapProjector:
    def project(self, bundle: CounterMapSourceBundle) -> CounterMapGraph:
        nodes: dict[str, CounterMapNode] = {}
        edges: dict[str, CounterMapEdge] = {}
        event_node_ids: dict[UUID, str] = {}
        response_node_ids: dict[UUID, str] = {}
        snapshot_node_ids: dict[UUID, str] = {}
        evidence_node_ids: dict[UUID, str] = {}
        delivery_node_ids: dict[UUID, str] = {}

        events = {item.id: item for item in bundle.events}
        transcripts_by_event = {item.event_id: item for item in bundle.transcripts}
        claims_by_event = {item.source_event_id: item for item in bundle.claims}
        claims_by_id = {item.id: item for item in bundle.claims}
        responses_by_id = {item.id: item for item in bundle.responses}
        response_by_event = {
            event_id: item for item in bundle.responses for event_id in item.source_event_ids
        }
        snapshots_by_id = {item.id: item for item in bundle.code_snapshots}
        snapshot_by_event = {item.created_from_event_id: item for item in bundle.code_snapshots}
        executions_by_event = {item.run_event_id: item for item in bundle.executions}
        decisions = {item.id: item for item in bundle.decisions}
        evidence_by_id = {item.id: item for item in bundle.evidence}

        def add_node(node: CounterMapNode) -> str:
            nodes[node.node_id] = node
            return node.node_id

        def ensure_response(response: ResponseSource) -> str:
            existing = response_node_ids.get(response.id)
            if existing:
                return existing
            transcript_rows = [
                transcripts_by_event[event_id]
                for event_id in response.source_event_ids
                if event_id in transcripts_by_event
                and transcripts_by_event[event_id].speaker == "CANDIDATE"
            ]
            exact_quote = len(transcript_rows) == 1
            summary = (
                transcript_rows[0].text
                if exact_quote
                else " ".join(item.text for item in transcript_rows)
            )
            if not summary:
                summary = response.summary or "You continued your answer."
            sources = [
                CanonicalSourceReference(
                    source_type="CANDIDATE_RESPONSE",
                    source_id=response.id,
                    interview_session_id=bundle.interview_session_id,
                )
            ]
            sources.extend(
                CanonicalSourceReference(
                    source_type="CANDIDATE_TRANSCRIPT",
                    source_id=item.id,
                    interview_session_id=bundle.interview_session_id,
                    server_sequence=item.server_sequence,
                )
                for item in transcript_rows
            )
            node_id = stable_node_id("RESPONSE", response.id)
            stage = transcript_rows[0].stage if transcript_rows else None
            add_node(
                CounterMapNode(
                    node_id=node_id,
                    node_type="RESPONSE",
                    subtype="PROMPT_RESPONSE" if response.prompt_id else "SPONTANEOUS_RESPONSE",
                    canonical_sources=sources,
                    title="You answered" if response.prompt_id else "Your reasoning",
                    summary=_bounded(summary),
                    causal_rank=0,
                    stage=stage,
                    event_range=CounterMapEventRange(
                        start_sequence=response.start_sequence,
                        end_sequence=response.end_sequence,
                    ),
                    display_metadata=CounterMapDisplayMetadata(exact_quote=exact_quote),
                )
            )
            response_node_ids[response.id] = node_id
            for event_id in response.source_event_ids:
                event_node_ids[event_id] = node_id
            return node_id

        def ensure_snapshot(snapshot: CodeSnapshotSource, *, correction: bool = False) -> str:
            existing = snapshot_node_ids.get(snapshot.id)
            if existing:
                if correction:
                    existing_node = nodes[existing]
                    nodes[existing] = existing_node.model_copy(
                        update={
                            "subtype": "SELF_CORRECTION",
                            "title": "Corrected independently",
                        }
                    )
                return existing
            node_id = stable_node_id("CODE", snapshot.id)
            add_node(
                CounterMapNode(
                    node_id=node_id,
                    node_type="CODE",
                    subtype="SELF_CORRECTION" if correction else "DECISION",
                    canonical_sources=[
                        CanonicalSourceReference(
                            source_type="CODE_SNAPSHOT",
                            source_id=snapshot.id,
                            interview_session_id=bundle.interview_session_id,
                            server_sequence=snapshot.server_sequence,
                            version=snapshot.version,
                            content_hash=snapshot.content_hash,
                        ),
                        CanonicalSourceReference(
                            source_type="SESSION_EVENT",
                            source_id=snapshot.created_from_event_id,
                            interview_session_id=bundle.interview_session_id,
                            server_sequence=snapshot.server_sequence,
                        ),
                    ],
                    title="Corrected independently" if correction else "Your code",
                    summary=(
                        f"{snapshot.language.upper()} code snapshot v{snapshot.version}, "
                        "preserved from this moment."
                    ),
                    causal_rank=0,
                    stage=snapshot.stage,
                    event_range=CounterMapEventRange(
                        start_sequence=snapshot.server_sequence,
                        end_sequence=snapshot.server_sequence,
                    ),
                    display_metadata=CounterMapDisplayMetadata(
                        code_snapshot_id=snapshot.id,
                        code_version=snapshot.version,
                        content_hash=snapshot.content_hash,
                        language=snapshot.language,
                    ),
                    available_actions=[
                        CounterMapAvailableAction(
                            action="VIEW_SOURCE",
                            label="View code at this moment",
                            availability="DEFERRED",
                            reason="Exact code detail opens with later graph interactions.",
                        )
                    ],
                )
            )
            snapshot_node_ids[snapshot.id] = node_id
            event_node_ids[snapshot.created_from_event_id] = node_id
            return node_id

        def ensure_event_anchor(event_id: UUID) -> str | None:
            existing = event_node_ids.get(event_id)
            if existing:
                return existing
            response = response_by_event.get(event_id)
            if response:
                return ensure_response(response)
            claim = claims_by_event.get(event_id)
            if claim:
                node_id = stable_node_id("CLAIM", claim.id)
                exact_quote = bool(claim.verbatim_excerpt)
                add_node(
                    CounterMapNode(
                        node_id=node_id,
                        node_type="CLAIM",
                        subtype=claim.claim_type,
                        canonical_sources=[
                            CanonicalSourceReference(
                                source_type="CANDIDATE_CLAIM",
                                source_id=claim.id,
                                interview_session_id=bundle.interview_session_id,
                                server_sequence=claim.source_server_sequence,
                            ),
                            CanonicalSourceReference(
                                source_type="SESSION_EVENT",
                                source_id=claim.source_event_id,
                                interview_session_id=bundle.interview_session_id,
                                server_sequence=claim.source_server_sequence,
                            ),
                        ],
                        title="You said",
                        summary=_bounded(claim.verbatim_excerpt or claim.normalized_claim),
                        causal_rank=0,
                        stage=events[event_id].stage if event_id in events else None,
                        event_range=CounterMapEventRange(
                            start_sequence=claim.source_server_sequence,
                            end_sequence=claim.source_server_sequence,
                        ),
                        display_metadata=CounterMapDisplayMetadata(exact_quote=exact_quote),
                    )
                )
                event_node_ids[event_id] = node_id
                return node_id
            snapshot = snapshot_by_event.get(event_id)
            if snapshot:
                return ensure_snapshot(snapshot)
            execution = executions_by_event.get(event_id)
            if execution:
                node_id = stable_node_id("TEST", execution.id)
                add_node(
                    CounterMapNode(
                        node_id=node_id,
                        node_type="TEST",
                        subtype="VISIBLE_RUN",
                        canonical_sources=[
                            CanonicalSourceReference(
                                source_type="EXECUTION",
                                source_id=execution.id,
                                interview_session_id=bundle.interview_session_id,
                                server_sequence=execution.server_sequence,
                            ),
                            CanonicalSourceReference(
                                source_type="SESSION_EVENT",
                                source_id=execution.run_event_id,
                                interview_session_id=bundle.interview_session_id,
                                server_sequence=execution.server_sequence,
                            ),
                        ],
                        title="You tested it",
                        summary=_execution_summary(
                            execution.status,
                            execution.visible_passed,
                            execution.visible_failed,
                        ),
                        causal_rank=0,
                        stage=events[event_id].stage if event_id in events else None,
                        event_range=CounterMapEventRange(
                            start_sequence=execution.server_sequence,
                            end_sequence=execution.server_sequence,
                        ),
                        display_metadata=CounterMapDisplayMetadata(
                            execution_status=execution.status,
                            visible_passed=execution.visible_passed,
                            visible_failed=execution.visible_failed,
                            language=execution.language,
                        ),
                    )
                )
                event_node_ids[event_id] = node_id
                return node_id
            transcript = transcripts_by_event.get(event_id)
            if transcript and transcript.speaker == "CANDIDATE":
                node_id = stable_node_id("REASONING", transcript.id)
                add_node(
                    CounterMapNode(
                        node_id=node_id,
                        node_type="REASONING",
                        subtype="CANDIDATE_REASONING",
                        canonical_sources=[
                            CanonicalSourceReference(
                                source_type="CANDIDATE_TRANSCRIPT",
                                source_id=transcript.id,
                                interview_session_id=bundle.interview_session_id,
                                server_sequence=transcript.server_sequence,
                            )
                        ],
                        title="Your reasoning",
                        summary=_bounded(transcript.text),
                        causal_rank=0,
                        stage=transcript.stage,
                        event_range=CounterMapEventRange(
                            start_sequence=transcript.server_sequence,
                            end_sequence=transcript.server_sequence,
                        ),
                        display_metadata=CounterMapDisplayMetadata(exact_quote=True),
                    )
                )
                event_node_ids[event_id] = node_id
                return node_id
            return None

        def add_edge(
            source_node_id: str,
            target_node_id: str,
            relationship: CounterMapRelationship,
            relationship_sources: list[CanonicalRelationshipSource],
        ) -> None:
            source_ids = [item.source_id for item in relationship_sources]
            edge_id = stable_edge_id(
                source_node_id,
                target_node_id,
                relationship,
                *source_ids,
            )
            edges[edge_id] = CounterMapEdge(
                edge_id=edge_id,
                from_node_id=source_node_id,
                to_node_id=target_node_id,
                relationship=relationship,
                canonical_relationship_sources=relationship_sources,
            )

        material_assistance = {
            delivery.id: evidence
            for delivery in bundle.deliveries
            if delivery.assistance_type is not None
            for evidence in bundle.evidence
            if _assistance_materially_targets(delivery, evidence)
        }

        for delivery in bundle.deliveries:
            decision = (
                decisions.get(delivery.examiner_decision_id)
                if delivery.examiner_decision_id is not None
                else None
            )
            if decision is not None and decision.status != "AUTHORIZED":
                continue
            if delivery.prompt_status in {"REJECTED", "STALE", "EXPIRED", "CANCELLED"}:
                continue
            if not _meaningful_delivery(delivery.actual_text):
                continue
            if delivery.assistance_type is not None:
                if bundle.mode != "COACH" or delivery.id not in material_assistance:
                    continue
                node_type: CounterMapNodeType = "ASSISTANCE"
                title = "Coach guidance"
                subtype = delivery.assistance_type
                assistance_label = _assistance_label(delivery.assistance_type)
            elif delivery.prompt_kind not in {"BASE_QUESTION", "CLARIFICATION", "PROBE"}:
                continue
            elif delivery.probe_strategy == "CONSTRAINT_MUTATION":
                node_type = "MUTATION"
                title = "Constraint change"
                subtype = "CONSTRAINT_CHANGE"
                assistance_label = None
            else:
                node_type = "QUESTION"
                title = "CounterQ asked"
                subtype = _question_subtype(delivery)
                assistance_label = None
            node_id = stable_node_id(node_type, delivery.id)
            target_anchor = _delivery_target_anchor(
                delivery,
                decision,
                claims_by_id=claims_by_id,
                snapshots_by_id=snapshots_by_id,
                ensure_event_anchor=ensure_event_anchor,
                ensure_snapshot=ensure_snapshot,
                event_node_ids=event_node_ids,
            )
            why = _why_question(nodes.get(target_anchor)) if target_anchor else None
            add_node(
                CounterMapNode(
                    node_id=node_id,
                    node_type=node_type,
                    subtype=subtype,
                    canonical_sources=[
                        CanonicalSourceReference(
                            source_type="DELIVERED_PROMPT",
                            source_id=delivery.id,
                            interview_session_id=bundle.interview_session_id,
                            server_sequence=delivery.server_sequence,
                        )
                    ],
                    title=title,
                    summary=delivery.actual_text,
                    causal_rank=0,
                    stage=delivery.stage,
                    event_range=CounterMapEventRange(
                        start_sequence=delivery.server_sequence,
                        end_sequence=delivery.server_sequence,
                    ),
                    display_metadata=CounterMapDisplayMetadata(
                        exact_quote=True,
                        delivery_state=delivery.delivery_state,
                        why=why,
                        assistance_label=assistance_label,
                    ),
                )
            )
            delivery_node_ids[delivery.id] = node_id
            event_node_ids[delivery.actual_event_id] = node_id
            if target_anchor:
                target_id = _target_identity(delivery, decision)
                add_edge(
                    target_anchor,
                    node_id,
                    "TRIGGERED",
                    [
                        CanonicalRelationshipSource(
                            source_type="PROMPT_TARGET",
                            source_id=delivery.prompt_id,
                            related_source_id=target_id,
                            interview_session_id=bundle.interview_session_id,
                            detail="STRUCTURED_DELIVERED_PROMPT_TARGET",
                        )
                    ],
                )

        deliveries_by_prompt = {item.prompt_id: item for item in bundle.deliveries}
        for response in bundle.responses:
            linked_delivery = (
                deliveries_by_prompt.get(response.prompt_id) if response.prompt_id else None
            )
            if linked_delivery is None or linked_delivery.id not in delivery_node_ids:
                continue
            response_node_id = ensure_response(response)
            add_edge(
                delivery_node_ids[linked_delivery.id],
                response_node_id,
                "ANSWERED_BY",
                [
                    CanonicalRelationshipSource(
                        source_type="RESPONSE_LINK",
                        source_id=linked_delivery.id,
                        related_source_id=response.id,
                        interview_session_id=bundle.interview_session_id,
                        detail="DELIVERED_PROMPT_RESPONSE_LINK",
                    )
                ],
            )

        for evidence in bundle.evidence:
            support_nodes: list[tuple[str, UUID, str]] = []
            if evidence.candidate_response_id in responses_by_id:
                response = responses_by_id[evidence.candidate_response_id]
                support_nodes.append(
                    (ensure_response(response), response.source_event_ids[0], "CANDIDATE_RESPONSE")
                )
            for link in evidence.source_links:
                anchor = ensure_event_anchor(link.event_id)
                if anchor is not None and all(anchor != existing[0] for existing in support_nodes):
                    support_nodes.append((anchor, link.event_id, link.source_role))
            if not support_nodes:
                continue
            evidence_node_id = stable_node_id("EVIDENCE", evidence.id)
            evidence_sequence = max(link.server_sequence for link in evidence.source_links)
            add_node(
                CounterMapNode(
                    node_id=evidence_node_id,
                    node_type="EVIDENCE",
                    subtype=evidence.polarity,
                    canonical_sources=[
                        CanonicalSourceReference(
                            source_type="EVIDENCE",
                            source_id=evidence.id,
                            interview_session_id=bundle.interview_session_id,
                            server_sequence=evidence_sequence,
                        )
                    ],
                    title=_evidence_title(evidence),
                    summary=evidence.finding,
                    causal_rank=0,
                    stage=_stage_for_evidence(evidence, events),
                    event_range=CounterMapEventRange(
                        start_sequence=min(link.server_sequence for link in evidence.source_links),
                        end_sequence=evidence_sequence,
                    ),
                    display_metadata=CounterMapDisplayMetadata(
                        polarity=evidence.polarity,
                        strength=evidence.strength,
                        independence_level=evidence.independence_level,
                    ),
                    available_actions=[
                        CounterMapAvailableAction(
                            action="DISPUTE_ASSESSMENT",
                            label="This assessment seems wrong",
                            availability="DEFERRED",
                            reason="Source-detail dispute handling is reserved for a later stage.",
                        )
                    ],
                )
            )
            evidence_node_ids[evidence.id] = evidence_node_id
            for support_node_id, source_event_id, role in support_nodes:
                add_edge(
                    support_node_id,
                    evidence_node_id,
                    "SUPPORTED",
                    [
                        CanonicalRelationshipSource(
                            source_type="EVIDENCE_SOURCE",
                            source_id=evidence.id,
                            related_source_id=source_event_id,
                            interview_session_id=bundle.interview_session_id,
                            detail=f"CANONICAL_{role}",
                        )
                    ],
                )

            correction_snapshots = _correction_snapshots(evidence, bundle, snapshots_by_id)
            if len(correction_snapshots) >= 2:
                before, after = correction_snapshots[-2:]
                before_node = ensure_snapshot(before)
                after_node = ensure_snapshot(after, correction=True)
                add_edge(
                    before_node,
                    after_node,
                    "CORRECTED_BY",
                    [
                        CanonicalRelationshipSource(
                            source_type="CORRECTION_EVIDENCE",
                            source_id=evidence.id,
                            related_source_id=evidence.originating_assessment_id,
                            interview_session_id=bundle.interview_session_id,
                            detail="VALIDATED_SELF_CORRECTION",
                        )
                    ],
                )

        for breakpoint in bundle.breakpoints:
            visible_links = [
                item for item in breakpoint.evidence_links if item.evidence_id in evidence_node_ids
            ]
            if not visible_links:
                continue
            node_id = stable_node_id("BREAKPOINT", breakpoint.id)
            relationship_names = sorted({item.relationship for item in visible_links})
            source_sequences = [
                max(link.server_sequence for link in evidence_by_id[item.evidence_id].source_links)
                for item in visible_links
            ]
            add_node(
                CounterMapNode(
                    node_id=node_id,
                    node_type="BREAKPOINT",
                    subtype="CANONICAL_BREAKPOINT",
                    canonical_sources=[
                        CanonicalSourceReference(
                            source_type="BREAKPOINT",
                            source_id=breakpoint.id,
                            interview_session_id=bundle.interview_session_id,
                        )
                    ],
                    title="Breakpoint",
                    summary=breakpoint.summary,
                    causal_rank=0,
                    stage=None,
                    event_range=CounterMapEventRange(
                        start_sequence=min(source_sequences),
                        end_sequence=max(source_sequences),
                    ),
                    display_metadata=CounterMapDisplayMetadata(
                        breakpoint_status=breakpoint.status,
                        breakpoint_severity=breakpoint.severity,
                        breakpoint_relationships=relationship_names,
                    ),
                    available_actions=[
                        CounterMapAvailableAction(
                            action="COUNTERQ_ME_AGAIN",
                            label="CounterQ me again",
                            availability="UNAVAILABLE",
                            reason="Retesting becomes available in a later stage.",
                        )
                    ],
                )
            )
            for breakpoint_link in visible_links:
                add_edge(
                    evidence_node_ids[breakpoint_link.evidence_id],
                    node_id,
                    "EXPOSED",
                    [
                        CanonicalRelationshipSource(
                            source_type="BREAKPOINT_EVIDENCE",
                            source_id=breakpoint.id,
                            related_source_id=breakpoint_link.evidence_id,
                            interview_session_id=bundle.interview_session_id,
                            detail=breakpoint_link.relationship,
                        )
                    ],
                )

        for delivery_id, evidence in material_assistance.items():
            assistance_node_id = delivery_node_ids.get(delivery_id)
            assisted_evidence_node_id = evidence_node_ids.get(evidence.id)
            if assistance_node_id is None or assisted_evidence_node_id is None:
                continue
            target_anchor = _preferred_evidence_anchor(
                evidence,
                response_node_ids,
                event_node_ids,
            )
            if target_anchor is None:
                continue
            delivery = next(item for item in bundle.deliveries if item.id == delivery_id)
            add_edge(
                assistance_node_id,
                target_anchor,
                "ASSISTED",
                [
                    CanonicalRelationshipSource(
                        source_type="ASSISTANCE_TARGET",
                        source_id=delivery.id,
                        related_source_id=evidence.id,
                        interview_session_id=bundle.interview_session_id,
                        detail="TARGET_MATCHED_ASSISTED_OUTCOME",
                    )
                ],
            )

        ranked_nodes = _assign_causal_ranks(nodes, edges)
        ordered_edges = sorted(
            edges.values(),
            key=lambda item: (
                ranked_nodes[item.from_node_id].causal_rank,
                ranked_nodes[item.to_node_id].causal_rank,
                item.edge_id,
            ),
        )
        ordered_nodes = sorted(
            ranked_nodes.values(),
            key=lambda item: (
                item.causal_rank,
                item.event_range.start_sequence
                if item.event_range
                else bundle.source_watermark + 1,
                item.node_id,
            ),
        )
        node_counts: Counter[str] = Counter(item.node_type for item in ordered_nodes)
        relationship_counts: Counter[str] = Counter(item.relationship for item in ordered_edges)
        return CounterMapGraph(
            schema_version=COUNTERMAP_SCHEMA_VERSION,
            generation_policy_version=COUNTERMAP_GENERATION_POLICY_VERSION,
            interview_session_id=bundle.interview_session_id,
            source_watermark=bundle.source_watermark,
            nodes=ordered_nodes,
            edges=ordered_edges,
            summary=CounterMapSummary(
                title="Your reasoning map",
                overview=_overview(node_counts),
                node_counts=dict(sorted(node_counts.items())),
                relationship_counts=dict(sorted(relationship_counts.items())),
            ),
        )


def _delivery_target_anchor(
    delivery: DeliverySource,
    decision: DecisionSource | None,
    *,
    claims_by_id: dict[UUID, ClaimSource],
    snapshots_by_id: dict[UUID, CodeSnapshotSource],
    ensure_event_anchor: Callable[[UUID], str | None],
    ensure_snapshot: Callable[..., str],
    event_node_ids: dict[UUID, str],
) -> str | None:
    target_claim_id = delivery.target_claim_id or (decision.target_claim_id if decision else None)
    target_event_id = delivery.target_event_id or (decision.target_event_id if decision else None)
    target_snapshot_id = delivery.source_code_snapshot_id or (
        decision.target_code_snapshot_id if decision else None
    )
    if target_claim_id is not None:
        claim = claims_by_id.get(target_claim_id)
        if claim is not None:
            return ensure_event_anchor(claim.source_event_id)
    if target_event_id is not None:
        return ensure_event_anchor(target_event_id)
    if target_snapshot_id is not None and target_snapshot_id in snapshots_by_id:
        return ensure_snapshot(snapshots_by_id[target_snapshot_id])
    if delivery.actual_event_id in event_node_ids:
        return event_node_ids[delivery.actual_event_id]
    return None


def _target_identity(delivery: DeliverySource, decision: DecisionSource | None) -> UUID:
    for value in (
        delivery.target_claim_id,
        delivery.target_event_id,
        delivery.source_code_snapshot_id,
        decision.target_claim_id if decision else None,
        decision.target_event_id if decision else None,
        decision.target_code_snapshot_id if decision else None,
    ):
        if value is not None:
            return value
    return delivery.id


def _assistance_materially_targets(
    delivery: DeliverySource,
    evidence: CanonicalEvidenceSource,
) -> bool:
    if evidence.independence_level not in ASSISTED_LEVELS:
        return False
    if max(link.server_sequence for link in evidence.source_links) <= delivery.server_sequence:
        return False
    concept_ids = {item.id for item in evidence.concept_targets}
    skill_ids = {item.id for item in evidence.skill_targets}
    explicit_target_match = (
        delivery.target_concept_id is not None and delivery.target_concept_id in concept_ids
    ) or (
        delivery.target_skill_dimension_id is not None
        and delivery.target_skill_dimension_id in skill_ids
    )
    event_target_match = delivery.target_event_id is not None and any(
        link.event_id == delivery.target_event_id for link in evidence.source_links
    )
    return explicit_target_match or event_target_match


def _preferred_evidence_anchor(
    evidence: CanonicalEvidenceSource,
    response_node_ids: dict[UUID, str],
    event_node_ids: dict[UUID, str],
) -> str | None:
    if evidence.candidate_response_id is not None:
        response = response_node_ids.get(evidence.candidate_response_id)
        if response is not None:
            return response
    for link in sorted(evidence.source_links, key=lambda item: item.server_sequence):
        if link.event_id in event_node_ids:
            return event_node_ids[link.event_id]
    return None


def _correction_snapshots(
    evidence: CanonicalEvidenceSource,
    bundle: CounterMapSourceBundle,
    snapshots_by_id: dict[UUID, CodeSnapshotSource],
) -> list[CodeSnapshotSource]:
    if not any(marker in evidence.finding.lower() for marker in CORRECTION_MARKERS):
        return []
    event_ids = {item.event_id for item in evidence.source_links}
    candidates = [item for item in bundle.code_snapshots if item.created_from_event_id in event_ids]
    if evidence.source_code_snapshot_id in snapshots_by_id:
        candidates.append(snapshots_by_id[evidence.source_code_snapshot_id])
    unique = {item.id: item for item in candidates}
    return sorted(
        unique.values(), key=lambda item: (item.server_sequence, item.version, str(item.id))
    )


def _assign_causal_ranks(
    nodes: dict[str, CounterMapNode],
    edges: dict[str, CounterMapEdge],
) -> dict[str, CounterMapNode]:
    inbound: dict[str, int] = {node_id: 0 for node_id in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges.values():
        if edge.from_node_id not in nodes or edge.to_node_id not in nodes:
            continue
        inbound[edge.to_node_id] += 1
        outgoing[edge.from_node_id].append(edge.to_node_id)

    def order_key(node_id: str) -> tuple[int, str]:
        event_range = nodes[node_id].event_range
        return (event_range.start_sequence if event_range else 10**18, node_id)

    queue = deque(
        sorted((node_id for node_id, value in inbound.items() if value == 0), key=order_key)
    )
    ranks = {node_id: 0 for node_id in nodes}
    visited: list[str] = []
    while queue:
        node_id = queue.popleft()
        visited.append(node_id)
        for target in sorted(outgoing[node_id], key=order_key):
            ranks[target] = max(ranks[target], ranks[node_id] + 1)
            inbound[target] -= 1
            if inbound[target] == 0:
                queue.append(target)
    if len(visited) != len(nodes):
        return nodes
    return {
        node_id: node.model_copy(update={"causal_rank": ranks[node_id]})
        for node_id, node in nodes.items()
    }


def _stage_for_evidence(
    evidence: CanonicalEvidenceSource,
    events: dict[UUID, EventSource],
) -> str | None:
    latest = max(evidence.source_links, key=lambda item: item.server_sequence)
    event = events.get(latest.event_id)
    return event.stage if event is not None else None


def _why_question(target: CounterMapNode | None) -> str | None:
    if target is None:
        return None
    labels = {
        "CLAIM": "what you said",
        "REASONING": "your reasoning",
        "CODE": "the code at that moment",
        "TEST": "that test result",
        "RESPONSE": "your preceding answer",
        "EVIDENCE": "what the session had established",
        "BREAKPOINT": "that breakpoint",
    }
    return f"CounterQ asked this in response to {labels.get(target.node_type, 'that moment')}."


def _question_subtype(delivery: DeliverySource) -> str:
    if delivery.prompt_kind == "CLARIFICATION":
        return "CLARIFICATION"
    if delivery.prompt_kind == "PROBE":
        return "CHALLENGE"
    return "QUESTION"


def _evidence_title(evidence: CanonicalEvidenceSource) -> str:
    if evidence.polarity == "POSITIVE" and evidence.strength == "STRONG":
        return "Strong demonstration"
    if evidence.polarity == "POSITIVE":
        return "What this showed"
    if evidence.polarity == "MIXED":
        return "Mixed evidence"
    return "Needs work"


def _assistance_label(assistance_type: str) -> str:
    return {
        "METACOGNITIVE": "Reflection prompt",
        "PROBLEM_NARROWING": "Problem-narrowing guidance",
        "CONCEPTUAL_HINT": "Conceptual hint",
        "STRUCTURAL_HINT": "Structural hint",
        "DIRECT_TEACHING": "Direct explanation",
        "DEBUGGING_HINT": "Debugging hint",
        "CORRECTNESS_FEEDBACK": "Correctness feedback",
    }.get(assistance_type, "Coach guidance")


def _meaningful_delivery(value: str) -> bool:
    normalized = " ".join(value.split())
    return len(normalized) >= 4 and any(character.isalnum() for character in normalized)


def _execution_summary(status: str, passed: int, failed: int) -> str:
    if passed or failed:
        return f"Visible checks: {passed} passed and {failed} failed. Run status: {status.lower()}."
    return f"Run status: {status.lower()}."


def _overview(counts: Counter[str]) -> str:
    if not counts:
        return "No material evidence-backed moments were available for this interview."
    evidence = counts.get("EVIDENCE", 0)
    breakpoints = counts.get("BREAKPOINT", 0)
    return (
        f"{evidence} evidence-backed moment{'s' if evidence != 1 else ''} and "
        f"{breakpoints} breakpoint{'s' if breakpoints != 1 else ''} trace how the "
        "interview developed."
    )


def _bounded(value: str) -> str:
    return " ".join(value.split())[:700]
