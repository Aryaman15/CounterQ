import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DevelopmentBootstrapResponse } from "@/features/interview-room/realtime/RealtimeControlClient";
import { RealtimeControlClient } from "@/features/interview-room/realtime/RealtimeControlClient";
import {
  SelfServeInterviewSetup,
  SelfServeInterviewSetupForm,
} from "@/features/interview-room/components/SelfServeInterviewSetup";
import { ProductionInterviewRoom } from "@/features/interview-room/components/ProductionInterviewRoom";
import { CounterQApiClient } from "@/lib/counterq-api";

const authMocks = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  useAuth: vi.fn(),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => authMocks.useAuth(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: authMocks.push, replace: authMocks.replace }),
}));

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const profileResponse = {
  user_id: "01991b74-927a-7000-8000-000000000001",
  account_status: "ACTIVE",
  onboarding_required: false,
  profile: {
    display_name: null,
    preferred_language: "cpp" as const,
    default_interview_mode: "COACH" as const,
    interview_level: "EARLY_CAREER" as const,
    target_role: null,
    timezone: "Asia/Calcutta",
    profile_version: 1,
    created_at: "2026-09-10T10:00:00Z",
    updated_at: "2026-09-10T10:00:00Z",
  },
};

const catalog = [
  {
    problem_version_id: "01991b74-927a-7000-8000-000000000020",
    slug: "later-problem",
    title: "Later Problem",
    supported_languages: ["python" as const],
    catalog_order: 2,
  },
  {
    problem_version_id: "01991b74-927a-7000-8000-000000000010",
    slug: "first-problem",
    title: "First Problem",
    supported_languages: ["cpp" as const, "python" as const],
    catalog_order: 1,
  },
];

const bootstrap: DevelopmentBootstrapResponse = {
  interview_session_id: "session-1",
  language: "cpp",
  problem: {
    ...catalog[1],
    statement: "Return the matching indices.",
    constraints: ["2 <= nums.length"],
    examples: [],
    selected_language: "cpp",
    display_signature: "vector<int> twoSum(vector<int>& nums, int target)",
    starter_code: "class Solution {};",
    argument_schema: [],
    return_type: "int[]",
    comparator: "EXACT",
    custom_test_supported: true,
  },
  template: "STANDARD_CODING_INTERVIEW",
  configured_duration_seconds: 1800,
  mode: "SIMULATION",
  current_stage: "INTRODUCTION",
  session_status: "ACTIVE",
  state_version: 0,
  started_at: "2026-09-10T10:00:00Z",
  deadline_at: "2026-09-10T10:30:00Z",
  time_remaining_seconds: 1700,
  time_pressure: "NORMAL",
  control_websocket_path: "/api/realtime/control/session-1",
  restoration: "RESTORED",
  restore_protocol_version: "session.restore.v1",
  latest_code_snapshot: null,
  recent_conversation: [],
  unresolved_prompt: null,
  highest_client_sequence: 0,
  last_server_sequence: 0,
  protocol_version: "counterq.realtime.control.v1",
};

class FakeControlWebSocket {
  static instances: FakeControlWebSocket[] = [];
  static autoOpen = false;
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  readyState: number = FakeControlWebSocket.CONNECTING;
  readonly send = vi.fn();
  readonly close = vi.fn();
  private listeners = new Map<string, Set<EventListener>>();

  constructor(readonly url: string) {
    FakeControlWebSocket.instances.push(this);
    if (FakeControlWebSocket.autoOpen) {
      queueMicrotask(() => {
        this.open();
        this.receive({
          type: "server_hello",
          interview_session_id: "session-1",
          current_stage: "INTRODUCTION",
          state_version: 0,
          last_server_sequence: 0,
        });
      });
    }
  }

  addEventListener(type: string, listener: EventListener) {
    const listeners = this.listeners.get(type) ?? new Set<EventListener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: EventListener) {
    this.listeners.get(type)?.delete(listener);
  }

  open() {
    this.readyState = FakeControlWebSocket.OPEN;
    this.emit("open", new Event("open"));
  }

