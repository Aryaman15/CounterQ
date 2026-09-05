from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import pytest

from app.config.settings import Settings
from app.countermap.projector import CounterMapProjector
from app.countermap.routes import development_countermap_fixtures
from app.countermap.schema import CounterMapEdge, CounterMapGraph, stable_edge_id, stable_node_id
from app.countermap.validator import CounterMapValidationError, CounterMapValidator
from app.evals.countermap.corpus import CounterMapCorpusFixture, load_countermap_corpus


@dataclass(frozen=True)
class IntegrityCase:
    case_id: str
    check: Callable[[], None]


def _fixture(fixture_id: str) -> CounterMapCorpusFixture:
    return next(item for item in load_countermap_corpus() if item.fixture_id == fixture_id)


def _graph(fixture_id: str) -> tuple[CounterMapCorpusFixture, CounterMapGraph]:
    fixture = _fixture(fixture_id)
    graph = CounterMapProjector().project(fixture.bundle)
    CounterMapValidator().validate(bundle=fixture.bundle, graph=graph)
    return fixture, graph


def _has_node(graph: CounterMapGraph, node_type: str, subtype: str | None = None) -> bool:
    return any(
        item.node_type == node_type and (subtype is None or item.subtype == subtype)
        for item in graph.nodes
    )


def _has_edge(graph: CounterMapGraph, relationship: str) -> bool:
    return any(item.relationship == relationship for item in graph.edges)


def _assert_validation_category(
    fixture: CounterMapCorpusFixture,
    graph: CounterMapGraph,
    category: str,
) -> None:
    with pytest.raises(CounterMapValidationError) as captured:
        CounterMapValidator().validate(bundle=fixture.bundle, graph=graph)
    assert category in {item.category for item in captured.value.issues}


