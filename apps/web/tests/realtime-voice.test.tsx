import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  RealtimeVoiceClient,
  type RealtimeClientEvent,
} from "../features/interview-room/realtime/RealtimeVoiceClient";
import { normalizeRealtimeEvent } from "../features/interview-room/realtime/events";
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

function mediaStreamFor(track: FakeTrack): MediaStream {
  return {
    getAudioTracks: () => [track],
    getTracks: () => [track],
  } as unknown as MediaStream;
}

function renderRealtimeHarness(client: HookFakeClient) {
  function Harness() {
    const voice = useRealtimeVoice({
      clientFactory: () => client as unknown as RealtimeVoiceClient,
    });
    return (
      <div>
        <p data-testid="voice-state">{voice.voiceState}</p>
        <p data-testid="muted-state">{String(voice.isMuted)}</p>
        <p data-testid="partial-transcript">{voice.partialTranscript}</p>
        <p data-testid="final-transcript">{voice.lastFinalTranscript}</p>
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
      </div>
    );
  }

  return render(<Harness />);
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
    renderRealtimeHarness(new HookFakeClient());

    fireEvent.click(screen.getByRole("button", { name: "enable" }));

    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    });
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
    renderRealtimeHarness(client);
    fireEvent.click(screen.getByRole("button", { name: "enable" }));
    await waitFor(() => {
      expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
    });

    act(() => {
      client.emit({ type: "transcript_delta", text: "hash map" });
    });
    expect(screen.getByTestId("partial-transcript")).toHaveTextContent("hash map");
    expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");

    act(() => {
      client.emit({ type: "transcript_final", text: "hash map lookup" });
    });
    expect(screen.getByTestId("partial-transcript")).toHaveTextContent("");
    expect(screen.getByTestId("final-transcript")).toHaveTextContent("hash map lookup");
    expect(screen.getByTestId("voice-state")).toHaveTextContent("Listening");
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
      { type: "counterq_output_interrupted" },
    ]);
    expect(normalizeRealtimeEvent({ type: "input_audio_buffer.speech_stopped" })).toEqual([
      { type: "candidate_speech_stopped" },
    ]);
    expect(
      normalizeRealtimeEvent({
        type: "conversation.item.input_audio_transcription.delta",
        delta: "hash",
      }),
    ).toEqual([{ type: "transcript_delta", text: "hash" }]);
    expect(
      normalizeRealtimeEvent({
        type: "conversation.item.input_audio_transcription.completed",
        transcript: "final text",
      }),
    ).toEqual([{ type: "transcript_final", text: "final text" }]);
    expect(normalizeRealtimeEvent({ type: "response.output_audio.delta" })).toEqual([
      { type: "counterq_output_started" },
    ]);
    expect(normalizeRealtimeEvent({ type: "response.output_audio.done" })).toEqual([
      { type: "counterq_output_ended" },
    ]);
    expect(normalizeRealtimeEvent({ type: "response.done", response: { status: "completed" } }))
      .toEqual([{ type: "counterq_output_ended" }]);
    expect(normalizeRealtimeEvent({ type: "response.done", response: { status: "cancelled" } }))
      .toEqual([{ type: "counterq_output_interrupted" }]);
    expect(normalizeRealtimeEvent({ type: "output_audio_buffer.cleared" })).toEqual([
      { type: "counterq_output_interrupted" },
    ]);
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
