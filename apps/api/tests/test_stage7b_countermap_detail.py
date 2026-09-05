from __future__ import annotations

from uuid import UUID

import pytest

from app.config.settings import Settings
from app.countermap.detail import (
    CounterMapNodeNotFound,
    assemble_candidate_detail,
    attach_development_source,
)
from app.countermap.development_fixtures import (
    DevelopmentCounterMapFixture,
    development_source_code,
    load_development_countermap_fixtures,
)
from app.countermap.projector import CounterMapProjector
from app.countermap.routes import development_countermap_node_detail
from app.countermap.schema import CounterMapGraph


def _fixture(fixture_id: str) -> DevelopmentCounterMapFixture:
    return next(
        item for item in load_development_countermap_fixtures() if item.fixture_id == fixture_id
    )


def _graph(fixture_id: str) -> tuple[DevelopmentCounterMapFixture, CounterMapGraph]:
    fixture = _fixture(fixture_id)
    return fixture, CounterMapProjector().project(fixture.bundle)


async def test_development_detail_uses_the_production_projected_graph() -> None:
    fixture, graph = _graph("simulation-success-and-misconception")
    question = next(item for item in graph.nodes if item.node_type == "QUESTION")

    response = await development_countermap_node_detail(
        fixture.fixture_id,
        question.node_id,
        Settings(app_env="test"),
    )

    assert response.node_id == question.node_id
    assert response.delivered_prompt is not None
    assert response.delivered_prompt.text == question.summary
    assert response.delivered_prompt.why == question.display_metadata.why


async def test_interrupted_detail_never_serializes_intended_suffix_or_private_rationale() -> None:
    fixture, graph = _graph("delivery-and-self-correction-integrity")
    question = next(item for item in graph.nodes if item.node_type == "QUESTION")

    response = await development_countermap_node_detail(
        fixture.fixture_id,
        question.node_id,
        Settings(app_env="test"),
    )
    serialized = response.model_dump_json()

    assert response.delivered_prompt is not None
    assert response.delivered_prompt.text == "What invariant"
    assert response.delivered_prompt.delivery_state == "INTERRUPTED"
    assert "moves backward" not in serialized
    assert "intended_text" not in serialized
    assert "technical_rationale" not in serialized
    assert "ExaminerDecision" not in serialized


async def test_exact_historical_snapshot_v2_wins_when_v5_exists() -> None:
    fixture, graph = _graph("delivery-and-self-correction-integrity")
    assert {item.version for item in fixture.bundle.code_snapshots} == {1, 2, 5}
    code = next(
        item
        for item in graph.nodes
        if item.node_type == "CODE" and item.display_metadata.code_version == 2
    )

    response = await development_countermap_node_detail(
        fixture.fixture_id,
        code.node_id,
        Settings(app_env="test"),
    )

    assert response.source_status == "AVAILABLE"
    assert response.code is not None
    assert response.code.version == 2
    assert "left = max(left, last[char] + 1)" in response.code.source_code
    assert "last.get(char, -1)" not in response.code.source_code


def test_code_hash_or_version_mismatch_fails_closed() -> None:
    fixture, graph = _graph("delivery-and-self-correction-integrity")
    code = next(item for item in graph.nodes if item.node_type == "CODE")
    mismatched = code.model_copy(
        update={
            "display_metadata": code.display_metadata.model_copy(
                update={"content_hash": "sha256:" + "0" * 64}
            )
        }
    )
    detail = assemble_candidate_detail(node=mismatched, bundle=fixture.bundle)

    response = attach_development_source(
        detail=detail,
        node=mismatched,
        bundle=fixture.bundle,
        source_code_for_version=lambda version: development_source_code(
            fixture.fixture_id, version
        ),
    )

    assert response.source_status == "UNAVAILABLE"
    assert response.code is None
    assert response.message is not None
    assert "no code is shown" in response.message


def test_cross_session_source_identity_is_rejected() -> None:
    fixture, graph = _graph("simulation-success-and-misconception")
    node = graph.nodes[0]
    foreign_session = UUID("7b000000-0000-4000-8000-000000000099")
    foreign = node.model_copy(
        update={
            "canonical_sources": [
                item.model_copy(update={"interview_session_id": foreign_session})
                for item in node.canonical_sources
            ]
        }
    )

    with pytest.raises(CounterMapNodeNotFound):
        assemble_candidate_detail(node=foreign, bundle=fixture.bundle)


async def test_test_detail_contains_no_hidden_test_material() -> None:
    fixture, graph = _graph("simulation-success-and-misconception")
    test_node = next(item for item in graph.nodes if item.node_type == "TEST")

    response = await development_countermap_node_detail(
        fixture.fixture_id,
        test_node.node_id,
        Settings(app_env="test"),
    )
    serialized = response.model_dump_json()

    assert response.execution is not None
    assert response.execution.visible_passed == 3
    assert response.execution.visible_failed == 0
    assert "hidden" not in serialized.lower()


async def test_coach_detail_preserves_assistance_and_open_verification_truth() -> None:
    fixture, graph = _graph("coach-assisted-improvement-open-breakpoint")
    evidence = next(
        item
        for item in graph.nodes
        if item.node_type == "EVIDENCE" and item.display_metadata.polarity == "POSITIVE"
    )
    breakpoint = next(item for item in graph.nodes if item.node_type == "BREAKPOINT")

    evidence_detail = await development_countermap_node_detail(
        fixture.fixture_id,
        evidence.node_id,
        Settings(app_env="test"),
    )
    breakpoint_detail = await development_countermap_node_detail(
        fixture.fixture_id,
        breakpoint.node_id,
        Settings(app_env="test"),
    )

    assert evidence_detail.evidence is not None
    assert evidence_detail.evidence.independence_level == "AFTER_LIGHT_GUIDANCE"
    assert breakpoint_detail.breakpoint is not None
    assert breakpoint_detail.breakpoint.status == "OPEN"
    assert breakpoint_detail.breakpoint.independent_verification_required is True
