import type { components } from "@counterq/contracts/openapi";

export type CounterMapGraph = components["schemas"]["CounterMapGraph"];
export type CounterMapNode = components["schemas"]["CounterMapNode"];
export type CounterMapEdge = components["schemas"]["CounterMapEdge"];
export type CounterMapNodeType = CounterMapNode["node_type"];
export type CounterMapDetail = components["schemas"]["CandidateCounterMapNodeDetailResponse"];

export type CounterMapFamily = "candidate" | "counterq" | "evaluation";

export const candidateNodeTypes: CounterMapNodeType[] = [
  "CLAIM",
  "REASONING",
  "CODE",
  "TEST",
  "RESPONSE",
];

export const counterqNodeTypes: CounterMapNodeType[] = [
  "QUESTION",
  "MUTATION",
  "ASSISTANCE",
];

export const evaluationNodeTypes: CounterMapNodeType[] = ["EVIDENCE", "BREAKPOINT"];

export function familyForNode(nodeType: CounterMapNodeType): CounterMapFamily {
  if (candidateNodeTypes.includes(nodeType)) return "candidate";
  if (counterqNodeTypes.includes(nodeType)) return "counterq";
  return "evaluation";
}

export function eyebrowForNode(node: CounterMapNode): string {
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
  if (node.node_type === "CODE" && node.subtype === "SELF_CORRECTION") {
    return "Corrected independently";
  }
  if (node.node_type === "CODE" && node.subtype === "CORRECTION") return "Updated code";
  if (node.node_type === "RESPONSE" && node.subtype === "SPONTANEOUS_RESPONSE") {
    return "Your reasoning";
  }
  if (node.node_type === "ASSISTANCE") return "Coach guidance";
  if (node.node_type === "BREAKPOINT") return "Evidence-backed boundary";
  return {
    CLAIM: "You said",
    REASONING: "Your reasoning",
    CODE: "Your code",
    TEST: "You tested it",
    QUESTION: "CounterQ asked",
    RESPONSE: "You answered",
    MUTATION: "Constraint change",
  }[node.node_type] ?? "Interview moment";
}

export function relationshipLabel(
  relationship: CounterMapEdge["relationship"],
  targetType?: CounterMapNodeType,
): string {
  if (relationship === "TRIGGERED") {
    if (targetType === "MUTATION") return "prompted this constraint change";
    if (targetType === "ASSISTANCE") return "led to this guidance";
    return "prompted this question";
  }
  return {
    ANSWERED_BY: "received this answer",
    LED_TO: "led to this moment",
    SUPPORTED: "supported this evidence",
    EXPOSED: "shaped this breakpoint",
    CORRECTED_BY: "was corrected by this change",
    ASSISTED: "helped shape this response",
  }[relationship];
}

export function shortRelationshipLabel(relationship: CounterMapEdge["relationship"]): string {
  return {
    TRIGGERED: "prompted",
    ANSWERED_BY: "answered by",
    LED_TO: "led to",
    SUPPORTED: "showed",
    EXPOSED: "exposed",
    CORRECTED_BY: "corrected by",
    ASSISTED: "assisted",
  }[relationship];
}

export function polarityLabel(value: "POSITIVE" | "NEGATIVE" | "MIXED"): string {
  return {
    POSITIVE: "Positive evidence",
    NEGATIVE: "Needs work",
    MIXED: "Mixed evidence",
  }[value];
}

export function independenceLabel(value: string): string {
  return {
    INDEPENDENT: "Independent",
    AFTER_PROBE: "After interviewer challenge",
    AFTER_LIGHT_GUIDANCE: "After light guidance",
    AFTER_STRONG_HINT: "After a strong hint",
    DIRECTLY_TAUGHT: "After explanation",
  }[value] ?? titleCase(value);
}

export function truthMarkers(node: CounterMapNode): string[] {
  const metadata = node.display_metadata;
  const parts: string[] = [];
  if (metadata?.independence_level) parts.push(independenceLabel(metadata.independence_level));
  if (metadata?.strength && node.node_type === "EVIDENCE") {
    parts.push(titleCase(metadata.strength));
  }
  if (metadata?.breakpoint_status) {
    parts.push(
      metadata.breakpoint_status === "OPEN"
        ? "Independent verification needed"
        : titleCase(metadata.breakpoint_status),
    );
  }
  if (metadata?.assistance_label) parts.push(metadata.assistance_label);
  if (metadata?.code_version) parts.push(`Snapshot v${metadata.code_version}`);
  if (metadata?.delivery_state && metadata.delivery_state !== "DELIVERED") {
    parts.push("Delivered portion only");
  }
  return parts;
}

export function whyLabel(nodeType: CounterMapNodeType): string {
  const labels: Partial<Record<CounterMapNodeType, string>> = {
    QUESTION: "Why this question?",
    MUTATION: "Why this constraint change?",
    ASSISTANCE: "Why this guidance?",
  };
  return labels[nodeType] ?? "Why this moment?";
}

export function titleCase(value: string): string {
  return value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function nodeAccessibleLabel(node: CounterMapNode): string {
  const markers = truthMarkers(node);
  return [eyebrowForNode(node), node.title, node.summary, ...markers].join(". ");
}
