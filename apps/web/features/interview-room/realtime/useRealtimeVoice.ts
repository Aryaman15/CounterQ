"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { MutableRefObject } from "react";

import type { VoicePresenceState } from "../models/candidate-visible";
import { RealtimeVoiceClient, type RealtimeClientEvent } from "./RealtimeVoiceClient";

type UseRealtimeVoiceOptions = {
  clientFactory?: () => RealtimeVoiceClient;
};

export type RealtimeVoiceControls = {
  voiceState: VoicePresenceState;
  errorMessage: string | null;
  enableMicrophone: () => Promise<void>;
  mute: () => void;
  unmute: () => void;
  disconnect: () => void;
  speakDevelopmentPhrase: () => void;
};

export function useRealtimeVoice(
  options: UseRealtimeVoiceOptions = {},
): RealtimeVoiceControls {
  const { clientFactory } = options;
  const [voiceState, setVoiceState] = useState<VoicePresenceState>("Ready");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const clientRef = useRef<RealtimeVoiceClient | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const connectedBeforeMuteRef = useRef<VoicePresenceState>("Listening");

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
      applyRealtimeEvent(event, setVoiceState, setErrorMessage, connectedBeforeMuteRef);
    });
    clientRef.current = client;
    return client;
  }, [clientFactory]);

  const enableMicrophone = useCallback(async () => {
    setErrorMessage(null);
    setVoiceState("Connecting");
    const client = ensureClient();
    try {
      await client.connect();
    } catch (error) {
      setVoiceState("Error");
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
    setVoiceState("Ready");
  }, []);

  const speakDevelopmentPhrase = useCallback(() => {
    clientRef.current?.speakAuthorizedDevelopmentPhrase();
  }, []);

  useEffect(() => disconnect, [disconnect]);

  return {
    voiceState,
    errorMessage,
    enableMicrophone,
    mute,
    unmute,
    disconnect,
    speakDevelopmentPhrase,
  };
}

function applyRealtimeEvent(
  event: RealtimeClientEvent,
  setVoiceState: (state: VoicePresenceState) => void,
  setErrorMessage: (message: string | null) => void,
  connectedBeforeMuteRef: MutableRefObject<VoicePresenceState>,
): void {
  if (event.type === "connecting") {
    setVoiceState("Connecting");
    return;
  }
  if (event.type === "connected") {
    connectedBeforeMuteRef.current = "Listening";
    setVoiceState("Listening");
    return;
  }
  if (event.type === "candidate_speech_started") {
    connectedBeforeMuteRef.current = "Listening";
    setVoiceState("Listening");
    return;
  }
  if (event.type === "counterq_output_started") {
    connectedBeforeMuteRef.current = "Speaking";
    setVoiceState("Speaking");
    return;
  }
  if (
    event.type === "counterq_output_ended" ||
    event.type === "counterq_output_interrupted" ||
    event.type === "candidate_speech_stopped"
  ) {
    connectedBeforeMuteRef.current = "Listening";
    setVoiceState("Listening");
    return;
  }
  if (event.type === "muted") {
    setVoiceState("Muted");
    return;
  }
  if (event.type === "unmuted") {
    setVoiceState(connectedBeforeMuteRef.current);
    return;
  }
  if (event.type === "disconnected") {
    setVoiceState("Ready");
    return;
  }
  if (event.type === "error" || event.type === "provider_error") {
    setErrorMessage(event.message);
    setVoiceState("Error");
  }
}
