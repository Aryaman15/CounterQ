import dagre from "@dagrejs/dagre";

import type { CounterMapGraph, CounterMapNode } from "./counterMapPresentation";

export const COUNTERMAP_NODE_WIDTH = 264;
export const COUNTERMAP_NODE_HEIGHT = 152;
export const COUNTERMAP_RANK_GAP = 116;

export type CounterMapLayoutNode = {
  id: string;
  position: { x: number; y: number };
  source: CounterMapNode;
};

export type CounterMapLayoutEdge = {
  id: string;
  source: string;
  target: string;
  relationship: CounterMapGraph["edges"][number]["relationship"];
};

export function layoutCounterMapGraph(graph: CounterMapGraph): {
  nodes: CounterMapLayoutNode[];
  edges: CounterMapLayoutEdge[];
} {
  const layout = new dagre.graphlib.Graph({ directed: true, multigraph: true, compound: false });
  layout.setDefaultEdgeLabel(() => ({}));
  layout.setGraph({
    rankdir: "LR",
    ranker: "network-simplex",
    ranksep: COUNTERMAP_RANK_GAP,
    nodesep: 42,
    edgesep: 18,
    marginx: 32,
    marginy: 34,
  });

  const orderedNodes = [...graph.nodes].sort(compareSemanticOrder);
  for (const node of orderedNodes) {
    layout.setNode(node.node_id, {
      width: COUNTERMAP_NODE_WIDTH,
      height: COUNTERMAP_NODE_HEIGHT,
    });
  }
  const orderedEdges = [...graph.edges].sort((left, right) => left.edge_id.localeCompare(right.edge_id));
  for (const edge of orderedEdges) {
    layout.setEdge(edge.from_node_id, edge.to_node_id, {}, edge.edge_id);
  }
  dagre.layout(layout);

  const positions = new Map<string, { x: number; y: number }>();
  const rankGroups = new Map<number, CounterMapNode[]>();
  for (const node of orderedNodes) {
    rankGroups.set(node.causal_rank, [...(rankGroups.get(node.causal_rank) ?? []), node]);
  }
  const orderedRanks = [...rankGroups.keys()].sort((left, right) => left - right);
  for (const [rankIndex, rank] of orderedRanks.entries()) {
    const rankNodes = [...(rankGroups.get(rank) ?? [])].sort((left, right) => {
      const leftY = layout.node(left.node_id)?.y ?? 0;
      const rightY = layout.node(right.node_id)?.y ?? 0;
      return leftY - rightY || compareSemanticOrder(left, right);
    });
    for (const node of rankNodes) {
      const point = layout.node(node.node_id);
      positions.set(node.node_id, {
        x: rankIndex * (COUNTERMAP_NODE_WIDTH + COUNTERMAP_RANK_GAP),
        y: (point?.y ?? COUNTERMAP_NODE_HEIGHT / 2) - COUNTERMAP_NODE_HEIGHT / 2,
      });
    }
  }

  return {
    nodes: orderedNodes.map((node) => ({
      id: node.node_id,
      position: positions.get(node.node_id) ?? { x: 0, y: 0 },
      source: node,
    })),
    edges: orderedEdges.map((edge) => ({
      id: edge.edge_id,
      source: edge.from_node_id,
      target: edge.to_node_id,
      relationship: edge.relationship,
    })),
  };
}

function compareSemanticOrder(left: CounterMapNode, right: CounterMapNode): number {
  return left.causal_rank - right.causal_rank
    || (left.event_range?.start_sequence ?? Number.MAX_SAFE_INTEGER)
      - (right.event_range?.start_sequence ?? Number.MAX_SAFE_INTEGER)
    || (left.event_range?.end_sequence ?? Number.MAX_SAFE_INTEGER)
      - (right.event_range?.end_sequence ?? Number.MAX_SAFE_INTEGER)
    || left.node_id.localeCompare(right.node_id);
}
