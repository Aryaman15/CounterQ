"use client";

import type { components } from "@counterq/contracts/openapi";
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
} from "lucide-react";
import { useMemo } from "react";

type CounterMapGraph = components["schemas"]["CounterMapGraph"];
type CounterMapNode = components["schemas"]["CounterMapNode"];
type CounterMapEdge = components["schemas"]["CounterMapEdge"];
type CounterMapDisplayMetadata = NonNullable<CounterMapNode["display_metadata"]>;

type ReasoningTimelineProps = {
  graph: CounterMapGraph;
};

export function ReasoningTimeline({ graph }: ReasoningTimelineProps) {
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
    return [...result.entries()].sort(([left], [right]) => left - right);
  }, [graph.nodes]);

  if (!graph.nodes.length) {
    return (
      <div className="countermap-empty">
        <ShieldCheck size={22} aria-hidden="true" />
        <h3>No material causal moments to map.</h3>
        <p>CounterQ kept the projection small because this session did not establish enough linked evidence.</p>
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
}: {
  node: CounterMapNode;
  incoming: CounterMapEdge[];
  nodesById: Map<string, CounterMapNode>;
}) {
  const Icon = iconForNode(node.node_type);
  const meta: Partial<CounterMapDisplayMetadata> = node.display_metadata ?? {};
  const availableActions = node.available_actions ?? [];
  return (
    <article className={`countermap-node countermap-node-${node.node_type.toLowerCase()}`}>
      {incoming.length ? (
        <div className="countermap-incoming" aria-label="Causal connections">
          <GitBranch size={13} aria-hidden="true" />
          <span>
            {incoming.map((edge) => {
              const source = nodesById.get(edge.from_node_id);
              return `${source?.title ?? "Earlier moment"} ${relationshipLabel(edge, node)}`;
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
      {meta.exact_quote ? <blockquote>{node.summary}</blockquote> : <p className="countermap-node-summary">{node.summary}</p>}
      <NodeTruthLine node={node} />
      {meta.why ? (
        <details className="countermap-why">
          <summary>{whyLabel(node.node_type)}</summary>
          <p>{meta.why}</p>
        </details>
      ) : null}
      {availableActions.length ? (
        <div className="countermap-actions" aria-label="Available actions">
          {availableActions.map((action) => (
            <button
              type="button"
              key={action.action}
              disabled={action.availability !== "AVAILABLE"}
              title={action.reason ?? undefined}
            >
              {action.label}{action.availability === "AVAILABLE" ? "" : " · Later"}
            </button>
          ))}
        </div>
      ) : null}
    </article>
  );
}

function NodeTruthLine({ node }: { node: CounterMapNode }) {
  const meta: Partial<CounterMapDisplayMetadata> = node.display_metadata ?? {};
  const parts: string[] = [];
  if (meta.independence_level) parts.push(independenceLabel(meta.independence_level));
  if (meta.polarity) parts.push(polarityLabel(meta.polarity));
  if (meta.breakpoint_status) {
    parts.push(meta.breakpoint_status === "OPEN" ? "Still needs independent verification" : titleCase(meta.breakpoint_status));
  }
  if (meta.assistance_label) parts.push(meta.assistance_label);
  if (meta.code_version) parts.push(`Code snapshot v${meta.code_version}`);
  if (meta.delivery_state && meta.delivery_state !== "DELIVERED") parts.push("Only the delivered words are shown");
  if (!parts.length) return null;
  return <p className="countermap-truth-line">{parts.join(" · ")}</p>;
}

function iconForNode(nodeType: CounterMapNode["node_type"]) {
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

function eyebrowForNode(node: CounterMapNode): string {
  if (node.node_type === "EVIDENCE") {
    const metadata = node.display_metadata;
    if (
      metadata?.polarity === "POSITIVE"
      && metadata.strength === "STRONG"
      && metadata.independence_level === "INDEPENDENT"
    ) {
      return "Strong demonstration";
    }
    return polarityLabel(metadata?.polarity ?? "MIXED");
  }
  if (node.node_type === "CODE" && node.subtype === "SELF_CORRECTION") return "Independent correction";
  if (node.node_type === "RESPONSE" && node.subtype === "SPONTANEOUS_RESPONSE") return "Your reasoning";
  if (node.node_type === "ASSISTANCE") return "Coach intervention";
  if (node.node_type === "BREAKPOINT") return "Evidence-backed boundary";
  return {
    CLAIM: "You said",
    REASONING: "Your reasoning",
    CODE: "Your code",
    TEST: "You tested it",
    QUESTION: "CounterQ asked",
    RESPONSE: "You answered",
    MUTATION: "CounterQ changed the constraint",
  }[node.node_type] ?? "Interview moment";
}

function whyLabel(nodeType: CounterMapNode["node_type"]): string {
  const labels: Partial<Record<CounterMapNode["node_type"], string>> = {
    QUESTION: "Why this question?",
    MUTATION: "Why this constraint change?",
    ASSISTANCE: "Why this guidance?",
  };
  return labels[nodeType] ?? "Why this moment?";
}

function relationshipLabel(edge: CounterMapEdge, target: CounterMapNode): string {
  if (edge.relationship === "TRIGGERED") {
    if (target.node_type === "MUTATION") return "prompted this constraint change";
    if (target.node_type === "ASSISTANCE") return "led to this guidance";
    return "prompted this question";
  }
  return {
    ANSWERED_BY: "received this answer",
    LED_TO: "led to this moment",
    SUPPORTED: "supported this evidence",
    EXPOSED: "shaped this breakpoint",
    CORRECTED_BY: "was corrected by this change",
    ASSISTED: "helped shape this response",
  }[edge.relationship];
}

function polarityLabel(value: "POSITIVE" | "NEGATIVE" | "MIXED"): string {
  return { POSITIVE: "Positive evidence", NEGATIVE: "Needs work", MIXED: "Mixed evidence" }[value];
}

function independenceLabel(value: string): string {
  return {
    INDEPENDENT: "Independently demonstrated",
    AFTER_PROBE: "Demonstrated after interviewer challenge",
    AFTER_LIGHT_GUIDANCE: "Improved after light guidance",
    AFTER_STRONG_HINT: "Improved after a strong hint",
    DIRECTLY_TAUGHT: "Demonstrated after explanation",
  }[value] ?? titleCase(value);
}

function titleCase(value: string): string {
  return value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
