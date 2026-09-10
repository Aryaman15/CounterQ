import type { components } from "@counterq/contracts/openapi";

import type { CounterMapTransport } from "./CounterMapExperience";

type CounterMapResponse = components["schemas"]["CandidateCounterMapResponse"];
type CounterMapDetail = components["schemas"]["CandidateCounterMapNodeDetailResponse"];
type CounterMapInspection = components["schemas"]["DevelopmentCounterMapInspection"];

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export function createDevelopmentCounterMapTransport(
  interviewSessionId: string,
): CounterMapTransport {
  const sessionPath = `/api/countermap/development/sessions/${interviewSessionId}`;
  return {
    loadCounterMap: (signal) => readJson<CounterMapResponse>(sessionPath, signal),
    loadNodeDetail: (nodeId, signal) => readJson<CounterMapDetail>(
      `${sessionPath}/nodes/${encodeURIComponent(nodeId)}`,
      signal,
    ),
    loadInspection: (signal) => readJson<CounterMapInspection>(
      `${sessionPath}/inspection`,
      signal,
    ),
  };
}

async function readJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error("Development CounterMap is unavailable");
  return response.json() as Promise<T>;
}
