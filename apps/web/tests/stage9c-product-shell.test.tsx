import type { components } from "@counterq/contracts/openapi";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { counterMapUiSamples } from "./counterMapUiSamples";

const productMocks = vi.hoisted(() => ({
  pathname: "/",
  push: vi.fn(),
  replace: vi.fn(),
  useAuth: vi.fn(),
}));

vi.mock("@clerk/nextjs", () => ({
  UserButton: () => <button type="button">Candidate account menu</button>,
  useAuth: () => productMocks.useAuth(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => productMocks.pathname,
  useRouter: () => ({ push: productMocks.push, replace: productMocks.replace }),
}));

import { AccountPageExperience } from "@/features/product-shell/AccountPageExperience";
import { HistoryPageExperience } from "@/features/product-shell/HistoryPageExperience";
import { HomePageExperience } from "@/features/product-shell/HomePageExperience";
import { ProductionCounterMapPage } from "@/features/countermap/ProductionCounterMapPage";
import { InterviewCompletionHandoff } from "@/features/interview-room/components/InterviewRoom";
import { ProductionSessionReportPage } from "@/features/interview-room/components/ProductionSessionReportPage";
import { ProductionMasteryPage } from "@/features/mastery/ProductionMasteryPage";
import type {
  CandidateInterviewHistoryResponse,
  CandidateMasteryOverviewResponse,
  CurrentUserResponse,
} from "@/lib/counterq-api";
import { CounterQApiClient } from "@/lib/counterq-api";

type ReportResponse = components["schemas"]["CandidateSessionReportResponse"];
type CounterMapResponse = components["schemas"]["CandidateCounterMapResponse"];

const userId = "9c000000-0000-4000-8000-000000000001";
const activeId = "9c000000-0000-4000-8000-000000000002";
const completedId = "9c000000-0000-4000-8000-000000000003";
const recommendationId = "9c000000-0000-4000-8000-000000000004";

function authState(overrides: Record<string, unknown> = {}) {
  return {
    getToken: vi.fn(async () => "stage9c-token"),
    isLoaded: true,
    isSignedIn: true,
    userId: "clerk-stage9c-user",
    ...overrides,
  };
}

function me(overrides: Partial<CurrentUserResponse> = {}): CurrentUserResponse {
  return {
    user_id: userId,
    account_status: "ACTIVE",
    onboarding_required: false,
    profile: {
      display_name: "Ada Candidate",
      preferred_language: "python",
      default_interview_mode: "SIMULATION",
      interview_level: "NEW_GRAD",
      target_role: "Backend engineer",
      timezone: "Asia/Kolkata",
      profile_version: 1,
      created_at: "2026-09-10T10:00:00Z",
      updated_at: "2026-09-10T10:00:00Z",
    },
    ...overrides,
  };
}

function history(items = [historyItem("ACTIVE"), historyItem("COMPLETED")]): CandidateInterviewHistoryResponse {
  return { items, limit: 50, offset: 0, has_more: false };
}

function historyItem(status: "ACTIVE" | "COMPLETED") {
  const id = status === "ACTIVE" ? activeId : completedId;
  return {
    interview_session_id: id,
    problem_title: status === "ACTIVE" ? "Merge Intervals" : "Two Sum",
    template: "STANDARD_CODING_INTERVIEW",
    mode: status === "ACTIVE" ? "SIMULATION" as const : "COACH" as const,
    language: status === "ACTIVE" ? "java" as const : "python" as const,
    candidate_level: "NEW_GRAD",
    status,
    display_status: status === "ACTIVE" ? "IN_PROGRESS" as const : "COMPLETED" as const,
    started_at: status === "ACTIVE" ? "2026-09-11T09:00:00Z" : "2026-09-10T09:00:00Z",
    completed_at: status === "COMPLETED" ? "2026-09-10T09:24:00Z" : null,
    deadline_at: "2026-09-11T09:30:00Z",
    configured_duration_seconds: 1800,
    can_resume: status === "ACTIVE",
    interview_path: `/interview/${id}`,
    report_path: `/interview/${id}/report`,
    countermap_path: `/interview/${id}/countermap`,
  };
}

function mastery(actionable = false): CandidateMasteryOverviewResponse {
  return {
    status: actionable ? "READY" : "EMPTY",
    user_id: userId,
    mastery_policy_version: "mastery_policy_v1",
    target_level: "NEW_GRAD",
    updated_at: "2026-09-11T08:00:00Z",
    message: actionable
      ? "What CounterQ has learned from your evidence across interviews."
      : "CounterQ is still learning where your interview strengths are.",
    technical_concepts: [],
    parent_summaries: [],
    interview_skills: [],
    retest_recommendations: actionable ? [{
      recommendation_id: recommendationId,
      target_type: "CONCEPT",
      target_id: "9c000000-0000-4000-8000-000000000005",
      target_name: "Hash table boundaries",
      status: "PENDING",
      reason: "Verify this gap independently in another context.",
      action_label: "CounterQ me again",
      action_enabled: true,
      availability_message: "Ready for a 10-minute Quick Drill.",
    }] : [],
  };
}

function preparingReport(): ReportResponse {
  return {
    status: "PREPARING",
    report_id: null,
    report_version: null,
    generated_at: null,
    message: "CounterQ is reviewing what you demonstrated.",
    session: {
      problem_title: "Two Sum",
      mode: "SIMULATION",
      language: "python",
      completed_at: "2026-09-10T09:24:00Z",
      duration_seconds: 1440,
    },
    report: null,
  };
}

function counterMap(): CounterMapResponse {
  return {
    status: "READY",
    session: {
      problem_title: "Two Sum",
      mode: "SIMULATION",
      language: "python",
      completed_at: "2026-09-10T09:24:00Z",
      duration_seconds: 1440,
    },
    projection_id: "9c000000-0000-4000-8000-000000000006",
    projection_version: 1,
    schema_version: "countermap.graph.v1",
    generated_at: "2026-09-10T09:25:00Z",
    graph: counterMapUiSamples[0],
    message: "Your evidence-backed reasoning map is ready.",
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function pathname(input: RequestInfo | URL): string {
  return new URL(String(input), "http://counterq.test").pathname;
}

function rejectOnAbort(signal: AbortSignal | null | undefined): Promise<Response> {
  if (!signal) return Promise.reject(new Error("Expected an AbortSignal"));
  return new Promise((_resolve, reject) => {
    const abort = () => reject(new DOMException("Request cancelled", "AbortError"));
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}

function productFetch({
  historyResponse = history(),
  masteryResponse = mastery(),
}: {
  historyResponse?: CandidateInterviewHistoryResponse;
  masteryResponse?: CandidateMasteryOverviewResponse;
} = {}) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const path = pathname(input);
    if (path === "/api/me") return json(me());
    if (path === "/api/interviews") return json(historyResponse);
    if (path === "/api/mastery/me") return json(masteryResponse);
    throw new Error(`Unexpected request: ${String(input)}`);
  });
}

beforeEach(() => {
  productMocks.pathname = "/";
  productMocks.push.mockReset();
  productMocks.replace.mockReset();
  productMocks.useAuth.mockReset();
  productMocks.useAuth.mockReturnValue(authState());
  window.sessionStorage.clear();
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("Stage 9C authenticated candidate product", () => {
  it("waits for Clerk hydration and gives signed-out candidates a concise handoff", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    productMocks.useAuth.mockReturnValue(authState({
      isLoaded: false,
      isSignedIn: undefined,
      userId: undefined,
    }));
    const { rerender } = render(<StrictMode><HomePageExperience /></StrictMode>);
    expect(screen.getByRole("status")).toHaveTextContent(/Confirming your CounterQ session/i);
    expect(fetch).not.toHaveBeenCalled();

    productMocks.useAuth.mockReturnValue(authState({ isSignedIn: false, userId: undefined }));
    rerender(<StrictMode><HomePageExperience /></StrictMode>);
    expect(screen.getByRole("heading", { name: /Practice the part after your answer/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Start practicing/i })).toHaveAttribute("href", "/sign-up");
    expect(screen.getAllByRole("link", { name: "Sign in" })).toHaveLength(2);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("redirects an authenticated candidate with no profile to onboarding", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(me({ onboarding_required: true, profile: null }))));
    render(<StrictMode><HomePageExperience /></StrictMode>);
    await waitFor(() => expect(productMocks.replace).toHaveBeenCalledWith("/onboarding"));
  });

  it("renders action-first Home without signed-out controls or fabricated learning data", async () => {
    vi.stubGlobal("fetch", productFetch());
    render(<HomePageExperience />);
    expect(await screen.findByRole("heading", { name: /Ready, Ada/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Start interview/i })).toHaveAttribute("href", "/interview/setup");
    expect(await screen.findByRole("link", { name: /Continue interview/i })).toHaveAttribute("href", `/interview/${activeId}`);
    expect(screen.getByRole("link", { name: "View Report" })).toHaveAttribute("href", `/interview/${completedId}/report`);
    expect(screen.getByRole("link", { name: /CounterMap/i })).toHaveAttribute("href", `/interview/${completedId}/countermap`);
    expect(screen.queryByRole("button", { name: "CounterQ me again" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Sign in" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Create account/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Candidate account menu" })).toBeInTheDocument();
  });

  it("shows only a real actionable Home retest and blocks duplicate launch", async () => {
    let resolveLaunch: (response: Response) => void = () => undefined;
    const launchResponse = new Promise<Response>((resolve) => { resolveLaunch = resolve; });
    const fetch = productFetch({ masteryResponse: mastery(true) });
    fetch.mockImplementation(async (input: RequestInfo | URL) => {
      const path = pathname(input);
      if (path === "/api/me") return json(me());
      if (path === "/api/interviews") return json(history());
      if (path === "/api/mastery/me") return json(mastery(true));
      if (path === `/api/retests/recommendations/${recommendationId}/start`) return launchResponse;
      throw new Error(`Unexpected request: ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetch);
    render(<HomePageExperience />);
    const action = await screen.findByRole("button", { name: "CounterQ me again" });
    fireEvent.click(action);
    fireEvent.click(action);
    await waitFor(() => expect(fetch.mock.calls.filter(([input]) => pathname(input) === `/api/retests/recommendations/${recommendationId}/start`)).toHaveLength(1));
    resolveLaunch(json({ interview_path: `/interview/${activeId}` }));
    await waitFor(() => expect(productMocks.push).toHaveBeenCalledWith(`/interview/${activeId}`));
  });

  it("requests backend History filters and exposes canonical session actions", async () => {
    productMocks.pathname = "/history";
    const fetch = productFetch();
    vi.stubGlobal("fetch", fetch);
    render(<HistoryPageExperience />);
    expect(await screen.findByRole("heading", { name: "Merge Intervals" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Continue interview" })).toHaveAttribute("href", `/interview/${activeId}`);
    expect(screen.getByRole("link", { name: "View Report" })).toHaveAttribute("href", `/interview/${completedId}/report`);
    fireEvent.click(screen.getByRole("button", { name: "Completed" }));
    await waitFor(() => expect(fetch.mock.calls.some(([input]) => String(input).includes("state=completed"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "In progress" }));
    await waitFor(() => expect(fetch.mock.calls.some(([input]) => String(input).includes("state=in_progress"))).toBe(true));
  });

  it("loads and saves only existing Account profile fields with managed user controls", async () => {
    productMocks.pathname = "/account";
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (pathname(input) === "/api/me" && !init?.method) return json(me());
      if (pathname(input) === "/api/me/profile" && init?.method === "PUT") return json(me());
      throw new Error(`Unexpected request: ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetch);
    render(<AccountPageExperience />);
    const displayName = await screen.findByLabelText(/Display name/i);
    fireEvent.change(displayName, { target: { value: "Ada Updated" } });
    fireEvent.click(screen.getByRole("button", { name: "Save preferences" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Preferences saved"));
    const saveCall = fetch.mock.calls.find(([input]) => pathname(input) === "/api/me/profile");
    expect(saveCall).toBeDefined();
    expect(JSON.parse(String(saveCall?.[1]?.body))).toMatchObject({
      display_name: "Ada Updated",
      preferred_language: "python",
      default_interview_mode: "SIMULATION",
    });
    expect(screen.getByRole("button", { name: "Candidate account menu" })).toBeInTheDocument();
    expect(screen.getByText("Not retained")).toBeInTheDocument();
  });

  it("loads production Mastery and launches a real route without development storage", async () => {
    productMocks.pathname = "/mastery";
    let resolveLaunch: (response: Response) => void = () => undefined;
    const pendingLaunch = new Promise<Response>((resolve) => { resolveLaunch = resolve; });
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const path = pathname(input);
      if (path === "/api/me") return json(me());
      if (path === "/api/mastery/me") return json(mastery(true));
      if (path === `/api/retests/recommendations/${recommendationId}/start`) return pendingLaunch;
      throw new Error(`Unexpected request: ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetch);
    render(<ProductionMasteryPage />);
    const action = await screen.findByRole("button", { name: "CounterQ me again" });
    fireEvent.click(action);
    fireEvent.click(action);
    await waitFor(() => expect(fetch.mock.calls.filter(([input]) => pathname(input) === `/api/retests/recommendations/${recommendationId}/start`)).toHaveLength(1));
    resolveLaunch(json({ interview_path: `/interview/${activeId}` }));
    await waitFor(() => expect(productMocks.push).toHaveBeenCalledWith(`/interview/${activeId}`));
    expect(sessionStorage.getItem("counterq:realtime-control:development-session-id")).toBeNull();
    expect(fetch.mock.calls.some(([input]) => pathname(input) === "/api/mastery/me")).toBe(true);
  });

  it("uses the authenticated production Report path under NODE_ENV development", async () => {
    vi.stubEnv("NODE_ENV", "development");
    productMocks.pathname = `/interview/${completedId}/report`;
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathname(input);
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer stage9c-token");
      if (path === "/api/me") return json(me());
      if (path === `/api/reports/sessions/${completedId}`) return json(preparingReport());
      throw new Error(`Unexpected request: ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetch);
    const rendered = render(<ProductionSessionReportPage interviewSessionId={completedId} />);
    expect(await screen.findByRole("heading", { name: /reviewing what you demonstrated/i })).toBeInTheDocument();
    expect(fetch.mock.calls.some(([input]) => String(input).includes("/api/reports/development/"))).toBe(false);
    rendered.unmount();
  });

  it("recovers the production Report request after Strict Mode cancels the first effect", async () => {
    vi.stubEnv("NODE_ENV", "development");
    productMocks.pathname = `/interview/${completedId}/report`;
    let reportRequests = 0;
    const reportPaths: string[] = [];
    const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathname(input);
      if (path === "/api/me") return Promise.resolve(json(me()));
      if (path === `/api/reports/sessions/${completedId}`) {
        reportPaths.push(path);
        reportRequests += 1;
        if (reportRequests === 1) return rejectOnAbort(init?.signal);
        return Promise.resolve(json(preparingReport()));
      }
      return Promise.reject(new Error(`Unexpected request: ${String(input)}`));
    });
    vi.stubGlobal("fetch", fetch);

    const rendered = render(
      <StrictMode>
        <ProductionSessionReportPage interviewSessionId={completedId} />
      </StrictMode>,
    );

    expect(await screen.findByRole("heading", {
      name: /reviewing what you demonstrated/i,
    })).toBeInTheDocument();
    await waitFor(() => expect(reportRequests).toBeGreaterThanOrEqual(2));
    expect(screen.queryByText(/temporarily out of reach/i)).not.toBeInTheDocument();
    expect(reportPaths.every((path) => path === `/api/reports/sessions/${completedId}`)).toBe(true);
    expect(reportPaths.some((path) => path.includes("/development/"))).toBe(false);
    rendered.unmount();
  });

  it("loads production CounterMap and node detail through the authenticated client", async () => {
    vi.stubEnv("NODE_ENV", "development");
    productMocks.pathname = `/interview/${completedId}/countermap`;
    const graph = counterMapUiSamples[0];
    const question = graph.nodes.find((node) => node.node_type === "QUESTION");
    if (!question) throw new Error("CounterMap sample must have a question");
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathname(input);
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer stage9c-token");
      if (path === "/api/me") return json(me());
      if (path === `/api/countermap/sessions/${completedId}`) return json(counterMap());
      if (path.endsWith(`/nodes/${question.node_id}`)) return json({
        node_id: question.node_id,
        node_type: question.node_type,
        title: question.title,
        summary: question.summary,
        stage: question.stage ?? null,
        source_status: "AVAILABLE",
      });
      throw new Error(`Unexpected request: ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetch);
    render(<ProductionCounterMapPage interviewSessionId={completedId} />);
    fireEvent.click(await screen.findByRole("button", { name: "Timeline" }));
    fireEvent.click(screen.getByRole("button", { name: /Inspect this moment: CounterQ asked/i }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(fetch.mock.calls.some(([input]) => String(input).includes(`/nodes/${question.node_id}`))).toBe(true);
    expect(fetch.mock.calls.some(([input]) => String(input).includes("/api/countermap/development/"))).toBe(false);
  });

  it("recovers the production CounterMap request after Strict Mode cancels the first effect", async () => {
    vi.stubEnv("NODE_ENV", "development");
    productMocks.pathname = `/interview/${completedId}/countermap`;
    let mapRequests = 0;
    const mapPaths: string[] = [];
    const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathname(input);
      if (path === "/api/me") return Promise.resolve(json(me()));
      if (path === `/api/countermap/sessions/${completedId}`) {
        mapPaths.push(path);
        mapRequests += 1;
        if (mapRequests === 1) return rejectOnAbort(init?.signal);
        return Promise.resolve(json(counterMap()));
      }
      return Promise.reject(new Error(`Unexpected request: ${String(input)}`));
    });
    vi.stubGlobal("fetch", fetch);

    render(
      <StrictMode>
        <ProductionCounterMapPage interviewSessionId={completedId} />
      </StrictMode>,
    );

    expect(await screen.findByRole("button", { name: "Timeline" })).toBeInTheDocument();
    await waitFor(() => expect(mapRequests).toBeGreaterThanOrEqual(2));
    expect(screen.queryByText(/unavailable for this interview/i)).not.toBeInTheDocument();
    expect(mapPaths.every((path) => path === `/api/countermap/sessions/${completedId}`)).toBe(true);
    expect(mapPaths.some((path) => path.includes("/development/"))).toBe(false);
  });

  it("does not let an aborted node A request fail or overwrite successful node B detail", async () => {
    productMocks.pathname = `/interview/${completedId}/countermap`;
    const graph = counterMapUiSamples[0];
    const nodeA = graph.nodes[0];
    const nodeB = graph.nodes.find((node) => node.node_type === "QUESTION");
    if (!nodeB) throw new Error("CounterMap sample must have a question");
    let nodeARequests = 0;
    let nodeAAborted = false;
    const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathname(input);
      if (path === "/api/me") return Promise.resolve(json(me()));
      if (path === `/api/countermap/sessions/${completedId}`) {
        return Promise.resolve(json(counterMap()));
      }
      if (path.endsWith(`/nodes/${nodeA.node_id}`)) {
        nodeARequests += 1;
        init?.signal?.addEventListener("abort", () => { nodeAAborted = true; }, { once: true });
        return rejectOnAbort(init?.signal);
      }
      if (path.endsWith(`/nodes/${nodeB.node_id}`)) {
        return Promise.resolve(json({
          node_id: nodeB.node_id,
          node_type: nodeB.node_type,
          title: nodeB.title,
          summary: nodeB.summary,
          stage: nodeB.stage ?? null,
          source_status: "AVAILABLE",
          statement: { text: "Node B detail loaded", exact_quote: true },
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${String(input)}`));
    });
    vi.stubGlobal("fetch", fetch);
    render(<ProductionCounterMapPage interviewSessionId={completedId} />);

    fireEvent.click(await screen.findByRole("button", { name: "Timeline" }));
    fireEvent.click(screen.getAllByRole("button", { name: /Inspect this moment: You said/i })[0]);
    await waitFor(() => expect(nodeARequests).toBe(1));
    fireEvent.click(screen.getByRole("button", { name: /View the question: CounterQ asked/i }));

    expect(await screen.findByText("Node B detail loaded")).toBeInTheDocument();
    expect(nodeAAborted).toBe(true);
    expect(screen.getByRole("dialog")).toHaveTextContent("CounterQ asked");
    expect(screen.queryByText(/This source could not be loaded/i)).not.toBeInTheDocument();
  });

  it("preserves native AbortError cancellation in the authenticated API client", async () => {
    const controller = new AbortController();
    const fetch = vi.fn(function (
      this: typeof globalThis,
      _input: RequestInfo | URL,
      init?: RequestInit,
    ) {
      expect(this).toBe(globalThis);
      return rejectOnAbort(init?.signal);
    });
    const api = new CounterQApiClient(
      async () => "fresh-token",
      "http://api.test",
      fetch as typeof globalThis.fetch,
    );
    const request = api.getSessionReport(completedId, controller.signal);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    controller.abort();

    await expect(request).rejects.toMatchObject({ name: "AbortError" });
  });

  it("keeps every production client method on current-user candidate routes", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toContain("http://api.test/api/");
      return json({});
    });
    const api = new CounterQApiClient(async () => "fresh-token", "http://api.test", fetch as typeof globalThis.fetch);
    await api.getSessionReport(completedId);
    await api.getCounterMap(completedId);
    await api.getCounterMapNodeDetail(completedId, "node/with spaces");
    await api.getMastery();
    await api.startRetest(recommendationId);
    const urls = fetch.mock.calls.map(([input]) => String(input));
    expect(urls).toEqual([
      `http://api.test/api/reports/sessions/${completedId}`,
      `http://api.test/api/countermap/sessions/${completedId}`,
      `http://api.test/api/countermap/sessions/${completedId}/nodes/node%2Fwith%20spaces`,
      "http://api.test/api/mastery/me",
      `http://api.test/api/retests/recommendations/${recommendationId}/start`,
    ]);
    expect(urls.every((url) => !url.includes("/development/"))).toBe(true);
  });

  it("hands a terminal production interview to Report and Home", () => {
    render(<InterviewCompletionHandoff interviewSessionId={completedId} reason="TIME_EXPIRED" />);
    expect(screen.getByRole("heading", { name: /Your evidence is being assembled/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Report" })).toHaveAttribute("href", `/interview/${completedId}/report`);
    expect(screen.getByRole("link", { name: "Return home" })).toHaveAttribute("href", "/");
  });

  it("keeps production components free of environment-selected development domains", () => {
    const sources = [
      "features/interview-room/components/ProductionSessionReportPage.tsx",
      "features/interview-room/components/SessionReportExperience.tsx",
      "features/countermap/ProductionCounterMapPage.tsx",
      "features/countermap/CounterMapExperience.tsx",
      "features/countermap/CounterMapDetailDrawer.tsx",
    ].map((path) => readFileSync(path, "utf8"));
    expect(sources.join("\n")).not.toContain("NODE_ENV");
    expect(sources.join("\n")).not.toContain("/development/");
  });
});