  receive(message: Record<string, unknown>) {
    this.emit("message", { data: JSON.stringify(message) } as MessageEvent);
  }

  unexpectedClose() {
    this.readyState = FakeControlWebSocket.CLOSED;
    this.emit("close", new Event("close"));
  }

  private emit(type: string, event: Event) {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

beforeEach(() => {
  authMocks.push.mockReset();
  authMocks.replace.mockReset();
  authMocks.useAuth.mockReset();
  authMocks.useAuth.mockReturnValue({
    getToken: vi.fn(async () => "clerk-token"),
    isLoaded: true,
    isSignedIn: true,
    userId: "clerk-user",
  });
  FakeControlWebSocket.instances = [];
  FakeControlWebSocket.autoOpen = false;
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function flushAsyncWork(): Promise<void> {
  for (let index = 0; index < 8; index += 1) {
    await Promise.resolve();
  }
}

describe("Stage 9B self-serve frontend", () => {
  it("waits for Clerk readiness and hands missing profiles to onboarding", async () => {
    const getToken = vi.fn(async () => null);
    const fetchFn = vi.fn();
    vi.stubGlobal("fetch", fetchFn);
    authMocks.useAuth.mockReturnValue({
      getToken,
      isLoaded: false,
      isSignedIn: undefined,
      userId: undefined,
    });
    const { rerender } = render(<SelfServeInterviewSetup />);

    expect(screen.getByRole("status")).toHaveTextContent(/Confirming/i);
    expect(fetchFn).not.toHaveBeenCalled();
    expect(getToken).not.toHaveBeenCalled();

    authMocks.useAuth.mockReturnValue({
      getToken: vi.fn(async () => "fresh-token"),
      isLoaded: true,
      isSignedIn: true,
      userId: "clerk-user",
    });
    fetchFn.mockResolvedValueOnce(jsonResponse({
      ...profileResponse,
      onboarding_required: true,
      profile: null,
    }));
    rerender(<SelfServeInterviewSetup />);

    await waitFor(() => expect(authMocks.replace).toHaveBeenCalledWith("/onboarding"));
    expect(fetchFn).toHaveBeenCalledOnce();
  });

  it("uses profile defaults, catalog order, compatibility, and an inherited level", async () => {
    const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return jsonResponse(profileResponse);
      return jsonResponse(catalog);
    });
    const api = new CounterQApiClient(async () => "token", "http://api.test", fetchFn as typeof fetch);
    const { container } = render(
      <SelfServeInterviewSetupForm
        api={api}
        onOnboardingRequired={vi.fn()}
        onCreated={vi.fn()}
      />,
    );

    await screen.findByRole("heading", { name: /Set the conditions/i });
    await waitFor(() => expect(screen.getByLabelText("Mode")).toHaveValue("COACH"));
    expect(screen.getByLabelText("Coding language")).toHaveValue("cpp");
    expect(screen.getByLabelText("Interview level inherited from profile"))
      .toHaveTextContent("Early career");
    expect(screen.queryByRole("combobox", { name: /Interview level/i })).not.toBeInTheDocument();
    const labels = [...container.querySelectorAll(".setup-catalog .problem-option strong")]
      .map((node) => node.textContent);
    expect(labels).toEqual(["First ProblemCounterQ pick", "Later Problem"]);
    expect(screen.getByLabelText(/First Problem/i)).toBeChecked();

    fireEvent.change(screen.getByLabelText("Coding language"), { target: { value: "java" } });
    expect(screen.getByRole("button", { name: "Start interview" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Coding language"), { target: { value: "python" } });
    expect(screen.getByLabelText(/First Problem/i)).toBeChecked();
  });

  it("submits only selectable fields once and navigates to the returned session", async () => {
    let resolveCreation!: (response: Response) => void;
    const pendingCreation = new Promise<Response>((resolve) => { resolveCreation = resolve; });
    const fetchFn = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      void init;
      const url = String(input);
      if (url.endsWith("/api/me")) return Promise.resolve(jsonResponse(profileResponse));
      if (url.endsWith("/api/problems/curated")) return Promise.resolve(jsonResponse(catalog));
      return pendingCreation;
    });
    const onCreated = vi.fn();
    const api = new CounterQApiClient(async () => "token", "http://api.test", fetchFn as typeof fetch);
    render(
      <SelfServeInterviewSetupForm
        api={api}
        onOnboardingRequired={vi.fn()}
        onCreated={onCreated}
      />,
    );
    const start = await screen.findByRole("button", { name: "Start interview" });
    await waitFor(() => expect(start).toBeEnabled());

    fireEvent.click(start);
    fireEvent.click(start);
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(3));
    const [url, init] = fetchFn.mock.calls[2];
    expect(url).toBe("http://api.test/api/interviews");
    expect(JSON.parse(String(init?.body))).toEqual({
      problem_version_id: catalog[1].problem_version_id,
      template: "STANDARD_CODING_INTERVIEW",
      mode: "COACH",
      language: "cpp",
    });
    expect(String(init?.body)).not.toMatch(/user_id|level|duration|pack|deadline/);

    resolveCreation(jsonResponse({
      interview_session_id: "session-new",
      interview_path: "/interview/session-new",
    }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("/interview/session-new"));
  });

  it("uses a current Clerk token for every production room REST operation", async () => {
    const getToken = vi.fn()
      .mockResolvedValueOnce("token-1")
      .mockResolvedValueOnce("token-2")
      .mockResolvedValueOnce("token-3")
      .mockResolvedValueOnce("token-4")
      .mockResolvedValueOnce("token-5");
    const fetchFn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void init;
      const url = String(input);
      if (url.endsWith("/restore")) return jsonResponse(bootstrap);
      if (url.endsWith("/session")) return jsonResponse({});
      if (url.endsWith("/control-ticket")) return jsonResponse({});
      if (url.endsWith("/runs")) return jsonResponse({});
      return jsonResponse({});
    });
    const api = new CounterQApiClient(getToken, "http://127.0.0.1:8000", fetchFn as typeof fetch);

    await api.restoreInterview("session-1", "browser-1");
    await api.createRealtimeSession("session-1");
    await api.createRealtimeControlTicket("session-1");
    await api.runInterviewCode("session-1", {
      source_code: "return 1;",
      idempotency_key: "run-1",
      client_event_id: "event-1",
      client_instance_id: "browser-1",
      client_sequence: 1,
      run_kind: "VISIBLE",
    });
    await api.requestAssistance("session-1");

    expect(getToken).toHaveBeenCalledTimes(5);
    expect(fetchFn.mock.calls.map(([url]) => String(url))).toEqual([
      "http://127.0.0.1:8000/api/interviews/session-1/restore",
      "http://127.0.0.1:8000/api/realtime/interviews/session-1/session",
      "http://127.0.0.1:8000/api/realtime/interviews/session-1/control-ticket",
      "http://127.0.0.1:8000/api/execution/interviews/session-1/runs",
      "http://127.0.0.1:8000/api/interviews/session-1/assistance-requests",
    ]);
    fetchFn.mock.calls.forEach(([, init], index) => {
      expect(new Headers(init?.headers).get("Authorization")).toBe(`Bearer token-${index + 1}`);
    });
    expect(fetchFn.mock.calls.flat().join(" ")).not.toContain("development");
  });

  it("restores by route session and mints an opaque production ticket before control connect", async () => {
    const restore = vi.fn(async () => bootstrap);
    const issueControlTicket = vi.fn(async () => ({
      ticket: "opaque ticket",
      control_websocket_path: "/api/realtime/control/session-1",
    }));
    const storage = new Map<string, string>();
    const client = new RealtimeControlClient({
      apiBaseUrl: "http://127.0.0.1:8000",
      production: { interviewSessionId: "session-1", restore, issueControlTicket },
      websocketFactory: (url) => new FakeControlWebSocket(url) as unknown as WebSocket,
      storage: {
        getItem: (key) => storage.get(key) ?? null,
        setItem: (key, value) => storage.set(key, value),
      },
      randomUUID: () => "stable-client",
    });

    const connecting = client.restoreProductionInterview();
    await waitFor(() => expect(FakeControlWebSocket.instances).toHaveLength(1));
    const socket = FakeControlWebSocket.instances[0];
    expect(socket.url).toBe(
      "ws://127.0.0.1:8000/api/realtime/control/session-1?ticket=opaque%20ticket",
    );
    expect(socket.url).not.toContain("development");
    expect(socket.url).not.toContain("Bearer");
    socket.open();
    socket.receive({
      type: "server_hello",
      interview_session_id: "session-1",
      current_stage: "INTRODUCTION",
      state_version: 0,
      last_server_sequence: 0,
    });
    const restored = await connecting;

    expect(restored.deadline_at).toBe("2026-09-10T10:30:00Z");
    expect(restore).toHaveBeenCalledOnce();
    expect(restore).toHaveBeenCalledWith("stable-client");
    expect(issueControlTicket).toHaveBeenCalledOnce();
  });

  it("re-reads production truth and resends the same pending durable message with a fresh ticket", async () => {
    vi.useFakeTimers();
    const refreshedBootstrap: DevelopmentBootstrapResponse = {
      ...bootstrap,
      state_version: 4,
      last_server_sequence: 9,
      time_remaining_seconds: 1600,
    };
    const restore = vi.fn(async () => bootstrap)
      .mockResolvedValueOnce(bootstrap)
      .mockResolvedValueOnce(refreshedBootstrap);
    const issueControlTicket = vi.fn(async () => ({
      ticket: "unused-ticket",
      control_websocket_path: "/api/realtime/control/session-1",
    }))
      .mockResolvedValueOnce({
        ticket: "first-consumed-ticket",
        control_websocket_path: "/api/realtime/control/session-1",
      })
      .mockResolvedValueOnce({
        ticket: "second-fresh-ticket",
        control_websocket_path: "/api/realtime/control/session-1",
      });
    const developmentFetch = vi.fn(async () => {
      throw new Error("Production reconnect must not use a development bootstrap.");
    });
    const storage = new Map<string, string>();
    const storageReads: string[] = [];
    const connectedBootstraps: DevelopmentBootstrapResponse[] = [];
    const client = new RealtimeControlClient({
      apiBaseUrl: "http://127.0.0.1:8000",
      production: { interviewSessionId: "session-1", restore, issueControlTicket },
      fetchFn: developmentFetch as typeof fetch,
      websocketFactory: (url) => new FakeControlWebSocket(url) as unknown as WebSocket,
      storage: {
        getItem: (key) => {
          storageReads.push(key);
          return storage.get(key) ?? null;
        },
        setItem: (key, value) => storage.set(key, value),
      },
      randomUUID: () => "stable-production-client",
    });
    client.on((event) => {
      if (event.type === "connected") connectedBootstraps.push(event.bootstrap);
    });

    const firstConnection = client.restoreProductionInterview();
    await flushAsyncWork();
    const firstSocket = FakeControlWebSocket.instances[0];
    expect(firstSocket.url).toContain("ticket=first-consumed-ticket");
    firstSocket.open();
    firstSocket.receive({
      type: "server_hello",
      interview_session_id: "session-1",
      current_stage: "INTRODUCTION",
      state_version: 0,
      last_server_sequence: 0,
    });
    await firstConnection;

    client.sendCandidateTranscriptFinal({
      providerItemId: "candidate-item-1",
      contentIndex: 0,
      transcript: "This durable message must retain its identity.",
    });
    const pendingMessage = firstSocket.send.mock.calls
      .map(([body]) => JSON.parse(String(body)) as Record<string, unknown>)
      .find((message) => message.type === "candidate_transcript_finalized");
    expect(pendingMessage).toBeDefined();
    expect(client.pendingCount).toBe(1);

    firstSocket.unexpectedClose();
    await vi.advanceTimersByTimeAsync(750);
    await flushAsyncWork();

    expect(restore).toHaveBeenCalledTimes(2);
    expect(restore).toHaveBeenNthCalledWith(2, "stable-production-client");
    expect(issueControlTicket).toHaveBeenCalledTimes(2);
    expect(FakeControlWebSocket.instances).toHaveLength(2);
    const secondSocket = FakeControlWebSocket.instances[1];
    expect(secondSocket.url).toBe(
      "ws://127.0.0.1:8000/api/realtime/control/session-1?ticket=second-fresh-ticket",
    );
    expect(secondSocket.url).not.toContain("first-consumed-ticket");
    secondSocket.open();
    secondSocket.receive({
      type: "server_hello",
      interview_session_id: "session-1",
      current_stage: "INTRODUCTION",
      state_version: 4,
      last_server_sequence: 9,
    });
    await flushAsyncWork();

    const resentMessage = secondSocket.send.mock.calls
      .map(([body]) => JSON.parse(String(body)) as Record<string, unknown>)
      .find((message) => message.type === "candidate_transcript_finalized");
    expect(resentMessage).toMatchObject({
      client_event_id: pendingMessage?.client_event_id,
      client_sequence: pendingMessage?.client_sequence,
      transcript: pendingMessage?.transcript,
    });
    expect(connectedBootstraps.at(-1)).toMatchObject({
      interview_session_id: "session-1",
      deadline_at: "2026-09-10T10:30:00Z",
      state_version: 4,
      last_server_sequence: 9,
    });
    expect(developmentFetch).not.toHaveBeenCalled();
    expect(FakeControlWebSocket.instances.map((socket) => socket.url).join(" "))
      .not.toContain("development");
    expect(storageReads).not.toContain("counterq:realtime-control:development-session-id");
    expect(client.pendingCount).toBe(1);
  });

  it("surfaces terminal production truth on reconnect without minting another ticket", async () => {
    vi.useFakeTimers();
    const completedBootstrap: DevelopmentBootstrapResponse = {
      ...bootstrap,
      session_status: "COMPLETED",
      state_version: 6,
      last_server_sequence: 12,
      time_remaining_seconds: 0,
      completed_at: "2026-09-10T10:20:00Z",
      terminal_reason: "USER_ENDED",
    };
    const restore = vi.fn(async () => bootstrap)
      .mockResolvedValueOnce(bootstrap)
      .mockResolvedValueOnce(completedBootstrap);
    const issueControlTicket = vi.fn(async () => ({
      ticket: "first-ticket",
      control_websocket_path: "/api/realtime/control/session-1",
    }));
    const connectedBootstraps: DevelopmentBootstrapResponse[] = [];
    const client = new RealtimeControlClient({
      apiBaseUrl: "http://127.0.0.1:8000",
      production: { interviewSessionId: "session-1", restore, issueControlTicket },
      websocketFactory: (url) => new FakeControlWebSocket(url) as unknown as WebSocket,
      storage: { getItem: () => null, setItem: vi.fn() },
      randomUUID: () => "terminal-production-client",
    });
    client.on((event) => {
      if (event.type === "connected") connectedBootstraps.push(event.bootstrap);
    });

    const firstConnection = client.restoreProductionInterview();
    await flushAsyncWork();
    const firstSocket = FakeControlWebSocket.instances[0];
    firstSocket.open();
    firstSocket.receive({
      type: "server_hello",
      interview_session_id: "session-1",
      current_stage: "INTRODUCTION",
      state_version: 0,
      last_server_sequence: 0,
    });
    await firstConnection;

    firstSocket.unexpectedClose();
    await vi.advanceTimersByTimeAsync(750);
    await flushAsyncWork();

    expect(restore).toHaveBeenCalledTimes(2);
    expect(issueControlTicket).toHaveBeenCalledOnce();
    expect(FakeControlWebSocket.instances).toHaveLength(1);
    expect(connectedBootstraps.at(-1)).toMatchObject({
      interview_session_id: "session-1",
      session_status: "COMPLETED",
      deadline_at: "2026-09-10T10:30:00Z",
      completed_at: "2026-09-10T10:20:00Z",
      terminal_reason: "USER_ENDED",
    });
  });

  it("survives Strict Mode restore replay without leaving an obsolete production client", async () => {
    FakeControlWebSocket.autoOpen = true;
    vi.stubGlobal("WebSocket", FakeControlWebSocket);
    let resolveObsoleteRestore!: (response: Response) => void;
    const obsoleteRestore = new Promise<Response>((resolve) => {
      resolveObsoleteRestore = resolve;
    });
    let restoreRequests = 0;
    let ticketRequests = 0;
    const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/interviews/session-1/restore")) {
        restoreRequests += 1;
        if (restoreRequests === 1) return obsoleteRestore;
        return jsonResponse({ ...bootstrap, deadline_at: "2099-09-10T10:30:00Z" });
      }
      if (url.endsWith("/api/realtime/interviews/session-1/control-ticket")) {
        ticketRequests += 1;
        return jsonResponse({
          ticket: `strict-ticket-${ticketRequests}`,
          expires_after_seconds: 60,
          control_websocket_path: "/api/realtime/control/session-1",
        });
      }
      throw new Error(`Unexpected Strict Mode production request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchFn);

    const mounted = render(
      <StrictMode>
        <ProductionInterviewRoom interviewSessionId="session-1" />
      </StrictMode>,
    );

    await waitFor(() => expect(restoreRequests).toBe(2));
    await screen.findByRole("heading", { name: "First Problem" });
    expect(screen.queryByRole("heading", { name: "Interview unavailable" })).toBeNull();
    expect(FakeControlWebSocket.instances).toHaveLength(1);
    expect(FakeControlWebSocket.instances[0].url).toContain("ticket=strict-ticket-1");

    resolveObsoleteRestore(jsonResponse({ ...bootstrap, deadline_at: "2099-09-10T10:30:00Z" }));
    await flushAsyncWork();

    const urls = fetchFn.mock.calls.map(([url]) => String(url));
    expect(urls.filter((url) => url.endsWith("/api/interviews/session-1/restore")))
      .toHaveLength(2);
    expect(urls.filter((url) => url.endsWith("/api/interviews"))).toHaveLength(0);
    expect(urls.filter((url) => url.includes("/development"))).toHaveLength(0);
    expect(urls.filter((url) => url.endsWith("/control-ticket"))).toHaveLength(1);
    expect(FakeControlWebSocket.instances).toHaveLength(1);

    const activeSocket = FakeControlWebSocket.instances[0];
    expect(activeSocket.close).not.toHaveBeenCalled();
    mounted.unmount();
    expect(activeSocket.close).toHaveBeenCalledOnce();
  });

  it("production room refresh restores the route ID and never creates or bootstraps development state", async () => {
    FakeControlWebSocket.autoOpen = true;
    vi.stubGlobal("WebSocket", FakeControlWebSocket);
    const getToken = vi.fn(async () => "fresh-room-token");
    authMocks.useAuth.mockReturnValue({
      getToken,
      isLoaded: true,
      isSignedIn: true,
      userId: "clerk-user",
    });
    const durableBootstrap = {
      ...bootstrap,
      deadline_at: "2099-09-10T10:30:00Z",
    };
    const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/interviews/session-1/restore")) {
        return jsonResponse(durableBootstrap);
      }
      if (url.endsWith("/api/realtime/interviews/session-1/control-ticket")) {
        return jsonResponse({
          ticket: `opaque-${FakeControlWebSocket.instances.length}`,
          expires_after_seconds: 60,
          control_websocket_path: "/api/realtime/control/session-1",
        });
      }
      throw new Error(`Unexpected production room request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchFn);

    const first = render(<ProductionInterviewRoom interviewSessionId="session-1" />);
    await screen.findByRole("heading", { name: "First Problem" });
    first.unmount();
    render(<ProductionInterviewRoom interviewSessionId="session-1" />);
    await screen.findByRole("heading", { name: "First Problem" });

    const urls = fetchFn.mock.calls.map(([url]) => String(url));
    expect(urls.filter((url) => url.endsWith("/api/interviews/session-1/restore")))
      .toHaveLength(2);
    expect(urls.filter((url) => url.endsWith("/api/interviews"))).toHaveLength(0);
    expect(urls.filter((url) => url.includes("/development"))).toHaveLength(0);
    expect(urls.filter((url) => url.endsWith("/control-ticket"))).toHaveLength(2);
    expect(getToken).toHaveBeenCalledTimes(4);
  });
});
