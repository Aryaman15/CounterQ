import type { components } from "@counterq/contracts/openapi";

export type CurrentUserResponse = components["schemas"]["CurrentUserResponse"];
export type CandidateProfileUpdate = components["schemas"]["CandidateProfileUpdate"];
export type CuratedCatalogItem = components["schemas"]["CuratedCatalogItem"];
export type CreateInterviewRequest = components["schemas"]["CreateInterviewRequest"];
export type CreateInterviewResponse = components["schemas"]["CreateInterviewResponse"];
export type InterviewBootstrapResponse = components["schemas"]["InterviewBootstrapResponse"];
export type InterviewBootstrap = Omit<
  InterviewBootstrapResponse,
  "latest_code_snapshot" | "unresolved_prompt"
> & Required<Pick<InterviewBootstrapResponse, "latest_code_snapshot" | "unresolved_prompt">>;
export type RealtimeSessionResponse = components["schemas"]["CreateRealtimeSessionResponse"];
export type RealtimeControlTicketResponse = components["schemas"]["RealtimeControlTicketResponse"];
export type CandidateRunRequest = components["schemas"]["CandidateRunRequest"];
export type ExecutionRunResponse = components["schemas"]["DevelopmentRunResponse"];
export type CandidateAssistanceResponse = components["schemas"]["CandidateAssistanceResponse"];
export type CandidateInterviewHistoryResponse = components["schemas"]["CandidateInterviewHistoryResponse"];
export type CandidateSessionReportResponse = components["schemas"]["CandidateSessionReportResponse"];
export type CandidateCounterMapResponse = components["schemas"]["CandidateCounterMapResponse"];
export type CandidateCounterMapNodeDetailResponse = components["schemas"]["CandidateCounterMapNodeDetailResponse"];
export type CandidateMasteryOverviewResponse = components["schemas"]["CandidateMasteryOverviewResponse"];
export type RetestLaunchResponse = components["schemas"]["RetestLaunchResponse"];

type GetToken = () => Promise<string | null>;
type Fetch = typeof fetch;

export type CounterQApiErrorStage =
  | "TOKEN_ACQUISITION"
  | "FETCH"
  | "HTTP_RESPONSE"
  | "RESPONSE_BODY";

export class CounterQApiError extends Error {
  constructor(
    readonly category: "AUTHENTICATION_REQUIRED" | "ACCESS_DENIED" | "REQUEST_FAILED",
    readonly status: number,
    readonly stage: CounterQApiErrorStage = "HTTP_RESPONSE",
  ) {
    super(category === "REQUEST_FAILED" ? "CounterQ request failed" : category);
    this.name = "CounterQApiError";
  }
}

export function isAbortError(error: unknown): boolean {
  return (
    typeof DOMException !== "undefined"
    && error instanceof DOMException
    && error.name === "AbortError"
  );
}

export class CounterQApiClient {
  private readonly baseUrl: string;

