import { describe, expect, it } from "vitest";

import { layoutCounterMapGraph } from "@/features/countermap/counterMapLayout";
import {
  candidateNodeTypes,
  counterqNodeTypes,
  evaluationNodeTypes,
  eyebrowForNode,
  familyForNode,
  relationshipLabel,
  shortRelationshipLabel,
  type CounterMapEdge,
  type CounterMapNode,
} from "@/features/countermap/counterMapPresentation";
import { counterMapUiSamples } from "./counterMapUiSamples";

describe("CounterMap deterministic graph presentation", () => {
  it("lays out equivalent graphs deterministically regardless of input ordering", () => {
    const graph = counterMapUiSamples[0];
    const shuffled = {
      ...graph,
      nodes: [...graph.nodes].reverse(),
      edges: [...graph.edges].reverse(),
    };

    expect(layoutCounterMapGraph(shuffled)).toEqual(layoutCounterMapGraph(graph));
  });

  it("keeps causal ranks in left-to-right semantic order", () => {
    const graph = counterMapUiSamples[0];
    const positions = new Map(
      layoutCounterMapGraph(graph).nodes.map((node) => [node.id, node.position.x]),
    );

    for (const left of graph.nodes) {
      for (const right of graph.nodes) {
        if (left.causal_rank < right.causal_rank) {
          expect(positions.get(left.node_id)).toBeLessThan(positions.get(right.node_id) ?? 0);
        }
      }
    }
  });

  it("vertically separates parallel branches", () => {
    const graph = counterMapUiSamples[0];
    const rankZero = layoutCounterMapGraph(graph).nodes.filter(
      (item) => item.source.causal_rank === 0,
    );

    expect(new Set(rankZero.map((item) => item.position.y)).size).toBe(rankZero.length);
  });

  it("passes every canonical edge through without adding or removing causality", () => {
    const graph = counterMapUiSamples[0];
    const layout = layoutCounterMapGraph(graph);

    expect(layout.edges).toEqual([...graph.edges]
      .sort((left, right) => left.edge_id.localeCompare(right.edge_id))
      .map((edge) => ({
        id: edge.edge_id,
        source: edge.from_node_id,
        target: edge.to_node_id,
        relationship: edge.relationship,
      })));
  });

  it("keeps positions outside the persisted CounterMapGraph contract", () => {
    const graph = counterMapUiSamples[0];
    const before = JSON.stringify(graph);

    layoutCounterMapGraph(graph);

    expect(JSON.stringify(graph)).toBe(before);
    expect(graph.nodes.every((node) => !("position" in node))).toBe(true);
  });

  it("supports every visible node type with a non-color semantic family", () => {
    const types = [...candidateNodeTypes, ...counterqNodeTypes, ...evaluationNodeTypes];

    expect(new Set(types)).toEqual(new Set([
      "CLAIM", "REASONING", "CODE", "TEST", "RESPONSE",
      "QUESTION", "MUTATION", "ASSISTANCE", "EVIDENCE", "BREAKPOINT",
    ]));
    expect(candidateNodeTypes.every((type) => familyForNode(type) === "candidate")).toBe(true);
    expect(counterqNodeTypes.every((type) => familyForNode(type) === "counterq")).toBe(true);
    expect(evaluationNodeTypes.every((type) => familyForNode(type) === "evaluation")).toBe(true);
  });

  it("supports candidate-facing copy for every relationship type", () => {
    const relationships: CounterMapEdge["relationship"][] = [
      "TRIGGERED", "ANSWERED_BY", "LED_TO", "SUPPORTED", "EXPOSED", "CORRECTED_BY", "ASSISTED",
    ];

    for (const relationship of relationships) {
      expect(relationshipLabel(relationship)).not.toMatch(/_/);
      expect(shortRelationshipLabel(relationship)).not.toMatch(/_/);
    }
  });

  it("does not visually upgrade an ordinary correction to self-correction", () => {
    const source = counterMapUiSamples[2].nodes.find((node) => node.node_type === "CODE");
    if (!source) throw new Error("Integrity sample must include code");
    const ordinary = { ...source, subtype: "CORRECTION" } as CounterMapNode;
    const independent = { ...source, subtype: "SELF_CORRECTION" } as CounterMapNode;

    expect(eyebrowForNode(ordinary)).toBe("Updated code");
    expect(eyebrowForNode(independent)).toBe("Corrected independently");
  });

  it("gives strong independent positive Evidence first-class language", () => {
    const evidence = counterMapUiSamples[0].nodes.find((node) => (
      node.node_type === "EVIDENCE" && node.display_metadata?.polarity === "POSITIVE"
    ));
    if (!evidence) throw new Error("Simulation sample must include positive evidence");

    expect(eyebrowForNode(evidence)).toBe("Strong demonstration");
  });
});
