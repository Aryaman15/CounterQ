import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  RealtimeControlClient,
  type RealtimeControlEvent,
} from "../features/interview-room/realtime/RealtimeControlClient";
import {
  RealtimeVoiceClient,
  type RealtimeClientEvent,
} from "../features/interview-room/realtime/RealtimeVoiceClient";
import { normalizeRealtimeEvent } from "../features/interview-room/realtime/events";
import {
  CODE_EDIT_BURST_IDLE_MS,
  useCodeObservationCollector,
} from "../features/interview-room/realtime/useCodeObservationCollector";
import { useRealtimeVoice } from "../features/interview-room/realtime/useRealtimeVoice";

class FakeTrack {
  enabled = true;
  stop = vi.fn();
}

class FakeDataChannel {
  readyState: RTCDataChannelState;
  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = "closed";
    this.emit("close");
  });
  private readonly listeners = new Map<string, Set<EventListener>>();

  constructor(initialState: RTCDataChannelState = "open") {
    this.readyState = initialState;
  }

  addEventListener(type: string, listener: EventListener): void {
    const current = this.listeners.get(type) ?? new Set<EventListener>();
    current.add(listener);
    this.listeners.set(type, current);
  }

  removeEventListener(type: string, listener: EventListener): void {
    this.listeners.get(type)?.delete(listener);
  }

  open(): void {
    this.readyState = "open";
    this.emit("open");
  }

  fail(): void {
    this.emit("error");
  }

  emitMessage(data: unknown): void {
    this.emit("message", { data: JSON.stringify(data) } as MessageEvent);
  }

  private emit(type: string, event: Event = new Event(type)): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

class FakeControlWebSocket {
  static readonly instances: FakeControlWebSocket[] = [];
  readyState: number = WebSocket.CONNECTING;
  readonly send = vi.fn();
  readonly close = vi.fn(() => {
    this.readyState = WebSocket.CLOSED;
    this.emit("close");
  });
  private readonly listeners = new Map<string, Set<EventListener>>();

  constructor(readonly url: string) {
    FakeControlWebSocket.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener): void {
    const current = this.listeners.get(type) ?? new Set<EventListener>();
    current.add(listener);
    this.listeners.set(type, current);
  }

  removeEventListener(type: string, listener: EventListener): void {
    this.listeners.get(type)?.delete(listener);
  }

  open(): void {
    this.readyState = WebSocket.OPEN;
    this.emit("open");
  }

  receive(data: unknown): void {
    this.emit("message", { data: JSON.stringify(data) } as MessageEvent);
  }

  private emit(type: string, event: Event = new Event(type)): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

class FakePeerConnection {
  connectionState: RTCPeerConnectionState = "connected";
  ontrack: ((event: RTCTrackEvent) => void) | null = null;
  readonly dataChannel: FakeDataChannel;
  readonly addTrack = vi.fn();
  readonly createDataChannel = vi.fn(() => this.dataChannel as unknown as RTCDataChannel);
  readonly createOffer = vi.fn(async () => ({ type: "offer" as RTCSdpType, sdp: "offer-sdp" }));
  readonly setLocalDescription = vi.fn();
  readonly setRemoteDescription = vi.fn();
  readonly close = vi.fn(() => {
    this.connectionState = "closed";
  });
  private readonly listeners = new Map<string, Set<EventListener>>();

  constructor(dataChannel = new FakeDataChannel()) {
    this.dataChannel = dataChannel;
  }

  addEventListener(type: string, listener: EventListener): void {
    const current = this.listeners.get(type) ?? new Set<EventListener>();
    current.add(listener);
    this.listeners.set(type, current);
  }

  removeEventListener(type: string, listener: EventListener): void {
    this.listeners.get(type)?.delete(listener);
  }

  emitConnectionState(state: RTCPeerConnectionState): void {
    this.connectionState = state;
    for (const listener of this.listeners.get("connectionstatechange") ?? []) {
      listener(new Event("connectionstatechange"));
    }
  }
}

class HookFakeClient {
  disconnect = vi.fn();
  setMuted = vi.fn((muted: boolean) => {
    this.emit({ type: muted ? "muted" : "unmuted" });
  });
  speakAuthorizedDevelopmentPhrase = vi.fn();
  speakAuthorizedPrompt = vi.fn();
  interruptActiveOutputForCandidateSpeech = vi.fn();
  private readonly listeners = new Set<(event: RealtimeClientEvent) => void>();
  private connectImpl: () => Promise<void> = async () => {
    this.emit({ type: "connected", session: fakeSessionResponse });
  };

