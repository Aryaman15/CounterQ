"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, MutableRefObject, SetStateAction } from "react";

import type { VoicePresenceState } from "../models/candidate-visible";
import {
  RealtimeControlClient,
  type CanonicalControlDebug,
  type DevelopmentBootstrapResponse,
} from "./RealtimeControlClient";
import { RealtimeVoiceClient, type RealtimeClientEvent } from "./RealtimeVoiceClient";

type UseRealtimeVoiceOptions = {
  clientFactory?: () => RealtimeVoiceClient;
  controlClientFactory?: () => RealtimeControlClient;
};

type RealtimeActivityState = Exclude<VoicePresenceState, "Muted">;

export type RealtimeVoiceControls = {
  voiceState: VoicePresenceState;
  isMuted: boolean;
  errorMessage: string | null;
  partialTranscript: string;
  lastFinalTranscript: string;
  currentCounterQDeliveryText: string;
  sessionDebug: RealtimeSessionDebug;
  canonicalDebug: CanonicalControlDebug;
  serverDeadlineAt: string | null;
  restoredBootstrap: DevelopmentBootstrapResponse | null;
  isRestoring: boolean;
  controlReconnecting: boolean;
  acknowledgedCodeSource: string | null;
  enableMicrophone: () => Promise<void>;
  mute: () => void;
  unmute: () => void;
  disconnect: () => void;
  speakDevelopmentPhrase: () => void;
  evaluateExaminerDecision: (examinerDecisionId: string) => void;
  deliverAuthorizedPrompt: (promptId: string) => void;
  observeCodeSnapshot: (
    sourceCode: string,
    trigger: "INITIAL_EDITOR_STATE" | "EDIT_BURST",
    idempotencyKey: string,
  ) => void;
  noteCodeActivityStarted: () => void;
  noteCodeActivityIdle: () => void;
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
  const { clientFactory, controlClientFactory } = options;
  const [activityState, setActivityState] = useState<RealtimeActivityState>("Ready");
  const [isMuted, setIsMuted] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [partialTranscript, setPartialTranscript] = useState("");
  const [lastFinalTranscript, setLastFinalTranscript] = useState("");
  const [currentCounterQDeliveryText, setCurrentCounterQDeliveryText] = useState("");
  const [sessionDebug, setSessionDebug] = useState<RealtimeSessionDebug>({
    eventType: null,
    sessionType: null,
    transcriptionModel: null,
    turnDetectionType: null,
    createResponse: null,
    interruptResponse: null,
  });
  const [canonicalDebug, setCanonicalDebug] = useState<CanonicalControlDebug>(
    emptyCanonicalDebug(),
  );
  const [serverDeadlineAt, setServerDeadlineAt] = useState<string | null>(null);
  const [restoredBootstrap, setRestoredBootstrap] = useState<DevelopmentBootstrapResponse | null>(
    null,
  );
  const [isRestoring, setIsRestoring] = useState(true);
  const [controlReconnecting, setControlReconnecting] = useState(false);
  const [acknowledgedCodeSource, setAcknowledgedCodeSource] = useState<string | null>(null);
  const clientRef = useRef<RealtimeVoiceClient | null>(null);
  const controlClientRef = useRef<RealtimeControlClient | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const unsubscribeControlRef = useRef<(() => void) | null>(null);
  const transcriptDraftsRef = useRef(new Map<string, string>());
  const activeTranscriptKeyRef = useRef<string | null>(null);
  const pendingCodeSourceRef = useRef<string | null>(null);
  const autoRestoreAttemptedRef = useRef(false);

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
        setCurrentCounterQDeliveryText,
        setSessionDebug,
        clientRef,
        controlClientRef,
        transcriptDraftsRef,
        activeTranscriptKeyRef,
      });
    });
    clientRef.current = client;
    return client;
  }, [clientFactory]);

  const ensureControlClient = useCallback(() => {
    if (controlClientRef.current) {
      return controlClientRef.current;
    }
    const controlClient =
      controlClientFactory?.() ??
      new RealtimeControlClient({
        apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000",
      });
    unsubscribeControlRef.current = controlClient.on((event) => {
      if (event.type === "connected") {
        setServerDeadlineAt(event.bootstrap.deadline_at);
        setRestoredBootstrap(event.bootstrap);
        setAcknowledgedCodeSource(event.bootstrap.latest_code_snapshot?.source_code ?? null);
        setIsRestoring(false);
        setControlReconnecting(false);
        return;
      }
      if (event.type === "reconnecting") {
        setControlReconnecting(true);
        return;
      }
      if (event.type === "disconnected") {
        setControlReconnecting(true);
        return;
      }
      if (event.type === "debug_updated") {
        setCanonicalDebug(event.debug);
        if (
          event.debug.lastCode.persistence === "ACKNOWLEDGED" &&
          pendingCodeSourceRef.current !== null
        ) {
          setAcknowledgedCodeSource(pendingCodeSourceRef.current);
          pendingCodeSourceRef.current = null;
        }
        return;
      }
      if (event.type === "authorized_prompt") {
        clientRef.current?.speakAuthorizedPrompt(event.prompt.text, {
          counterq_prompt_id: event.prompt.promptId,
          counterq_prompt_origin: event.prompt.origin ?? "SYSTEM",
          counterq_prompt_kind: event.prompt.kind ?? "INSTRUCTION",
        });
        return;
      }
      if (event.type === "error") {
        setErrorMessage(event.message);
      }
    });
    controlClientRef.current = controlClient;
    return controlClient;
  }, [controlClientFactory]);

  const enableMicrophone = useCallback(async () => {
    setErrorMessage(null);
    setActivityState("Connecting");
    const controlClient = ensureControlClient();
    const client = ensureClient();
    try {
      if (!restoredBootstrap) {
        setIsRestoring(true);
      }
      await controlClient.connectDevelopmentInterview();
      await client.connect();
    } catch (error) {
      setActivityState("Error");
      setIsRestoring(false);
      setIsMuted(false);
      setErrorMessage(error instanceof Error ? error.message : "Realtime voice connection failed.");
    }
  }, [ensureClient, ensureControlClient, restoredBootstrap]);

  const mute = useCallback(() => {
    clientRef.current?.setMuted(true);
  }, []);

  const unmute = useCallback(() => {
    clientRef.current?.setMuted(false);
  }, []);

  const disconnect = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
    unsubscribeControlRef.current?.();
    unsubscribeControlRef.current = null;
    clientRef.current?.disconnect();
    clientRef.current = null;
    controlClientRef.current?.disconnect();
    controlClientRef.current = null;
    setErrorMessage(null);
    setActivityState("Ready");
    setIsMuted(false);
    setPartialTranscript("");
    setCurrentCounterQDeliveryText("");
    transcriptDraftsRef.current.clear();
    activeTranscriptKeyRef.current = null;
    pendingCodeSourceRef.current = null;
    setCanonicalDebug(emptyCanonicalDebug());
    setServerDeadlineAt(null);
    setRestoredBootstrap(null);
    setIsRestoring(false);
    setControlReconnecting(false);
    setAcknowledgedCodeSource(null);
  }, []);

  const speakDevelopmentPhrase = useCallback(() => {
    controlClientRef.current?.requestDevelopmentPrompt();
  }, []);

  const evaluateExaminerDecision = useCallback((examinerDecisionId: string) => {
    controlClientRef.current?.requestExaminerDecisionPolicyGate(examinerDecisionId);
  }, []);

  const deliverAuthorizedPrompt = useCallback((promptId: string) => {
    controlClientRef.current?.requestPromptDeliveryPermit(promptId);
  }, []);

  const observeCodeSnapshot = useCallback(
    (
      sourceCode: string,
      trigger: "INITIAL_EDITOR_STATE" | "EDIT_BURST",
      idempotencyKey: string,
    ) => {
      pendingCodeSourceRef.current = sourceCode;
      controlClientRef.current?.sendCandidateCodeSnapshot({
        sourceCode,
        language: "cpp",
        trigger,
        idempotencyKey,
      });
    },
    [],
  );

  const noteCodeActivityStarted = useCallback(() => {
    controlClientRef.current?.sendCandidateCodeActivityStarted();
  }, []);

  const noteCodeActivityIdle = useCallback(() => {
    controlClientRef.current?.sendCandidateCodeActivityIdle();
  }, []);

  useLayoutEffect(() => {
    if (autoRestoreAttemptedRef.current) {
      return;
    }
    autoRestoreAttemptedRef.current = true;
    const controlClient = ensureControlClient();
    if (!controlClient.hasStoredDevelopmentSession()) {
      setIsRestoring(false);
      return;
    }
    setIsRestoring(true);
    void controlClient.restoreExistingDevelopmentInterview().catch((error) => {
      setIsRestoring(false);
      setErrorMessage(
        error instanceof Error ? error.message : "CounterQ could not restore this interview.",
      );
    });
  }, [ensureControlClient]);

  useEffect(() => disconnect, [disconnect]);

  return {
    voiceState,
    isMuted,
    errorMessage,
    partialTranscript,
    lastFinalTranscript,
    currentCounterQDeliveryText,
    sessionDebug,
    canonicalDebug,
    serverDeadlineAt,
    restoredBootstrap,
    isRestoring,
    controlReconnecting,
    acknowledgedCodeSource,
    enableMicrophone,
    mute,
    unmute,
    disconnect,
    speakDevelopmentPhrase,
    evaluateExaminerDecision,
    deliverAuthorizedPrompt,
    observeCodeSnapshot,
    noteCodeActivityStarted,
    noteCodeActivityIdle,
  };
}

