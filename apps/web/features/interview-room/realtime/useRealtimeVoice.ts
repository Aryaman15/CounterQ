"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MutableRefObject } from "react";

import type { VoicePresenceState } from "../models/candidate-visible";
import { RealtimeVoiceClient, type RealtimeClientEvent } from "./RealtimeVoiceClient";

type UseRealtimeVoiceOptions = {
  clientFactory?: () => RealtimeVoiceClient;
};

type RealtimeActivityState = Exclude<VoicePresenceState, "Muted">;

export type RealtimeVoiceControls = {
  voiceState: VoicePresenceState;
  isMuted: boolean;
  errorMessage: string | null;
  partialTranscript: string;
  lastFinalTranscript: string;
  sessionDebug: RealtimeSessionDebug;
  enableMicrophone: () => Promise<void>;
  mute: () => void;
  unmute: () => void;
  disconnect: () => void;
  speakDevelopmentPhrase: () => void;
};

export type RealtimeSessionDebug = {
  eventType: "session.created" | "session.updated" | null;
  sessionType: string | null;
  transcriptionModel: string | null;
  turnDetectionType: string | null;
  createResponse: boolean | null;
  interruptResponse: boolean | null;
};

export function useRealtimeVoice(
  options: UseRealtimeVoiceOptions = {},
): RealtimeVoiceControls {
  const { clientFactory } = options;
  const [activityState, setActivityState] = useState<RealtimeActivityState>("Ready");
  const [isMuted, setIsMuted] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [partialTranscript, setPartialTranscript] = useState("");
  const [lastFinalTranscript, setLastFinalTranscript] = useState("");
  const [sessionDebug, setSessionDebug] = useState<RealtimeSessionDebug>({
    eventType: null,
    sessionType: null,
    transcriptionModel: null,
    turnDetectionType: null,
    createResponse: null,
    interruptResponse: null,
  });
  const clientRef = useRef<RealtimeVoiceClient | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const transcriptDraftsRef = useRef(new Map<string, string>());
  const activeTranscriptKeyRef = useRef<string | null>(null);

  const voiceState = useMemo<VoicePresenceState>(() => {
    if (isMuted && activityState === "Listening") {
      return "Muted";
    }
    return activityState;
  }, [activityState, isMuted]);

  const ensureClient = useCallback(() => {
    if (clientRef.current) {
      return clientRef.current;
    }
    const client =
      clientFactory?.() ??
      new RealtimeVoiceClient({
        apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000",
      });
    unsubscribeRef.current = client.on((event) => {
      applyRealtimeEvent(event, {
        setActivityState,
        setErrorMessage,
        setIsMuted,
        setPartialTranscript,
        setLastFinalTranscript,
        setSessionDebug,
        transcriptDraftsRef,
        activeTranscriptKeyRef,
      });
    });
    clientRef.current = client;
    return client;
  }, [clientFactory]);

  const enableMicrophone = useCallback(async () => {
    setErrorMessage(null);
    setActivityState("Connecting");
    const client = ensureClient();
    try {
      await client.connect();
    } catch (error) {
      setActivityState("Error");
      setIsMuted(false);
      setErrorMessage(error instanceof Error ? error.message : "Realtime voice connection failed.");
    }
  }, [ensureClient]);

  const mute = useCallback(() => {
    clientRef.current?.setMuted(true);
  }, []);

  const unmute = useCallback(() => {
    clientRef.current?.setMuted(false);
  }, []);

  const disconnect = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
    clientRef.current?.disconnect();
    clientRef.current = null;
    setErrorMessage(null);
    setActivityState("Ready");
    setIsMuted(false);
    setPartialTranscript("");
    transcriptDraftsRef.current.clear();
    activeTranscriptKeyRef.current = null;
  }, []);

  const speakDevelopmentPhrase = useCallback(() => {
    clientRef.current?.speakAuthorizedDevelopmentPhrase();
  }, []);

  useEffect(() => disconnect, [disconnect]);

  return {
    voiceState,
    isMuted,
    errorMessage,
    partialTranscript,
    lastFinalTranscript,
    sessionDebug,
    enableMicrophone,
    mute,
    unmute,
    disconnect,
    speakDevelopmentPhrase,
  };
}

