export const CONTROL_PROTOCOL_VERSION = "counterq.realtime.control.v1";
const CLIENT_INSTANCE_STORAGE_KEY = "counterq:realtime-control:client-instance-id";
const MAX_PENDING_MESSAGES = 20;

export type DevelopmentBootstrapResponse = {
  interview_session_id: string;
  current_stage: string;
  state_version: number;
  deadline_at: string;
  control_websocket_path: string;
  protocol_version: typeof CONTROL_PROTOCOL_VERSION;
};

export type AuthorizedDevelopmentPrompt = {
  promptId: string;
  text: string;
  origin?: string;
  kind?: string;
};

export type PolicyGateDebug = {
  decisionId: string | null;
  disposition: string | null;
  decisionStatus: string | null;
  policyGateOutcome: string | null;
  promptId: string | null;
  promptKind: string | null;
};

export type DeliveryPermitDebug = {
  promptId: string | null;
  status: string | null;
  reason: string | null;
};

export type CanonicalCandidateFinal = {
  providerItemId: string | null;
  eventId: string | null;
  transcriptSegmentId: string | null;
  persistence: "ACKNOWLEDGED" | "PENDING" | "FAILED";
};

export type CanonicalDeliveryDebug = {
  promptId: string | null;
  deliveryId: string | null;
  deliveryState: string | null;
  providerResponseId: string | null;
  actualTranscriptId: string | null;
};

export type CanonicalObservationDebug = {
  kind: string | null;
  sourceEventId: string | null;
  sourceEventWatermark: number | null;
  stateVersion: number | null;
  stage: string | null;
  triggerClass: string | null;
};

export type CanonicalCodeDebug = {
  snapshotId: string | null;
  version: number | null;
  hashPrefix: string | null;
  diffId: string | null;
  persistence: "ACKNOWLEDGED" | "PENDING" | "FAILED";
};

export type CanonicalVoiceDebug = {
  transcriptSegmentId: string | null;
  associatedCodeSnapshotId: string | null;
  associatedCodeSnapshotVersion: number | null;
};

export type CanonicalControlDebug = {
  sessionId: string | null;
  controlConnected: boolean;
  pendingDurableMessages: number;
  lastServerSequence: number | null;
  stateVersion: number | null;
  lastCandidateFinal: CanonicalCandidateFinal;
  lastDelivery: CanonicalDeliveryDebug;
  lastObservation: CanonicalObservationDebug;
  lastCode: CanonicalCodeDebug;
  lastVoice: CanonicalVoiceDebug;
  lastPolicyGate: PolicyGateDebug;
  lastDeliveryPermit: DeliveryPermitDebug;
};

export type RealtimeControlEvent =
  | { type: "connected"; bootstrap: DevelopmentBootstrapResponse }
  | { type: "disconnected" }
  | { type: "debug_updated"; debug: CanonicalControlDebug }
  | { type: "authorized_prompt"; prompt: AuthorizedDevelopmentPrompt }
  | { type: "policy_gate_result"; result: PolicyGateDebug }
  | { type: "delivery_permit_result"; result: DeliveryPermitDebug }
  | { type: "delivery_started"; promptId: string; deliveryId: string; providerResponseId: string }
  | { type: "error"; message: string };

type RealtimeControlListener = (event: RealtimeControlEvent) => void;

type ControlWebSocket = Pick<
  WebSocket,
  "readyState" | "send" | "close" | "addEventListener" | "removeEventListener"
>;

export type RealtimeControlClientOptions = {
  apiBaseUrl: string;
  fetchFn?: typeof fetch;
  websocketFactory?: (url: string) => ControlWebSocket;
  storage?: Pick<Storage, "getItem" | "setItem">;
  randomUUID?: () => string;
};

type PendingEnvelope = {
  clientEventId: string;
  message: Record<string, unknown>;
};

type ActivePromptDelivery = {
  promptId: string;
  intendedText: string;
  providerResponseId: string;
  providerItemId: string | null;
  deliveryId: string | null;
  outputTranscript: string;
};