type RealtimeEventSetters = {
  setActivityState: (state: RealtimeActivityState) => void;
  setErrorMessage: (message: string | null) => void;
  setIsMuted: (muted: boolean) => void;
  setPartialTranscript: (text: string) => void;
  setLastFinalTranscript: (text: string) => void;
  setCurrentCounterQDeliveryText: Dispatch<SetStateAction<string>>;
  setSessionDebug: (debug: RealtimeSessionDebug) => void;
  clientRef: MutableRefObject<RealtimeVoiceClient | null>;
  controlClientRef: MutableRefObject<RealtimeControlClient | null>;
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
    setCurrentCounterQDeliveryText,
    setSessionDebug,
    clientRef,
    controlClientRef,
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
    clientRef.current?.interruptActiveOutputForCandidateSpeech();
    controlClientRef.current?.sendCandidateSpeechStarted(null);
    return;
  }
  if (event.type === "counterq_output_started") {
    setActivityState("Speaking");
    setCurrentCounterQDeliveryText("");
    if (!event.playbackStarted) {
      controlClientRef.current?.noteOutputAudioDelta(event.responseId ?? null);
    }
    if (event.playbackStarted) {
      controlClientRef.current?.sendDeliveryStarted(
        event.responseId ?? null,
        event.itemId ?? null,
      );
    }
    return;
  }
  if (event.type === "counterq_response_created") {
    controlClientRef.current?.noteProviderResponseCreated(event.responseId, event.itemId);
    return;
  }
  if (
    event.type === "counterq_output_ended" ||
    event.type === "counterq_output_interrupted" ||
    event.type === "candidate_speech_stopped"
  ) {
    setActivityState("Listening");
    if (event.type === "candidate_speech_stopped") {
      controlClientRef.current?.sendCandidateSpeechStopped(null);
    }
    if (event.type === "counterq_output_ended" && event.playbackComplete) {
      controlClientRef.current?.sendDeliveryCompleted(event.responseId ?? null);
    }
    if (
      event.type === "counterq_output_interrupted" &&
      event.confirmedBy &&
      event.confirmedBy !== "input_audio_buffer.speech_started"
    ) {
      controlClientRef.current?.sendDeliveryInterrupted(
        event.responseId ?? null,
        event.itemId ?? null,
        event.confirmedBy,
        event.audioEndMs ?? null,
      );
    }
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
    setCurrentCounterQDeliveryText("");
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
    controlClientRef.current?.sendCandidateTranscriptFinal({
      providerItemId: event.itemId,
      contentIndex: event.contentIndex,
      transcript: event.text,
    });
    return;
  }
  if (event.type === "counterq_output_transcript_delta") {
    setCurrentCounterQDeliveryText((current) => `${current}${event.text}`);
    controlClientRef.current?.noteOutputTranscriptDelta(event.responseId, event.text);
    return;
  }
  if (event.type === "counterq_output_transcript_final") {
    setCurrentCounterQDeliveryText(event.text);
    controlClientRef.current?.noteOutputTranscriptFinal(event.responseId, event.text);
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
    controlClientRef.current?.noteRealtimeDisconnected("voice_error");
  }
}

