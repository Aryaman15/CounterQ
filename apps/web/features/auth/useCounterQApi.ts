"use client";

import { useAuth } from "@clerk/nextjs";
import { useMemo } from "react";

import { CounterQApiClient } from "@/lib/counterq-api";

export function useCounterQApi(): CounterQApiClient {
  const { getToken } = useAuth();
  return useMemo(() => new CounterQApiClient(() => getToken()), [getToken]);
}
