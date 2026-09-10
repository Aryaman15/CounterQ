"use client";

import { useMemo } from "react";

import { AuthenticatedProductPage } from "@/features/product-shell/AuthenticatedProductPage";
import type { CounterQApiClient } from "@/lib/counterq-api";

import { CounterMapExperience, type CounterMapTransport } from "./CounterMapExperience";

export function ProductionCounterMapPage({
  interviewSessionId,
}: {
  interviewSessionId: string;
}) {
  return (
    <AuthenticatedProductPage>
      {({ api }) => (
        <AuthenticatedCounterMap api={api} interviewSessionId={interviewSessionId} />
      )}
    </AuthenticatedProductPage>
  );
}

function AuthenticatedCounterMap({
  api,
  interviewSessionId,
}: {
  api: CounterQApiClient;
  interviewSessionId: string;
}) {
  const transport = useMemo<CounterMapTransport>(() => ({
    loadCounterMap: (signal) => api.getCounterMap(interviewSessionId, signal),
    loadNodeDetail: (nodeId, signal) => (
      api.getCounterMapNodeDetail(interviewSessionId, nodeId, signal)
    ),
  }), [api, interviewSessionId]);
  return (
    <div className="production-countermap-page">
      <CounterMapExperience
        interviewSessionId={interviewSessionId}
        transport={transport}
      />
    </div>
  );
}