type RealtimeEventSetters = {
  setActivityState: (state: RealtimeActivityState) => void;
  setErrorMessage: (message: string | null) => void;
  setIsMuted: (muted: boolean) => void;
  setPartialTranscript: (text: string) => void;
  setLastFinalTranscript: (text: string) => void;
  setSessionDebug: (debug: RealtimeSessionDebug) => void;
  transcriptDraftsRef: MutableRefObject<Map<string, string>>;
  activeTranscriptKeyRef: MutableRefObject<string | null>;
};

function applyRealtimeEvent(event: RealtimeClientEvent, setters: RealtimeEventSetters): void {
  const {
    setActivityState,
    setErrorMessage,
    setIsMuted,
    setPartialTranscript,
    setLastFinalTranscript,
    setSessionDebug,
    transcriptDraftsRef,
    activeTranscriptKeyRef,
  } = setters;

  if (event.type === "connecting") {
    setActivityState("Connecting");
    return;
  }
  if (event.type === "connected") {
    setActivityState("Listening");
    return;
  }
  if (event.type === "candidate_speech_started") {
    setActivityState("Listening");
    return;
  }
  if (event.type === "counterq_output_started") {
    setActivityState("Speaking");
    return;
  }
  if (
    event.type === "counterq_output_ended" ||
    event.type === "counterq_output_interrupted" ||
    event.type === "candidate_speech_stopped"
  ) {
    setActivityState("Listening");
    return;
  }
  if (event.type === "muted") {
    setIsMuted(true);
    return;
  }
  if (event.type === "unmuted") {
    setIsMuted(false);
    return;
  }
  if (event.type === "disconnected") {
    setActivityState("Ready");
    setIsMuted(false);
    setPartialTranscript("");
    transcriptDraftsRef.current.clear();
    activeTranscriptKeyRef.current = null;
    setErrorMessage(null);
    return;
  }
  if (event.type === "transcript_delta") {
    const transcriptKey = transcriptEventKey(event.itemId, event.contentIndex);
    const nextText = `${transcriptDraftsRef.current.get(transcriptKey) ?? ""}${event.text}`;
    transcriptDraftsRef.current.set(transcriptKey, nextText);
    activeTranscriptKeyRef.current = transcriptKey;
    setPartialTranscript(nextText);
    return;
  }
  if (event.type === "transcript_final") {
    const transcriptKey = transcriptEventKey(event.itemId, event.contentIndex);
    transcriptDraftsRef.current.delete(transcriptKey);
    if (activeTranscriptKeyRef.current === transcriptKey) {
      activeTranscriptKeyRef.current = null;
      setPartialTranscript("");
    }
    setLastFinalTranscript(event.text);
    return;
  }
  if (event.type === "transcript_failed") {
    const transcriptKey = transcriptEventKey(event.itemId, null);
    transcriptDraftsRef.current.delete(transcriptKey);
    if (activeTranscriptKeyRef.current === transcriptKey) {
      activeTranscriptKeyRef.current = null;
      setPartialTranscript("");
    }
    setErrorMessage(event.message);
    return;
  }
  if (event.type === "realtime_session_observed") {
    setSessionDebug({
      eventType: event.eventType,
      sessionType: event.sessionType,
      transcriptionModel: event.transcriptionModel,
      turnDetectionType: event.turnDetectionType,
      createResponse: event.createResponse,
      interruptResponse: event.interruptResponse,
    });
    return;
  }
  if (event.type === "error" || event.type === "provider_error") {
    setErrorMessage(event.message);
    setActivityState("Error");
    setIsMuted(false);
  }
}

function transcriptEventKey(itemId: string | null, contentIndex: number | null): string {
  return `${itemId ?? "latest"}:${contentIndex ?? 0}`;
}
