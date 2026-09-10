import type { components } from "@counterq/contracts/openapi";

type ReportResponse = components["schemas"]["CandidateSessionReportResponse"];
type ReportInspection = components["schemas"]["DevelopmentReportInspection"];

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export function createDevelopmentReportTransport(interviewSessionId: string) {
  return {
    loadReport: (signal?: AbortSignal) => readJson<ReportResponse>(
      `/api/reports/development/sessions/${interviewSessionId}`,
      signal,
    ),
    loadInspection: (signal?: AbortSignal) => readJson<ReportInspection>(
      `/api/reports/development/sessions/${interviewSessionId}/inspection`,
      signal,
    ),
  };
}

async function readJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error("Development Session Report is unavailable");
  return response.json() as Promise<T>;
}
