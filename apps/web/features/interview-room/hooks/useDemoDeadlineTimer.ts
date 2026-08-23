"use client";

import { useEffect, useMemo, useState } from "react";

export function formatRemainingTime(totalSeconds: number): string {
  const clampedSeconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(clampedSeconds / 60);
  const seconds = clampedSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export function getFixtureRemainingSeconds(serverNowIso: string, deadlineAtIso: string): number {
  return Math.max(0, Math.floor((Date.parse(deadlineAtIso) - Date.parse(serverNowIso)) / 1000));
}

export function useDemoDeadlineTimer(serverNowIso: string, deadlineAtIso: string): string {
  const baseSeconds = useMemo(
    () => getFixtureRemainingSeconds(serverNowIso, deadlineAtIso),
    [deadlineAtIso, serverNowIso],
  );
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  useEffect(() => {
    const startedAt = Date.now();
    const intervalId = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(intervalId);
  }, []);

  return formatRemainingTime(baseSeconds - elapsedSeconds);
}