  constructor(
    private readonly getToken: GetToken,
    baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000",
    private readonly fetchFn: Fetch = fetch,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async getMe(): Promise<CurrentUserResponse> {
    developmentAuthDiagnostic("profile load started");
    return this.request<CurrentUserResponse>("/api/me");
  }

  async saveProfile(profile: CandidateProfileUpdate): Promise<CurrentUserResponse> {
    return this.request<CurrentUserResponse>("/api/me/profile", {
      method: "PUT",
      body: JSON.stringify(profile),
    });
  }

  async getCuratedCatalog(): Promise<CuratedCatalogItem[]> {
    return this.request<CuratedCatalogItem[]>("/api/problems/curated");
  }

  async createInterview(input: CreateInterviewRequest): Promise<CreateInterviewResponse> {
    return this.request<CreateInterviewResponse>("/api/interviews", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async listInterviews(
    state: "all" | "in_progress" | "completed" = "all",
    limit = 20,
    offset = 0,
    signal?: AbortSignal,
  ): Promise<CandidateInterviewHistoryResponse> {
    const query = new URLSearchParams({
      state,
      limit: String(limit),
      offset: String(offset),
    });
    return this.request<CandidateInterviewHistoryResponse>(
      `/api/interviews?${query.toString()}`,
      { signal, cache: "no-store" },
    );
  }

  async getSessionReport(
    interviewSessionId: string,
    signal?: AbortSignal,
  ): Promise<CandidateSessionReportResponse> {
    return this.request<CandidateSessionReportResponse>(
      `/api/reports/sessions/${encodeURIComponent(interviewSessionId)}`,
      { signal, cache: "no-store" },
    );
  }

  async getCounterMap(
    interviewSessionId: string,
    signal?: AbortSignal,
  ): Promise<CandidateCounterMapResponse> {
    return this.request<CandidateCounterMapResponse>(
      `/api/countermap/sessions/${encodeURIComponent(interviewSessionId)}`,
      { signal, cache: "no-store" },
    );
  }

  async getCounterMapNodeDetail(
    interviewSessionId: string,
    nodeId: string,
    signal?: AbortSignal,
  ): Promise<CandidateCounterMapNodeDetailResponse> {
    return this.request<CandidateCounterMapNodeDetailResponse>(
      `/api/countermap/sessions/${encodeURIComponent(interviewSessionId)}`
      + `/nodes/${encodeURIComponent(nodeId)}`,
      { signal, cache: "no-store" },
    );
  }

  async getMastery(signal?: AbortSignal): Promise<CandidateMasteryOverviewResponse> {
    return this.request<CandidateMasteryOverviewResponse>(
      "/api/mastery/me",
      { signal, cache: "no-store" },
    );
  }

  async startRetest(recommendationId: string): Promise<RetestLaunchResponse> {
    return this.request<RetestLaunchResponse>(
      `/api/retests/recommendations/${encodeURIComponent(recommendationId)}/start`,
      { method: "POST" },
    );
  }

  async restoreInterview(
    interviewSessionId: string,
    clientInstanceId: string,
  ): Promise<InterviewBootstrap> {
    const restored = await this.request<InterviewBootstrapResponse>(
      `/api/interviews/${interviewSessionId}/restore`,
      {
        method: "POST",
        body: JSON.stringify({ client_instance_id: clientInstanceId }),
      },
    );
    return {
      ...restored,
      latest_code_snapshot: restored.latest_code_snapshot ?? null,
      unresolved_prompt: restored.unresolved_prompt ?? null,
    };
  }

  async createRealtimeSession(interviewSessionId: string): Promise<RealtimeSessionResponse> {
    return this.request<RealtimeSessionResponse>(
      `/api/realtime/interviews/${interviewSessionId}/session`,
      { method: "POST" },
    );
  }

  async createRealtimeControlTicket(
    interviewSessionId: string,
  ): Promise<RealtimeControlTicketResponse> {
    return this.request<RealtimeControlTicketResponse>(
      `/api/realtime/interviews/${interviewSessionId}/control-ticket`,
      { method: "POST" },
    );
  }

  async runInterviewCode(
    interviewSessionId: string,
    input: CandidateRunRequest,
  ): Promise<ExecutionRunResponse> {
    return this.request<ExecutionRunResponse>(
      `/api/execution/interviews/${interviewSessionId}/runs`,
      { method: "POST", body: JSON.stringify(input) },
    );
  }

  async requestAssistance(interviewSessionId: string): Promise<CandidateAssistanceResponse> {
    const idempotencyKey = globalThis.crypto?.randomUUID?.() ?? `hint-${Date.now()}`;
    return this.request<CandidateAssistanceResponse>(
      `/api/interviews/${interviewSessionId}/assistance-requests`,
      {
        method: "POST",
        body: JSON.stringify({ idempotency_key: idempotencyKey }),
      },
    );
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    assertNoClientSelectedUserId(init.body);
    let token: string | null;
    try {
      token = await this.getToken();
    } catch {
      developmentAuthDiagnostic("token acquisition failed");
      throw new CounterQApiError("AUTHENTICATION_REQUIRED", 401, "TOKEN_ACQUISITION");
    }
    if (!token) {
      developmentAuthDiagnostic("token acquisition failed");
      throw new CounterQApiError("AUTHENTICATION_REQUIRED", 401, "TOKEN_ACQUISITION");
    }
    developmentAuthDiagnostic("Clerk token acquired");
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${token}`);
    if (init.body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    developmentAuthDiagnostic("API fetch started");
    let response: Response;
    try {
      response = await this.fetchFn.call(
        globalThis,
        `${this.baseUrl}${path}`,
        { ...init, headers },
      );
    } catch (error) {
      if (init.signal?.aborted || isAbortError(error)) throw error;
      developmentAuthDiagnostic("fetch failed");
      throw new CounterQApiError("REQUEST_FAILED", 0, "FETCH");
    }
    developmentAuthDiagnostic(`API response ${response.status}`);
    if (!response.ok) {
      const category = response.status === 401
        ? "AUTHENTICATION_REQUIRED"
        : response.status === 403
          ? "ACCESS_DENIED"
          : "REQUEST_FAILED";
      throw new CounterQApiError(category, response.status, "HTTP_RESPONSE");
    }
    try {
      return await response.json() as T;
    } catch (error) {
      if (init.signal?.aborted || isAbortError(error)) throw error;
      developmentAuthDiagnostic("API response body invalid");
      throw new CounterQApiError("REQUEST_FAILED", response.status, "RESPONSE_BODY");
    }
  }
}

function developmentAuthDiagnostic(stage: string): void {
  if (process.env.NODE_ENV === "development") {
    console.info(`[CounterQ auth] ${stage}`);
  }
}

function assertNoClientSelectedUserId(body: BodyInit | null | undefined): void {
  if (typeof body !== "string") return;
  try {
    const value: unknown = JSON.parse(body);
    if (value && typeof value === "object" && "user_id" in value) {
      throw new Error("CounterQ user identity cannot be supplied by the browser");
    }
  } catch (error) {
    if (error instanceof SyntaxError) return;
    throw error;
  }
}
