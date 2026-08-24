import type { components } from "@counterq/contracts/openapi";

export type DevelopmentAnalyzeLatestResponse =
  components["schemas"]["DevelopmentAnalyzeLatestResponse"];
export type DevelopmentAnalyzeAndAuthorizeResponse =
  components["schemas"]["DevelopmentAnalyzeAndAuthorizeResponse"];

export async function requestDevelopmentLiveExaminerAnalysis(
  interviewSessionId: string,
  apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000",
): Promise<DevelopmentAnalyzeLatestResponse> {
  const response = await fetch(`${apiBaseUrl}/api/examiner/development-analyze-latest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ interview_session_id: interviewSessionId }),
  });
  if (!response.ok) {
    throw new Error("Live Examiner analysis request failed");
  }
  return (await response.json()) as DevelopmentAnalyzeLatestResponse;
}

export async function requestDevelopmentAnalyzeAndAuthorize(
  interviewSessionId: string,
  apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000",
): Promise<DevelopmentAnalyzeAndAuthorizeResponse> {
  const response = await fetch(`${apiBaseUrl}/api/examiner/development-analyze-and-authorize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ interview_session_id: interviewSessionId }),
  });
  if (!response.ok) {
    throw new Error("Live Examiner analyze-and-authorize request failed");
  }
  return (await response.json()) as DevelopmentAnalyzeAndAuthorizeResponse;
}
