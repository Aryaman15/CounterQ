from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from app.countermap.projector import CounterMapProjector
from app.countermap.schema import CounterMapGraph, stable_edge_id, stable_node_id
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
            update={"finding": "Two code versions were observed."}
        )
        changed = integrity.bundle.model_copy(update={"evidence": [evidence]})
        assert not _has_edge(CounterMapProjector().project(changed), "CORRECTED_BY")

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
        IntegrityCase(
            "authorized-undelivered-excluded", authorized_but_undelivered
        ),
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
                all(node.display_metadata.code_version != 3 for node in snapshot_nodes)
            ),
        ),
        IntegrityCase("correction-not-inferred-from-diff", correction_not_from_diff),
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


def test_stage7_corpus_has_at_least_twenty_six_integrity_cases() -> None:
    assert len(CASES) == 38


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


def _expect(value: object) -> None:
    assert value


def _evidence_polarity(graph: CounterMapGraph, polarity: str) -> bool:
    return any(
        node.node_type == "EVIDENCE" and node.display_metadata.polarity == polarity
        for node in graph.nodes
    )


def _serialized(graph: CounterMapGraph) -> str:
    return str(graph.model_dump(mode="json"))
