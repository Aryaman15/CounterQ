import type { components } from "@counterq/contracts/openapi";

type CounterMapGraph = components["schemas"]["CounterMapGraph"];
type CounterMapNode = components["schemas"]["CounterMapNode"];
type CounterMapEdge = components["schemas"]["CounterMapEdge"];
type CounterMapDisplayMetadata = NonNullable<CounterMapNode["display_metadata"]>;
type CounterMapAction = NonNullable<CounterMapNode["available_actions"]>[number];

export type CounterMapDemoFixture = {
  id: "simulation" | "coach" | "integrity";
  label: string;
  description: string;
  graph: CounterMapGraph;
};

const ids = {
  simulation: "7a000000-0000-4000-8000-000000000101",
  coach: "7a000000-0000-4000-8000-000000000102",
  integrity: "7a000000-0000-4000-8000-000000000103",
} as const;

function node(
  sessionId: string,
  nodeId: string,
  nodeType: CounterMapNode["node_type"],
  subtype: string,
  title: string,
  summary: string,
  rank: number,
  sequence: number,
  displayMetadata: Partial<CounterMapDisplayMetadata> = {},
  availableActions: CounterMapAction[] = [],
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
    node_id: nodeId,
    node_type: nodeType,
    subtype,
    canonical_sources: [{
      source_type: sourceType,
      source_id: `7a000000-0000-4000-8000-${nodeId.slice(-12)}`,
      interview_session_id: sessionId,
      server_sequence: sequence,
      version: nodeType === "CODE" ? Number(displayMetadata.code_version ?? 1) : null,
      content_hash: nodeType === "CODE" ? String(displayMetadata.content_hash) : null,
    }],
    title,
    summary,
    causal_rank: rank,
    stage: rank < 3 ? "IMPLEMENTATION" : "COMPLEXITY_EDGE_CASES",
    event_range: { start_sequence: sequence, end_sequence: sequence },
    display_metadata: { breakpoint_relationships: [], exact_quote: false, ...displayMetadata },
    available_actions: availableActions,
  };
}

function edge(
  sessionId: string,
  edgeId: string,
  from: string,
  to: string,
  relationship: CounterMapEdge["relationship"],
  detail: string,
): CounterMapEdge {
  return {
    edge_id: edgeId,
    from_node_id: from,
    to_node_id: to,
    relationship,
    canonical_relationship_sources: [{
      source_type: relationship === "EXPOSED" ? "BREAKPOINT_EVIDENCE" : relationship === "ASSISTED" ? "ASSISTANCE_TARGET" : relationship === "CORRECTED_BY" ? "CORRECTION_EVIDENCE" : relationship === "SUPPORTED" ? "EVIDENCE_SOURCE" : relationship === "ANSWERED_BY" ? "RESPONSE_LINK" : "PROMPT_TARGET",
      source_id: `7a000000-0000-4000-8000-${edgeId.slice(-12)}`,
      related_source_id: `7a000000-0000-4000-8000-${to.slice(-12)}`,
      interview_session_id: sessionId,
      detail,
    }],
  };
}

const viewLater = [{
  action: "VIEW_SOURCE" as const,
  label: "View code at this moment",
  availability: "DEFERRED" as const,
  reason: "Exact code detail opens with the later graph interaction stage.",
}];
const retestLater = [{
  action: "COUNTERQ_ME_AGAIN" as const,
  label: "CounterQ me again",
  availability: "UNAVAILABLE" as const,
  reason: "Retesting becomes available in a later stage.",
}];

