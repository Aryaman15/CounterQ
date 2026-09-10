"use client";

import { useCallback } from "react";

import { AuthenticatedProductPage } from "@/features/product-shell/AuthenticatedProductPage";
import type { CounterQApiClient } from "@/lib/counterq-api";

import { SessionReportExperience } from "./SessionReportExperience";

export function ProductionSessionReportPage({
  interviewSessionId,
}: {
  interviewSessionId: string;
}) {
  return (
    <AuthenticatedProductPage>
      {({ api }) => (
        <AuthenticatedSessionReport api={api} interviewSessionId={interviewSessionId} />
      )}
    </AuthenticatedProductPage>
  );
}

function AuthenticatedSessionReport({
  api,
  interviewSessionId,
}: {
  api: CounterQApiClient;
  interviewSessionId: string;
}) {
  const loadReport = useCallback(
    (signal?: AbortSignal) => api.getSessionReport(interviewSessionId, signal),
    [api, interviewSessionId],
  );
  return (
    <SessionReportExperience
      interviewSessionId={interviewSessionId}
      loadReport={loadReport}
      productNavigation
    />
  );
}