  on(listener: (event: RealtimeClientEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  setConnectImpl(connectImpl: () => Promise<void>): void {
    this.connectImpl = connectImpl;
  }

  async connect(): Promise<void> {
    await this.connectImpl();
  }

  emit(event: RealtimeClientEvent): void {
    for (const listener of this.listeners) {
      listener(event);
    }
  }
}

class HookFakeControlClient {
  private connectImpl: () => Promise<typeof fakeDevelopmentBootstrap> = async () =>
    fakeDevelopmentBootstrap;
  connectDevelopmentInterview = vi.fn(() => this.connectImpl());
  disconnect = vi.fn(() => {
    this.emit({ type: "disconnected" });
  });
  sendCandidateSpeechStarted = vi.fn();
  sendCandidateSpeechStopped = vi.fn();
  sendCandidateTranscriptFinal = vi.fn();
  requestDevelopmentPrompt = vi.fn();
  requestExaminerDecisionPolicyGate = vi.fn();
  requestPromptDeliveryPermit = vi.fn();
  sendCandidateCodeActivityStarted = vi.fn();
  sendCandidateCodeActivityIdle = vi.fn();
  noteProviderResponseCreated = vi.fn();
  noteOutputTranscriptDelta = vi.fn();
  noteOutputTranscriptFinal = vi.fn();
  sendDeliveryStarted = vi.fn();
  sendDeliveryCompleted = vi.fn();
  sendDeliveryInterrupted = vi.fn();
  noteRealtimeDisconnected = vi.fn();
  noteRealtimeReconnected = vi.fn();
  sendCandidateCodeSnapshot = vi.fn();
  private readonly listeners = new Set<(event: RealtimeControlEvent) => void>();

  setConnectImpl(connectImpl: () => Promise<typeof fakeDevelopmentBootstrap>): void {
    this.connectImpl = connectImpl;
  }

  on(listener: (event: RealtimeControlEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  emit(event: RealtimeControlEvent): void {
    for (const listener of this.listeners) {
      listener(event);
    }
  }
}

const fakeSessionResponse = {
  provider: "openai" as const,
  client_secret: "ephemeral",
  webrtc_url: "https://api.openai.com/v1/realtime/calls",
  model: "gpt-realtime-2.1",
  voice: "marin",
  transcription_model: "gpt-live-transcribe",
  expires_at: "2026-08-24T12:00:00Z",
  expires_after_seconds: 600,
  turn_detection: {
    type: "semantic_vad" as const,
    eagerness: "low" as const,
    create_response: false as const,
    interrupt_response: true as const,
  },
};

const fakeDevelopmentBootstrap = {
  interview_session_id: "session-1",
  current_stage: "IMPLEMENTATION",
  state_version: 0,
  deadline_at: "2026-08-24T12:30:00Z",
  control_websocket_path: "/api/realtime/control/session-1",
  protocol_version: "counterq.realtime.control.v1" as const,
};

function mediaStreamFor(track: FakeTrack): MediaStream {
  return {
    getAudioTracks: () => [track],
    getTracks: () => [track],
  } as unknown as MediaStream;
}

function renderRealtimeHarness(
  client: HookFakeClient,
  controlClient = new HookFakeControlClient(),
) {
  function Harness() {
    const voice = useRealtimeVoice({
      clientFactory: () => client as unknown as RealtimeVoiceClient,
      controlClientFactory: () => controlClient as unknown as RealtimeControlClient,
    });
    return (
      <div>
        <p data-testid="voice-state">{voice.voiceState}</p>
        <p data-testid="muted-state">{String(voice.isMuted)}</p>
        <p data-testid="partial-transcript">{voice.partialTranscript}</p>
        <p data-testid="final-transcript">{voice.lastFinalTranscript}</p>
        <p data-testid="counterq-delivery-text">{voice.currentCounterQDeliveryText}</p>
        <p data-testid="session-transcription-model">{voice.sessionDebug.transcriptionModel}</p>
        <p data-testid="canonical-session">{voice.canonicalDebug.sessionId}</p>
        <p data-testid="pending-durable">{voice.canonicalDebug.pendingDurableMessages}</p>
        {voice.errorMessage ? <p>{voice.errorMessage}</p> : null}
        <button type="button" onClick={() => void voice.enableMicrophone()}>
          enable
        </button>
        <button type="button" onClick={voice.mute}>
          mute
        </button>
        <button type="button" onClick={voice.unmute}>
          unmute
        </button>
        <button type="button" onClick={voice.disconnect}>
          disconnect
        </button>
        <button type="button" onClick={voice.speakDevelopmentPhrase}>
          phrase
        </button>
        <button type="button" onClick={() => voice.evaluateExaminerDecision("decision-1")}>
          gate
        </button>
        <button type="button" onClick={() => voice.deliverAuthorizedPrompt("prompt-1")}>
          deliver
        </button>
      </div>
    );
  }

  return { ...render(<Harness />), controlClient };
}

function createBrowserClient({
  track = new FakeTrack(),
  dataChannel = new FakeDataChannel(),
  audioPlay = vi.fn(async () => undefined),
  connectionTimeoutMs = 1_000,
}: {
  track?: FakeTrack;
  dataChannel?: FakeDataChannel;
  audioPlay?: ReturnType<typeof vi.fn>;
  connectionTimeoutMs?: number;
} = {}) {
  const stream = mediaStreamFor(track);
  const peerConnection = new FakePeerConnection(dataChannel);
  const audioElement = {
    autoplay: false,
    srcObject: null as MediaStream | null,
    play: audioPlay,
    pause: vi.fn(),
  } as unknown as HTMLAudioElement;
  const fetchFn = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(fakeSessionResponse), { status: 200 }))
    .mockResolvedValueOnce(new Response("answer-sdp", { status: 200 }));
  const client = new RealtimeVoiceClient({
    apiBaseUrl: "http://127.0.0.1:8000",
    fetchFn,
    mediaDevices: {
      getUserMedia: vi.fn(async () => stream),
    },
    peerConnectionFactory: () => peerConnection as unknown as RTCPeerConnection,
    audioElementFactory: () => audioElement,
    connectionTimeoutMs,
  });

  return { audioElement, client, dataChannel, fetchFn, peerConnection, stream, track };
}

async function flushAsyncWork(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("Realtime voice foundation", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    FakeControlWebSocket.instances.length = 0;
  });

  it("starts Ready, not falsely Listening", () => {
    renderRealtimeHarness(new HookFakeClient());

    expect(screen.getByTestId("voice-state")).toHaveTextContent("Ready");
    expect(screen.queryByText("Listening")).not.toBeInTheDocument();
  });

  it("shows Connecting while microphone enablement is in progress", async () => {
    const client = new HookFakeClient();
    client.setConnectImpl(
      () =>
        new Promise(() => {
          client.emit({ type: "connecting" });
        }),
    );
    renderRealtimeHarness(client);

    fireEvent.click(screen.getByRole("button", { name: "enable" }));

    expect(await screen.findByTestId("voice-state")).toHaveTextContent("Connecting");
  });

  it("does not show a control rejection while control startup is still pending", async () => {
    const client = new HookFakeClient();
    const controlClient = new HookFakeControlClient();
    controlClient.setConnectImpl(
      () =>
        new Promise(() => {
          // Startup is still waiting for server_hello; this is pending, not rejected.
        }),
    );
    renderRealtimeHarness(client, controlClient);

    fireEvent.click(screen.getByRole("button", { name: "enable" }));

    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Connecting");
    });
    expect(screen.queryByText("CounterQ control message was rejected.")).not.toBeInTheDocument();
  });

