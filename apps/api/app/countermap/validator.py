"""Strict admission validator for candidate-visible CounterMap projections."""

from __future__ import annotations

import json
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from uuid import UUID

from app.countermap.schema import (
    CounterMapEdge,
    CounterMapGraph,
    CounterMapNode,
    stable_edge_id,
    stable_node_id,
)
from app.countermap.source import (
    CanonicalEvidenceSource,
    CounterMapSourceBundle,
    DecisionSource,
    DeliverySource,
    ResponseSource,
)

PROMPT_NODE_TYPES = {"QUESTION", "MUTATION", "ASSISTANCE"}
PRIVATE_VALUE_MARKERS = (
    "chain-of-thought",
    "chain of thought",
    "scratchpad",
    "technical_rationale",
    "examinerdecision",
    "probestrategy",
    "aiinvocation",
    "model confidence",
    "target rank",
)


@dataclass(frozen=True)
class CounterMapValidationIssue:
    category: str
    message: str


class CounterMapValidationError(ValueError):
    def __init__(self, issues: list[CounterMapValidationIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{item.category}: {item.message}" for item in issues))


class CounterMapValidator:
    def validate(self, *, bundle: CounterMapSourceBundle, graph: CounterMapGraph) -> None:
        issues: list[CounterMapValidationIssue] = []
        if graph.interview_session_id != bundle.interview_session_id:
            issues.append(_issue("SESSION_MISMATCH", "Graph belongs to another interview"))
        if graph.source_watermark != bundle.source_watermark:
            issues.append(
                _issue("SOURCE_WATERMARK_MISMATCH", "Graph does not use final source truth")
            )

        nodes = {item.node_id: item for item in graph.nodes}
        if len(nodes) != len(graph.nodes):
            issues.append(_issue("DUPLICATE_NODE", "Node identities must be unique"))
        edge_ids = {item.edge_id for item in graph.edges}
        if len(edge_ids) != len(graph.edges):
            issues.append(_issue("DUPLICATE_EDGE", "Edge identities must be unique"))

        for node in graph.nodes:
            issues.extend(self._validate_node(bundle, node))
        for edge in graph.edges:
            issues.extend(self._validate_edge(bundle, nodes, edge))
        issues.extend(_validate_correction_subtypes(bundle, nodes, graph.edges))

        deliveries: dict[UUID, list[CounterMapNode]] = defaultdict(list)
        for node in graph.nodes:
            for source in node.canonical_sources:
                if source.source_type == "DELIVERED_PROMPT":
                    deliveries[source.source_id].append(node)
        for delivery_id, prompt_nodes in deliveries.items():
            if len(prompt_nodes) > 1 or any(
                item.node_type not in PROMPT_NODE_TYPES for item in prompt_nodes
            ):
                issues.append(
                    _issue(
                        "PROMPT_UNIQUENESS",
                        f"Delivery {delivery_id} has more than one primary representation",
                    )
                )

        if _has_cycle(graph):
            issues.append(_issue("CAUSAL_CYCLE", "CounterMap causal edges must remain acyclic"))
        degrees = Counter[str]()
        for edge in graph.edges:
            degrees[edge.from_node_id] += 1
            degrees[edge.to_node_id] += 1
            source_rank = nodes.get(edge.from_node_id)
            target_rank = nodes.get(edge.to_node_id)
            if source_rank and target_rank and source_rank.causal_rank >= target_rank.causal_rank:
                issues.append(_issue("CAUSAL_RANK", "Every edge must advance the causal rank"))
        for node in graph.nodes:
            intentionally_standalone = node.node_type in {"QUESTION", "MUTATION"} or (
                node.node_type == "EVIDENCE" and node.display_metadata.polarity == "POSITIVE"
            )
            if len(graph.nodes) > 1 and degrees[node.node_id] == 0 and not intentionally_standalone:
                issues.append(_issue("ORPHAN_NODE", f"{node.node_id} has no grounded relationship"))

        expected_node_counts = dict(sorted(Counter(item.node_type for item in graph.nodes).items()))
        expected_edge_counts = dict(
            sorted(Counter(item.relationship for item in graph.edges).items())
        )
        if graph.summary.node_counts != expected_node_counts:
            issues.append(_issue("SUMMARY_COUNTS", "Node summary counts do not match the graph"))
        if graph.summary.relationship_counts != expected_edge_counts:
            issues.append(_issue("SUMMARY_COUNTS", "Edge summary counts do not match the graph"))

        serialized = json.dumps(graph.model_dump(mode="json"), sort_keys=True).lower()
        if any(marker in serialized for marker in PRIVATE_VALUE_MARKERS):
            issues.append(
                _issue("PRIVATE_REASONING", "Candidate projection contains private reasoning")
            )
        if issues:
            raise CounterMapValidationError(issues)

    def _validate_node(
        self,
        bundle: CounterMapSourceBundle,
        node: CounterMapNode,
    ) -> list[CounterMapValidationIssue]:
        issues: list[CounterMapValidationIssue] = []
        if not node.canonical_sources:
            return [_issue("NODE_PROVENANCE", "Visible node has no canonical source")]
        expected_id = stable_node_id(node.node_type, *_node_identity_sources(node))
        if node.node_id != expected_id:
            issues.append(_issue("UNSTABLE_NODE_ID", f"{node.node_id} is not source-derived"))
        for source in node.canonical_sources:
            if source.interview_session_id != bundle.interview_session_id:
                issues.append(_issue("SOURCE_SESSION_MISMATCH", "Node source belongs elsewhere"))
            if not _source_exists(bundle, source.source_type, source.source_id):
                issues.append(_issue("NODE_PROVENANCE", "Node source is not canonical input"))

        if node.node_type in PROMPT_NODE_TYPES:
            delivery = _node_delivery(bundle, node)
            if delivery is None:
                issues.append(_issue("DELIVERY_TRUTH", "Prompt node lacks actual delivery"))
            else:
                issues.extend(_validate_delivery_node(bundle, node, delivery))
        if node.node_type == "EVIDENCE":
            evidence_id = _source_id(node, "EVIDENCE")
            evidence = next((item for item in bundle.evidence if item.id == evidence_id), None)
            if evidence is None:
                issues.append(_issue("EVIDENCE_INVALID", "Evidence is not valid and active"))
            elif (
                node.summary != evidence.finding
                or node.subtype != evidence.polarity
                or node.display_metadata.polarity != evidence.polarity
                or node.display_metadata.strength != evidence.strength
                or node.display_metadata.independence_level != evidence.independence_level
            ):
                issues.append(
                    _issue("EVIDENCE_ATTRIBUTION", "Evidence metadata changed canonical truth")
                )
        if node.node_type == "BREAKPOINT":
            breakpoint_id = _source_id(node, "BREAKPOINT")
            breakpoint = next(
                (item for item in bundle.breakpoints if item.id == breakpoint_id), None
            )
            if breakpoint is None or not breakpoint.evidence_links:
                issues.append(_issue("BREAKPOINT_UNSUPPORTED", "Breakpoint lacks valid Evidence"))
            elif (
                node.summary != breakpoint.summary
                or node.display_metadata.breakpoint_status != breakpoint.status
                or node.display_metadata.breakpoint_severity != breakpoint.severity
            ):
                issues.append(
                    _issue("BREAKPOINT_ATTRIBUTION", "Breakpoint display changed canonical truth")
                )
            elif set(node.display_metadata.breakpoint_relationships) != {
                item.relationship for item in breakpoint.evidence_links
            }:
                issues.append(
                    _issue("BREAKPOINT_RELATIONSHIP", "Breakpoint relationships were collapsed")
                )
        if node.node_type == "CODE":
            snapshot_id = _source_id(node, "CODE_SNAPSHOT")
            snapshot = next(
                (item for item in bundle.code_snapshots if item.id == snapshot_id), None
            )
            metadata = node.display_metadata
            if (
                snapshot is None
                or metadata.code_snapshot_id != snapshot.id
                or metadata.code_version != snapshot.version
                or metadata.content_hash != snapshot.content_hash
            ):
                issues.append(
                    _issue("CODE_PROVENANCE", "Code node is not the exact historical snapshot")
                )
        if node.display_metadata.exact_quote:
            exact_values = _exact_source_values(bundle, node)
            if node.summary not in exact_values:
                issues.append(
                    _issue("QUOTE_INTEGRITY", "Exact quote differs from canonical wording")
                )
        return issues

    def _validate_edge(
        self,
        bundle: CounterMapSourceBundle,
        nodes: dict[str, CounterMapNode],
        edge: CounterMapEdge,
    ) -> list[CounterMapValidationIssue]:
        issues: list[CounterMapValidationIssue] = []
        if edge.from_node_id not in nodes or edge.to_node_id not in nodes:
            return [_issue("DANGLING_EDGE", "Edge endpoint does not exist")]
        if not edge.canonical_relationship_sources:
            return [_issue("EDGE_PROVENANCE", "Visible edge has no relationship source")]
        expected_id = stable_edge_id(
            edge.from_node_id,
            edge.to_node_id,
            edge.relationship,
            *(item.source_id for item in edge.canonical_relationship_sources),
        )
        if edge.edge_id != expected_id:
            issues.append(_issue("UNSTABLE_EDGE_ID", "Edge identity is not source-derived"))
        for source in edge.canonical_relationship_sources:
            if source.interview_session_id != bundle.interview_session_id:
                issues.append(_issue("EDGE_SESSION_MISMATCH", "Edge source belongs elsewhere"))
        if not _relationship_has_exact_endpoints(bundle, nodes, edge):
            issues.append(
                _issue(
                    "EDGE_ENDPOINT_BINDING",
                    f"{edge.relationship} endpoints do not resolve to its canonical relationship",
                )
            )
        if edge.relationship == "CORRECTED_BY" and not _valid_correction(bundle, nodes, edge):
            issues.append(
                _issue("CORRECTION_UNPROVEN", "A code change alone does not establish correction")
            )
        return issues


def _validate_delivery_node(
    bundle: CounterMapSourceBundle,
    node: CounterMapNode,
    delivery: DeliverySource,
) -> list[CounterMapValidationIssue]:
    issues: list[CounterMapValidationIssue] = []
    if node.summary != delivery.actual_text:
        issues.append(_issue("DELIVERY_TEXT", "Prompt node is not actual delivered wording"))
    expected_type = (
        "ASSISTANCE"
        if delivery.assistance_type is not None
        else "MUTATION"
        if delivery.probe_strategy == "CONSTRAINT_MUTATION"
        else "QUESTION"
    )
    if node.node_type != expected_type:
        issues.append(
            _issue("PROMPT_CLASSIFICATION", "Delivered turn has the wrong primary node type")
        )
    if delivery.prompt_status in {"REJECTED", "STALE", "EXPIRED", "CANCELLED"}:
        issues.append(_issue("STALE_PROMPT", "Rejected, stale, or cancelled prompt is visible"))
    if delivery.examiner_decision_id is not None:
        decision = next(
            (item for item in bundle.decisions if item.id == delivery.examiner_decision_id),
            None,
        )
        if decision is None or decision.status != "AUTHORIZED":
            issues.append(_issue("STALE_DECISION", "Prompt came from a non-authorized decision"))
    if delivery.delivery_state in {"PARTIALLY_DELIVERED", "INTERRUPTED"} and (
        node.summary != delivery.actual_text
        or node.summary == delivery.intended_text != delivery.actual_text
    ):
        issues.append(_issue("INTERRUPTION_LEAK", "Interrupted node leaks intended prompt text"))
    if node.node_type == "ASSISTANCE":
        matching = [
            item for item in bundle.evidence if _assistance_matches(bundle, delivery, item)
        ]
        if bundle.mode != "COACH" or delivery.assistance_type is None or not matching:
            issues.append(
                _issue("ASSISTANCE_UNSCOPED", "Assistance is not delivered and target-scoped")
            )
    elif delivery.assistance_type is not None:
        issues.append(_issue("PROMPT_UNIQUENESS", "Assistance delivery has another primary type"))
    return issues


def _relationship_has_exact_endpoints(
    bundle: CounterMapSourceBundle,
    nodes: dict[str, CounterMapNode],
    edge: CounterMapEdge,
) -> bool:
    from_node = nodes[edge.from_node_id]
    to_node = nodes[edge.to_node_id]
    sources = edge.canonical_relationship_sources
    if edge.relationship == "TRIGGERED":
        if len(sources) != 1 or from_node.node_type not in {
            "CLAIM",
            "REASONING",
            "CODE",
            "TEST",
            "RESPONSE",
        } or to_node.node_type not in {"QUESTION", "MUTATION"}:
            return False
        source = sources[0]
        delivery = next(
            (item for item in bundle.deliveries if item.prompt_id == source.source_id), None
        )
        if delivery is None or source.source_type != "PROMPT_TARGET":
            return False
        decision = next(
            (item for item in bundle.decisions if item.id == delivery.examiner_decision_id),
            None,
        )
        exact_target = _delivery_target(delivery, decision)
        return bool(
            exact_target is not None
            and source.related_source_id == exact_target
            and _source_id(to_node, "DELIVERED_PROMPT") == delivery.id
            and _node_resolves_target(bundle, from_node, exact_target)
        )
    if edge.relationship == "ANSWERED_BY":
        if len(sources) != 1 or from_node.node_type not in {
            "QUESTION",
            "MUTATION",
        } or to_node.node_type != "RESPONSE":
            return False
        source = sources[0]
        delivery = next((item for item in bundle.deliveries if item.id == source.source_id), None)
        response = next(
            (item for item in bundle.responses if item.id == source.related_source_id), None
        )
        return bool(
            source.source_type == "RESPONSE_LINK"
            and delivery
            and response
            and response.prompt_id == delivery.prompt_id
            and _source_id(from_node, "DELIVERED_PROMPT") == delivery.id
            and _source_id(to_node, "CANDIDATE_RESPONSE") == response.id
        )
    if edge.relationship == "SUPPORTED":
        if len(sources) != 1 or from_node.node_type not in {
            "CLAIM",
            "REASONING",
            "CODE",
            "TEST",
            "RESPONSE",
        } or to_node.node_type != "EVIDENCE":
            return False
        source = sources[0]
        evidence = next((item for item in bundle.evidence if item.id == source.source_id), None)
        return bool(
            source.source_type == "EVIDENCE_SOURCE"
            and evidence
            and source.related_source_id in {item.event_id for item in evidence.source_links}
            and _source_id(to_node, "EVIDENCE") == evidence.id
            and _node_resolves_evidence_source(
                bundle,
                from_node,
                evidence,
                source.related_source_id,
            )
        )
    if edge.relationship == "EXPOSED":
        if (
            len(sources) != 1
            or from_node.node_type != "EVIDENCE"
            or to_node.node_type != "BREAKPOINT"
        ):
            return False
        source = sources[0]
        breakpoint = next(
            (item for item in bundle.breakpoints if item.id == source.source_id), None
        )
        return bool(
            source.source_type == "BREAKPOINT_EVIDENCE"
            and breakpoint
            and source.related_source_id in {item.evidence_id for item in breakpoint.evidence_links}
            and _source_id(from_node, "EVIDENCE") == source.related_source_id
            and _source_id(to_node, "BREAKPOINT") == breakpoint.id
            and source.detail
            in {
                item.relationship
                for item in breakpoint.evidence_links
                if item.evidence_id == source.related_source_id
            }
        )
    if edge.relationship == "ASSISTED":
        if from_node.node_type != "ASSISTANCE" or to_node.node_type != "RESPONSE":
            return False
        delivery_id = _source_id(from_node, "DELIVERED_PROMPT")
        response_id = _source_id(to_node, "CANDIDATE_RESPONSE")
        delivery = next((item for item in bundle.deliveries if item.id == delivery_id), None)
        response = next((item for item in bundle.responses if item.id == response_id), None)
        return bool(
            delivery
            and response
            and response.prompt_id == delivery.prompt_id
            and all(
                _assisted_source_matches(
                    bundle,
                    source=source,
                    delivery=delivery,
                    response=response,
                )
                for source in sources
            )
        )
    if edge.relationship == "CORRECTED_BY":
        return len(sources) == 1 and _valid_correction(bundle, nodes, edge)
    if edge.relationship == "LED_TO":
        return len(sources) == 1 and _valid_event_causation(bundle, nodes, edge)
    return False


def _valid_correction(
    bundle: CounterMapSourceBundle,
    nodes: dict[str, CounterMapNode],
    edge: CounterMapEdge,
) -> bool:
    if len(edge.canonical_relationship_sources) != 1:
        return False
    source = edge.canonical_relationship_sources[0]
    evidence = next((item for item in bundle.evidence if item.id == source.source_id), None)
    response = next(
        (
            item
            for item in bundle.responses
            if evidence is not None and item.id == evidence.candidate_response_id
        ),
        None,
    )
    if (
        source.source_type != "CORRECTION_EVIDENCE"
        or evidence is None
        or response is None
        or source.related_source_id != response.id
        or nodes[edge.from_node_id].node_type != "CODE"
        or nodes[edge.to_node_id].node_type != "CODE"
    ):
        return False
    from_snapshot = _source_id(nodes[edge.from_node_id], "CODE_SNAPSHOT")
    to_snapshot = _source_id(nodes[edge.to_node_id], "CODE_SNAPSHOT")
    event_ids = {item.event_id for item in evidence.source_links}.intersection(
        response.source_event_ids
    )
    snapshots = {item.id: item for item in bundle.code_snapshots}
    supported_snapshots = {
        item.id for item in snapshots.values() if item.created_from_event_id in event_ids
    }
    before = snapshots.get(from_snapshot) if from_snapshot is not None else None
    after = snapshots.get(to_snapshot) if to_snapshot is not None else None
    return bool(
        before
        and after
        and from_snapshot != to_snapshot
        and len(supported_snapshots) == 2
        and {from_snapshot, to_snapshot} == supported_snapshots
        and before.server_sequence < after.server_sequence
    )


def _validate_correction_subtypes(
    bundle: CounterMapSourceBundle,
    nodes: dict[str, CounterMapNode],
    edges: list[CounterMapEdge],
) -> list[CounterMapValidationIssue]:
    issues: list[CounterMapValidationIssue] = []
    incoming: dict[str, list[CounterMapEdge]] = defaultdict(list)
    for edge in edges:
        if edge.relationship == "CORRECTED_BY" and edge.to_node_id in nodes:
            incoming[edge.to_node_id].append(edge)
    for node in nodes.values():
        correction_edges = incoming.get(node.node_id, [])
        if node.node_type != "CODE":
            continue
        if node.subtype == "SELF_CORRECTION" and not correction_edges:
            issues.append(
                _issue(
                    "CORRECTION_INDEPENDENCE",
                    "Self-correction label lacks structured correction provenance",
                )
            )
            continue
        if not correction_edges:
            continue
        independent = all(
            _independent_correction_edge(bundle, nodes, edge) for edge in correction_edges
        )
        expected_subtype = "SELF_CORRECTION" if independent else "CORRECTION"
        expected_title = "Corrected independently" if independent else "Updated code"
        if node.subtype != expected_subtype or node.title != expected_title:
            issues.append(
                _issue(
                    "CORRECTION_INDEPENDENCE",
                    "Correction subtype overstates or changes canonical independence",
                )
            )
    return issues


def _independent_correction_edge(
    bundle: CounterMapSourceBundle,
    nodes: dict[str, CounterMapNode],
    edge: CounterMapEdge,
) -> bool:
    if not _valid_correction(bundle, nodes, edge):
        return False
    source = edge.canonical_relationship_sources[0]
    evidence = next((item for item in bundle.evidence if item.id == source.source_id), None)
    response = next(
        (
            item
            for item in bundle.responses
            if evidence is not None and item.id == evidence.candidate_response_id
        ),
        None,
    )
    return bool(
        evidence
        and response
        and evidence.independence_level == "INDEPENDENT"
        and response.prompt_id is None
        and not any(
            delivery.assistance_type is not None
            and response.prompt_id == delivery.prompt_id
            for delivery in bundle.deliveries
        )
    )


def _valid_event_causation(
    bundle: CounterMapSourceBundle,
    nodes: dict[str, CounterMapNode],
    edge: CounterMapEdge,
) -> bool:
    source = edge.canonical_relationship_sources[0]
    if source.source_type != "EVENT_CAUSATION" or source.related_source_id is None:
        return False
    target = next((item for item in bundle.events if item.id == source.related_source_id), None)
    return bool(
        target
        and target.causation_id == source.source_id
        and source.source_id in _node_event_ids(bundle, nodes[edge.from_node_id])
        and source.related_source_id in _node_event_ids(bundle, nodes[edge.to_node_id])
    )


def _source_exists(bundle: CounterMapSourceBundle, source_type: str, source_id: UUID) -> bool:
    collections: dict[str, object] = {
        "SESSION_EVENT": bundle.events,
        "CANDIDATE_TRANSCRIPT": bundle.transcripts,
        "DELIVERED_PROMPT": bundle.deliveries,
        "CANDIDATE_CLAIM": bundle.claims,
        "CANDIDATE_RESPONSE": bundle.responses,
        "CODE_SNAPSHOT": bundle.code_snapshots,
        "CODE_DIFF": bundle.code_diffs,
        "EXECUTION": bundle.executions,
        "EVIDENCE": bundle.evidence,
        "BREAKPOINT": bundle.breakpoints,
    }
    values = collections.get(source_type)
    return isinstance(values, list) and any(
        getattr(item, "id", None) == source_id for item in values
    )


def _node_identity_sources(node: CounterMapNode) -> list[UUID]:
    primary_by_node = {
        "CLAIM": "CANDIDATE_CLAIM",
        "REASONING": "CANDIDATE_TRANSCRIPT",
        "CODE": "CODE_SNAPSHOT",
        "TEST": "EXECUTION",
        "QUESTION": "DELIVERED_PROMPT",
        "RESPONSE": "CANDIDATE_RESPONSE",
        "EVIDENCE": "EVIDENCE",
        "BREAKPOINT": "BREAKPOINT",
        "ASSISTANCE": "DELIVERED_PROMPT",
        "MUTATION": "DELIVERED_PROMPT",
    }
    source_type = primary_by_node[node.node_type]
    return [item.source_id for item in node.canonical_sources if item.source_type == source_type]


def _node_delivery(bundle: CounterMapSourceBundle, node: CounterMapNode) -> DeliverySource | None:
    delivery_id = _source_id(node, "DELIVERED_PROMPT")
    return next((item for item in bundle.deliveries if item.id == delivery_id), None)


def _source_id(node: CounterMapNode, source_type: str) -> UUID | None:
    return next(
        (item.source_id for item in node.canonical_sources if item.source_type == source_type),
        None,
    )


def _exact_source_values(bundle: CounterMapSourceBundle, node: CounterMapNode) -> set[str]:
    result: set[str] = set()
    for source in node.canonical_sources:
        if source.source_type == "CANDIDATE_TRANSCRIPT":
            result.update(item.text for item in bundle.transcripts if item.id == source.source_id)
        elif source.source_type == "DELIVERED_PROMPT":
            result.update(
                item.actual_text for item in bundle.deliveries if item.id == source.source_id
            )
        elif source.source_type == "CANDIDATE_CLAIM":
            result.update(
                item.verbatim_excerpt
                for item in bundle.claims
                if item.id == source.source_id and item.verbatim_excerpt
            )
    return result


def _delivery_target(delivery: DeliverySource, decision: DecisionSource | None) -> UUID | None:
    for value in (
        delivery.target_claim_id or (decision.target_claim_id if decision else None),
        delivery.source_code_snapshot_id
        or (decision.target_code_snapshot_id if decision else None),
        delivery.target_event_id or (decision.target_event_id if decision else None),
    ):
        if value is not None:
            return value
    return None


def _assistance_matches(
    bundle: CounterMapSourceBundle,
    delivery: DeliverySource,
    evidence: CanonicalEvidenceSource,
) -> bool:
    if evidence.candidate_response_id is None:
        return False
    response = next(
        (item for item in bundle.responses if item.id == evidence.candidate_response_id),
        None,
    )
    if delivery.assistance_type is None or evidence.independence_level not in {
        "AFTER_LIGHT_GUIDANCE",
        "AFTER_STRONG_HINT",
        "DIRECTLY_TAUGHT",
    }:
        return False
    if response is None or response.prompt_id != delivery.prompt_id:
        return False
    if not set(response.source_event_ids).intersection(
        item.event_id for item in evidence.source_links
    ):
        return False
    if max(item.server_sequence for item in evidence.source_links) <= delivery.server_sequence:
        return False
    concepts = {item.id for item in evidence.concept_targets}
    skills = {item.id for item in evidence.skill_targets}
    return bool(
        (delivery.target_concept_id and delivery.target_concept_id in concepts)
        or (delivery.target_skill_dimension_id and delivery.target_skill_dimension_id in skills)
        or (
            delivery.target_event_id
            and delivery.target_event_id in {item.event_id for item in evidence.source_links}
        )
    )


def _assisted_source_matches(
    bundle: CounterMapSourceBundle,
    *,
    source: object,
    delivery: DeliverySource,
    response: ResponseSource,
) -> bool:
    from app.countermap.schema import CanonicalRelationshipSource

    if not isinstance(source, CanonicalRelationshipSource):
        return False
    evidence = next(
        (item for item in bundle.evidence if item.id == source.related_source_id),
        None,
    )
    return bool(
        source.source_type == "ASSISTANCE_TARGET"
        and source.source_id == delivery.id
        and evidence
        and evidence.candidate_response_id == response.id
        and _assistance_matches(bundle, delivery, evidence)
    )


def _node_event_ids(bundle: CounterMapSourceBundle, node: CounterMapNode) -> set[UUID]:
    result = {
        source.source_id
        for source in node.canonical_sources
        if source.source_type == "SESSION_EVENT"
    }
    for source in node.canonical_sources:
        if source.source_type == "CANDIDATE_TRANSCRIPT":
            result.update(
                item.event_id
                for item in bundle.transcripts
                if item.id == source.source_id
            )
        elif source.source_type == "CANDIDATE_CLAIM":
            result.update(
                item.source_event_id for item in bundle.claims if item.id == source.source_id
            )
        elif source.source_type == "CANDIDATE_RESPONSE":
            result.update(
                event_id
                for item in bundle.responses
                if item.id == source.source_id
                for event_id in item.source_event_ids
            )
        elif source.source_type == "CODE_SNAPSHOT":
            result.update(
                item.created_from_event_id
                for item in bundle.code_snapshots
                if item.id == source.source_id
            )
        elif source.source_type == "EXECUTION":
            result.update(
                item.run_event_id for item in bundle.executions if item.id == source.source_id
            )
        elif source.source_type == "DELIVERED_PROMPT":
            result.update(
                item.actual_event_id for item in bundle.deliveries if item.id == source.source_id
            )
    return result


def _node_resolves_target(
    bundle: CounterMapSourceBundle,
    node: CounterMapNode,
    target_id: UUID | None,
) -> bool:
    if target_id is None:
        return False
    claim = next((item for item in bundle.claims if item.id == target_id), None)
    if claim is not None:
        return _source_id(node, "CANDIDATE_CLAIM") == claim.id
    snapshot = next((item for item in bundle.code_snapshots if item.id == target_id), None)
    if snapshot is not None:
        return _source_id(node, "CODE_SNAPSHOT") == snapshot.id
    return _node_resolves_event_anchor(bundle, node, target_id)


def _node_resolves_evidence_source(
    bundle: CounterMapSourceBundle,
    node: CounterMapNode,
    evidence: CanonicalEvidenceSource,
    event_id: UUID | None,
) -> bool:
    if event_id is None:
        return False
    response_id = evidence.candidate_response_id
    return _node_resolves_event_anchor(
        bundle,
        node,
        event_id,
        response_id=response_id,
    )


def _node_resolves_event_anchor(
    bundle: CounterMapSourceBundle,
    node: CounterMapNode,
    event_id: UUID,
    *,
    response_id: UUID | None = None,
) -> bool:
    snapshot = next(
        (item for item in bundle.code_snapshots if item.created_from_event_id == event_id),
        None,
    )
    if snapshot is not None:
        return _source_id(node, "CODE_SNAPSHOT") == snapshot.id
    execution = next((item for item in bundle.executions if item.run_event_id == event_id), None)
    if execution is not None:
        return _source_id(node, "EXECUTION") == execution.id
    if response_id is not None:
        response = next(
            (
                item
                for item in bundle.responses
                if item.id == response_id and event_id in item.source_event_ids
            ),
            None,
        )
        if response is not None:
            return _source_id(node, "CANDIDATE_RESPONSE") == response.id
    responses = [item for item in bundle.responses if event_id in item.source_event_ids]
    if len(responses) == 1:
        return _source_id(node, "CANDIDATE_RESPONSE") == responses[0].id
    claims = [item for item in bundle.claims if item.source_event_id == event_id]
    if len(claims) == 1:
        return _source_id(node, "CANDIDATE_CLAIM") == claims[0].id
    transcript = next(
        (
            item
            for item in bundle.transcripts
            if item.event_id == event_id and item.speaker == "CANDIDATE"
        ),
        None,
    )
    return bool(
        transcript and _source_id(node, "CANDIDATE_TRANSCRIPT") == transcript.id
    )


def _has_cycle(graph: CounterMapGraph) -> bool:
    inbound = {item.node_id: 0 for item in graph.nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if edge.from_node_id not in inbound or edge.to_node_id not in inbound:
            continue
        inbound[edge.to_node_id] += 1
        outgoing[edge.from_node_id].append(edge.to_node_id)
    queue = deque(item for item, count in inbound.items() if count == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for target in outgoing[current]:
            inbound[target] -= 1
            if inbound[target] == 0:
                queue.append(target)
    return visited != len(inbound)


def _issue(category: str, message: str) -> CounterMapValidationIssue:
    return CounterMapValidationIssue(category=category, message=message)