def _cases() -> list[IntegrityCase]:
    simulation, simulation_graph = _graph("simulation-success-and-misconception")
    coach, coach_graph = _graph("coach-assisted-improvement-open-breakpoint")
    integrity, integrity_graph = _graph("delivery-and-self-correction-integrity")

    def independence(level: str, graph: CounterMapGraph) -> None:
        assert any(
            node.node_type == "EVIDENCE" and node.display_metadata.independence_level == level
            for node in graph.nodes
        )

    def stale_or_rejected(status: str) -> None:
        decision = integrity.bundle.decisions[0].model_copy(update={"status": status})
        changed = integrity.bundle.model_copy(update={"decisions": [decision]})
        graph = CounterMapProjector().project(changed)
        assert not _has_node(graph, "QUESTION")

    def cancelled() -> None:
        delivery = integrity.bundle.deliveries[0].model_copy(update={"prompt_status": "CANCELLED"})
        changed = integrity.bundle.model_copy(update={"deliveries": [delivery]})
        assert not _has_node(CounterMapProjector().project(changed), "QUESTION")

    def authorized_but_undelivered() -> None:
        changed = simulation.bundle.model_copy(update={"deliveries": []})
        graph = CounterMapProjector().project(changed)
        assert not _has_node(graph, "QUESTION")
        assert all(
            source.source_type != "DELIVERED_PROMPT"
            for node in graph.nodes
            for source in node.canonical_sources
        )

    def invalidated_exclusion() -> None:
        changed = simulation.bundle.model_copy(update={"evidence": simulation.bundle.evidence[:2]})
        graph = CounterMapProjector().project(changed)
        ids = {
            source.source_id
            for node in graph.nodes
            for source in node.canonical_sources
            if source.source_type == "EVIDENCE"
        }
        assert simulation.bundle.evidence[2].id not in ids

    def changed_assistance_level(level: str) -> None:
        evidence = coach.bundle.evidence[1].model_copy(update={"independence_level": level})
        changed = coach.bundle.model_copy(update={"evidence": [coach.bundle.evidence[0], evidence]})
        graph = CounterMapProjector().project(changed)
        independence(level, graph)
        assert _has_node(graph, "ASSISTANCE")

    def after_probe_not_assistance() -> None:
        independence("AFTER_PROBE", simulation_graph)
        assert not _has_node(simulation_graph, "ASSISTANCE")

    def unscoped_assistance() -> None:
        delivery = coach.bundle.deliveries[0].model_copy(
            update={
                "target_event_id": None,
                "target_concept_id": None,
                "target_skill_dimension_id": None,
            }
        )
        changed = coach.bundle.model_copy(update={"deliveries": [delivery]})
        assert not _has_node(CounterMapProjector().project(changed), "ASSISTANCE")

    def self_correction_without_prompt() -> None:
        changed = integrity.bundle.model_copy(
            update={"deliveries": [], "decisions": [], "transcripts": []}
        )
        graph = CounterMapProjector().project(changed)
        assert _has_edge(graph, "CORRECTED_BY")
        assert not _has_node(graph, "QUESTION")

    def correction_not_from_diff() -> None:
        evidence = integrity.bundle.evidence[0].model_copy(
            update={
                "finding": "Two code versions were observed.",
                "candidate_response_id": None,
            }
        )
        changed = integrity.bundle.model_copy(update={"evidence": [evidence]})
        assert not _has_edge(CounterMapProjector().project(changed), "CORRECTED_BY")

    def correction_ignores_free_text(marker: str) -> None:
        evidence = integrity.bundle.evidence[0].model_copy(
            update={"finding": marker, "candidate_response_id": None}
        )
        changed = integrity.bundle.model_copy(update={"evidence": [evidence]})
        assert not _has_edge(CounterMapProjector().project(changed), "CORRECTED_BY")

    def correction_uses_structured_response_not_finding() -> None:
        evidence = integrity.bundle.evidence[0].model_copy(
            update={"finding": "Two code moments belong to one independently evidenced response."}
        )
        changed = integrity.bundle.model_copy(update={"evidence": [evidence]})
        graph = CounterMapProjector().project(changed)
        CounterMapValidator().validate(bundle=changed, graph=graph)
        assert _has_edge(graph, "CORRECTED_BY")

    def ambiguous_three_action_correction_is_omitted() -> None:
        response = integrity.bundle.responses[0].model_copy(
            update={
                "source_event_ids": [
                    *integrity.bundle.responses[0].source_event_ids,
                    integrity.bundle.events[2].id,
                ],
                "end_sequence": 3,
            }
        )
        evidence = integrity.bundle.evidence[0].model_copy(
            update={
                "source_links": [
                    *integrity.bundle.evidence[0].source_links,
                    integrity.bundle.evidence[0]
                    .source_links[0]
                    .model_copy(
                        update={
                            "event_id": integrity.bundle.events[2].id,
                            "server_sequence": 3,
                        }
                    ),
                ]
            }
        )
        changed = integrity.bundle.model_copy(
            update={"responses": [response], "evidence": [evidence]}
        )
        assert not _has_edge(CounterMapProjector().project(changed), "CORRECTED_BY")

    def correction_bundle(
        independence_level: str,
        *,
        prompt_bound: bool = False,
        assistance_linked: bool = False,
    ) -> CounterMapCorpusFixture:
        response = integrity.bundle.responses[0]
        deliveries = integrity.bundle.deliveries
        mode = integrity.bundle.mode
        if prompt_bound or assistance_linked:
            response = response.model_copy(
                update={"prompt_id": integrity.bundle.deliveries[0].prompt_id}
            )
        if assistance_linked:
            mode = "COACH"
            deliveries = [
                integrity.bundle.deliveries[0].model_copy(
                    update={
                        "prompt_kind": "INSTRUCTION",
                        "prompt_origin": "SYSTEM",
                        "probe_strategy": None,
                        "examiner_decision_id": None,
                        "assistance_type": "CONCEPTUAL_HINT",
                        "hint_level": "CONCEPTUAL_HINT",
                        "target_event_id": integrity.bundle.events[0].id,
                        "server_sequence": 1,
                    }
                )
            ]
        evidence = integrity.bundle.evidence[0].model_copy(
            update={"independence_level": independence_level}
        )
        bundle = integrity.bundle.model_copy(
            update={
                "mode": mode,
                "responses": [response],
                "deliveries": deliveries,
                "evidence": [evidence],
            }
        )
        return CounterMapCorpusFixture(
            fixture_id=f"correction-{independence_level.lower()}",
            label="Correction",
            description="Structured correction subtype test fixture.",
            bundle=bundle,
        )

    def assert_correction_subtype(
        level: str,
        expected: str,
        *,
        prompt_bound: bool = False,
        assistance_linked: bool = False,
    ) -> None:
        fixture = correction_bundle(
            level,
            prompt_bound=prompt_bound,
            assistance_linked=assistance_linked,
        )
        graph = CounterMapProjector().project(fixture.bundle)
        CounterMapValidator().validate(bundle=fixture.bundle, graph=graph)
        correction = next(
            node
            for node in graph.nodes
            if node.node_type == "CODE" and node.subtype in {"SELF_CORRECTION", "CORRECTION"}
        )
        assert correction.subtype == expected
        assert (correction.title == "Corrected independently") is (expected == "SELF_CORRECTION")

    def validator_rejects_assisted_self_correction() -> None:
        fixture = correction_bundle("AFTER_LIGHT_GUIDANCE", assistance_linked=True)
        graph = CounterMapProjector().project(fixture.bundle)
        correction = next(
            node
            for node in graph.nodes
            if node.node_type == "CODE" and node.subtype == "CORRECTION"
        )
        corrupted = correction.model_copy(
            update={"subtype": "SELF_CORRECTION", "title": "Corrected independently"}
        )
        changed = graph.model_copy(
            update={
                "nodes": [
                    corrupted if node.node_id == correction.node_id else node
                    for node in graph.nodes
                ]
            }
        )
        _assert_validation_category(fixture, changed, "CORRECTION_INDEPENDENCE")

    def multi_claim_fixture(*, reverse_claims: bool = False) -> CounterMapCorpusFixture:
        first_claim = simulation.bundle.claims[0]
        second_claim = first_claim.model_copy(
            update={
                "id": UUID("7a000000-0000-4000-8000-000000000201"),
                "claim_type": "INVARIANT",
                "normalized_claim": "Insert only after checking the complement.",
                "verbatim_excerpt": None,
            }
        )
        target_response = simulation.bundle.responses[0].model_copy(
            update={
                "id": UUID("7a000000-0000-4000-8000-000000000202"),
                "prompt_id": None,
                "source_event_ids": [first_claim.source_event_id],
                "start_sequence": 1,
                "end_sequence": 1,
            }
        )
        second_decision = simulation.bundle.decisions[0].model_copy(
            update={
                "id": UUID("7a000000-0000-4000-8000-000000000203"),
                "target_claim_id": second_claim.id,
            }
        )
        second_delivery = simulation.bundle.deliveries[0].model_copy(
            update={
                "id": UUID("7a000000-0000-4000-8000-000000000204"),
                "prompt_id": UUID("7a000000-0000-4000-8000-000000000205"),
                "examiner_decision_id": second_decision.id,
                "target_claim_id": second_claim.id,
                "actual_transcript_segment_id": UUID("7a000000-0000-4000-8000-000000000206"),
                "actual_text": "Why must insertion happen after the check?",
                "intended_text": "Why must insertion happen after the check?",
            }
        )
        claims = [first_claim, second_claim]
        if reverse_claims:
            claims.reverse()
        bundle = simulation.bundle.model_copy(
            update={
                "claims": claims,
                "responses": [*simulation.bundle.responses, target_response],
                "decisions": [*simulation.bundle.decisions, second_decision],
                "deliveries": [*simulation.bundle.deliveries, second_delivery],
            }
        )
        return CounterMapCorpusFixture(
            fixture_id="multi-claim-same-event",
            label="Claims",
            description="Two distinct claims share one canonical event.",
            bundle=bundle,
        )

    def triggered_claim_bindings(graph: CounterMapGraph) -> dict[UUID, UUID]:
        nodes = {node.node_id: node for node in graph.nodes}
        result: dict[UUID, UUID] = {}
        for edge in graph.edges:
            if edge.relationship != "TRIGGERED":
                continue
            source = edge.canonical_relationship_sources[0]
            from_claim = next(
                (
                    item.source_id
                    for item in nodes[edge.from_node_id].canonical_sources
                    if item.source_type == "CANDIDATE_CLAIM"
                ),
                None,
            )
            if from_claim is not None and source.related_source_id is not None:
                result[source.related_source_id] = from_claim
        return result

    def exact_multi_claim_targets() -> None:
        fixture = multi_claim_fixture()
        graph = CounterMapProjector().project(fixture.bundle)
        CounterMapValidator().validate(bundle=fixture.bundle, graph=graph)
        expected = {claim.id: claim.id for claim in fixture.bundle.claims}
        assert triggered_claim_bindings(graph) == expected

    def swapped_multi_claim_targets_are_rejected() -> None:
        fixture = multi_claim_fixture()
        graph = CounterMapProjector().project(fixture.bundle)
        triggered = [edge for edge in graph.edges if edge.relationship == "TRIGGERED"]
        assert len(triggered) == 2
        swapped: list[CounterMapEdge] = []
        for edge, other in zip(triggered, reversed(triggered), strict=True):
            swapped.append(
                edge.model_copy(
                    update={
                        "from_node_id": other.from_node_id,
                        "edge_id": stable_edge_id(
                            other.from_node_id,
                            edge.to_node_id,
                            edge.relationship,
                            *(item.source_id for item in edge.canonical_relationship_sources),
                        ),
                    }
                )
            )
        changed = graph.model_copy(
            update={
                "edges": [
                    *[edge for edge in graph.edges if edge.relationship != "TRIGGERED"],
                    *swapped,
                ]
            }
        )
        _assert_validation_category(fixture, changed, "EDGE_ENDPOINT_BINDING")

    def multi_claim_order_is_deterministic() -> None:
        first = CounterMapProjector().project(multi_claim_fixture().bundle)
        second = CounterMapProjector().project(multi_claim_fixture(reverse_claims=True).bundle)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")

    def generic_multi_claim_event_uses_response() -> None:
        fixture = multi_claim_fixture()
        delivery = fixture.bundle.deliveries[0].model_copy(update={"target_claim_id": None})
        decision = fixture.bundle.decisions[0].model_copy(update={"target_claim_id": None})
        changed_bundle = fixture.bundle.model_copy(
            update={"deliveries": [delivery], "decisions": [decision]}
        )
        graph = CounterMapProjector().project(changed_bundle)
        CounterMapValidator().validate(bundle=changed_bundle, graph=graph)
        triggered = next(edge for edge in graph.edges if edge.relationship == "TRIGGERED")
        source_node = next(node for node in graph.nodes if node.node_id == triggered.from_node_id)
        assert source_node.node_type == "RESPONSE"

    def response_with_material_code() -> None:
        response = simulation.bundle.responses[0].model_copy(
            update={
                "source_event_ids": [
                    simulation.bundle.events[2].id,
                    simulation.bundle.events[3].id,
                ],
                "end_sequence": 4,
            }
        )
        evidence = simulation.bundle.evidence[0].model_copy(
            update={
                "source_links": [
                    simulation.bundle.evidence[0]
                    .source_links[0]
                    .model_copy(
                        update={
                            "event_id": simulation.bundle.events[3].id,
                            "server_sequence": 4,
                        }
                    )
                ],
                "source_code_snapshot_id": simulation.bundle.code_snapshots[0].id,
            }
        )
        later_event = simulation.bundle.events[0].model_copy(
            update={
                "id": UUID("7a000000-0000-4000-8000-000000000208"),
                "server_sequence": 7,
                "event_type": "CODE_SNAPSHOT_CREATED",
                "source": "NATIVE_EDITOR",
                "stage": "IMPLEMENTATION",
            }
        )
        later_snapshot = simulation.bundle.code_snapshots[0].model_copy(
            update={
                "id": UUID("7a000000-0000-4000-8000-000000000207"),
                "version": 2,
                "parent_snapshot_id": simulation.bundle.code_snapshots[0].id,
                "content_hash": "sha256:" + "2" * 64,
                "created_from_event_id": later_event.id,
                "server_sequence": 7,
            }
        )
        changed = simulation.bundle.model_copy(
            update={
                "source_watermark": 7,
                "events": [*simulation.bundle.events, later_event],
                "responses": [response],
                "evidence": [evidence, *simulation.bundle.evidence[1:]],
                "code_snapshots": [*simulation.bundle.code_snapshots, later_snapshot],
            }
        )
        graph = CounterMapProjector().project(changed)
        CounterMapValidator().validate(bundle=changed, graph=graph)
        evidence_node = next(
            node
            for node in graph.nodes
            if any(
                source.source_type == "EVIDENCE" and source.source_id == evidence.id
                for source in node.canonical_sources
            )
        )
        supported = next(
            edge
            for edge in graph.edges
            if edge.relationship == "SUPPORTED" and edge.to_node_id == evidence_node.node_id
        )
        code = next(node for node in graph.nodes if node.node_id == supported.from_node_id)
        assert code.node_type == "CODE"
        assert code.display_metadata.code_snapshot_id == simulation.bundle.code_snapshots[0].id
        assert code.display_metadata.code_version == 1
        assert (
            code.display_metadata.content_hash == simulation.bundle.code_snapshots[0].content_hash
        )
        assert _has_node(graph, "RESPONSE")
        assert all(
            node.display_metadata.code_snapshot_id != later_snapshot.id
            for node in graph.nodes
            if node.node_type == "CODE"
        )

    def routine_response_code_is_not_materialized() -> None:
        response = simulation.bundle.responses[0].model_copy(
            update={
                "source_event_ids": [
                    simulation.bundle.events[2].id,
                    simulation.bundle.events[3].id,
                ],
                "end_sequence": 4,
            }
        )
        evidence = simulation.bundle.evidence[0].model_copy(
            update={"source_code_snapshot_id": simulation.bundle.code_snapshots[0].id}
        )
        changed = simulation.bundle.model_copy(
            update={
                "responses": [response],
                "evidence": [evidence, *simulation.bundle.evidence[1:]],
            }
        )
        graph = CounterMapProjector().project(changed)
        CounterMapValidator().validate(bundle=changed, graph=graph)
        assert not _has_node(graph, "CODE")

    def meaningful_execution_resolves_to_test() -> None:
        evidence = simulation.bundle.evidence[2]
        evidence_event = evidence.source_links[0].event_id
        graph = CounterMapProjector().project(simulation.bundle)
        CounterMapValidator().validate(bundle=simulation.bundle, graph=graph)
        evidence_node = next(
            node
            for node in graph.nodes
            if any(
                source.source_type == "EVIDENCE" and source.source_id == evidence.id
                for source in node.canonical_sources
            )
        )
        supported = next(
            edge
            for edge in graph.edges
            if edge.relationship == "SUPPORTED"
            and edge.to_node_id == evidence_node.node_id
            and edge.canonical_relationship_sources[0].related_source_id == evidence_event
        )
        source_node = next(node for node in graph.nodes if node.node_id == supported.from_node_id)
        assert source_node.node_type == "TEST"
        assert any(
            source.source_type == "EXECUTION"
            and source.source_id == simulation.bundle.executions[0].id
            for source in source_node.canonical_sources
        )

    def exact_code_prompt_target() -> None:
        delivery = integrity.bundle.deliveries[0]
        triggered = next(edge for edge in integrity_graph.edges if edge.relationship == "TRIGGERED")
        source_node = next(
            node for node in integrity_graph.nodes if node.node_id == triggered.from_node_id
        )
        assert source_node.node_type == "CODE"
        assert source_node.display_metadata.code_snapshot_id == delivery.source_code_snapshot_id
        assert triggered.canonical_relationship_sources[0].related_source_id == (
            delivery.source_code_snapshot_id
        )

    def swapped_code_prompt_target_is_rejected() -> None:
        triggered = next(edge for edge in integrity_graph.edges if edge.relationship == "TRIGGERED")
        wrong_code = next(
            node
            for node in integrity_graph.nodes
            if node.node_type == "CODE" and node.node_id != triggered.from_node_id
        )
        changed_edge = triggered.model_copy(
            update={
                "from_node_id": wrong_code.node_id,
                "edge_id": stable_edge_id(
                    wrong_code.node_id,
                    triggered.to_node_id,
                    triggered.relationship,
                    *(item.source_id for item in triggered.canonical_relationship_sources),
                ),
            }
        )
        changed = integrity_graph.model_copy(
            update={
                "edges": [
                    changed_edge if edge.edge_id == triggered.edge_id else edge
                    for edge in integrity_graph.edges
                ]
            }
        )
        _assert_validation_category(integrity, changed, "EDGE_ENDPOINT_BINDING")

    def response_cannot_replace_execution_evidence_source() -> None:
        evidence = simulation.bundle.evidence[2]
        evidence_node = next(
            node
            for node in simulation_graph.nodes
            if any(
                source.source_type == "EVIDENCE" and source.source_id == evidence.id
                for source in node.canonical_sources
            )
        )
        supported = next(
            edge
            for edge in simulation_graph.edges
            if edge.relationship == "SUPPORTED" and edge.to_node_id == evidence_node.node_id
        )
        response_node = next(
            node for node in simulation_graph.nodes if node.node_type == "RESPONSE"
        )
        changed_edge = supported.model_copy(
            update={
                "from_node_id": response_node.node_id,
                "edge_id": stable_edge_id(
                    response_node.node_id,
                    supported.to_node_id,
                    supported.relationship,
                    *(item.source_id for item in supported.canonical_relationship_sources),
                ),
            }
        )
        changed = simulation_graph.model_copy(
            update={
                "edges": [
                    changed_edge if edge.edge_id == supported.edge_id else edge
                    for edge in simulation_graph.edges
                ]
            }
        )
        _assert_validation_category(simulation, changed, "EDGE_ENDPOINT_BINDING")

    def coach_has_only_assisted_outcome_semantics() -> None:
        assert _has_node(coach_graph, "ASSISTANCE")
        assert _has_edge(coach_graph, "ASSISTED")
        assert not _has_edge(coach_graph, "ANSWERED_BY")
        assert not _has_edge(coach_graph, "TRIGGERED")

    def assistance_requires_explicit_prompt_response_link() -> None:
        base_delivery = coach.bundle.deliveries[0]
        second_delivery = base_delivery.model_copy(
            update={
                "id": UUID("7a000000-0000-4000-8000-000000000111"),
                "prompt_id": UUID("7a000000-0000-4000-8000-000000000112"),
            }
        )
        ambiguous = coach.bundle.evidence[1].model_copy(
            update={
                "id": UUID("7a000000-0000-4000-8000-000000000113"),
                "candidate_response_id": None,
            }
        )
        changed = coach.bundle.model_copy(
            update={
                "deliveries": [base_delivery, second_delivery],
                "evidence": [*coach.bundle.evidence, ambiguous],
            }
        )
        graph = CounterMapProjector().project(changed)
        CounterMapValidator().validate(bundle=changed, graph=graph)
        assistance_ids = {
            source.source_id
            for node in graph.nodes
            if node.node_type == "ASSISTANCE"
            for source in node.canonical_sources
            if source.source_type == "DELIVERED_PROMPT"
        }
        assisted_evidence_ids = {
            source.related_source_id
            for edge in graph.edges
            if edge.relationship == "ASSISTED"
            for source in edge.canonical_relationship_sources
        }
        assert assistance_ids == {base_delivery.id}
        assert assisted_evidence_ids == {coach.bundle.evidence[1].id}

    def assistance_preserves_all_explicit_evidence_links() -> None:
        linked = coach.bundle.evidence[1]
        second_linked = linked.model_copy(
            update={"id": UUID("7a000000-0000-4000-8000-000000000114")}
        )
        changed = coach.bundle.model_copy(
            update={"evidence": [*coach.bundle.evidence, second_linked]}
        )
        graph = CounterMapProjector().project(changed)
        CounterMapValidator().validate(bundle=changed, graph=graph)
        assisted = next(item for item in graph.edges if item.relationship == "ASSISTED")
        assert {source.related_source_id for source in assisted.canonical_relationship_sources} == {
            linked.id,
            second_linked.id,
        }

    def endpoint_substitution_rejected(
        fixture: CounterMapCorpusFixture,
        graph: CounterMapGraph,
        relationship: str,
    ) -> None:
        edge = next(item for item in graph.edges if item.relationship == relationship)
        changed_edge = edge.model_copy(
            update={
                "from_node_id": edge.to_node_id,
                "to_node_id": edge.from_node_id,
                "edge_id": stable_edge_id(
                    edge.to_node_id,
                    edge.from_node_id,
                    edge.relationship,
                    *(item.source_id for item in edge.canonical_relationship_sources),
                ),
            }
        )
        changed = graph.model_copy(
            update={
                "edges": [
                    changed_edge if item.edge_id == edge.edge_id else item for item in graph.edges
                ]
            }
        )
        _assert_validation_category(fixture, changed, "EDGE_ENDPOINT_BINDING")

    def deterministic() -> None:
        second = CounterMapProjector().project(simulation.bundle)
        assert second.model_dump(mode="json") == simulation_graph.model_dump(mode="json")
        assert second.semantic_identity() == simulation_graph.semantic_identity()

    def duplicate_prompt_node_rejected() -> None:
        question = next(node for node in simulation_graph.nodes if node.node_type == "QUESTION")
        duplicate = question.model_copy(
            update={
                "node_id": stable_node_id("MUTATION", question.canonical_sources[0].source_id),
                "node_type": "MUTATION",
            }
        )
        changed = simulation_graph.model_copy(
            update={"nodes": [*simulation_graph.nodes, duplicate]}
        )
        _assert_validation_category(simulation, changed, "PROMPT_UNIQUENESS")

    def cycle_rejected() -> None:
        edge = simulation_graph.edges[0]
        reverse = edge.model_copy(
            update={
                "edge_id": stable_edge_id(
                    edge.to_node_id,
                    edge.from_node_id,
                    edge.relationship,
                    edge.canonical_relationship_sources[0].source_id,
                ),
                "from_node_id": edge.to_node_id,
                "to_node_id": edge.from_node_id,
            }
        )
        changed = simulation_graph.model_copy(update={"edges": [*simulation_graph.edges, reverse]})
        _assert_validation_category(simulation, changed, "CAUSAL_CYCLE")

    def dangling_rejected() -> None:
        edge = simulation_graph.edges[0]
        target = "cmn_000000000000000000000000"
        dangling = edge.model_copy(
            update={
                "edge_id": stable_edge_id(
                    edge.from_node_id,
                    target,
                    edge.relationship,
                    edge.canonical_relationship_sources[0].source_id,
                ),
                "to_node_id": target,
            }
        )
        changed = simulation_graph.model_copy(update={"edges": [*simulation_graph.edges, dangling]})
        _assert_validation_category(simulation, changed, "DANGLING_EDGE")

    def private_reasoning_rejected() -> None:
        evidence = next(node for node in simulation_graph.nodes if node.node_type == "EVIDENCE")
        changed_node = evidence.model_copy(
            update={"summary": "technical_rationale must stay hidden"}
        )
        changed = simulation_graph.model_copy(
            update={
                "nodes": [
                    changed_node if node.node_id == evidence.node_id else node
                    for node in simulation_graph.nodes
                ]
            }
        )
        _assert_validation_category(simulation, changed, "PRIVATE_REASONING")

    def invalid_never_admitted() -> None:
        evidence = next(node for node in simulation_graph.nodes if node.node_type == "EVIDENCE")
        changed_node = evidence.model_copy(update={"canonical_sources": []})
        changed = simulation_graph.model_copy(
            update={
                "nodes": [
                    changed_node if node.node_id == evidence.node_id else node
                    for node in simulation_graph.nodes
                ]
            }
        )
        _assert_validation_category(simulation, changed, "NODE_PROVENANCE")

    def generation_failure_isolation() -> None:
        before = simulation.bundle.model_dump(mode="json")
        private_reasoning_rejected()
        assert simulation.bundle.model_dump(mode="json") == before

    question = next(node for node in integrity_graph.nodes if node.node_type == "QUESTION")
    snapshot_nodes = [node for node in integrity_graph.nodes if node.node_type == "CODE"]
    breakpoint_node = next(node for node in coach_graph.nodes if node.node_type == "BREAKPOINT")
    assistance_edge = next(edge for edge in coach_graph.edges if edge.relationship == "ASSISTED")

    return [
        IntegrityCase(
            "meaningful-delivered-question",
            lambda: _expect(_has_node(simulation_graph, "QUESTION")),
        ),
        IntegrityCase(
            "proven-triggered-edge", lambda: _expect(_has_edge(simulation_graph, "TRIGGERED"))
        ),
        IntegrityCase(
            "proven-answered-by-edge", lambda: _expect(_has_edge(simulation_graph, "ANSWERED_BY"))
        ),
        IntegrityCase(
            "proven-supported-edge", lambda: _expect(_has_edge(simulation_graph, "SUPPORTED"))
        ),
        IntegrityCase(
            "proven-exposed-edge", lambda: _expect(_has_edge(simulation_graph, "EXPOSED"))
        ),
        IntegrityCase(
            "no-temporal-fallback-edge", lambda: _expect(not _has_edge(simulation_graph, "LED_TO"))
        ),
        IntegrityCase("stale-decision-excluded", lambda: stale_or_rejected("STALE")),
        IntegrityCase("rejected-decision-excluded", lambda: stale_or_rejected("REJECTED")),
        IntegrityCase("authorized-undelivered-excluded", authorized_but_undelivered),
        IntegrityCase("cancelled-prompt-excluded", cancelled),
        IntegrityCase(
            "interrupted-actual-wording", lambda: _expect(question.summary == "What invariant")
        ),
        IntegrityCase(
            "interrupted-hidden-suffix",
            lambda: _expect("moves backward" not in _serialized(integrity_graph)),
        ),
        IntegrityCase(
            "one-delivery-one-primary",
            lambda: _expect(
                sum(
                    node.node_type in {"QUESTION", "MUTATION", "ASSISTANCE"}
                    for node in integrity_graph.nodes
                )
                == 1
            ),
        ),
        IntegrityCase(
            "positive-evidence", lambda: _expect(_evidence_polarity(simulation_graph, "POSITIVE"))
        ),
        IntegrityCase(
            "negative-evidence", lambda: _expect(_evidence_polarity(simulation_graph, "NEGATIVE"))
        ),
        IntegrityCase(
            "mixed-evidence", lambda: _expect(_evidence_polarity(simulation_graph, "MIXED"))
        ),
        IntegrityCase("invalidated-evidence-excluded", invalidated_exclusion),
        IntegrityCase(
            "independent-preserved", lambda: independence("INDEPENDENT", simulation_graph)
        ),
        IntegrityCase(
            "after-probe-is-not-assistance",
            after_probe_not_assistance,
        ),
        IntegrityCase(
            "after-light-guidance", lambda: independence("AFTER_LIGHT_GUIDANCE", coach_graph)
        ),
        IntegrityCase("after-strong-hint", lambda: changed_assistance_level("AFTER_STRONG_HINT")),
        IntegrityCase("directly-taught", lambda: changed_assistance_level("DIRECTLY_TAUGHT")),
        IntegrityCase(
            "assistance-target-scoped",
            lambda: _expect(
                assistance_edge.canonical_relationship_sources[0].related_source_id
                == coach.bundle.evidence[1].id
            ),
        ),
        IntegrityCase("unscoped-assistance-excluded", unscoped_assistance),
        IntegrityCase(
            "assisted-open-breakpoint",
            lambda: _expect(breakpoint_node.display_metadata.breakpoint_status == "OPEN"),
        ),
        IntegrityCase("self-correction-no-imaginary-question", self_correction_without_prompt),
        IntegrityCase(
            "exact-historical-snapshot",
            lambda: _expect(
                {node.display_metadata.code_version for node in snapshot_nodes} == {1, 2}
            ),
        ),
        IntegrityCase(
            "later-code-does-not-rewrite-history",
            lambda: _expect(
                all(node.display_metadata.code_version != 5 for node in snapshot_nodes)
            ),
        ),
        IntegrityCase("correction-not-inferred-from-diff", correction_not_from_diff),
        IntegrityCase(
            "correction-finding-fixed-size-window-is-not-proof",
            lambda: correction_ignores_free_text("A fixed-size window was selected."),
        ),
        IntegrityCase(
            "correction-finding-revised-constraint-is-not-proof",
            lambda: correction_ignores_free_text("The revised constraint changes the bound."),
        ),
        IntegrityCase(
            "correction-finding-debugged-build-is-not-proof",
            lambda: correction_ignores_free_text("The debugged build configuration is stable."),
        ),
        IntegrityCase(
            "correction-finding-corrected-format-is-not-proof",
            lambda: correction_ignores_free_text("The corrected output formatting is readable."),
        ),
        IntegrityCase(
            "structured-correction-independent-of-finding",
            correction_uses_structured_response_not_finding,
        ),
        IntegrityCase(
            "ambiguous-three-action-correction-omitted",
            ambiguous_three_action_correction_is_omitted,
        ),
        IntegrityCase(
            "independent-spontaneous-correction-is-self-correction",
            lambda: assert_correction_subtype("INDEPENDENT", "SELF_CORRECTION"),
        ),
        IntegrityCase(
            "after-probe-correction-is-not-self-correction",
            lambda: assert_correction_subtype("AFTER_PROBE", "CORRECTION"),
        ),
        IntegrityCase(
            "after-light-guidance-correction-is-not-self-correction",
            lambda: assert_correction_subtype("AFTER_LIGHT_GUIDANCE", "CORRECTION"),
        ),
        IntegrityCase(
            "after-strong-hint-correction-is-not-self-correction",
            lambda: assert_correction_subtype("AFTER_STRONG_HINT", "CORRECTION"),
        ),
        IntegrityCase(
            "directly-taught-correction-is-not-self-correction",
            lambda: assert_correction_subtype("DIRECTLY_TAUGHT", "CORRECTION"),
        ),
        IntegrityCase(
            "prompt-bound-correction-is-not-independent",
            lambda: assert_correction_subtype("INDEPENDENT", "CORRECTION", prompt_bound=True),
        ),
        IntegrityCase(
            "assistance-linked-correction-is-not-independent",
            lambda: assert_correction_subtype(
                "AFTER_LIGHT_GUIDANCE", "CORRECTION", assistance_linked=True
            ),
        ),
        IntegrityCase(
            "validator-rejects-assisted-self-correction-label",
            validator_rejects_assisted_self_correction,
        ),
        IntegrityCase("same-event-claims-bind-exactly", exact_multi_claim_targets),
        IntegrityCase(
            "same-event-claim-endpoint-swap-rejected",
            swapped_multi_claim_targets_are_rejected,
        ),
        IntegrityCase(
            "same-event-claim-order-deterministic",
            multi_claim_order_is_deterministic,
        ),
        IntegrityCase(
            "generic-multi-claim-event-uses-broader-response",
            generic_multi_claim_event_uses_response,
        ),
        IntegrityCase(
            "response-material-code-keeps-exact-code-source",
            response_with_material_code,
        ),
        IntegrityCase(
            "routine-response-code-is-not-materialized",
            routine_response_code_is_not_materialized,
        ),
        IntegrityCase(
            "meaningful-execution-source-resolves-to-test",
            meaningful_execution_resolves_to_test,
        ),
        IntegrityCase("exact-code-prompt-target", exact_code_prompt_target),
        IntegrityCase(
            "code-prompt-target-substitution-rejected",
            swapped_code_prompt_target_is_rejected,
        ),
        IntegrityCase(
            "response-cannot-replace-execution-evidence-source",
            response_cannot_replace_execution_evidence_source,
        ),
        IntegrityCase(
            "coach-assistance-has-no-question-edges",
            coach_has_only_assisted_outcome_semantics,
        ),
        IntegrityCase(
            "assistance-explicit-link-avoids-last-wins",
            assistance_requires_explicit_prompt_response_link,
        ),
        IntegrityCase(
            "assistance-preserves-multiple-explicit-evidence-links",
            assistance_preserves_all_explicit_evidence_links,
        ),
        IntegrityCase(
            "triggered-endpoint-substitution-rejected",
            lambda: endpoint_substitution_rejected(simulation, simulation_graph, "TRIGGERED"),
        ),
        IntegrityCase(
            "answered-by-endpoint-substitution-rejected",
            lambda: endpoint_substitution_rejected(simulation, simulation_graph, "ANSWERED_BY"),
        ),
        IntegrityCase(
            "supported-endpoint-substitution-rejected",
            lambda: endpoint_substitution_rejected(simulation, simulation_graph, "SUPPORTED"),
        ),
        IntegrityCase(
            "exposed-endpoint-substitution-rejected",
            lambda: endpoint_substitution_rejected(simulation, simulation_graph, "EXPOSED"),
        ),
        IntegrityCase(
            "assisted-endpoint-substitution-rejected",
            lambda: endpoint_substitution_rejected(coach, coach_graph, "ASSISTED"),
        ),
        IntegrityCase(
            "corrected-by-endpoint-substitution-rejected",
            lambda: endpoint_substitution_rejected(integrity, integrity_graph, "CORRECTED_BY"),
        ),
        IntegrityCase(
            "breakpoint-relationships-preserved",
            lambda: _expect(
                set(breakpoint_node.display_metadata.breakpoint_relationships)
                == {"CREATED", "RESOLUTION_SUPPORT"}
            ),
        ),
        IntegrityCase("deterministic-regeneration", deterministic),
        IntegrityCase("duplicate-primary-rejected", duplicate_prompt_node_rejected),
        IntegrityCase("generation-failure-preserves-canonical-input", generation_failure_isolation),
        IntegrityCase(
            "derived-products-remain-independent",
            lambda: _expect("session_report" not in _serialized(simulation_graph).lower()),
        ),
        IntegrityCase("invalid-projection-never-admitted", invalid_never_admitted),
        IntegrityCase("cycle-validation", cycle_rejected),
        IntegrityCase("dangling-validation", dangling_rejected),
        IntegrityCase("private-reasoning-excluded", private_reasoning_rejected),
    ]


