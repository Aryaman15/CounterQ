export const CONTROL_PROTOCOL_VERSION = "counterq.realtime.control.v1";
const CLIENT_INSTANCE_STORAGE_KEY = "counterq:realtime-control:client-instance-id";
const MAX_PENDING_MESSAGES = 20;

export type DevelopmentBootstrapResponse = {
  interview_session_id: string;
  template: string;
  configured_duration_seconds: number;
  current_stage: string;
  state_version: number;
  deadline_at: string;
  time_remaining_seconds: number;
  time_pressure: string;
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
  requestState?: "IDLE" | "REQUESTED" | "RECEIVED" | "FAILED";
  disposition: string | null;
  decisionStatus: string | null;
  policyGateOutcome: string | null;
  promptId: string | null;
  promptKind: string | null;
  reason?: string | null;
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
  localPlaybackState: "NOT_STARTED" | "PLAYING" | "COMPLETED" | "INTERRUPTED";
  canonicalState:
    | "PERMITTED"
    | "PLAYBACK_START_OBSERVED"
    | "START_REQUESTED"
    | "STARTED"
    | "COMPLETION_OBSERVED"
    | "COMPLETION_REQUESTED"
    | "DELIVERED"
    | "INTERRUPTION_OBSERVED"
    | "INTERRUPTION_REQUESTED"
    | "INTERRUPTED"
    | null;
  outputTranscriptState: "NONE" | "PARTIAL" | "FINAL";
  pendingTerminalEvent: "NONE" | "COMPLETION" | "INTERRUPTION";
  lifecycleEvents: string[];
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
  probeBudgetUsed: number | null;
  probeBudgetMax: number | null;
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
  outputTranscriptState: "NONE" | "PARTIAL" | "FINAL";
  localPlaybackState: "NOT_STARTED" | "PLAYING" | "COMPLETED" | "INTERRUPTED";
  canonicalState: NonNullable<CanonicalDeliveryDebug["canonicalState"]>;
  playbackStartObserved: boolean;
  startRequested: boolean;
  completionRequested: boolean;
  interruptionRequested: boolean;
  pendingTerminalEvent: PendingTerminalEvent | null;
};

