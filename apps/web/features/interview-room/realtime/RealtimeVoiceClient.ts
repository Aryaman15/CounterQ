import type { components } from "@counterq/contracts/openapi";

import { normalizeRealtimeEvent, type NormalizedRealtimeEvent } from "./events";

export type RealtimeSessionResponse = components["schemas"]["CreateRealtimeSessionResponse"];

export type RealtimeClientEvent =
  | { type: "connecting" }
  | { type: "connected"; session: RealtimeSessionResponse }
  | { type: "muted" }
  | { type: "unmuted" }
  | { type: "disconnected" }
  | { type: "error"; message: string }
  | NormalizedRealtimeEvent;

type RealtimeClientListener = (event: RealtimeClientEvent) => void;

type BrowserMediaDevices = Pick<MediaDevices, "getUserMedia">;

export type RealtimeVoiceClientOptions = {
  apiBaseUrl: string;
  fetchFn?: typeof fetch;
  mediaDevices?: BrowserMediaDevices;
  peerConnectionFactory?: () => RTCPeerConnection;
  audioElementFactory?: () => HTMLAudioElement;
  connectionTimeoutMs?: number;
};

export class RealtimeVoiceClient {
  private readonly apiBaseUrl: string;
  private readonly fetchFn: typeof fetch;
  private readonly mediaDevices: BrowserMediaDevices | undefined;
  private readonly peerConnectionFactory: () => RTCPeerConnection;
  private readonly audioElementFactory: () => HTMLAudioElement;
  private readonly connectionTimeoutMs: number;
  private readonly listeners = new Set<RealtimeClientListener>();
  private readonly cleanupCallbacks: Array<() => void> = [];
  private localStream: MediaStream | null = null;
  private peerConnection: RTCPeerConnection | null = null;
  private dataChannel: RTCDataChannel | null = null;
  private remoteAudio: HTMLAudioElement | null = null;
  private muted = false;
  private activeResponseId: string | null = null;
  private activeAssistantItemId: string | null = null;
  private outputStartedAtMs: number | null = null;

  constructor(options: RealtimeVoiceClientOptions) {
    this.apiBaseUrl = options.apiBaseUrl.replace(/\/$/, "");
    this.fetchFn = options.fetchFn ?? fetch.bind(globalThis);
    this.mediaDevices = options.mediaDevices ?? globalThis.navigator?.mediaDevices;
    this.peerConnectionFactory =
      options.peerConnectionFactory ?? (() => new RTCPeerConnection());
    this.audioElementFactory = options.audioElementFactory ?? (() => new Audio());
    this.connectionTimeoutMs = options.connectionTimeoutMs ?? 12_000;
  }