const simulationNodes = [
  node(ids.simulation, "cmn_000000000000000000000101", "CLAIM", "ALGORITHM_CHOICE", "You said", "I will store each complement in a map.", 0, 1, { exact_quote: true }),
  node(ids.simulation, "cmn_000000000000000000000102", "QUESTION", "CHALLENGE", "CounterQ asked", "Why do you check before inserting the current value?", 1, 2, { exact_quote: true, delivery_state: "DELIVERED", why: "CounterQ asked this in response to what you said." }),
  node(ids.simulation, "cmn_000000000000000000000103", "RESPONSE", "PROMPT_RESPONSE", "You answered", "Checking first prevents one index from matching itself.", 2, 3, { exact_quote: true }),
  node(ids.simulation, "cmn_000000000000000000000104", "EVIDENCE", "POSITIVE", "Strong demonstration", "The complement-before-insertion invariant was defended independently.", 3, 3, { polarity: "POSITIVE", strength: "STRONG", independence_level: "INDEPENDENT" }),
  node(ids.simulation, "cmn_000000000000000000000105", "CLAIM", "COMPLEXITY", "You said", "Hash lookups are always constant time.", 0, 6, { exact_quote: true }),
  node(ids.simulation, "cmn_000000000000000000000106", "EVIDENCE", "NEGATIVE", "Needs work", "The claim omitted worst-case hash collision behavior.", 1, 6, { polarity: "NEGATIVE", strength: "MODERATE", independence_level: "INDEPENDENT" }),
  node(ids.simulation, "cmn_000000000000000000000107", "BREAKPOINT", "CANONICAL_BREAKPOINT", "Breakpoint", "Worst-case hash lookup remains an open reasoning boundary.", 2, 6, { breakpoint_status: "OPEN", breakpoint_severity: "MEDIUM", breakpoint_relationships: ["CREATED"] }, retestLater),
];
const simulationEdges = [
  edge(ids.simulation, "cme_000000000000000000000101", simulationNodes[0].node_id, simulationNodes[1].node_id, "TRIGGERED", "STRUCTURED_DELIVERED_PROMPT_TARGET"),
  edge(ids.simulation, "cme_000000000000000000000102", simulationNodes[1].node_id, simulationNodes[2].node_id, "ANSWERED_BY", "DELIVERED_PROMPT_RESPONSE_LINK"),
  edge(ids.simulation, "cme_000000000000000000000103", simulationNodes[2].node_id, simulationNodes[3].node_id, "SUPPORTED", "CANONICAL_PRIMARY"),
  edge(ids.simulation, "cme_000000000000000000000104", simulationNodes[4].node_id, simulationNodes[5].node_id, "SUPPORTED", "CANONICAL_PRIMARY"),
  edge(ids.simulation, "cme_000000000000000000000105", simulationNodes[5].node_id, simulationNodes[6].node_id, "EXPOSED", "CREATED"),
];

const coachNodes = [
  node(ids.coach, "cmn_000000000000000000000201", "CLAIM", "COMPLEXITY", "You said", "Hash lookup is always O(1).", 0, 1, { exact_quote: true }),
  node(ids.coach, "cmn_000000000000000000000202", "EVIDENCE", "NEGATIVE", "Needs work", "The initial complexity claim omitted the worst-case boundary.", 1, 1, { polarity: "NEGATIVE", strength: "MODERATE", independence_level: "INDEPENDENT" }),
  node(ids.coach, "cmn_000000000000000000000203", "ASSISTANCE", "CONCEPTUAL_HINT", "Coach guidance", "Separate the expected case from the worst case.", 1, 2, { exact_quote: true, delivery_state: "DELIVERED", assistance_label: "Conceptual hint" }),
  node(ids.coach, "cmn_000000000000000000000204", "RESPONSE", "PROMPT_RESPONSE", "You revised your reasoning", "Expected lookup is O(1), but collisions can make the worst case O(n).", 2, 3, { exact_quote: true }),
  node(ids.coach, "cmn_000000000000000000000205", "EVIDENCE", "POSITIVE", "What this showed", "The expected and worst cases were distinguished after light guidance.", 3, 3, { polarity: "POSITIVE", strength: "MODERATE", independence_level: "AFTER_LIGHT_GUIDANCE" }),
  node(ids.coach, "cmn_000000000000000000000206", "BREAKPOINT", "CANONICAL_BREAKPOINT", "Breakpoint", "Complexity reasoning still needs independent verification.", 4, 3, { breakpoint_status: "OPEN", breakpoint_severity: "MEDIUM", breakpoint_relationships: ["CREATED", "RESOLUTION_SUPPORT"] }, retestLater),
];
const coachEdges = [
  edge(ids.coach, "cme_000000000000000000000201", coachNodes[0].node_id, coachNodes[1].node_id, "SUPPORTED", "CANONICAL_PRIMARY"),
  edge(ids.coach, "cme_000000000000000000000202", coachNodes[0].node_id, coachNodes[2].node_id, "TRIGGERED", "STRUCTURED_DELIVERED_PROMPT_TARGET"),
  edge(ids.coach, "cme_000000000000000000000203", coachNodes[2].node_id, coachNodes[3].node_id, "ASSISTED", "TARGET_MATCHED_ASSISTED_OUTCOME"),
  edge(ids.coach, "cme_000000000000000000000204", coachNodes[3].node_id, coachNodes[4].node_id, "SUPPORTED", "CANONICAL_PRIMARY"),
  edge(ids.coach, "cme_000000000000000000000205", coachNodes[1].node_id, coachNodes[5].node_id, "EXPOSED", "CREATED"),
  edge(ids.coach, "cme_000000000000000000000206", coachNodes[4].node_id, coachNodes[5].node_id, "EXPOSED", "RESOLUTION_SUPPORT"),
];