type PendingTerminalEvent =
  | {
      kind: "COMPLETION";
      providerResponseId: string;
      providerItemId: string | null;
    }
  | {
      kind: "INTERRUPTION";
      providerResponseId: string;
      providerItemId: string | null;
      confirmedBy: string;
      audioEndMs: number | null;
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
    const controlConnected = this.websocket?.readyState === WebSocket.OPEN && this.controlReady;
    this.patchDebug({
      lastPolicyGate: {
        decisionId: examinerDecisionId,
        requestState: "REQUESTED",
        disposition: null,
        decisionStatus: null,
        policyGateOutcome: null,
        promptId: null,
        promptKind: null,
        reason: controlConnected
          ? "Policy gate request sent."
          : "CONTROL DISCONNECTED; policy request pending/not sent.",
      },
    });
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
    if (!this.acceptProviderResponse(providerResponseId)) {
      return;
    }
    this.activeDelivery.providerResponseId = providerResponseId;
    this.activeDelivery.providerItemId = providerItemId ?? this.activeDelivery.providerItemId;
    this.recordDeliveryLifecycle("response.created");
    this.syncDeliveryDebug();
    this.maybeSendDeliveryStarted();
  }

  noteOutputTranscriptDelta(providerResponseId: string | null, text: string): void {
    if (!this.activeDelivery) {
      return;
    }
    if (!this.reconcileProviderResponse(providerResponseId)) {
      return;
    }
    this.activeDelivery.outputTranscript += text;
    if (this.activeDelivery.outputTranscriptState !== "FINAL") {
      this.activeDelivery.outputTranscriptState = "PARTIAL";
    }
    this.recordDeliveryLifecycle("response.output_audio_transcript.delta");
    this.syncDeliveryDebug();
  }

  noteOutputAudioDelta(providerResponseId: string | null): void {
    if (!this.activeDelivery) {
      return;
    }
    if (!this.reconcileProviderResponse(providerResponseId)) {
      return;
    }
    this.recordDeliveryLifecycle("response.output_audio.delta");
    this.syncDeliveryDebug();
  }

  noteOutputTranscriptFinal(providerResponseId: string | null, text: string): void {
    if (!this.activeDelivery) {
      return;
    }
    if (
      this.activeDelivery.completionRequested ||
      this.activeDelivery.canonicalState === "DELIVERED"
    ) {
      return;
    }
    if (!this.reconcileProviderResponse(providerResponseId)) {
      return;
    }
    this.activeDelivery.outputTranscript = text;
    this.activeDelivery.outputTranscriptState = "FINAL";
    this.recordDeliveryLifecycle("response.output_audio_transcript.done");
    this.syncDeliveryDebug();
    this.maybeFlushPendingTerminalEvent();
  }

  sendDeliveryStarted(providerResponseId: string | null, providerItemId: string | null): void {
    if (!this.activeDelivery) {
      return;
    }
    if (
      this.activeDelivery.interruptionRequested ||
      this.activeDelivery.canonicalState === "INTERRUPTED"
    ) {
      return;
    }
    if (!this.reconcileProviderResponse(providerResponseId)) {
      return;
    }
    this.activeDelivery.providerItemId = providerItemId ?? this.activeDelivery.providerItemId;
    this.activeDelivery.playbackStartObserved = true;
    this.activeDelivery.localPlaybackState = "PLAYING";
    if (!this.activeDelivery.startRequested) {
      this.activeDelivery.canonicalState = "PLAYBACK_START_OBSERVED";
    }
    this.recordDeliveryLifecycle("playback_start_observed");
    this.syncDeliveryDebug();
    this.maybeSendDeliveryStarted();
  }

  private maybeSendDeliveryStarted(): void {
    if (!this.activeDelivery?.playbackStartObserved || this.activeDelivery.startRequested) {
      return;
    }
    if (isPendingProviderResponseId(this.activeDelivery.providerResponseId)) {
      return;
    }
    this.activeDelivery.startRequested = true;
    this.activeDelivery.canonicalState = "START_REQUESTED";
    this.recordDeliveryLifecycle("delivery_start_requested");
    this.syncDeliveryDebug();
    this.sendDurable({
      type: "counterq_delivery_started",
      interviewer_prompt_id: this.activeDelivery.promptId,
      intended_text: this.activeDelivery.intendedText,
      provider_response_id: this.activeDelivery.providerResponseId,
      provider_item_id: this.activeDelivery.providerItemId,
    });
  }

  sendDeliveryCompleted(providerResponseId: string | null): void {
    if (!this.activeDelivery) {
      return;
    }
    if (!this.reconcileProviderResponse(providerResponseId)) {
      return;
    }
    if (isPendingProviderResponseId(this.activeDelivery.providerResponseId)) {
      return;
    }
    this.activeDelivery.localPlaybackState = "COMPLETED";
    this.activeDelivery.canonicalState = "COMPLETION_OBSERVED";
    this.activeDelivery.pendingTerminalEvent = {
      kind: "COMPLETION",
      providerResponseId: this.activeDelivery.providerResponseId,
      providerItemId: this.activeDelivery.providerItemId,
    };
    this.recordDeliveryLifecycle("delivery_completion_observed");
    this.syncDeliveryDebug();
    this.maybeFlushPendingTerminalEvent();
  }

  private maybeFlushPendingTerminalEvent(): void {
    if (!this.activeDelivery?.pendingTerminalEvent || !this.activeDelivery.deliveryId) {
      return;
    }
    const terminal = this.activeDelivery.pendingTerminalEvent;
    if (terminal.kind === "COMPLETION") {
      if (
        this.activeDelivery.completionRequested ||
        this.activeDelivery.outputTranscriptState !== "FINAL" ||
        !this.activeDelivery.outputTranscript.trim()
      ) {
        return;
      }
      this.activeDelivery.completionRequested = true;
      this.activeDelivery.canonicalState = "COMPLETION_REQUESTED";
      this.activeDelivery.pendingTerminalEvent = null;
      this.recordDeliveryLifecycle("delivery_completed_sent");
      this.syncDeliveryDebug();
      this.sendDurable({
        type: "counterq_delivery_completed",
        interviewer_prompt_id: this.activeDelivery.promptId,
        prompt_delivery_id: this.activeDelivery.deliveryId,
        provider_response_id: terminal.providerResponseId,
        provider_item_id: terminal.providerItemId,
        transcript: this.activeDelivery.outputTranscript,
        idempotency_key: `counterq-delivered:${this.activeDelivery.deliveryId}:${terminal.providerResponseId}`,
      });
      return;
    }
    if (this.activeDelivery.interruptionRequested) {
      return;
    }
    this.activeDelivery.interruptionRequested = true;
    this.activeDelivery.canonicalState = "INTERRUPTION_REQUESTED";
    this.activeDelivery.pendingTerminalEvent = null;
    this.recordDeliveryLifecycle("delivery_interrupted_sent");
    this.syncDeliveryDebug();
    this.sendDurable({
      type: "counterq_delivery_interrupted",
      interviewer_prompt_id: this.activeDelivery.promptId,
      prompt_delivery_id: this.activeDelivery.deliveryId,
      provider_response_id: terminal.providerResponseId,
      provider_item_id: terminal.providerItemId,
      confirmed_by: terminal.confirmedBy,
      audio_end_ms: terminal.audioEndMs,
      idempotency_key: `counterq-interrupted:${this.activeDelivery.deliveryId}:${terminal.providerResponseId}`,
    });
  }

  sendDeliveryInterrupted(
    providerResponseId: string | null,
    providerItemId: string | null,
    confirmedBy: string,
    audioEndMs: number | null,
  ): void {
    if (!this.activeDelivery) {
      return;
    }
    if (!this.reconcileProviderResponse(providerResponseId)) {
      return;
    }
    this.activeDelivery.providerItemId = providerItemId ?? this.activeDelivery.providerItemId;
    if (isPendingProviderResponseId(this.activeDelivery.providerResponseId)) {
      return;
    }
    this.activeDelivery.localPlaybackState = "INTERRUPTED";
    this.activeDelivery.canonicalState = "INTERRUPTION_OBSERVED";
    this.activeDelivery.pendingTerminalEvent = {
      kind: "INTERRUPTION",
      providerResponseId: this.activeDelivery.providerResponseId,
      providerItemId: this.activeDelivery.providerItemId,
      confirmedBy,
      audioEndMs,
    };
    this.recordDeliveryLifecycle("delivery_interruption_observed");
    this.syncDeliveryDebug();
    this.maybeFlushPendingTerminalEvent();
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
        probeBudgetUsed: numberField(message.probe_budget_used),
        probeBudgetMax: numberField(message.probe_budget_max),
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
        this.activeDelivery = this.createActiveDelivery(promptId, text, "PERMITTED");
        this.syncDeliveryDebug();
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
        requestState: "RECEIVED",
        disposition: stringField(message.disposition),
        decisionStatus: stringField(message.decision_status),
        policyGateOutcome: stringField(message.policy_gate_outcome),
        promptId: stringField(message.interviewer_prompt_id),
        promptKind: stringField(message.prompt_kind),
        reason: stringField(message.reason),
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
        this.activeDelivery = this.createActiveDelivery(promptId, text, "PERMITTED");
        this.syncDeliveryDebug();
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
        probeBudgetUsed: numberField(message.probe_budget_used) ?? this.debug.probeBudgetUsed,
        probeBudgetMax: numberField(message.probe_budget_max) ?? this.debug.probeBudgetMax,
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
        if (deliveryState === "STARTED") {
          this.activeDelivery.canonicalState = "STARTED";
          this.recordDeliveryLifecycle("delivery_started_ack");
        }
        if (deliveryState === "DELIVERED") {
          this.activeDelivery.canonicalState = "DELIVERED";
          this.activeDelivery.pendingTerminalEvent = null;
          this.recordDeliveryLifecycle("delivery_delivered_ack");
        }
        if (deliveryState === "INTERRUPTED") {
          this.activeDelivery.canonicalState = "INTERRUPTED";
          this.activeDelivery.localPlaybackState = "INTERRUPTED";
          this.activeDelivery.pendingTerminalEvent = null;
          this.recordDeliveryLifecycle("delivery_interrupted_ack");
        }
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
          localPlaybackState:
            this.activeDelivery?.localPlaybackState ?? this.debug.lastDelivery.localPlaybackState,
          canonicalState:
            this.activeDelivery?.canonicalState ?? this.debug.lastDelivery.canonicalState,
          outputTranscriptState:
            this.activeDelivery?.outputTranscriptState ?? this.debug.lastDelivery.outputTranscriptState,
          pendingTerminalEvent:
            this.activeDelivery?.pendingTerminalEvent?.kind ??
            this.debug.lastDelivery.pendingTerminalEvent,
          lifecycleEvents: this.debug.lastDelivery.lifecycleEvents,
        },
      });
      this.maybeFlushPendingTerminalEvent();
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
        if (pendingMessage?.type === "examiner_decision_policy_gate_requested") {
          this.patchDebug({
            lastPolicyGate: {
              decisionId: stringField(pendingMessage.examiner_decision_id),
              requestState: "FAILED",
              disposition: null,
              decisionStatus: null,
              policyGateOutcome: null,
              promptId: null,
              promptKind: null,
              reason: stringField(message.message) ?? "Policy gate request was rejected.",
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

  private createActiveDelivery(
    promptId: string,
    intendedText: string,
    canonicalState: ActivePromptDelivery["canonicalState"],
  ): ActivePromptDelivery {
    return {
      promptId,
      intendedText,
      providerResponseId: `pending-${promptId}`,
      providerItemId: null,
      deliveryId: null,
      outputTranscript: "",
      outputTranscriptState: "NONE",
      localPlaybackState: "NOT_STARTED",
      canonicalState,
      playbackStartObserved: false,
      startRequested: false,
      completionRequested: false,
      interruptionRequested: false,
      pendingTerminalEvent: null,
    };
  }

  private reconcileProviderResponse(providerResponseId: string | null): boolean {
    if (!this.activeDelivery || providerResponseId === null) {
      return true;
    }
    if (!this.acceptProviderResponse(providerResponseId)) {
      return false;
    }
    this.activeDelivery.providerResponseId = providerResponseId;
    return true;
  }

  private acceptProviderResponse(providerResponseId: string): boolean {
    if (!this.activeDelivery) {
      return false;
    }
    if (isPendingProviderResponseId(this.activeDelivery.providerResponseId)) {
      return true;
    }
    return this.activeDelivery.providerResponseId === providerResponseId;
  }

  private syncDeliveryDebug(): void {
    if (!this.activeDelivery) {
      return;
    }
    this.patchDebug({
      lastDelivery: {
        promptId: this.activeDelivery.promptId,
        deliveryId: this.activeDelivery.deliveryId,
        deliveryState: this.activeDelivery.canonicalState,
        providerResponseId: isPendingProviderResponseId(this.activeDelivery.providerResponseId)
          ? null
          : this.activeDelivery.providerResponseId,
        actualTranscriptId: this.debug.lastDelivery.actualTranscriptId,
        localPlaybackState: this.activeDelivery.localPlaybackState,
        canonicalState: this.activeDelivery.canonicalState,
        outputTranscriptState: this.activeDelivery.outputTranscriptState,
        pendingTerminalEvent: this.activeDelivery.pendingTerminalEvent?.kind ?? "NONE",
        lifecycleEvents: this.debug.lastDelivery.lifecycleEvents,
      },
    });
  }

  private recordDeliveryLifecycle(eventType: string): void {
    const events = [...this.debug.lastDelivery.lifecycleEvents, eventType].slice(-12);
    this.patchDebug({
      lastDelivery: {
        ...this.debug.lastDelivery,
        lifecycleEvents: events,
      },
    });
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

function isPendingProviderResponseId(providerResponseId: string): boolean {
  return providerResponseId.startsWith("pending-");
}

function emptyDebug(): CanonicalControlDebug {
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
      requestState: "IDLE",
      disposition: null,
      decisionStatus: null,
      policyGateOutcome: null,
      promptId: null,
      promptKind: null,
      reason: null,
    },
    lastDeliveryPermit: {
      promptId: null,
      status: null,
      reason: null,
    },
  };
}
