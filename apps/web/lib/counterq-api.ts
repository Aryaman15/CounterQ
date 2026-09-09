import type { components } from "@counterq/contracts/openapi";

export type CurrentUserResponse = components["schemas"]["CurrentUserResponse"];
export type CandidateProfileUpdate = components["schemas"]["CandidateProfileUpdate"];

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
    } catch {
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
    } catch {
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
