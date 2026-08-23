import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RealtimeVoiceClient, type RealtimeClientEvent } from "../features/interview-room/realtime/RealtimeVoiceClient";
import { normalizeRealtimeEvent } from "../features/interview-room/realtime/events";
import { useRealtimeVoice } from "../features/interview-room/realtime/useRealtimeVoice";

class FakeTrack {
  enabled = true;
  stop = vi.fn();
}

class FakeDataChannel {
  readyState: RTCDataChannelState = "open";
  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = "closed";
  });
  private readonly listeners = new Map<string, Array<(event: MessageEvent) => void>>();

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    const current = this.listeners.get(type) ?? [];
    current.push(listener);
    this.listeners.set(type, current);
  }

  emitMessage(data: unknown): void {
    for (const listener of this.listeners.get("message") ?? []) {
      listener({ data: JSON.stringify(data) } as MessageEvent);
    }
  }
}

class FakePeerConnection {
  connectionState: RTCPeerConnectionState = "connected";
  ontrack: ((event: RTCTrackEvent) => void) | null = null;
  readonly dataChannel = new FakeDataChannel();
  readonly addEventListener = vi.fn();
  readonly addTrack = vi.fn();
  readonly createDataChannel = vi.fn(() => this.dataChannel as unknown as RTCDataChannel);
  readonly createOffer = vi.fn(async () => ({ type: "offer" as RTCSdpType, sdp: "offer-sdp" }));
  readonly setLocalDescription = vi.fn();
  readonly setRemoteDescription = vi.fn();
  readonly close = vi.fn();
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
        <p>{voice.voiceState}</p>
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

describe("Realtime voice foundation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts Ready, not falsely Listening", () => {
    renderRealtimeHarness(new HookFakeClient());

    expect(screen.getByText("Ready")).toBeInTheDocument();
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

    expect(await screen.findByText("Connecting")).toBeInTheDocument();
  });

  it("shows a recoverable Error when microphone permission is denied", async () => {
    const client = new HookFakeClient();
    client.setConnectImpl(async () => {
      throw new Error("Microphone permission was denied.");
    });
    renderRealtimeHarness(client);

    fireEvent.click(screen.getByRole("button", { name: "enable" }));

    expect(await screen.findByText("Error")).toBeInTheDocument();
    expect(screen.getByText("Microphone permission was denied.")).toBeInTheDocument();
  });

  it("reaches Listening after mocked WebRTC connection succeeds", async () => {
    renderRealtimeHarness(new HookFakeClient());

    fireEvent.click(screen.getByRole("button", { name: "enable" }));

    expect(await screen.findByText("Listening")).toBeInTheDocument();
  });

  it("mutes and unmutes without tearing down the realtime session", async () => {
    const client = new HookFakeClient();
    renderRealtimeHarness(client);
    fireEvent.click(screen.getByRole("button", { name: "enable" }));
    await screen.findByText("Listening");

    fireEvent.click(screen.getByRole("button", { name: "mute" }));
    expect(screen.getByText("Muted")).toBeInTheDocument();
    expect(client.setMuted).toHaveBeenCalledWith(true);

    fireEvent.click(screen.getByRole("button", { name: "unmute" }));
    expect(screen.getByText("Listening")).toBeInTheDocument();
    expect(client.setMuted).toHaveBeenCalledWith(false);
  });

  it("disconnects client resources on component cleanup", async () => {
    const client = new HookFakeClient();
    const { unmount } = renderRealtimeHarness(client);
    fireEvent.click(screen.getByRole("button", { name: "enable" }));
    await screen.findByText("Listening");

    unmount();

    expect(client.disconnect).toHaveBeenCalled();
  });

  it("normalizes speech, transcript, and output events", () => {
    expect(normalizeRealtimeEvent({ type: "input_audio_buffer.speech_started" })).toEqual([
      { type: "candidate_speech_started" },
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
  });

  it("moves to Speaking when provider output begins", async () => {
    const client = new HookFakeClient();
    renderRealtimeHarness(client);
    fireEvent.click(screen.getByRole("button", { name: "enable" }));
    await screen.findByText("Listening");

    act(() => {
      client.emit({ type: "counterq_output_started" });
    });

    expect(screen.getByText("Speaking")).toBeInTheDocument();
  });

  it("connects WebRTC with an ephemeral credential and releases media tracks", async () => {
    const track = new FakeTrack();
    const stream = mediaStreamFor(track);
    const peerConnection = new FakePeerConnection();
    const audioElement = {
      autoplay: false,
      srcObject: null as MediaStream | null,
      play: vi.fn(async () => undefined),
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
    });
    const events: RealtimeClientEvent[] = [];
    client.on((event) => events.push(event));

    await client.connect();
    client.setMuted(true);
    client.setMuted(false);
    peerConnection.dataChannel.emitMessage({ type: "response.output_audio.delta" });
    client.speakAuthorizedDevelopmentPhrase("Walk me through the approach you're considering.");
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
    expect(peerConnection.addTrack).toHaveBeenCalledWith(track, stream);
    expect(peerConnection.setRemoteDescription).toHaveBeenCalledWith({
      type: "answer",
      sdp: "answer-sdp",
    });
    expect(track.enabled).toBe(true);
    expect(track.stop).toHaveBeenCalled();
    expect(peerConnection.close).toHaveBeenCalled();
    expect(peerConnection.dataChannel.close).toHaveBeenCalled();
    expect(peerConnection.dataChannel.send).toHaveBeenCalledWith(
      expect.stringContaining("Walk me through the approach"),
    );
    expect(events.map((event) => event.type)).toContain("connected");
    expect(events.map((event) => event.type)).toContain("counterq_output_started");
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