export class RealtimeControlClient {
  private readonly apiBaseUrl: string;
  private readonly fetchFn: typeof fetch;
  private readonly websocketFactory: (url: string) => ControlWebSocket;
  private readonly storage: Pick<Storage, "getItem" | "setItem"> | undefined;
  private readonly randomUUID: () => string;
  private readonly listeners = new Set<RealtimeControlListener>();
  private readonly pending = new Map<string, PendingEnvelope>();
  private websocket: ControlWebSocket | null = null;
  private bootstrap: DevelopmentBootstrapResponse | null = null;
  private controlReady = false;
  private clientSequence = 0;
  private activeDelivery: ActivePromptDelivery | null = null;
  private debug: CanonicalControlDebug = emptyDebug();

  constructor(options: RealtimeControlClientOptions) {
    this.apiBaseUrl = options.apiBaseUrl.replace(/\/$/, "");
    this.fetchFn = options.fetchFn ?? fetch.bind(globalThis);
    this.storage = options.storage ?? globalThis.sessionStorage;
    this.randomUUID = options.randomUUID ?? (() => globalThis.crypto?.randomUUID?.() ?? fallbackId());
    this.websocketFactory = options.websocketFactory ?? ((url) => new WebSocket(url));
  }

  on(listener: RealtimeControlListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  get pendingCount(): number {
    return this.pending.size;
  }

  async connectDevelopmentInterview(): Promise<DevelopmentBootstrapResponse> {
    if (!this.bootstrap) {
      const response = await this.fetchFn(`${this.apiBaseUrl}/api/realtime/development-interview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ purpose: "interview_demo" }),
      });
      if (!response.ok) {
        throw new Error("CounterQ could not create a development interview session.");
      }
      this.bootstrap = (await response.json()) as DevelopmentBootstrapResponse;
      this.patchDebug({
        sessionId: this.bootstrap.interview_session_id,
        stateVersion: this.bootstrap.state_version,
      });
    }

    await this.openWebSocket();
    return this.bootstrap;
  }

  disconnect(): void {
    this.websocket?.close();
    this.websocket = null;
    this.controlReady = false;
    this.activeDelivery = null;
    this.patchDebug({ controlConnected: false, pendingDurableMessages: this.pending.size });
    this.emit({ type: "disconnected" });
  }

  sendCandidateSpeechStarted(providerItemId: string | null): void {
    this.sendBestEffort({
      type: "candidate_speech_started",
      provider_item_id: providerItemId,
    });
  }

  sendCandidateSpeechStopped(providerItemId: string | null): void {
    this.sendBestEffort({
      type: "candidate_speech_stopped",
      provider_item_id: providerItemId,
    });
  }

  sendCandidateTranscriptFinal({
    providerItemId,
    contentIndex,
    transcript,
  }: {
    providerItemId: string | null;
    contentIndex: number | null;
    transcript: string;
  }): void {
    const normalizedProviderItemId = providerItemId ?? "provider-item-unavailable";
    this.sendDurable({
      type: "candidate_transcript_finalized",
      provider_item_id: normalizedProviderItemId,
      content_index: contentIndex,
      transcript,
      idempotency_key: `candidate-transcript:${normalizedProviderItemId}:${contentIndex ?? 0}`,
    });
    this.patchDebug({
      lastCandidateFinal: {
        providerItemId: normalizedProviderItemId,
        eventId: null,
        transcriptSegmentId: null,
        persistence: "PENDING",
      },
    });
  }

  requestDevelopmentPrompt(): void {
    this.sendDurable({ type: "development_authorized_prompt_requested" });
  }

  requestExaminerDecisionPolicyGate(examinerDecisionId: string): void {
    this.sendDurable({
      type: "examiner_decision_policy_gate_requested",
      examiner_decision_id: examinerDecisionId,
    });
  }

  requestPromptDeliveryPermit(promptId: string): void {
    this.sendDurable({
      type: "prompt_delivery_permit_requested",
      interviewer_prompt_id: promptId,
    });
  }

  sendCandidateCodeActivityStarted(): void {
    this.sendBestEffort({ type: "candidate_code_activity_started" });
  }

  sendCandidateCodeActivityIdle(): void {
    this.sendBestEffort({ type: "candidate_code_activity_idle" });
  }

  noteProviderResponseCreated(providerResponseId: string, providerItemId: string | null): void {
    if (!this.activeDelivery) {
      return;
    }
    this.activeDelivery.providerResponseId = providerResponseId;
    this.activeDelivery.providerItemId = providerItemId;
    this.patchDebug({
      lastDelivery: {
        ...this.debug.lastDelivery,
        promptId: this.activeDelivery.promptId,
        providerResponseId,
      },
    });
  }

  noteOutputTranscriptDelta(providerResponseId: string | null, text: string): void {
    if (!this.activeDelivery) {
      return;
    }
    if (providerResponseId && providerResponseId !== this.activeDelivery.providerResponseId) {
      return;
    }
    this.activeDelivery.outputTranscript += text;
  }

  noteOutputTranscriptFinal(providerResponseId: string | null, text: string): void {
    if (!this.activeDelivery) {
      return;
    }
    if (providerResponseId && providerResponseId !== this.activeDelivery.providerResponseId) {
      return;
    }
    this.activeDelivery.outputTranscript = text;
  }

  sendDeliveryStarted(providerResponseId: string | null, providerItemId: string | null): void {
    if (!this.activeDelivery) {
      return;
    }
    const responseId = providerResponseId ?? this.activeDelivery.providerResponseId;
    this.activeDelivery.providerResponseId = responseId;
    this.activeDelivery.providerItemId = providerItemId ?? this.activeDelivery.providerItemId;
    this.sendDurable({
      type: "counterq_delivery_started",
      interviewer_prompt_id: this.activeDelivery.promptId,
      intended_text: this.activeDelivery.intendedText,
      provider_response_id: responseId,
      provider_item_id: this.activeDelivery.providerItemId,
    });
  }

  sendDeliveryCompleted(providerResponseId: string | null): void {
    if (!this.activeDelivery?.deliveryId || !this.activeDelivery.outputTranscript.trim()) {
      return;
    }
    const responseId = providerResponseId ?? this.activeDelivery.providerResponseId;
    this.sendDurable({
      type: "counterq_delivery_completed",
      interviewer_prompt_id: this.activeDelivery.promptId,
      prompt_delivery_id: this.activeDelivery.deliveryId,
      provider_response_id: responseId,
      provider_item_id: this.activeDelivery.providerItemId,
      transcript: this.activeDelivery.outputTranscript,
      idempotency_key: `counterq-delivered:${this.activeDelivery.deliveryId}:${responseId}`,
    });
  }

  sendDeliveryInterrupted(
    providerResponseId: string | null,
    providerItemId: string | null,
    confirmedBy: string,
    audioEndMs: number | null,
  ): void {
    if (!this.activeDelivery?.deliveryId) {
      return;
    }
    const responseId = providerResponseId ?? this.activeDelivery.providerResponseId;
    this.sendDurable({
      type: "counterq_delivery_interrupted",
      interviewer_prompt_id: this.activeDelivery.promptId,
      prompt_delivery_id: this.activeDelivery.deliveryId,
      provider_response_id: responseId,
      provider_item_id: providerItemId ?? this.activeDelivery.providerItemId,
      confirmed_by: confirmedBy,
      audio_end_ms: audioEndMs,
      idempotency_key: `counterq-interrupted:${this.activeDelivery.deliveryId}:${responseId}`,
    });
  }

  noteRealtimeDisconnected(reason: string): void {
    this.sendDurable({
      type: "realtime_disconnected",
      reason,
      idempotency_key: `realtime-disconnected:${this.bootstrap?.interview_session_id ?? "unknown"}:${reason}`,
    });
  }

  noteRealtimeReconnected(): void {
    this.sendDurable({
      type: "realtime_reconnected",
      idempotency_key: `realtime-reconnected:${this.bootstrap?.interview_session_id ?? "unknown"}:${this.clientSequence + 1}`,
    });
  }

  sendCandidateCodeSnapshot({
    sourceCode,
    language,
    trigger,
    idempotencyKey,
  }: {
    sourceCode: string;
    language: string;
    trigger: "INITIAL_EDITOR_STATE" | "EDIT_BURST";
    idempotencyKey: string;
  }): void {
    this.sendDurable({
      type: "candidate_code_snapshot",
      source_code: sourceCode,
      language,
      trigger,
      idempotency_key: idempotencyKey,
    });
    this.patchDebug({
      lastCode: {
        ...this.debug.lastCode,
        persistence: "PENDING",
      },
    });
  }

  private async openWebSocket(): Promise<void> {
    if (!this.bootstrap) {
      throw new Error("Development interview has not been bootstrapped.");
    }
    if (this.websocket?.readyState === WebSocket.OPEN) {
      if (this.controlReady) {
        return;
      }
      await this.waitForControlReady();
      return;
    }
    const url = websocketUrl(this.apiBaseUrl, this.bootstrap.control_websocket_path);
    const websocket = this.websocketFactory(url);
    this.websocket = websocket;
    this.controlReady = false;
    websocket.addEventListener("message", (event) => this.handleServerMessage(event));
    websocket.addEventListener("close", () => {
      if (this.websocket === websocket) {
        this.controlReady = false;
        this.patchDebug({ controlConnected: false });
        this.emit({ type: "disconnected" });
      }
    });

    await new Promise<void>((resolve, reject) => {
      const cleanup = () => {
        websocket.removeEventListener("open", handleOpen);
        websocket.removeEventListener("error", handleError);
      };
      const handleOpen = () => {
        cleanup();
        resolve();
      };
      const handleError = () => {
        cleanup();
        reject(new Error("CounterQ realtime control channel failed."));
      };
      websocket.addEventListener("open", handleOpen);
      websocket.addEventListener("error", handleError);
    });
    await this.waitForControlReady();
    this.sendBestEffort({
      type: "client_hello",
      last_acknowledged_server_sequence: this.debug.lastServerSequence,
    });
  }

  private sendBestEffort(message: Record<string, unknown>): void {
    this.sendNow(this.wrapMessage(message));
  }

  private sendDurable(message: Record<string, unknown>): string {
    const wrapped = this.wrapMessage(message);
    const clientEventId = String(wrapped.client_event_id);
    this.pending.set(clientEventId, { clientEventId, message: wrapped });
    while (this.pending.size > MAX_PENDING_MESSAGES) {
      const oldest = this.pending.keys().next().value as string | undefined;
      if (!oldest) {
        break;
      }
      this.pending.delete(oldest);
    }
    this.patchDebug({ pendingDurableMessages: this.pending.size });
    this.sendNow(wrapped);
    return clientEventId;
  }

  private wrapMessage(message: Record<string, unknown>): Record<string, unknown> {
    this.clientSequence += 1;
    return {
      protocol_version: CONTROL_PROTOCOL_VERSION,
      client_event_id: `ctrl-${this.clientSequence}-${this.randomUUID()}`,
      client_instance_id: this.clientInstanceId(),
      client_sequence: this.clientSequence,
      ...message,
    };
  }

  private sendNow(message: Record<string, unknown>): void {
    if (this.websocket?.readyState !== WebSocket.OPEN || !this.controlReady) {
      return;
    }
    this.websocket.send(JSON.stringify(message));
  }

  private resendPending(): void {
    for (const pending of this.pending.values()) {
      this.sendNow(pending.message);
    }
    this.patchDebug({ pendingDurableMessages: this.pending.size });
  }

  private handleServerMessage(event: MessageEvent): void {
    let raw: unknown;
    try {
      raw = JSON.parse(String(event.data));
    } catch {
      this.emit({ type: "error", message: "CounterQ control channel sent malformed data." });
      return;
    }
    if (!raw || typeof raw !== "object") {
      return;
    }
    const message = raw as Record<string, unknown>;
    const type = typeof message.type === "string" ? message.type : null;
    if (type === "server_hello") {
      this.controlReady = true;
      this.patchDebug({
        controlConnected: true,
        lastServerSequence: numberField(message.last_server_sequence),
        stateVersion: numberField(message.state_version),
      });
      if (this.bootstrap) {
        this.emit({ type: "connected", bootstrap: this.bootstrap });
      }
      this.resendPending();
      return;
    }
    if (type === "development_authorized_prompt") {
      const promptId = stringField(message.interviewer_prompt_id);
      const text = stringField(message.text);
      const clientEventId = stringField(message.client_event_id);
      if (clientEventId) {
        this.ackPending(clientEventId);
      }
      if (promptId && text) {
        this.activeDelivery = {
          promptId,
          intendedText: text,
          providerResponseId: `pending-${promptId}`,
          providerItemId: null,
          deliveryId: null,
          outputTranscript: "",
        };
        this.patchDebug({
          lastDelivery: {
            promptId,
            deliveryId: null,
            deliveryState: "AUTHORIZED",
            providerResponseId: null,
            actualTranscriptId: null,
          },
        });
        this.emit({ type: "authorized_prompt", prompt: { promptId, text } });
      }
      return;
    }
    if (type === "policy_gate_result") {
      const clientEventId = stringField(message.client_event_id);
      if (clientEventId) {
        this.ackPending(clientEventId);
      }
      const result: PolicyGateDebug = {
        decisionId: stringField(message.examiner_decision_id),
        disposition: stringField(message.disposition),
        decisionStatus: stringField(message.decision_status),
        policyGateOutcome: stringField(message.policy_gate_outcome),
        promptId: stringField(message.interviewer_prompt_id),
        promptKind: stringField(message.prompt_kind),
      };
      this.patchDebug({ lastPolicyGate: result });
      this.emit({ type: "policy_gate_result", result });
      return;
    }
    if (type === "prompt_delivery_permit") {
      const clientEventId = stringField(message.client_event_id);
      if (clientEventId) {
        this.ackPending(clientEventId);
      }
      const promptId = stringField(message.interviewer_prompt_id);
      const text = stringField(message.text);
      const result: DeliveryPermitDebug = {
        promptId,
        status: stringField(message.status) ?? "PERMITTED",
        reason: stringField(message.reason),
      };
      this.patchDebug({ lastDeliveryPermit: result });
      this.emit({ type: "delivery_permit_result", result });
      if (promptId && text) {
        this.activeDelivery = {
          promptId,
          intendedText: text,
          providerResponseId: `pending-${promptId}`,
          providerItemId: null,
          deliveryId: null,
          outputTranscript: "",
        };
        this.patchDebug({
          lastDelivery: {
            promptId,
            deliveryId: null,
            deliveryState: "PERMITTED",
            providerResponseId: null,
            actualTranscriptId: null,
          },
        });
        this.emit({
          type: "authorized_prompt",
          prompt: {
            promptId,
            text,
            origin: stringField(message.origin) ?? undefined,
            kind: stringField(message.kind) ?? undefined,
          },
        });
      }
      return;
    }
    if (type === "prompt_delivery_permit_result") {
      const clientEventId = stringField(message.client_event_id);
      if (clientEventId) {
        this.ackPending(clientEventId);
      }
      const result: DeliveryPermitDebug = {
        promptId: stringField(message.interviewer_prompt_id),
        status: stringField(message.status),
        reason: stringField(message.reason),
      };
      this.patchDebug({ lastDeliveryPermit: result });
      this.emit({ type: "delivery_permit_result", result });
      return;
    }
    if (type === "durable_event_ack") {
      const clientEventId = stringField(message.client_event_id);
      if (clientEventId) {
        this.ackPending(clientEventId);
      }
      const observationKind = stringField(message.observation_kind);
      const sourceEventId = stringField(message.interview_event_id);
      const sourceWatermark = numberField(message.server_sequence);
      const stateVersion = numberField(message.interview_state_version);
      const observationStage = stringField(message.observation_interview_stage);
      const triggerClass = stringField(message.observation_trigger_class);
      this.patchDebug({
        lastServerSequence: sourceWatermark ?? this.debug.lastServerSequence,
        stateVersion: stateVersion ?? this.debug.stateVersion,
        lastObservation: observationKind
          ? {
              kind: observationKind,
              sourceEventId,
              sourceEventWatermark: sourceWatermark,
              stateVersion,
              stage: observationStage,
              triggerClass,
            }
          : this.debug.lastObservation,
      });
      if (message.transcript_segment_id) {
        this.patchDebug({
          lastCandidateFinal: {
            providerItemId: this.debug.lastCandidateFinal.providerItemId,
            eventId: stringField(message.interview_event_id),
            transcriptSegmentId: stringField(message.transcript_segment_id),
            persistence: "ACKNOWLEDGED",
          },
          lastVoice: {
            transcriptSegmentId: stringField(message.transcript_segment_id),
            associatedCodeSnapshotId: stringField(message.associated_code_snapshot_id),
            associatedCodeSnapshotVersion: numberField(message.associated_code_snapshot_version),
          },
        });
      }
      if (message.code_snapshot_id) {
        const contentHash = stringField(message.content_hash);
        this.patchDebug({
          lastCode: {
            snapshotId: stringField(message.code_snapshot_id),
            version: numberField(message.code_version),
            hashPrefix: contentHash ? contentHash.slice(0, 12) : null,
            diffId: stringField(message.code_diff_id),
            persistence: "ACKNOWLEDGED",
          },
        });
      }
      return;
    }
    if (type === "delivery_ack") {
      const clientEventId = stringField(message.client_event_id);
      if (clientEventId) {
        this.ackPending(clientEventId);
      }
      const promptId = stringField(message.interviewer_prompt_id);
      const deliveryId = stringField(message.prompt_delivery_id);
      const deliveryState = stringField(message.delivery_state);
      const actualTranscriptId = stringField(message.actual_transcript_segment_id);
      const observationKind = stringField(message.observation_kind);
      const sourceEventId = stringField(message.interview_event_id);
      const sourceWatermark = numberField(message.server_sequence);
      const stateVersion = numberField(message.interview_state_version);
      const observationStage = stringField(message.observation_interview_stage);
      const triggerClass = stringField(message.observation_trigger_class);
      if (this.activeDelivery && deliveryId) {
        this.activeDelivery.deliveryId = deliveryId;
      }
      this.patchDebug({
        lastServerSequence: sourceWatermark ?? this.debug.lastServerSequence,
        stateVersion: stateVersion ?? this.debug.stateVersion,
        lastObservation: observationKind
          ? {
              kind: observationKind,
              sourceEventId,
              sourceEventWatermark: sourceWatermark,
              stateVersion,
              stage: observationStage,
              triggerClass,
            }
          : this.debug.lastObservation,
        lastDelivery: {
          promptId: promptId ?? this.debug.lastDelivery.promptId,
          deliveryId: deliveryId ?? this.debug.lastDelivery.deliveryId,
          deliveryState: deliveryState ?? this.debug.lastDelivery.deliveryState,
          providerResponseId: this.activeDelivery?.providerResponseId ?? this.debug.lastDelivery.providerResponseId,
          actualTranscriptId,
        },
      });
      if (deliveryState === "STARTED" && promptId && deliveryId) {
        this.emit({
          type: "delivery_started",
          promptId,
          deliveryId,
          providerResponseId:
            this.activeDelivery?.providerResponseId ??
            this.debug.lastDelivery.providerResponseId ??
            "provider-response-unavailable",
        });
      }
      return;
    }
    if (type === "control_error") {
      const clientEventId = stringField(message.client_event_id);
      if (clientEventId) {
        const pendingMessage = this.pending.get(clientEventId)?.message;
        this.pending.delete(clientEventId);
        this.patchDebug({ pendingDurableMessages: this.pending.size });
        if (pendingMessage?.type === "candidate_code_snapshot") {
          this.patchDebug({
            lastCode: {
              ...this.debug.lastCode,
              persistence: "FAILED",
            },
          });
        }
      }
      this.emit({ type: "error", message: "CounterQ control message was rejected." });
    }
  }

  private ackPending(clientEventId: string): void {
    this.pending.delete(clientEventId);
    this.patchDebug({ pendingDurableMessages: this.pending.size });
  }

  private clientInstanceId(): string {
    const existing = this.storage?.getItem(CLIENT_INSTANCE_STORAGE_KEY);
    if (existing) {
      return existing;
    }
    const next = this.randomUUID();
    this.storage?.setItem(CLIENT_INSTANCE_STORAGE_KEY, next);
    return next;
  }

  private patchDebug(patch: Partial<CanonicalControlDebug>): void {
    this.debug = { ...this.debug, ...patch };
    this.emit({ type: "debug_updated", debug: this.debug });
  }

  private emit(event: RealtimeControlEvent): void {
    for (const listener of this.listeners) {
      listener(event);
    }
  }

  private waitForControlReady(): Promise<void> {
    if (this.controlReady) {
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      const timeoutId = window.setTimeout(() => {
        cleanup();
        reject(new Error("CounterQ realtime control channel did not become ready."));
      }, 8_000);
      const unsubscribe = this.on((event) => {
        if (event.type === "connected") {
          cleanup();
          resolve();
        }
      });
      const cleanup = () => {
        window.clearTimeout(timeoutId);
        unsubscribe();
      };
    });
  }
}

function websocketUrl(apiBaseUrl: string, path: string): string {
  const url = new URL(path, apiBaseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function fallbackId(): string {
  return `id-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function stringField(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function numberField(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function emptyDebug(): CanonicalControlDebug {
  return {
    sessionId: null,
    controlConnected: false,
    pendingDurableMessages: 0,
    lastServerSequence: null,
    stateVersion: null,
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
