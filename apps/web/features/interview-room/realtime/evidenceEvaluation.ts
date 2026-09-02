import type { components } from "@counterq/contracts/openapi";

export type DevelopmentSessionEvaluationResponse =
  components["schemas"]["DevelopmentSessionEvaluationResponse"];
export type DevelopmentCanonicalEvaluationSnapshot =
  components["schemas"]["DevelopmentCanonicalEvaluationSnapshot"];

const defaultApiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export async function evaluateDevelopmentEvidence(
  interviewSessionId: string,
  apiBaseUrl = defaultApiBaseUrl,
): Promise<DevelopmentSessionEvaluationResponse> {
  const response = await fetch(`${apiBaseUrl}/api/evidence/development/session-evaluation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ interview_session_id: interviewSessionId }),
  });
  if (!response.ok) {
    throw new Error("Stage 5 evidence evaluation failed");
  }
  return (await response.json()) as DevelopmentSessionEvaluationResponse;
}

export async function fetchDevelopmentEvidenceSnapshot(
  interviewSessionId: string,
  apiBaseUrl = defaultApiBaseUrl,
): Promise<DevelopmentCanonicalEvaluationSnapshot> {
  const response = await fetch(
    `${apiBaseUrl}/api/evidence/development/session-evaluation/${interviewSessionId}`,
  );
  if (!response.ok) {
    throw new Error("Stage 5 evidence snapshot failed");
  }
  return (await response.json()) as DevelopmentCanonicalEvaluationSnapshot;
}
