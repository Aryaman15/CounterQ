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
};

export class RealtimeVoiceClient {
  private readonly apiBaseUrl: string;
  private readonly fetchFn: typeof fetch;
  private readonly mediaDevices: BrowserMediaDevices | undefined;
  private readonly peerConnectionFactory: () => RTCPeerConnection;
  private readonly audioElementFactory: () => HTMLAudioElement;
  private readonly listeners = new Set<RealtimeClientListener>();
  private localStream: MediaStream | null = null;
  private peerConnection: RTCPeerConnection | null = null;
  private dataChannel: RTCDataChannel | null = null;
  private remoteAudio: HTMLAudioElement | null = null;
  private muted = false;

  constructor(options: RealtimeVoiceClientOptions) {
    this.apiBaseUrl = options.apiBaseUrl.replace(/\/$/, "");
    this.fetchFn = options.fetchFn ?? fetch.bind(globalThis);
    this.mediaDevices = options.mediaDevices ?? globalThis.navigator?.mediaDevices;
    this.peerConnectionFactory =
      options.peerConnectionFactory ?? (() => new RTCPeerConnection());
    this.audioElementFactory = options.audioElementFactory ?? (() => new Audio());
  }

  on(listener: RealtimeClientListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async connect(): Promise<void> {
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

      peerConnection.addEventListener("connectionstatechange", () => {
        if (
          peerConnection.connectionState === "failed" ||
          peerConnection.connectionState === "disconnected"
        ) {
          this.emit({
            type: "error",
            message: "Realtime voice connection was interrupted.",
          });
        }
      });

      peerConnection.ontrack = (event) => {
        const stream = event.streams[0] ?? new MediaStream([event.track]);
        if (this.remoteAudio) {
          this.remoteAudio.srcObject = stream;
          void this.remoteAudio.play().catch(() => {
            this.emit({
              type: "error",
              message: "Browser blocked realtime audio playback.",
            });
          });
        }
      };

      for (const track of this.localStream.getAudioTracks()) {
        peerConnection.addTrack(track, this.localStream);
      }

      const dataChannel = peerConnection.createDataChannel("oai-events");
      this.dataChannel = dataChannel;
      dataChannel.addEventListener("message", (event) => this.handleProviderMessage(event));
      dataChannel.addEventListener("error", () => {
        this.emit({ type: "error", message: "Realtime event channel failed." });
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
      this.emit({ type: "connected", session });
    } catch (error) {
      this.disconnect();
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
    phrase = "Walk me through the approach you're considering.",
  ): void {
    this.sendProviderEvent({
      type: "response.create",
      response: {
        output_modalities: ["audio"],
        instructions: `Speak exactly this sentence and nothing else: "${phrase}"`,
      },
    });
  }

  disconnect(): void {
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
    this.emit({ type: "disconnected" });
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
