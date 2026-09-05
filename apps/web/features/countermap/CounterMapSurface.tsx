"use client";

import { GitBranch, ListTree } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { CounterMapDetailDrawer } from "./CounterMapDetailDrawer";
import { CounterMapGraph } from "./CounterMapGraph";
import { ReasoningTimeline } from "./ReasoningTimeline";
import type { CounterMapGraph as CounterMapGraphContract, CounterMapNode } from "./counterMapPresentation";

type CounterMapView = "GRAPH" | "TIMELINE";

export function CounterMapSurface({
  graph,
  detailUrlForNode,
}: {
  graph: CounterMapGraphContract;
  detailUrlForNode: (nodeId: string) => string;
}) {
  const [view, setView] = useState<CounterMapView>("GRAPH");
  const [selectedNode, setSelectedNode] = useState<CounterMapNode | null>(null);
  const userSelectedView = useRef(false);

  useEffect(() => {
    const query = window.matchMedia("(max-width: 720px)");
    if (!userSelectedView.current) setView(query.matches ? "TIMELINE" : "GRAPH");
  }, []);

  useEffect(() => setSelectedNode(null), [graph]);

  const selectNode = useCallback((node: CounterMapNode) => setSelectedNode(node), []);
  const closeDrawer = useCallback(() => setSelectedNode(null), []);
  const selectView = (next: CounterMapView) => {
    userSelectedView.current = true;
    setView(next);
  };

  if (!graph.nodes.length) {
    return <ReasoningTimeline graph={graph} />;
  }

  return (
    <div className="countermap-surface" data-view={view.toLowerCase()}>
      <div className="countermap-surface-toolbar">
        <div>
          <p>{graph.nodes.length} material moments</p>
          <span>{graph.edges.length} evidence-backed connections</span>
        </div>
        <div className="countermap-view-toggle" role="group" aria-label="CounterMap view">
          <button
            type="button"
            aria-pressed={view === "GRAPH"}
            onClick={() => selectView("GRAPH")}
          >
            <GitBranch size={15} aria-hidden="true" /> Graph
          </button>
          <button
            type="button"
            aria-pressed={view === "TIMELINE"}
            onClick={() => selectView("TIMELINE")}
          >
            <ListTree size={15} aria-hidden="true" /> Timeline
          </button>
        </div>
      </div>
      {view === "GRAPH" ? (
        <CounterMapGraph
          graph={graph}
          selectedNodeId={selectedNode?.node_id ?? null}
          onSelectNode={selectNode}
        />
      ) : (
        <ReasoningTimeline
          graph={graph}
          selectedNodeId={selectedNode?.node_id ?? null}
          onSelectNode={selectNode}
        />
      )}
      {selectedNode ? (
        <CounterMapDetailDrawer
          graph={graph}
          node={selectedNode}
          detailUrl={detailUrlForNode(selectedNode.node_id)}
          onClose={closeDrawer}
        />
      ) : null}
    </div>
  );
}
