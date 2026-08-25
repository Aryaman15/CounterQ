import type { components } from "@counterq/contracts/openapi";

export type DevelopmentAnalyzeLatestResponse =
  components["schemas"]["DevelopmentAnalyzeLatestResponse"];
export type DevelopmentAnalyzeAndAuthorizeResponse =
  components["schemas"]["DevelopmentAnalyzeAndAuthorizeResponse"];

export class DevelopmentExaminerRequestError extends Error {
  constructor(
    readonly category: string | null,
    message: string,
  ) {
    super(message);
  }
}

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
    throw await examinerRequestError(response, "Live Examiner analysis request failed");
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
    throw await examinerRequestError(response, "Live Examiner analyze-and-authorize request failed");
  }
  return (await response.json()) as DevelopmentAnalyzeAndAuthorizeResponse;
}

async function examinerRequestError(
  response: Response,
  fallback: string,
): Promise<DevelopmentExaminerRequestError> {
  try {
    const body = (await response.json()) as { detail?: { category?: unknown; message?: unknown } };
    const category = typeof body.detail?.category === "string" ? body.detail.category : null;
    const message = typeof body.detail?.message === "string" ? body.detail.message : fallback;
    return new DevelopmentExaminerRequestError(category, message);
  } catch {
    return new DevelopmentExaminerRequestError(null, fallback);
  }
}
