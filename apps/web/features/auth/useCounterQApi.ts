"use client";

import { useMemo, useRef } from "react";

import { CounterQApiClient } from "@/lib/counterq-api";

export type GetCounterQToken = () => Promise<string | null>;

export function useCounterQApi(
  getToken: GetCounterQToken,
): CounterQApiClient {
  const currentGetToken = useRef(getToken);
  currentGetToken.current = getToken;

  return useMemo(
    () => new CounterQApiClient(() => currentGetToken.current()),
    [],
  );
}