  it("waits for control ready before connecting realtime voice", async () => {
    const order: string[] = [];
    const client = new HookFakeClient();
    client.setConnectImpl(async () => {
      order.push("voice-connected");
      client.emit({ type: "connected", session: fakeSessionResponse });
    });
    const controlClient = new HookFakeControlClient();
    controlClient.setConnectImpl(async () => {
      order.push("control-ready");
      return fakeDevelopmentBootstrap;
    });
    renderRealtimeHarness(client, controlClient);

    fireEvent.click(screen.getByRole("button", { name: "enable" }));

    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    });
    expect(order).toEqual(["control-ready", "voice-connected"]);
  });

  it("shows a recoverable Error when microphone permission is denied", async () => {
    const client = new HookFakeClient();
    client.setConnectImpl(async () => {
      throw new Error("Microphone permission was denied.");
    });
    renderRealtimeHarness(client);

    fireEvent.click(screen.getByRole("button", { name: "enable" }));

    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Error");
    });
    expect(screen.getByText("Microphone permission was denied.")).toBeInTheDocument();
  });

  it("reaches Listening after mocked WebRTC connection succeeds", async () => {
    const { controlClient } = renderRealtimeHarness(new HookFakeClient());

    fireEvent.click(screen.getByRole("button", { name: "enable" }));

    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    });
    expect(controlClient.connectDevelopmentInterview).toHaveBeenCalled();
  });

  it("keeps microphone mute separate from provider speech state", async () => {
    const client = new HookFakeClient();
    renderRealtimeHarness(client);
    fireEvent.click(screen.getByRole("button", { name: "enable" }));
    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    });

    fireEvent.click(screen.getByRole("button", { name: "mute" }));
    expect(screen.getByTestId("voice-state")).toHaveTextContent("Muted");
    expect(screen.getByTestId("muted-state")).toHaveTextContent("true");

    act(() => {
      client.emit({ type: "counterq_output_started" });
    });
    expect(screen.getByTestId("voice-state")).toHaveTextContent("Speaking");
    expect(screen.getByTestId("muted-state")).toHaveTextContent("true");

    act(() => {
      client.emit({ type: "counterq_output_ended" });
      client.emit({ type: "candidate_speech_started" });
    });
    expect(screen.getByTestId("voice-state")).toHaveTextContent("Muted");
    expect(screen.getByTestId("muted-state")).toHaveTextContent("true");

    fireEvent.click(screen.getByRole("button", { name: "unmute" }));
    expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    expect(screen.getByTestId("muted-state")).toHaveTextContent("false");
    expect(client.setMuted).toHaveBeenCalledWith(false);
  });

  it("disconnect resets mute state", async () => {
    const client = new HookFakeClient();
    renderRealtimeHarness(client);
    fireEvent.click(screen.getByRole("button", { name: "enable" }));
    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    });
    fireEvent.click(screen.getByRole("button", { name: "mute" }));
    expect(screen.getByTestId("muted-state")).toHaveTextContent("true");

    fireEvent.click(screen.getByRole("button", { name: "disconnect" }));

    expect(screen.getByTestId("voice-state")).toHaveTextContent("Ready");
    expect(screen.getByTestId("muted-state")).toHaveTextContent("false");
  });

  it("exposes transcript deltas and finals without corrupting voice state", async () => {
    const client = new HookFakeClient();
    const { controlClient } = renderRealtimeHarness(client);
    fireEvent.click(screen.getByRole("button", { name: "enable" }));
    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    });

    act(() => {
      client.emit({
        type: "transcript_delta",
        text: "hash ",
        itemId: "item_1",
        contentIndex: 0,
      });
      client.emit({
        type: "transcript_delta",
        text: "map",
        itemId: "item_1",
        contentIndex: 0,
      });
    });
    expect(screen.getByTestId("partial-transcript")).toHaveTextContent("hash map");
    expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");

    act(() => {
      client.emit({
        type: "transcript_final",
        text: "hash map lookup",
        itemId: "item_1",
        contentIndex: 0,
      });
    });
    expect(screen.getByTestId("partial-transcript")).toHaveTextContent("");
    expect(screen.getByTestId("final-transcript")).toHaveTextContent("hash map lookup");
    expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    expect(controlClient.sendCandidateTranscriptFinal).toHaveBeenCalledWith({
      providerItemId: "item_1",
      contentIndex: 0,
      transcript: "hash map lookup",
    });

    act(() => {
      client.emit({
        type: "realtime_session_observed",
        eventType: "session.updated",
        sessionType: "realtime",
        transcriptionModel: "gpt-live-transcribe",
        turnDetectionType: "semantic_vad",
        createResponse: false,
        interruptResponse: true,
      });
    });
    expect(screen.getByTestId("session-transcription-model")).toHaveTextContent(
      "gpt-live-transcribe",
    );
    expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
  });

  it("hook keeps policy gate and delivery permit separate from voice speech", async () => {
    const client = new HookFakeClient();
    const controlClient = new HookFakeControlClient();
    renderRealtimeHarness(client, controlClient);
    fireEvent.click(screen.getByRole("button", { name: "enable" }));
    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    });

    fireEvent.click(screen.getByText("gate"));
    expect(controlClient.requestExaminerDecisionPolicyGate).toHaveBeenCalledWith("decision-1");
    expect(client.speakAuthorizedPrompt).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("deliver"));
    expect(controlClient.requestPromptDeliveryPermit).toHaveBeenCalledWith("prompt-1");

    controlClient.emit({
      type: "authorized_prompt",
      prompt: {
        promptId: "prompt-1",
        text: "What invariant holds?",
        origin: "EXAMINER_DECISION",
        kind: "PROBE",
      },
    });
    expect(client.speakAuthorizedPrompt).toHaveBeenCalledWith("What invariant holds?", {
      counterq_prompt_id: "prompt-1",
      counterq_prompt_origin: "EXAMINER_DECISION",
      counterq_prompt_kind: "PROBE",
    });
    expect(screen.getByTestId("counterq-delivery-text")).toHaveTextContent("");

    act(() => {
      client.emit({
        type: "counterq_output_transcript_delta",
        text: "What invariant",
        responseId: "resp-1",
        itemId: "item-1",
        contentIndex: 0,
      });
    });
    expect(screen.getByTestId("counterq-delivery-text")).toHaveTextContent("What invariant");
  });

  it("uses backend-authorized dev prompt text before asking the provider to speak", async () => {
    const client = new HookFakeClient();
    const { controlClient } = renderRealtimeHarness(client);
    fireEvent.click(screen.getByRole("button", { name: "enable" }));
    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    });

    fireEvent.click(screen.getByRole("button", { name: "phrase" }));
    expect(controlClient.requestDevelopmentPrompt).toHaveBeenCalled();

    act(() => {
      controlClient.emit({
        type: "authorized_prompt",
        prompt: {
          promptId: "prompt-1",
          text: "Walk me through the approach you're considering.",
        },
      });
    });

    expect(client.speakAuthorizedPrompt).toHaveBeenCalledWith(
      "Walk me through the approach you're considering.",
      {
        counterq_prompt_id: "prompt-1",
        counterq_prompt_origin: "SYSTEM",
        counterq_prompt_kind: "INSTRUCTION",
      },
    );
  });

  it("sends delivery controls only after playback lifecycle events", async () => {
    const client = new HookFakeClient();
    const { controlClient } = renderRealtimeHarness(client);
    fireEvent.click(screen.getByRole("button", { name: "enable" }));
    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    });

    act(() => {
      client.emit({ type: "counterq_response_created", responseId: "resp-1", itemId: "item-a" });
      client.emit({ type: "counterq_output_started", responseId: "resp-1" });
    });
    expect(controlClient.sendDeliveryStarted).not.toHaveBeenCalled();

    act(() => {
      client.emit({
        type: "counterq_output_started",
        responseId: "resp-1",
        itemId: "item-a",
        playbackStarted: true,
      });
      client.emit({
        type: "counterq_output_transcript_delta",
        responseId: "resp-1",
        itemId: "item-a",
        contentIndex: 0,
        text: "Walk ",
      });
      client.emit({
        type: "counterq_output_transcript_final",
        responseId: "resp-1",
        itemId: "item-a",
        contentIndex: 0,
        text: "Walk me through it.",
      });
      client.emit({
        type: "counterq_output_ended",
        responseId: "resp-1",
        itemId: "item-a",
        playbackComplete: true,
      });
    });

    expect(controlClient.noteProviderResponseCreated).toHaveBeenCalledWith("resp-1", "item-a");
    expect(controlClient.sendDeliveryStarted).toHaveBeenCalledWith("resp-1", "item-a");
    expect(controlClient.noteOutputTranscriptDelta).toHaveBeenCalledWith("resp-1", "Walk ");
    expect(controlClient.noteOutputTranscriptFinal).toHaveBeenCalledWith(
      "resp-1",
      "Walk me through it.",
    );
    expect(controlClient.sendDeliveryCompleted).toHaveBeenCalledWith("resp-1");
  });

  it("reports confirmed interruption semantics without waiting for control ack", async () => {
    const client = new HookFakeClient();
    const { controlClient } = renderRealtimeHarness(client);
    fireEvent.click(screen.getByRole("button", { name: "enable" }));
    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    });

    act(() => {
      client.emit({ type: "counterq_output_started", responseId: "resp-1" });
      client.emit({
        type: "counterq_output_interrupted",
        responseId: "resp-1",
        itemId: "item-a",
        confirmedBy: "input_audio_buffer.speech_started",
      });
    });
    expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    expect(controlClient.sendDeliveryInterrupted).not.toHaveBeenCalled();

    act(() => {
      client.emit({
        type: "counterq_output_interrupted",
        responseId: "resp-1",
        itemId: "item-a",
        confirmedBy: "output_audio_buffer.cleared",
        audioEndMs: 900,
      });
    });
    expect(controlClient.sendDeliveryInterrupted).toHaveBeenCalledWith(
      "resp-1",
      "item-a",
      "output_audio_buffer.cleared",
      900,
    );
  });

  it("candidate speech triggers local provider interruption cleanup", async () => {
    const client = new HookFakeClient();
    const { controlClient } = renderRealtimeHarness(client);
    fireEvent.click(screen.getByRole("button", { name: "enable" }));
    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    });

    act(() => {
      client.emit({ type: "counterq_output_started", playbackStarted: true });
      client.emit({ type: "candidate_speech_started" });
    });

    expect(client.interruptActiveOutputForCandidateSpeech).toHaveBeenCalledOnce();
    expect(controlClient.sendCandidateSpeechStarted).toHaveBeenCalled();
    expect(controlClient.sendDeliveryInterrupted).not.toHaveBeenCalled();
  });

  it("disconnects client resources on component cleanup", async () => {
    const client = new HookFakeClient();
    const { unmount } = renderRealtimeHarness(client);
    fireEvent.click(screen.getByRole("button", { name: "enable" }));
    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    });

    unmount();

    expect(client.disconnect).toHaveBeenCalled();
  });

  it("normalizes current GA response, interruption, speech, transcript, and audio events", () => {
    expect(normalizeRealtimeEvent({ type: "input_audio_buffer.speech_started" })).toEqual([
      { type: "candidate_speech_started" },
      {
        type: "counterq_output_interrupted",
        itemId: null,
        confirmedBy: "input_audio_buffer.speech_started",
        audioEndMs: null,
      },
    ]);
    expect(normalizeRealtimeEvent({ type: "input_audio_buffer.speech_stopped" })).toEqual([
      { type: "candidate_speech_stopped" },
    ]);
    expect(
      normalizeRealtimeEvent({
        type: "conversation.item.input_audio_transcription.delta",
        item_id: "item_1",
        content_index: 0,
        delta: "hash",
      }),
    ).toEqual([{ type: "transcript_delta", text: "hash", itemId: "item_1", contentIndex: 0 }]);
    expect(
      normalizeRealtimeEvent({
        type: "conversation.item.input_audio_transcription.completed",
        item_id: "item_1",
        content_index: 0,
        transcript: "final text",
      }),
    ).toEqual([
      { type: "transcript_final", text: "final text", itemId: "item_1", contentIndex: 0 },
    ]);
    expect(
      normalizeRealtimeEvent({
        type: "conversation.item.input_audio_transcription.failed",
        item_id: "item_1",
      }),
    ).toEqual([
      {
        type: "transcript_failed",
        itemId: "item_1",
        message: "Realtime transcription failed for the current audio turn.",
      },
    ]);
    expect(
      normalizeRealtimeEvent({
        type: "session.created",
        session: {
          type: "realtime",
          audio: {
            input: {
              transcription: { model: "gpt-live-transcribe" },
              turn_detection: {
                type: "semantic_vad",
                create_response: false,
                interrupt_response: true,
              },
            },
          },
        },
      }),
    ).toEqual([
      {
        type: "realtime_session_observed",
        eventType: "session.created",
        sessionType: "realtime",
        transcriptionModel: "gpt-live-transcribe",
        turnDetectionType: "semantic_vad",
        createResponse: false,
        interruptResponse: true,
      },
    ]);
    expect(normalizeRealtimeEvent({ type: "response.output_audio.delta" })).toEqual([
      { type: "counterq_output_started" },
    ]);
    expect(
      normalizeRealtimeEvent({
        type: "output_audio_buffer.started",
        event_id: "event-output-start",
        response_id: "resp_1",
        item_id: "item_out",
      }),
    ).toEqual([
      {
        type: "counterq_output_started",
        responseId: "resp_1",
        itemId: "item_out",
        providerEventId: "event-output-start",
        playbackStarted: true,
      },
    ]);
    expect(normalizeRealtimeEvent({ type: "response.output_audio.done" })).toEqual([
      { type: "counterq_output_ended" },
    ]);
    expect(normalizeRealtimeEvent({ type: "output_audio_buffer.stopped" })).toEqual([
      { type: "counterq_output_ended", playbackComplete: true },
    ]);
    expect(
      normalizeRealtimeEvent({
        type: "response.output_audio_transcript.delta",
        response_id: "resp_1",
        item_id: "item_out",
        content_index: 0,
        delta: "Walk",
      }),
    ).toEqual([
      {
        type: "counterq_output_transcript_delta",
        text: "Walk",
        responseId: "resp_1",
        itemId: "item_out",
        contentIndex: 0,
      },
    ]);
    expect(
      normalizeRealtimeEvent({
        type: "response.output_audio_transcript.done",
        response_id: "resp_1",
        item_id: "item_out",
        content_index: 0,
        transcript: "Walk me through it.",
      }),
    ).toEqual([
      {
        type: "counterq_output_transcript_final",
        text: "Walk me through it.",
        responseId: "resp_1",
        itemId: "item_out",
        contentIndex: 0,
      },
    ]);
    expect(normalizeRealtimeEvent({ type: "response.done", response: { status: "completed" } }))
      .toEqual([{ type: "counterq_output_ended" }]);
    expect(normalizeRealtimeEvent({ type: "response.done", response: { status: "cancelled" } }))
      .toEqual([
        { type: "counterq_output_interrupted", confirmedBy: "response.done:cancelled" },
      ]);
    expect(normalizeRealtimeEvent({ type: "output_audio_buffer.cleared" })).toEqual([
      {
        type: "counterq_output_interrupted",
        confirmedBy: "output_audio_buffer.cleared",
        audioEndMs: null,
      },
    ]);
  });

  it("control client bootstraps, queues final transcripts, resends, and acks", async () => {
    const fetchFn = vi.fn(async () => new Response(JSON.stringify(fakeDevelopmentBootstrap)));
    const storage = new Map<string, string>();
    const client = new RealtimeControlClient({
      apiBaseUrl: "http://127.0.0.1:8000",
      fetchFn: fetchFn as typeof fetch,
      websocketFactory: (url) => new FakeControlWebSocket(url) as unknown as WebSocket,
      storage: {
        getItem: (key) => storage.get(key) ?? null,
        setItem: (key, value) => storage.set(key, value),
      },
      randomUUID: () => "stable-id",
    });
    const events: RealtimeControlEvent[] = [];
    client.on((event) => events.push(event));

    const connectPromise = client.connectDevelopmentInterview();
    await waitFor(() => {
      expect(FakeControlWebSocket.instances.length).toBe(1);
    });
    const socket = FakeControlWebSocket.instances[0];
    socket.open();
    socket.receive({
      type: "server_hello",
      interview_session_id: "session-1",
      current_stage: "IMPLEMENTATION",
      state_version: 0,
      last_server_sequence: 0,
    });
    await connectPromise;

    client.sendCandidateTranscriptFinal({
      providerItemId: "item-1",
      contentIndex: 0,
      transcript: "final transcript",
    });

    expect(socket.send).toHaveBeenCalledWith(
      expect.stringContaining('"type":"candidate_transcript_finalized"'),
    );
    expect(client.pendingCount).toBe(1);
    const sent = JSON.parse(String(socket.send.mock.calls.at(-1)?.[0])) as Record<string, string>;

    socket.close();
    const reconnectPromise = client.connectDevelopmentInterview();
    await waitFor(() => {
      expect(FakeControlWebSocket.instances.length).toBe(2);
    });
    const retrySocket = FakeControlWebSocket.instances[1];
    retrySocket.open();
    retrySocket.receive({
      type: "server_hello",
      interview_session_id: "session-1",
      current_stage: "IMPLEMENTATION",
      state_version: 0,
      last_server_sequence: 0,
    });
    await reconnectPromise;

    expect(retrySocket.send).toHaveBeenCalledWith(expect.stringContaining(sent.client_event_id));
    retrySocket.receive({
      type: "durable_event_ack",
      client_event_id: sent.client_event_id,
      created: true,
      interview_event_id: "event-1",
      transcript_segment_id: "segment-1",
      server_sequence: 1,
      interview_state_version: 0,
    });

    expect(client.pendingCount).toBe(0);
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "debug_updated",
        debug: expect.objectContaining({
          pendingDurableMessages: 0,
          lastServerSequence: 1,
        }),
      }),
    );
  });

  it("control client requests policy gate, then delivery permit before speaking", async () => {
    const fetchFn = vi.fn(async () => new Response(JSON.stringify(fakeDevelopmentBootstrap)));
    const client = new RealtimeControlClient({
      apiBaseUrl: "http://127.0.0.1:8000",
      fetchFn: fetchFn as typeof fetch,
      websocketFactory: (url) => new FakeControlWebSocket(url) as unknown as WebSocket,
      storage: {
        getItem: () => "client-instance",
        setItem: vi.fn(),
      },
      randomUUID: () => "stable-id",
    });
    const events: RealtimeControlEvent[] = [];
    client.on((event) => events.push(event));

    const connectPromise = client.connectDevelopmentInterview();
    await waitFor(() => expect(FakeControlWebSocket.instances.length).toBeGreaterThan(0));
    const socket = FakeControlWebSocket.instances.at(-1)!;
    socket.open();
    socket.receive({
      type: "server_hello",
      interview_session_id: "session-1",
      current_stage: "IMPLEMENTATION",
      state_version: 0,
      last_server_sequence: 0,
    });
    await connectPromise;

    client.requestExaminerDecisionPolicyGate("decision-1");
    expect(socket.send).toHaveBeenLastCalledWith(
      expect.stringContaining('"type":"examiner_decision_policy_gate_requested"'),
    );
    socket.receive({
      type: "policy_gate_result",
      client_event_id: "ctrl-2-stable-id",
      examiner_decision_id: "decision-1",
      disposition: "AUTHORIZED",
      decision_status: "AUTHORIZED",
      policy_gate_outcome: "AUTHORIZED",
      reason: "authorized",
      interviewer_prompt_id: "prompt-1",
      prompt_kind: "PROBE",
      probe_strategy: "PROVE",
      candidate_safe_text: "What invariant holds?",
    });

    expect(events.some((event) => event.type === "authorized_prompt")).toBe(false);
    const gateEvent = events.find((event) => event.type === "policy_gate_result");
    expect(gateEvent).toMatchObject({
      type: "policy_gate_result",
      result: { disposition: "AUTHORIZED", promptId: "prompt-1" },
    });

    client.requestPromptDeliveryPermit("prompt-1");
    expect(socket.send).toHaveBeenLastCalledWith(
      expect.stringContaining('"type":"prompt_delivery_permit_requested"'),
    );
    socket.receive({
      type: "prompt_delivery_permit",
      client_event_id: "ctrl-3-stable-id",
      interviewer_prompt_id: "prompt-1",
      text: "What invariant holds?",
      origin: "EXAMINER_DECISION",
      kind: "PROBE",
    });

    expect(events.at(-1)).toMatchObject({
      type: "authorized_prompt",
      prompt: {
        promptId: "prompt-1",
        text: "What invariant holds?",
        origin: "EXAMINER_DECISION",
        kind: "PROBE",
      },
    });
  });

  it("queues durable control messages until server hello marks the channel ready", async () => {
    const fetchFn = vi.fn(async () => new Response(JSON.stringify(fakeDevelopmentBootstrap)));
    const storage = new Map<string, string>();
    const client = new RealtimeControlClient({
      apiBaseUrl: "http://127.0.0.1:8000",
      fetchFn: fetchFn as typeof fetch,
      websocketFactory: (url) => new FakeControlWebSocket(url) as unknown as WebSocket,
      storage: {
        getItem: (key) => storage.get(key) ?? null,
        setItem: (key, value) => {
          storage.set(key, value);
        },
      },
      randomUUID: () => "stable-id",
    });
    const events: RealtimeControlEvent[] = [];
    client.on((event) => events.push(event));

    const connectPromise = client.connectDevelopmentInterview();
    await waitFor(() => {
      expect(FakeControlWebSocket.instances.length).toBe(1);
    });
    const socket = FakeControlWebSocket.instances[0];
    socket.open();

    client.sendCandidateTranscriptFinal({
      providerItemId: "provider-item-before-ready",
      contentIndex: 0,
      transcript: "final transcript",
    });

    expect(client.pendingCount).toBe(1);
    expect(socket.send).not.toHaveBeenCalled();
    expect(events).not.toContainEqual(
      expect.objectContaining({ type: "error" }),
    );

    socket.receive({
      type: "server_hello",
      interview_session_id: "session-1",
      current_stage: "IMPLEMENTATION",
      state_version: 0,
      last_server_sequence: 0,
    });
    const flushed = JSON.parse(String(socket.send.mock.calls[0]?.[0])) as Record<
      string,
      unknown
    >;
    await connectPromise;

    expect(flushed).toMatchObject({
      type: "candidate_transcript_finalized",
      client_event_id: "ctrl-1-stable-id",
      client_sequence: 1,
      idempotency_key: "candidate-transcript:provider-item-before-ready:0",
    });
    expect(socket.send).toHaveBeenCalledWith(expect.stringContaining('"type":"client_hello"'));

    socket.receive({
      type: "durable_event_ack",
      client_event_id: flushed.client_event_id,
      created: true,
      interview_event_id: "event-1",
      transcript_segment_id: "segment-1",
      server_sequence: 1,
      interview_state_version: 0,
    });

    expect(client.pendingCount).toBe(0);
    expect(events).not.toContainEqual(expect.objectContaining({ type: "error" }));
  });

  it("sends canonical code snapshots through the durable control queue and records safe metadata", async () => {
    const fetchFn = vi.fn(async () => new Response(JSON.stringify(fakeDevelopmentBootstrap)));
    const client = new RealtimeControlClient({
      apiBaseUrl: "http://127.0.0.1:8000",
      fetchFn: fetchFn as typeof fetch,
      websocketFactory: (url) => new FakeControlWebSocket(url) as unknown as WebSocket,
      randomUUID: () => "stable-id",
    });
    const events: RealtimeControlEvent[] = [];
    client.on((event) => events.push(event));

    const connectPromise = client.connectDevelopmentInterview();
    await waitFor(() => {
      expect(FakeControlWebSocket.instances.length).toBe(1);
    });
    const socket = FakeControlWebSocket.instances[0];
    socket.open();

    client.sendCandidateCodeSnapshot({
      sourceCode: "class Solution {};",
      language: "cpp",
      trigger: "INITIAL_EDITOR_STATE",
      idempotencyKey: "code-initial-1",
    });
    expect(socket.send).not.toHaveBeenCalled();

    socket.receive({
      type: "server_hello",
      interview_session_id: "session-1",
      current_stage: "IMPLEMENTATION",
      state_version: 0,
      last_server_sequence: 0,
    });
    await connectPromise;

    const sent = JSON.parse(String(socket.send.mock.calls[0]?.[0])) as Record<string, unknown>;
    expect(sent).toMatchObject({
      type: "candidate_code_snapshot",
      source_code: "class Solution {};",
      language: "cpp",
      trigger: "INITIAL_EDITOR_STATE",
      idempotency_key: "code-initial-1",
    });

    socket.receive({
      type: "durable_event_ack",
      client_event_id: sent.client_event_id,
      created: true,
      interview_event_id: "event-code-1",
      code_snapshot_id: "snapshot-1",
      code_diff_id: null,
      code_version: 1,
      content_hash: "abcdef1234567890",
      observation_kind: "CODE_SNAPSHOT_CREATED",
      observation_trigger_class: "INTERVIEWER_CONTEXT",
      observation_interview_stage: "IMPLEMENTATION",
      server_sequence: 1,
      interview_state_version: 0,
    });

    expect(client.pendingCount).toBe(0);
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "debug_updated",
        debug: expect.objectContaining({
          lastCode: {
            snapshotId: "snapshot-1",
            version: 1,
            hashPrefix: "abcdef123456",
            diffId: null,
            persistence: "ACKNOWLEDGED",
          },
          lastObservation: expect.objectContaining({
            kind: "CODE_SNAPSHOT_CREATED",
            sourceEventId: "event-code-1",
            sourceEventWatermark: 1,
          }),
        }),
      }),
    );
  });

  it("keeps the initial code snapshot immediate and waits 2500ms for edit bursts", async () => {
    vi.useFakeTimers();
    const sendSnapshot = vi.fn();
    function Harness(props: { sourceCode: string; controlReady: boolean }) {
      useCodeObservationCollector({
        sourceCode: props.sourceCode,
        controlReady: props.controlReady,
        sendSnapshot,
        randomId: () => "stable-code-id",
      });
      return <p data-testid="code-source-length">{props.sourceCode.length}</p>;
    }

    const live = render(<Harness sourceCode="class Solution {};" controlReady={false} />);
    expect(sendSnapshot).not.toHaveBeenCalled();

    live.rerender(<Harness sourceCode="class Solution {};" controlReady />);
    expect(sendSnapshot).toHaveBeenCalledWith(
      "class Solution {};",
      "INITIAL_EDITOR_STATE",
      "candidate-code:INITIAL_EDITOR_STATE:1:stable-code-id",
    );

    live.rerender(<Harness sourceCode="class Solution { int x; };" controlReady />);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(sendSnapshot).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(sendSnapshot).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(499);
    expect(sendSnapshot).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(sendSnapshot).toHaveBeenLastCalledWith(
      "class Solution { int x; };",
      "EDIT_BURST",
      "candidate-code:EDIT_BURST:2:stable-code-id",
    );
    expect(sendSnapshot).toHaveBeenCalledTimes(2);
    live.unmount();
  });

  it("coalesces short natural pauses into one code observation containing the final source", async () => {
    vi.useFakeTimers();
    const sendSnapshot = vi.fn();
    function Harness(props: { sourceCode: string }) {
      useCodeObservationCollector({
        sourceCode: props.sourceCode,
        controlReady: true,
        sendSnapshot,
        randomId: () => "stable-code-id",
      });
      return null;
    }

    const live = render(<Harness sourceCode="source A" />);
    expect(sendSnapshot).toHaveBeenCalledTimes(1);

    live.rerender(<Harness sourceCode="source B" />);
    await vi.advanceTimersByTimeAsync(900);
    live.rerender(<Harness sourceCode="source C" />);
    await vi.advanceTimersByTimeAsync(1_400);
    live.rerender(<Harness sourceCode="source D" />);
    await vi.advanceTimersByTimeAsync(2_000);
    live.rerender(<Harness sourceCode="source E" />);
    await vi.advanceTimersByTimeAsync(CODE_EDIT_BURST_IDLE_MS - 1);
    expect(sendSnapshot).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1);
    expect(sendSnapshot).toHaveBeenCalledTimes(2);
    expect(sendSnapshot).toHaveBeenLastCalledWith(
      "source E",
      "EDIT_BURST",
      "candidate-code:EDIT_BURST:2:stable-code-id",
    );
    live.unmount();
  });

  it("does not create periodic code snapshots during continuous typing", async () => {
    vi.useFakeTimers();
    const sendSnapshot = vi.fn();
    function Harness(props: { sourceCode: string }) {
      useCodeObservationCollector({
        sourceCode: props.sourceCode,
        controlReady: true,
        sendSnapshot,
        randomId: () => "stable-code-id",
      });
      return null;
    }

    const live = render(<Harness sourceCode="source 0" />);
    for (let index = 1; index <= 11; index += 1) {
      live.rerender(<Harness sourceCode={`source ${index}`} />);
      await vi.advanceTimersByTimeAsync(1_000);
    }

    expect(sendSnapshot).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(CODE_EDIT_BURST_IDLE_MS);
    expect(sendSnapshot).toHaveBeenCalledTimes(2);
    expect(sendSnapshot).toHaveBeenLastCalledWith(
      "source 11",
      "EDIT_BURST",
      "candidate-code:EDIT_BURST:2:stable-code-id",
    );
    live.unmount();
  });

  it("emits one new code observation for each idle-separated edit burst", async () => {
    vi.useFakeTimers();
    const sendSnapshot = vi.fn();
    function Harness(props: { sourceCode: string }) {
      useCodeObservationCollector({
        sourceCode: props.sourceCode,
        controlReady: true,
        sendSnapshot,
        randomId: () => "stable-code-id",
      });
      return null;
    }

    const live = render(<Harness sourceCode="source 0" />);
    live.rerender(<Harness sourceCode="source 1" />);
    await vi.advanceTimersByTimeAsync(CODE_EDIT_BURST_IDLE_MS);
    expect(sendSnapshot).toHaveBeenCalledTimes(2);

    live.rerender(<Harness sourceCode="source 2" />);
    await vi.advanceTimersByTimeAsync(CODE_EDIT_BURST_IDLE_MS);
    expect(sendSnapshot).toHaveBeenCalledTimes(3);
    expect(sendSnapshot).toHaveBeenLastCalledWith(
      "source 2",
      "EDIT_BURST",
      "candidate-code:EDIT_BURST:3:stable-code-id",
    );
    live.unmount();
  });

  it("emits ephemeral code activity started and idle around an edit burst", async () => {
    vi.useFakeTimers();
    const sendSnapshot = vi.fn();
    const noteActivityStarted = vi.fn();
    const noteActivityIdle = vi.fn();
    function Harness(props: { sourceCode: string }) {
      useCodeObservationCollector({
        sourceCode: props.sourceCode,
        controlReady: true,
        sendSnapshot,
        noteActivityStarted,
        noteActivityIdle,
        randomId: () => "stable-code-id",
      });
      return null;
    }

    const live = render(<Harness sourceCode="source 0" />);
    live.rerender(<Harness sourceCode="source 1" />);
    expect(noteActivityStarted).toHaveBeenCalledTimes(1);
    expect(noteActivityIdle).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(CODE_EDIT_BURST_IDLE_MS);
    expect(noteActivityIdle).toHaveBeenCalledTimes(1);
    expect(sendSnapshot).toHaveBeenLastCalledWith(
      "source 1",
      "EDIT_BURST",
      "candidate-code:EDIT_BURST:2:stable-code-id",
    );
    live.unmount();
  });

  it("cancels a pending code-observation timer on unchanged source and unmount", async () => {
    vi.useFakeTimers();
    const sendSnapshot = vi.fn();
    function Harness(props: { sourceCode: string }) {
      useCodeObservationCollector({
        sourceCode: props.sourceCode,
        controlReady: true,
        sendSnapshot,
        randomId: () => "stable-code-id",
      });
      return null;
    }

    const live = render(<Harness sourceCode="source 0" />);
    live.rerender(<Harness sourceCode="source 1" />);
    await vi.advanceTimersByTimeAsync(1_000);
    live.rerender(<Harness sourceCode="source 0" />);
    await vi.advanceTimersByTimeAsync(CODE_EDIT_BURST_IDLE_MS);
    expect(sendSnapshot).toHaveBeenCalledTimes(1);

    live.rerender(<Harness sourceCode="source 2" />);
    await vi.advanceTimersByTimeAsync(1_000);
    live.unmount();
    await vi.advanceTimersByTimeAsync(CODE_EDIT_BURST_IDLE_MS);
    expect(sendSnapshot).toHaveBeenCalledTimes(1);
  });

  it("surfaces genuine backend semantic rejection after control is ready", async () => {
    const fetchFn = vi.fn(async () => new Response(JSON.stringify(fakeDevelopmentBootstrap)));
    const client = new RealtimeControlClient({
      apiBaseUrl: "http://127.0.0.1:8000",
      fetchFn: fetchFn as typeof fetch,
      websocketFactory: (url) => new FakeControlWebSocket(url) as unknown as WebSocket,
      randomUUID: () => "stable-id",
    });
    const events: RealtimeControlEvent[] = [];
    client.on((event) => events.push(event));

    const connectPromise = client.connectDevelopmentInterview();
    await waitFor(() => {
      expect(FakeControlWebSocket.instances.length).toBe(1);
    });
    const socket = FakeControlWebSocket.instances[0];
    socket.open();
    socket.receive({
      type: "server_hello",
      interview_session_id: "session-1",
      current_stage: "IMPLEMENTATION",
      state_version: 0,
      last_server_sequence: 0,
    });
    await connectPromise;

    client.sendCandidateTranscriptFinal({
      providerItemId: "provider-item-rejected",
      contentIndex: 0,
      transcript: "final transcript",
    });
    const sent = JSON.parse(String(socket.send.mock.calls.at(-1)?.[0])) as Record<
      string,
      unknown
    >;
    socket.receive({
      type: "control_error",
      client_event_id: sent.client_event_id,
      category: "control_rejected",
      message: "Realtime control message conflicts with previously accepted truth",
    });

    expect(client.pendingCount).toBe(0);
    expect(events).toContainEqual({
      type: "error",
      message: "CounterQ control message was rejected.",
    });
  });

  it("connects WebRTC only after the realtime data channel is open", async () => {
    const dataChannel = new FakeDataChannel("connecting");
    const { client, fetchFn, peerConnection, track } = createBrowserClient({ dataChannel });
    const events: RealtimeClientEvent[] = [];
    client.on((event) => events.push(event));

    const connectPromise = client.connect();
    await waitFor(() => {
      expect(peerConnection.setRemoteDescription).toHaveBeenCalled();
    });
    expect(events.map((event) => event.type)).not.toContain("connected");

    dataChannel.open();
    await connectPromise;
    client.disconnect();

    expect(fetchFn).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:8000/api/realtime/session",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchFn).toHaveBeenNthCalledWith(
      2,
      "https://api.openai.com/v1/realtime/calls",
      expect.objectContaining({
        method: "POST",
        body: "offer-sdp",
        headers: expect.objectContaining({
          Authorization: "Bearer ephemeral",
          "Content-Type": "application/sdp",
        }),
      }),
    );
    expect(peerConnection.addTrack).toHaveBeenCalledWith(track, expect.anything());
    expect(events.map((event) => event.type)).toContain("connected");
  });

  it("sends OpenAI cancel, clear, and truncate events for active output interruption", async () => {
    const nowSpy = vi
      .spyOn(performance, "now")
      .mockReturnValueOnce(1_000)
      .mockReturnValueOnce(1_420);
    const { client, dataChannel } = createBrowserClient();

    await client.connect();
    dataChannel.emitMessage({
      type: "response.created",
      response: { id: "resp-1", output: [{ id: "assistant-item-1" }] },
    });
    dataChannel.emitMessage({
      type: "output_audio_buffer.started",
      response_id: "resp-1",
      item_id: "assistant-item-1",
    });

    client.interruptActiveOutputForCandidateSpeech();

    const sent = dataChannel.send.mock.calls.map((call) => JSON.parse(String(call[0])));
    expect(sent).toEqual([
      expect.objectContaining({ type: "response.cancel", response_id: "resp-1" }),
      expect.objectContaining({ type: "output_audio_buffer.clear" }),
      expect.objectContaining({
        type: "conversation.item.truncate",
        item_id: "assistant-item-1",
        content_index: 0,
        audio_end_ms: 420,
      }),
    ]);
    client.disconnect();
    nowSpy.mockRestore();
  });

  it("times out and releases resources when the data channel never becomes ready", async () => {
    vi.useFakeTimers();
    const dataChannel = new FakeDataChannel("connecting");
    const { client, peerConnection, track } = createBrowserClient({
      dataChannel,
      connectionTimeoutMs: 25,
    });
    const events: RealtimeClientEvent[] = [];
    client.on((event) => events.push(event));

    const connectPromise = client.connect();
    const rejectionExpectation = expect(connectPromise).rejects.toThrow(
      "Realtime voice connection timed out.",
    );
    await flushAsyncWork();
    await vi.advanceTimersByTimeAsync(30);

    await rejectionExpectation;
    expect(track.stop).toHaveBeenCalled();
    expect(peerConnection.close).toHaveBeenCalled();
    expect(dataChannel.close).toHaveBeenCalled();
    expect(events).toContainEqual({
      type: "error",
      message: "Realtime voice connection timed out.",
    });
  });

  it("stops microphone and closes transports on post-connect connection failure", async () => {
    const { audioElement, client, dataChannel, peerConnection, track } = createBrowserClient();
    const events: RealtimeClientEvent[] = [];
    client.on((event) => events.push(event));
    await client.connect();

    peerConnection.emitConnectionState("failed");

    expect(track.stop).toHaveBeenCalled();
    expect(peerConnection.close).toHaveBeenCalled();
    expect(dataChannel.close).toHaveBeenCalled();
    expect(audioElement.pause).toHaveBeenCalled();
    expect(events.at(-1)).toEqual({
      type: "error",
      message: "Realtime voice connection was interrupted.",
    });
  });

  it("cleans resources on fatal data-channel and autoplay failures", async () => {
    const first = createBrowserClient();
    const firstEvents: RealtimeClientEvent[] = [];
    first.client.on((event) => firstEvents.push(event));
    await first.client.connect();

    first.dataChannel.fail();

    expect(first.track.stop).toHaveBeenCalled();
    expect(first.peerConnection.close).toHaveBeenCalled();
    expect(firstEvents.at(-1)).toEqual({
      type: "error",
      message: "Realtime event channel failed.",
    });

    const audioPlay = vi.fn(async () => {
      throw new Error("autoplay blocked");
    });
    const second = createBrowserClient({ audioPlay });
    const secondEvents: RealtimeClientEvent[] = [];
    second.client.on((event) => secondEvents.push(event));
    await second.client.connect();
    second.peerConnection.ontrack?.({
      streams: [second.stream],
      track: second.track,
    } as unknown as RTCTrackEvent);

    await waitFor(() => {
      expect(second.track.stop).toHaveBeenCalled();
    });
    expect(second.audioElement.pause).toHaveBeenCalled();
    expect(secondEvents.at(-1)).toEqual({
      type: "error",
      message: "Browser blocked realtime audio playback. Re-enable voice after allowing audio.",
    });
  });

  it("retries after fatal failure with clean microphone resources", async () => {
    const firstTrack = new FakeTrack();
    const secondTrack = new FakeTrack();
    let connectionCount = 0;
    const first = createBrowserClient({ track: firstTrack });
    const second = createBrowserClient({ track: secondTrack });
    const client = new RealtimeVoiceClient({
      apiBaseUrl: "http://127.0.0.1:8000",
      fetchFn: vi.fn((...args: Parameters<typeof fetch>) =>
        connectionCount === 1 ? first.fetchFn(...args) : second.fetchFn(...args),
      ) as typeof fetch,
      mediaDevices: {
        getUserMedia: vi.fn(async () => {
          connectionCount += 1;
          return connectionCount === 1 ? first.stream : second.stream;
        }),
      },
      peerConnectionFactory: () =>
        (connectionCount === 1
          ? first.peerConnection
          : second.peerConnection) as unknown as RTCPeerConnection,
      audioElementFactory: () => first.audioElement,
    });

    await client.connect();
    first.peerConnection.emitConnectionState("failed");
    expect(firstTrack.stop).toHaveBeenCalledTimes(1);

    await client.connect();
    client.disconnect();

    expect(second.peerConnection.addTrack).toHaveBeenCalledWith(secondTrack, second.stream);
    expect(secondTrack.stop).toHaveBeenCalledTimes(1);
    expect(firstTrack.stop).toHaveBeenCalledTimes(1);
  });

  it("surfaces permission denial from browser media APIs", async () => {
    const client = new RealtimeVoiceClient({
      apiBaseUrl: "http://127.0.0.1:8000",
      fetchFn: vi.fn(),
      mediaDevices: {
        getUserMedia: vi.fn(async () => {
          throw new DOMException("Denied", "NotAllowedError");
        }),
      },
      peerConnectionFactory: () => new FakePeerConnection() as unknown as RTCPeerConnection,
    });
    const events: RealtimeClientEvent[] = [];
    client.on((event) => events.push(event));

    await expect(client.connect()).rejects.toThrow("Microphone permission was denied.");

    await waitFor(() => {
      expect(events).toContainEqual({
        type: "error",
        message: "Microphone permission was denied.",
      });
    });
  });
});
