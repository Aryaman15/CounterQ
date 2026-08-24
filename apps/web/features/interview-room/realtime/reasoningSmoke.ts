import type { components } from "@counterq/contracts/openapi";

export type DevelopmentReasoningSmokeResponse =
  components["schemas"]["DevelopmentReasoningSmokeResponse"];

export async function requestDevelopmentReasoningSmoke(
  interviewSessionId: string,
  apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000",
): Promise<DevelopmentReasoningSmokeResponse> {
  const response = await fetch(`${apiBaseUrl}/api/ai/development-reasoning-smoke`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ interview_session_id: interviewSessionId }),
  });
  if (!response.ok) {
    throw new Error("AI Gateway smoke request failed");
  }
  return (await response.json()) as DevelopmentReasoningSmokeResponse;
}
