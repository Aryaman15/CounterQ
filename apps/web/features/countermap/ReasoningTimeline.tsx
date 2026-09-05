"use client";

import {
  ArrowDown,
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
import { useMemo } from "react";

import {
  eyebrowForNode,
  relationshipLabel,
  truthMarkers,
  whyLabel,
  type CounterMapEdge,
  type CounterMapGraph,
  type CounterMapNode,
} from "./counterMapPresentation";

export function ReasoningTimeline({
  graph,
  selectedNodeId = null,
  onSelectNode,
}: {
  graph: CounterMapGraph;
  selectedNodeId?: string | null;
  onSelectNode?: (node: CounterMapNode) => void;
}) {
  const nodesById = useMemo(
    () => new Map(graph.nodes.map((node) => [node.node_id, node])),
    [graph.nodes],
  );
  const incomingByNode = useMemo(() => {
    const result = new Map<string, CounterMapEdge[]>();
    for (const edge of graph.edges) {
      result.set(edge.to_node_id, [...(result.get(edge.to_node_id) ?? []), edge]);
    }
    return result;
  }, [graph.edges]);
  const ranks = useMemo(() => {
    const result = new Map<number, CounterMapNode[]>();
    for (const node of graph.nodes) {
      result.set(node.causal_rank, [...(result.get(node.causal_rank) ?? []), node]);
    }
    return [...result.entries()]
      .sort(([left], [right]) => left - right)
      .map(([rank, nodes]) => [rank, [...nodes].sort((left, right) => (
        (left.event_range?.start_sequence ?? Number.MAX_SAFE_INTEGER)
          - (right.event_range?.start_sequence ?? Number.MAX_SAFE_INTEGER)
        || left.node_id.localeCompare(right.node_id)
      ))] as const);
  }, [graph.nodes]);

  if (!graph.nodes.length) {
    return (
      <div className="countermap-empty">
        <ShieldCheck size={22} aria-hidden="true" />
        <h3>No material causal moments to map.</h3>
        <p>CounterQ kept this review small because the session did not establish enough linked evidence.</p>
      </div>
    );
  }

  return (
    <ol className="countermap-timeline" aria-label="Evidence-backed reasoning timeline">
      {ranks.map(([rank, nodes], rankIndex) => (
        <li className="countermap-rank" key={rank}>
          {rankIndex > 0 ? (
            <div className="countermap-rank-connector" aria-hidden="true">
              <ArrowDown size={15} />
            </div>
          ) : null}
          <div className="countermap-rank-label">
            <span>{String(rankIndex + 1).padStart(2, "0")}</span>
            <p>{nodes.length > 1 ? "Parallel branches" : "Causal step"}</p>
          </div>
          <div className={`countermap-rank-nodes${nodes.length > 1 ? " countermap-rank-branched" : ""}`}>
            {nodes.map((node) => (
              <TimelineNode
                key={node.node_id}
                node={node}
                incoming={incomingByNode.get(node.node_id) ?? []}
                nodesById={nodesById}
                selected={selectedNodeId === node.node_id}
                onSelect={onSelectNode}
              />
            ))}
          </div>
        </li>
      ))}
    </ol>
  );
}

function TimelineNode({
  node,
  incoming,
  nodesById,
  selected,
  onSelect,
}: {
  node: CounterMapNode;
  incoming: CounterMapEdge[];
  nodesById: Map<string, CounterMapNode>;
  selected: boolean;
  onSelect?: (node: CounterMapNode) => void;
}) {
  const Icon = iconForNode(node.node_type);
  const metadata = node.display_metadata;
  const markers = truthMarkers(node);
  return (
    <article
      className={`countermap-node countermap-node-${node.node_type.toLowerCase()}`}
      data-selected={selected ? "true" : "false"}
      data-subtype={node.subtype.toLowerCase()}
    >
      {incoming.length ? (
        <div className="countermap-incoming" aria-label="Causal connections">
          <GitBranch size={13} aria-hidden="true" />
          <span>
            {incoming.map((edge) => {
              const source = nodesById.get(edge.from_node_id);
              return `${source?.title ?? "Earlier moment"} ${relationshipLabel(edge.relationship, node.node_type)}`;
            }).join(" · ")}
          </span>
        </div>
      ) : null}
      <div className="countermap-node-heading">
        <span className="countermap-node-icon" aria-hidden="true"><Icon size={17} /></span>
        <div>
          <p>{eyebrowForNode(node)}</p>
          <h3>{node.title}</h3>
        </div>
      </div>
      {metadata?.exact_quote
        ? <blockquote>{node.summary}</blockquote>
        : <p className="countermap-node-summary">{node.summary}</p>}
      {markers.length ? <p className="countermap-truth-line">{markers.join(" · ")}</p> : null}
      {metadata?.why ? (
        <details className="countermap-why">
          <summary>{whyLabel(node.node_type)}</summary>
          <p>{metadata.why}</p>
        </details>
      ) : null}
      {onSelect ? (
        <button
          type="button"
          className="countermap-open-node"
          aria-label={`${node.node_type === "CODE" ? "View code at this moment" : "Inspect this moment"}: ${node.title}`}
          onClick={() => onSelect(node)}
        >
          {node.node_type === "CODE" ? "View code at this moment" : "Inspect this moment"}
        </button>
      ) : null}
      {node.node_type === "BREAKPOINT" ? (
        <div className="countermap-actions" aria-label="Retest availability">
          <button type="button" disabled title="Retesting becomes available in a later stage.">
            CounterQ me again · Later
          </button>
        </div>
      ) : null}
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
