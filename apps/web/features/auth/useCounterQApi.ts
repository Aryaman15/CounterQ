"use client";

import { useState } from "react";

import { CounterQApiClient } from "@/lib/counterq-api";

type GetToken = () => Promise<string | null>;

export function useCounterQApi(
  getToken: GetToken,
): CounterQApiClient {
  const [api] = useState(() => new CounterQApiClient(() => getToken()));
  return api;
}
