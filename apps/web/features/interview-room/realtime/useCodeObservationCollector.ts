"use client";

import { useEffect, useRef } from "react";

export const CODE_EDIT_BURST_IDLE_MS = 2500;

type UseCodeObservationCollectorOptions = {
  sourceCode: string;
  controlReady: boolean;
  sendSnapshot: (
    sourceCode: string,
    trigger: "INITIAL_EDITOR_STATE" | "EDIT_BURST",
    idempotencyKey: string,
  ) => void;
  noteActivityStarted?: () => void;
  noteActivityIdle?: () => void;
  delayMs?: number;
  randomId?: () => string;
};

export function useCodeObservationCollector({
  sourceCode,
  controlReady,
  sendSnapshot,
  noteActivityStarted,
  noteActivityIdle,
  delayMs = CODE_EDIT_BURST_IDLE_MS,
  randomId = defaultRandomId,
}: UseCodeObservationCollectorOptions): void {
  const hasSentInitialRef = useRef(false);
  const lastSubmittedSourceRef = useRef<string | null>(null);
  const timerRef = useRef<number | null>(null);
  const burstCounterRef = useRef(0);

  useEffect(() => {
    if (!controlReady) {
      return;
    }
    if (!hasSentInitialRef.current) {
      hasSentInitialRef.current = true;
      lastSubmittedSourceRef.current = sourceCode;
      sendSnapshot(
        sourceCode,
        "INITIAL_EDITOR_STATE",
        createCodeObservationIdempotencyKey(
          "INITIAL_EDITOR_STATE",
          ++burstCounterRef.current,
          randomId,
        ),
      );
      return;
    }
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (sourceCode === lastSubmittedSourceRef.current) {
      noteActivityIdle?.();
      return;
    }
    noteActivityStarted?.();
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      if (sourceCode === lastSubmittedSourceRef.current) {
        return;
      }
      noteActivityIdle?.();
      lastSubmittedSourceRef.current = sourceCode;
      sendSnapshot(
        sourceCode,
        "EDIT_BURST",
        createCodeObservationIdempotencyKey("EDIT_BURST", ++burstCounterRef.current, randomId),
      );
    }, delayMs);
  }, [
    controlReady,
    delayMs,
    noteActivityIdle,
    noteActivityStarted,
    randomId,
    sendSnapshot,
    sourceCode,
  ]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
        noteActivityIdle?.();
      }
    },
    [noteActivityIdle],
  );
}

export function createCodeObservationIdempotencyKey(
  trigger: "INITIAL_EDITOR_STATE" | "EDIT_BURST",
  sequence: number,
  randomId: () => string = defaultRandomId,
): string {
  return `candidate-code:${trigger}:${sequence}:${randomId()}`;
}

function defaultRandomId(): string {
  return (
    globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}
