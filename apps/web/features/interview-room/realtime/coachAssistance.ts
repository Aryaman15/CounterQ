import type { components } from "@counterq/contracts/openapi";

export type CandidateAssistanceResponse =
  components["schemas"]["CandidateAssistanceResponse"];

export async function requestCoachAssistance(
  interviewSessionId: string,
  apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000",
): Promise<CandidateAssistanceResponse> {
  const idempotencyKey = globalThis.crypto?.randomUUID?.() ?? `hint-${Date.now()}`;
  const response = await fetch(
    `${apiBaseUrl}/api/interviews/${interviewSessionId}/assistance-requests`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idempotency_key: idempotencyKey }),
    },
  );
  if (!response.ok) {
    throw new Error("CounterQ could not evaluate the hint request.");
  }
  return (await response.json()) as CandidateAssistanceResponse;
}