CASES = _cases()


def test_stage7_corpus_expands_the_original_fifty_three_integrity_cases() -> None:
    assert len(CASES) == 71


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.case_id)
def test_stage7_countermap_integrity_case(case: IntegrityCase) -> None:
    case.check()


@pytest.mark.parametrize("fixture", load_countermap_corpus(), ids=lambda item: item.fixture_id)
def test_stage7_fixtures_use_production_projection_and_validator(
    fixture: CounterMapCorpusFixture,
) -> None:
    graph = CounterMapProjector().project(fixture.bundle)
    CounterMapValidator().validate(bundle=fixture.bundle, graph=graph)
    assert graph.interview_session_id == fixture.bundle.interview_session_id
    assert graph.source_watermark == fixture.bundle.source_watermark


async def test_development_fixture_api_runs_the_production_projector_and_validator() -> None:
    responses = await development_countermap_fixtures(Settings(app_env="test"))

    assert {item.fixture_id for item in responses} == {
        "simulation-success-and-misconception",
        "coach-assisted-improvement-open-breakpoint",
        "delivery-and-self-correction-integrity",
    }
    assert all(
        item.graph.generation_policy_version == "countermap-projector.v3" for item in responses
    )


def _expect(value: object) -> None:
    assert value


def _evidence_polarity(graph: CounterMapGraph, polarity: str) -> bool:
    return any(
        node.node_type == "EVIDENCE" and node.display_metadata.polarity == polarity
        for node in graph.nodes
    )


def _serialized(graph: CounterMapGraph) -> str:
    return str(graph.model_dump(mode="json"))