function transcriptEventKey(itemId: string | null, contentIndex: number | null): string {
  return `${itemId ?? "latest"}:${contentIndex ?? 0}`;
}

function emptyCanonicalDebug(): CanonicalControlDebug {
  return {
    sessionId: null,
    controlConnected: false,
    pendingDurableMessages: 0,
    lastServerSequence: null,
    stateVersion: null,
    probeBudgetUsed: null,
    probeBudgetMax: null,
    lastCandidateFinal: {
      providerItemId: null,
      eventId: null,
      transcriptSegmentId: null,
      persistence: "PENDING",
    },
    lastDelivery: {
      promptId: null,
      deliveryId: null,
      deliveryState: null,
      providerResponseId: null,
      actualTranscriptId: null,
      localPlaybackState: "NOT_STARTED",
      canonicalState: null,
      outputTranscriptState: "NONE",
      pendingTerminalEvent: "NONE",
      lifecycleEvents: [],
    },
    lastObservation: {
      kind: null,
      sourceEventId: null,
      sourceEventWatermark: null,
      stateVersion: null,
      stage: null,
      triggerClass: null,
    },
    lastCode: {
      snapshotId: null,
      version: null,
      hashPrefix: null,
      diffId: null,
      persistence: "PENDING",
    },
    lastVoice: {
      transcriptSegmentId: null,
      associatedCodeSnapshotId: null,
      associatedCodeSnapshotVersion: null,
    },
    lastPolicyGate: {
      decisionId: null,
      disposition: null,
      decisionStatus: null,
      policyGateOutcome: null,
      promptId: null,
      promptKind: null,
    },
    lastDeliveryPermit: {
      promptId: null,
      status: null,
      reason: null,
    },
  };
}
