"use client";

import { useMemo, useRef } from "react";

import { CounterQApiClient } from "@/lib/counterq-api";

export type CounterQTokenSession = {
  getToken: () => Promise<string | null>;
};

export function useCounterQApi(
  session: CounterQTokenSession,
): CounterQApiClient {
  const currentSession = useRef(session);
  currentSession.current = session;

  return useMemo(
    () => new CounterQApiClient(() => currentSession.current.getToken()),
    [],
  );
}
