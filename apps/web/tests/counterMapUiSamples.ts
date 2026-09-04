import type { components } from "@counterq/contracts/openapi";

type CounterMapGraph = components["schemas"]["CounterMapGraph"];
type CounterMapNode = components["schemas"]["CounterMapNode"];
type CounterMapEdge = components["schemas"]["CounterMapEdge"];

const sessionId = "7a000000-0000-4000-8000-000000000001";

function uuid(value: number): string {
  return `7a000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
}

function node(
  index: number,
  nodeType: CounterMapNode["node_type"],
  title: string,
  summary: string,
  rank: number,
  metadata: Partial<CounterMapNode["display_metadata"]> = {},
  subtype = "UI_SAMPLE",
): CounterMapNode {
  const sourceType = {
    CLAIM: "CANDIDATE_CLAIM",
    REASONING: "CANDIDATE_TRANSCRIPT",
    CODE: "CODE_SNAPSHOT",
    TEST: "EXECUTION",
    QUESTION: "DELIVERED_PROMPT",
    RESPONSE: "CANDIDATE_RESPONSE",
    EVIDENCE: "EVIDENCE",
    BREAKPOINT: "BREAKPOINT",
    ASSISTANCE: "DELIVERED_PROMPT",
    MUTATION: "DELIVERED_PROMPT",
  }[nodeType] as CounterMapNode["canonical_sources"][number]["source_type"];
  return {
    node_id: `ui-node-${index}`,
    node_type: nodeType,
    subtype,
    canonical_sources: [{
      source_type: sourceType,
      source_id: uuid(index),
      interview_session_id: sessionId,
      server_sequence: index,
    }],
    title,
    summary,
    causal_rank: rank,
    stage: "IMPLEMENTATION",
    event_range: { start_sequence: index, end_sequence: index },
    display_metadata: { exact_quote: false, ...metadata },
    available_actions: nodeType === "BREAKPOINT" ? [{
      action: "COUNTERQ_ME_AGAIN",
      label: "CounterQ me again",
      availability: "UNAVAILABLE",
      reason: "Retesting becomes available in a later stage.",
    }] : [],
  };
}

function edge(
  index: number,
  from: CounterMapNode,
  to: CounterMapNode,
  relationship: CounterMapEdge["relationship"],
): CounterMapEdge {
  const sourceType = {
    TRIGGERED: "PROMPT_TARGET",
    ANSWERED_BY: "RESPONSE_LINK",
    LED_TO: "EVENT_CAUSATION",
    SUPPORTED: "EVIDENCE_SOURCE",
    EXPOSED: "BREAKPOINT_EVIDENCE",
    CORRECTED_BY: "CORRECTION_EVIDENCE",
    ASSISTED: "ASSISTANCE_TARGET",
  }[relationship] as CounterMapEdge["canonical_relationship_sources"][number]["source_type"];
  return {
    edge_id: `ui-edge-${index}`,
    from_node_id: from.node_id,
    to_node_id: to.node_id,
    relationship,
    canonical_relationship_sources: [{
      source_type: sourceType,
      source_id: uuid(100 + index),
      related_source_id: uuid(200 + index),
      interview_session_id: sessionId,
      detail: "UI_COMPONENT_SAMPLE",
    }],
  };
}

function graph(nodes: CounterMapNode[], edges: CounterMapEdge[]): CounterMapGraph {
  return {
    schema_version: "countermap.graph.v1",
    generation_policy_version: "countermap-projector.v3",
    interview_session_id: sessionId,
    source_watermark: nodes.length,
    nodes,
    edges,
    summary: {
      title: "Your reasoning map",
      overview: "A compact UI sample for the reasoning timeline.",
      node_counts: count(nodes.map((item) => item.node_type)),
      relationship_counts: count(edges.map((item) => item.relationship)),
    },
  };
}

function count(values: string[]): Record<string, number> {
  return values.reduce<Record<string, number>>((result, value) => {
    result[value] = (result[value] ?? 0) + 1;
    return result;
  }, {});
}

function simulationSample(): CounterMapGraph {
  const claim = node(1, "CLAIM", "You said", "I will store complements in a map.", 0, {
    exact_quote: true,
  });
  const laterClaim = node(2, "CLAIM", "You said", "Hash lookups are always constant time.", 0, {
    exact_quote: true,
  });
  const question = node(3, "QUESTION", "CounterQ asked", "Why check before inserting?", 1, {
    exact_quote: true,
    delivery_state: "DELIVERED",
    why: "CounterQ asked this in response to what you said.",
  });
  const needsWork = node(4, "EVIDENCE", "Needs work", "Worst-case behavior was omitted.", 1, {
    polarity: "NEGATIVE",
    strength: "MODERATE",
    independence_level: "INDEPENDENT",
  });
  const response = node(5, "RESPONSE", "You answered", "It prevents self-matching.", 2, {
    exact_quote: true,
  }, "PROMPT_RESPONSE");
  const breakpoint = node(6, "BREAKPOINT", "Breakpoint", "Verify the worst-case boundary.", 2, {
    breakpoint_status: "OPEN",
    breakpoint_severity: "MEDIUM",
    breakpoint_relationships: ["CREATED"],
  });
  const evidence = node(7, "EVIDENCE", "Strong demonstration", "The invariant was defended.", 3, {
    polarity: "POSITIVE",
    strength: "STRONG",
    independence_level: "INDEPENDENT",
  });
  const nodes = [claim, laterClaim, question, needsWork, response, breakpoint, evidence];
  return graph(nodes, [
    edge(1, claim, question, "TRIGGERED"),
    edge(2, question, response, "ANSWERED_BY"),
    edge(3, laterClaim, needsWork, "SUPPORTED"),
    edge(4, needsWork, breakpoint, "EXPOSED"),
    edge(5, response, evidence, "SUPPORTED"),
  ]);
}

function coachSample(): CounterMapGraph {
  const claim = node(11, "CLAIM", "You said", "Hash lookup is always O(1).", 0, {
    exact_quote: true,
  });
  const assistance = node(12, "ASSISTANCE", "Coach guidance", "Separate expected and worst case.", 0, {
    exact_quote: true,
    assistance_label: "Conceptual hint",
  });
  const weakness = node(13, "EVIDENCE", "Needs work", "The initial boundary was missing.", 1, {
    polarity: "NEGATIVE",
    strength: "MODERATE",
    independence_level: "INDEPENDENT",
  });
  const response = node(14, "RESPONSE", "You answered", "Worst case can be O(n).", 1, {
    exact_quote: true,
  }, "PROMPT_RESPONSE");
  const improvement = node(15, "EVIDENCE", "What this showed", "Reasoning improved after guidance.", 2, {
    polarity: "POSITIVE",
    strength: "MODERATE",
    independence_level: "AFTER_LIGHT_GUIDANCE",
  });
  const breakpoint = node(16, "BREAKPOINT", "Breakpoint", "Independent verification remains open.", 3, {
    breakpoint_status: "OPEN",
    breakpoint_severity: "MEDIUM",
    breakpoint_relationships: ["CREATED", "RESOLUTION_SUPPORT"],
  });
  const nodes = [claim, assistance, weakness, response, improvement, breakpoint];
  return graph(nodes, [
    edge(11, claim, weakness, "SUPPORTED"),
    edge(12, assistance, response, "ASSISTED"),
    edge(13, response, improvement, "SUPPORTED"),
    edge(14, weakness, breakpoint, "EXPOSED"),
    edge(15, improvement, breakpoint, "EXPOSED"),
  ]);
}

function integritySample(): CounterMapGraph {
  const before = node(21, "CODE", "Your code", "Python code snapshot v1.", 0, {
    code_snapshot_id: uuid(21),
    code_version: 1,
    content_hash: "sha256:01",
    language: "python",
  });
  const question = node(22, "QUESTION", "CounterQ asked", "What invariant", 0, {
    exact_quote: true,
    delivery_state: "INTERRUPTED",
    why: "CounterQ asked this in response to your code.",
  });
  const after = node(23, "CODE", "Corrected independently", "Python code snapshot v2.", 1, {
    code_snapshot_id: uuid(23),
    code_version: 2,
    content_hash: "sha256:02",
    language: "python",
  }, "SELF_CORRECTION");
  return graph([before, question, after], [edge(21, before, after, "CORRECTED_BY")]);
}

export const counterMapUiSamples = [
  simulationSample(),
  coachSample(),
  integritySample(),
] as const;
