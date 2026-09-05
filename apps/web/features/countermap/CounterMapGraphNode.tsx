"use client";

import {
  Braces,
  CircleHelp,
  GitBranch,
  Lightbulb,
  MessageSquareText,
  ShieldCheck,
  TestTube2,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";

import {
  eyebrowForNode,
  familyForNode,
  nodeAccessibleLabel,
  truthMarkers,
  type CounterMapNode,
} from "./counterMapPresentation";

export type CounterMapFlowNodeData = Record<string, unknown> & {
  counterMapNode: CounterMapNode;
  isSelected: boolean;
  onSelect: (node: CounterMapNode) => void;
};

export type CounterMapFlowNode = Node<CounterMapFlowNodeData, "countermapNode">;

export function CounterMapGraphNode({ data }: NodeProps<CounterMapFlowNode>) {
  const node = data.counterMapNode;
  const Icon = iconForNode(node.node_type);
  const family = familyForNode(node.node_type);
  const markers = truthMarkers(node).slice(0, 2);
  const polarity = node.display_metadata?.polarity?.toLowerCase();
  const actionLabel = node.node_type === "CODE" ? "View code at this moment" : "Inspect this moment";

  return (
    <article
      className={`countermap-graph-node countermap-graph-node-${family}`}
      data-node-type={node.node_type.toLowerCase()}
      data-polarity={polarity}
      data-selected={data.isSelected ? "true" : "false"}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} aria-hidden="true" />
      <button
        type="button"
        className="countermap-graph-node-button"
        aria-label={`${nodeAccessibleLabel(node)}. ${actionLabel}`}
        aria-pressed={data.isSelected}
        onClick={(event) => {
          event.stopPropagation();
          data.onSelect(node);
        }}
      >
        <span className="countermap-graph-node-head">
          <span className="countermap-graph-node-icon" aria-hidden="true"><Icon size={15} /></span>
          <span>
            <small>{eyebrowForNode(node)}</small>
            <strong>{node.title}</strong>
          </span>
          <em>{family === "candidate" ? "YOU" : family === "counterq" ? "CQ" : "EVIDENCE"}</em>
        </span>
        <span className="countermap-graph-node-preview">{node.summary}</span>
        <span className="countermap-graph-node-foot">
          {markers.length ? markers.join(" · ") : actionLabel}
        </span>
      </button>
      <Handle type="source" position={Position.Right} isConnectable={false} aria-hidden="true" />
    </article>
  );
}

function iconForNode(nodeType: CounterMapNode["node_type"]): LucideIcon {
  return {
    CLAIM: MessageSquareText,
    REASONING: MessageSquareText,
    CODE: Braces,
    TEST: TestTube2,
    QUESTION: CircleHelp,
    RESPONSE: MessageSquareText,
    EVIDENCE: ShieldCheck,
    BREAKPOINT: TriangleAlert,
    ASSISTANCE: Lightbulb,
    MUTATION: GitBranch,
  }[nodeType];
}
