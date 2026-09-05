"use client";

import { Maximize2, Minus, Plus } from "lucide-react";
import {
  Background,
  BackgroundVariant,
  MarkerType,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type ReactFlowInstance,
} from "@xyflow/react";
import { useEffect, useMemo, useState } from "react";

import { layoutCounterMapGraph } from "./counterMapLayout";
import {
  CounterMapGraphNode,
  type CounterMapFlowNode,
} from "./CounterMapGraphNode";
import {
  nodeAccessibleLabel,
  shortRelationshipLabel,
  type CounterMapGraph as CounterMapGraphContract,
  type CounterMapNode,
} from "./counterMapPresentation";

const nodeTypes = { countermapNode: CounterMapGraphNode };

export function CounterMapGraph({
  graph,
  selectedNodeId,
  onSelectNode,
}: {
  graph: CounterMapGraphContract;
  selectedNodeId: string | null;
  onSelectNode: (node: CounterMapNode) => void;
}) {
  return (
    <ReactFlowProvider>
      <CounterMapGraphCanvas
        graph={graph}
        selectedNodeId={selectedNodeId}
        onSelectNode={onSelectNode}
      />
    </ReactFlowProvider>
  );
}

function CounterMapGraphCanvas({
  graph,
  selectedNodeId,
  onSelectNode,
}: {
  graph: CounterMapGraphContract;
  selectedNodeId: string | null;
  onSelectNode: (node: CounterMapNode) => void;
}) {
  const [instance, setInstance] = useState<ReactFlowInstance<CounterMapFlowNode, Edge> | null>(null);
  const layout = useMemo(() => layoutCounterMapGraph(graph), [graph]);
  const reducedMotion = useReducedMotion();
  const nodes = useMemo<CounterMapFlowNode[]>(
    () => layout.nodes.map((item) => ({
      id: item.id,
      type: "countermapNode",
      position: item.position,
      draggable: false,
      selectable: false,
      connectable: false,
      focusable: false,
      ariaLabel: nodeAccessibleLabel(item.source),
      data: {
        counterMapNode: item.source,
        isSelected: selectedNodeId === item.id,
        onSelect: onSelectNode,
      },
    })),
    [layout.nodes, onSelectNode, selectedNodeId],
  );
  const edges = useMemo<Edge[]>(
    () => layout.edges.map((item) => ({
      id: item.id,
      source: item.source,
      target: item.target,
      type: "smoothstep",
      label: shortRelationshipLabel(item.relationship),
      className: `countermap-graph-edge countermap-graph-edge-${item.relationship.toLowerCase()}`,
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
      focusable: false,
      selectable: false,
      animated: false,
    })),
    [layout.edges],
  );
  const fitAll = () => {
    void instance?.fitView({
      padding: 0.18,
      minZoom: 0.35,
      maxZoom: 0.95,
      duration: reducedMotion ? 0 : 280,
    });
  };

  const showCausalBeginning = () => {
    void instance?.setViewport(
      { x: 28, y: 28, zoom: 0.82 },
      { duration: reducedMotion ? 0 : 240 },
    );
  };

  useEffect(() => {
    if (!instance) return;
    // Begin at a legible scale and let the candidate pan through the causal path.
    // "Fit" remains available when seeing the full projection matters more than detail.
    const frame = window.requestAnimationFrame(showCausalBeginning);
    return () => window.cancelAnimationFrame(frame);
  // Fit only when a new graph/instance is mounted, not when a node is selected.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, instance]);

  return (
    <section className="countermap-graph-shell" aria-label="Interactive CounterMap graph">
      <div className="countermap-graph-legend" aria-label="Node visual families">
        <span data-family="candidate">Your moments</span>
        <span data-family="counterq">CounterQ</span>
        <span data-family="evaluation">What this showed</span>
      </div>
      <div className="countermap-graph-canvas" data-testid="countermap-graph-canvas">
        <ReactFlow<CounterMapFlowNode, Edge>
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onInit={setInstance}
          minZoom={0.35}
          maxZoom={1.5}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          onNodeClick={(_, node) => onSelectNode(node.data.counterMapNode)}
          panOnDrag
          panOnScroll={false}
          zoomOnPinch
          zoomOnScroll
          zoomOnDoubleClick={false}
          preventScrolling
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#2b3731" />
          <Panel position="top-right" className="countermap-viewport-controls">
            <button type="button" aria-label="Zoom in" onClick={() => instance?.zoomIn({ duration: reducedMotion ? 0 : 140 })}>
              <Plus size={15} aria-hidden="true" />
            </button>
            <button type="button" aria-label="Zoom out" onClick={() => instance?.zoomOut({ duration: reducedMotion ? 0 : 140 })}>
              <Minus size={15} aria-hidden="true" />
            </button>
            <button type="button" aria-label="Fit causal map" onClick={fitAll}>
              <Maximize2 size={15} aria-hidden="true" />
              <span>Fit</span>
            </button>
          </Panel>
        </ReactFlow>
      </div>
      <p className="countermap-graph-help">Drag to pan · Scroll or pinch to zoom · Select any moment for its sources</p>
    </section>
  );
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reduced;
}