  on(listener: RealtimeClientListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async connect(): Promise<void> {
    this.releaseResources({ emitDisconnected: false });
    this.emit({ type: "connecting" });

    if (!this.mediaDevices?.getUserMedia) {
      throw this.fail("Microphone access is not available in this browser.");
    }

    try {
      this.localStream = await this.mediaDevices.getUserMedia({ audio: true });
      const session = await this.createCounterQRealtimeSession();
      const peerConnection = this.peerConnectionFactory();
      this.peerConnection = peerConnection;
      this.remoteAudio = this.audioElementFactory();
      this.remoteAudio.autoplay = true;

      this.addManagedListener(peerConnection, "connectionstatechange", () => {
        if (
          peerConnection.connectionState === "failed" ||
          peerConnection.connectionState === "disconnected"
        ) {
          this.handleFatalTransportFailure("Realtime voice connection was interrupted.");
        }
      });

      peerConnection.ontrack = (event) => {
        const stream = event.streams[0] ?? new MediaStream([event.track]);
        if (this.remoteAudio) {
          this.remoteAudio.srcObject = stream;
          void this.remoteAudio.play().catch(() => {
            this.handleFatalTransportFailure(
              "Browser blocked realtime audio playback. Re-enable voice after allowing audio.",
            );
          });
        }
      };

      for (const track of this.localStream.getAudioTracks()) {
        peerConnection.addTrack(track, this.localStream);
      }

      const dataChannel = peerConnection.createDataChannel("oai-events");
      this.dataChannel = dataChannel;
      this.addManagedListener(dataChannel, "message", (event) => this.handleProviderMessage(event));
      this.addManagedListener(dataChannel, "error", () => {
        this.handleFatalTransportFailure("Realtime event channel failed.");
      });
      this.addManagedListener(dataChannel, "close", () => {
        if (this.dataChannel === dataChannel) {
          this.handleFatalTransportFailure("Realtime event channel closed unexpectedly.");
        }
      });

      const offer = await peerConnection.createOffer();
      await peerConnection.setLocalDescription(offer);
      const sdpResponse = await this.fetchFn(session.webrtc_url, {
        method: "POST",
        body: offer.sdp ?? "",
        headers: {
          Authorization: `Bearer ${session.client_secret}`,
          "Content-Type": "application/sdp",
        },
      });

      if (!sdpResponse.ok) {
        throw new Error("Realtime WebRTC negotiation failed.");
      }

      const answerSdp = await sdpResponse.text();
      await peerConnection.setRemoteDescription({ type: "answer", sdp: answerSdp });
      await this.waitForDataChannelOpen(dataChannel);
      this.emit({ type: "connected", session });
    } catch (error) {
      this.releaseResources({ emitDisconnected: false });
      if (error instanceof RealtimeClientFailure) {
        throw error;
      }
      throw this.fail(safeRealtimeErrorMessage(error));
    }
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    for (const track of this.localStream?.getAudioTracks() ?? []) {
      track.enabled = !muted;
    }
    this.emit({ type: muted ? "muted" : "unmuted" });
  }

  speakAuthorizedDevelopmentPhrase(
    phrase: string,
    metadata: Record<string, string> = {},
  ): void {
    this.speakAuthorizedPrompt(phrase, metadata);
  }

  speakAuthorizedPrompt(
    phrase: string,
    metadata: Record<string, string> = {},
  ): void {
    this.sendProviderEvent({
      event_id: `counterq-authorized-${globalThis.crypto?.randomUUID?.() ?? Date.now()}`,
      type: "response.create",
      response: {
        output_modalities: ["audio"],
        metadata,
        instructions: `Speak exactly this sentence and nothing else: "${phrase}"`,
      },
    });
  }

  interruptActiveOutputForCandidateSpeech(): void {
    if (!this.activeResponseId && !this.activeAssistantItemId) {
      return;
    }

    if (this.activeResponseId) {
      this.sendProviderEvent({
        event_id: this.providerEventId("cancel"),
        type: "response.cancel",
        response_id: this.activeResponseId,
      });
    }
    this.sendProviderEvent({
      event_id: this.providerEventId("clear"),
      type: "output_audio_buffer.clear",
    });

    if (this.activeAssistantItemId) {
      this.sendProviderEvent({
        event_id: this.providerEventId("truncate"),
        type: "conversation.item.truncate",
        item_id: this.activeAssistantItemId,
        content_index: 0,
        audio_end_ms: this.elapsedOutputAudioMs(),
      });
    }
  }

  disconnect(): void {
    this.releaseResources({ emitDisconnected: true });
  }

  private releaseResources({ emitDisconnected }: { emitDisconnected: boolean }): void {
    while (this.cleanupCallbacks.length > 0) {
      this.cleanupCallbacks.pop()?.();
    }

    this.dataChannel?.close();
    this.dataChannel = null;

    this.peerConnection?.close();
    this.peerConnection = null;

    for (const track of this.localStream?.getTracks() ?? []) {
      track.stop();
    }
    this.localStream = null;

    if (this.remoteAudio) {
      this.remoteAudio.pause();
      this.remoteAudio.srcObject = null;
    }
    this.remoteAudio = null;
    this.muted = false;
    this.activeResponseId = null;
    this.activeAssistantItemId = null;
    this.outputStartedAtMs = null;
    if (emitDisconnected) {
      this.emit({ type: "disconnected" });
    }
  }

  private async createCounterQRealtimeSession(): Promise<RealtimeSessionResponse> {
    const response = await this.fetchFn(`${this.apiBaseUrl}/api/realtime/session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ purpose: "interview_demo" }),
    });
    if (!response.ok) {
      throw new Error("CounterQ could not create a realtime voice session.");
    }
    return (await response.json()) as RealtimeSessionResponse;
  }

  private handleProviderMessage(event: MessageEvent): void {
    try {
      const raw = JSON.parse(String(event.data)) as unknown;
      for (const normalized of normalizeRealtimeEvent(raw)) {
        this.updateProviderPlaybackState(normalized);
        this.emit(normalized);
      }
    } catch {
      this.emit({ type: "error", message: "Received malformed realtime event data." });
    }
  }

  private sendProviderEvent(event: Record<string, unknown>): void {
    if (!this.dataChannel || this.dataChannel.readyState !== "open") {
      this.emit({ type: "error", message: "Realtime event channel is not ready." });
      return;
    }
    this.dataChannel.send(JSON.stringify(event));
  }

  private emit(event: RealtimeClientEvent): void {
    for (const listener of this.listeners) {
      listener(event);
    }
  }

  private fail(message: string): RealtimeClientFailure {
    this.emit({ type: "error", message });
    return new RealtimeClientFailure(message);
  }

  private handleFatalTransportFailure(message: string): void {
    this.releaseResources({ emitDisconnected: false });
    this.emit({ type: "error", message });
  }

  private updateProviderPlaybackState(event: NormalizedRealtimeEvent): void {
    if (event.type === "counterq_response_created") {
      this.activeResponseId = event.responseId;
      this.activeAssistantItemId = event.itemId ?? this.activeAssistantItemId;
      return;
    }
    if (event.type === "counterq_output_started") {
      this.activeResponseId = event.responseId ?? this.activeResponseId;
      this.activeAssistantItemId = event.itemId ?? this.activeAssistantItemId;
      if (event.playbackStarted) {
        this.outputStartedAtMs = this.nowMs();
      }
      return;
    }
    if (event.type === "counterq_output_ended" || event.type === "counterq_output_interrupted") {
      this.activeResponseId = null;
      this.activeAssistantItemId = null;
      this.outputStartedAtMs = null;
    }
  }

  private elapsedOutputAudioMs(): number {
    if (this.outputStartedAtMs === null) {
      return 0;
    }
    return Math.max(0, Math.floor(this.nowMs() - this.outputStartedAtMs));
  }

  private nowMs(): number {
    return globalThis.performance?.now?.() ?? Date.now();
  }

  private providerEventId(purpose: string): string {
    return `counterq-${purpose}-${globalThis.crypto?.randomUUID?.() ?? Date.now()}`;
  }

  private waitForDataChannelOpen(dataChannel: RTCDataChannel): Promise<void> {
    if (dataChannel.readyState === "open") {
      return Promise.resolve();
    }
    if (dataChannel.readyState === "closing" || dataChannel.readyState === "closed") {
      return Promise.reject(new Error("Realtime event channel closed before it was ready."));
    }

    return new Promise((resolve, reject) => {
      const timeoutId = window.setTimeout(() => {
        cleanup();
        reject(new Error("Realtime voice connection timed out."));
      }, this.connectionTimeoutMs);

      const handleOpen = () => {
        cleanup();
        resolve();
      };
      const handleFailure = () => {
        cleanup();
        reject(new Error("Realtime event channel failed before it was ready."));
      };
      const cleanup = () => {
        window.clearTimeout(timeoutId);
        dataChannel.removeEventListener("open", handleOpen);
        dataChannel.removeEventListener("error", handleFailure);
        dataChannel.removeEventListener("close", handleFailure);
      };

      dataChannel.addEventListener("open", handleOpen);
      dataChannel.addEventListener("error", handleFailure);
      dataChannel.addEventListener("close", handleFailure);
    });
  }

  private addManagedListener<K extends keyof RTCPeerConnectionEventMap>(
    target: RTCPeerConnection,
    type: K,
    listener: (event: RTCPeerConnectionEventMap[K]) => void,
  ): void;
  private addManagedListener<K extends keyof RTCDataChannelEventMap>(
    target: RTCDataChannel,
    type: K,
    listener: (event: RTCDataChannelEventMap[K]) => void,
  ): void;
  private addManagedListener(
    target: RTCPeerConnection | RTCDataChannel,
    type: string,
    listener: EventListener,
  ): void {
    target.addEventListener(type, listener);
    this.cleanupCallbacks.push(() => target.removeEventListener(type, listener));
  }
}

class RealtimeClientFailure extends Error {}

function safeRealtimeErrorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return "Microphone permission was denied.";
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "Realtime voice connection failed.";
}