const integrityNodes = [
  node(ids.integrity, "cmn_000000000000000000000301", "CODE", "DECISION", "Your code", "Python code snapshot v4, preserved from this moment.", 0, 9, { code_snapshot_id: "7a000000-0000-4000-8000-000000000304", code_version: 4, content_hash: "sha256:04", language: "python" }, viewLater),
  node(ids.integrity, "cmn_000000000000000000000302", "CODE", "SELF_CORRECTION", "Corrected independently", "Python code snapshot v5, preserved from this moment.", 1, 10, { code_snapshot_id: "7a000000-0000-4000-8000-000000000305", code_version: 5, content_hash: "sha256:05", language: "python" }, viewLater),
  node(ids.integrity, "cmn_000000000000000000000303", "EVIDENCE", "MIXED", "Mixed evidence", "You corrected the stale window boundary without an interviewer prompt.", 2, 10, { polarity: "MIXED", strength: "STRONG", independence_level: "INDEPENDENT" }),
  node(ids.integrity, "cmn_000000000000000000000304", "QUESTION", "CHALLENGE", "CounterQ asked", "What invariant", 2, 12, { exact_quote: true, delivery_state: "INTERRUPTED", why: "CounterQ asked this in response to the code at that moment." }),
];
const integrityEdges = [
  edge(ids.integrity, "cme_000000000000000000000301", integrityNodes[0].node_id, integrityNodes[1].node_id, "CORRECTED_BY", "VALIDATED_SELF_CORRECTION"),
  edge(ids.integrity, "cme_000000000000000000000302", integrityNodes[1].node_id, integrityNodes[2].node_id, "SUPPORTED", "CANONICAL_PRIMARY"),
  edge(ids.integrity, "cme_000000000000000000000303", integrityNodes[1].node_id, integrityNodes[3].node_id, "TRIGGERED", "STRUCTURED_DELIVERED_PROMPT_TARGET"),
];

function graph(sessionId: string, nodes: CounterMapNode[], edges: CounterMapEdge[], overview: string): CounterMapGraph {
  return {
    schema_version: "countermap.graph.v1",
    generation_policy_version: "countermap-projector.v1",
    interview_session_id: sessionId,
    source_watermark: Math.max(...nodes.map((item) => item.event_range?.end_sequence ?? 0)),
    nodes,
    edges,
    summary: {
      title: "Your reasoning map",
      overview,
      node_counts: countBy(nodes.map((item) => item.node_type)),
      relationship_counts: countBy(edges.map((item) => item.relationship)),
    },
  };
}

function countBy(values: string[]): Record<string, number> {
  return values.reduce<Record<string, number>>((counts, value) => {
    counts[value] = (counts[value] ?? 0) + 1;
    return counts;
  }, {});
}

export const counterMapDemoFixtures: CounterMapDemoFixture[] = [
  {
    id: "simulation",
    label: "Simulation",
    description: "Independent success and a separate open misconception, with no coaching.",
    graph: graph(ids.simulation, simulationNodes, simulationEdges, "Independent strength and an open complexity boundary remain equally visible."),
  },
  {
    id: "coach",
    label: "Coach",
    description: "Improvement after light guidance without converting it into independent mastery.",
    graph: graph(ids.coach, coachNodes, coachEdges, "Guidance restarted the reasoning, while independent verification remains open."),
  },
  {
    id: "integrity",
    label: "Delivery integrity",
    description: "Independent self-correction and an interrupted question showing only delivered words.",
    graph: graph(ids.integrity, integrityNodes, integrityEdges, "Exact historical code and actual delivery wording preserve what really happened."),
  },
];
