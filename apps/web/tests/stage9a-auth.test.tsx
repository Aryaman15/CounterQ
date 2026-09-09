import { StrictMode } from "react";
import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CurrentUserResponse } from "@/lib/counterq-api";
import { CounterQApiClient, CounterQApiError } from "@/lib/counterq-api";

const clerkMocks = vi.hoisted(() => ({
  router: { replace: vi.fn() },
  useAuth: vi.fn(),
}));

vi.mock("@clerk/nextjs", () => ({
  ClerkProvider: ({ children }: { children: ReactNode }) => (
    <div data-testid="clerk-provider">{children}</div>
  ),
  SignIn: ({ forceRedirectUrl }: { forceRedirectUrl: string }) => (
    <div data-testid="clerk-sign-in" data-redirect={forceRedirectUrl} />
  ),
  SignUp: ({ forceRedirectUrl }: { forceRedirectUrl: string }) => (
    <div data-testid="clerk-sign-up" data-redirect={forceRedirectUrl} />
  ),
  useAuth: () => clerkMocks.useAuth(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => clerkMocks.router,
}));

import SignInPage from "@/app/sign-in/[[...sign-in]]/page";
import SignUpPage from "@/app/sign-up/[[...sign-up]]/page";
import { CounterQAuthProvider } from "@/features/auth/CounterQAuthProvider";
import { DevelopmentAuthCheck } from "@/features/auth/DevelopmentAuthCheck";
import {
  OnboardingExperience,
  OnboardingForm,
} from "@/features/auth/OnboardingExperience";

const userId = "01991b74-927a-7000-8000-000000000001";

function signedInAuth(
  getToken: () => Promise<string | null>,
  sessionId = "sess_test",
) {
  return {
    getToken,
    isLoaded: true,
    isSignedIn: true,
    sessionId,
    userId: "clerk_user_test",
  };
}

function currentUser(overrides: Partial<CurrentUserResponse> = {}): CurrentUserResponse {
  return {
    user_id: userId,
    account_status: "ACTIVE",
    onboarding_required: true,
    profile: null,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  clerkMocks.router.replace.mockReset();
  clerkMocks.useAuth.mockReset();
  clerkMocks.useAuth.mockReturnValue(
    signedInAuth(vi.fn(async () => "test-token")),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("Stage 9A authenticated frontend", () => {
  it("keeps the Next environment contract in the Next project root", () => {
    const webEnvironment = readFileSync(resolve(process.cwd(), ".env.example"), "utf8")
      .replaceAll("\r\n", "\n")
      .trimEnd();
    const repositoryEnvironment = readFileSync(
      resolve(process.cwd(), "../../.env.example"),
      "utf8",
    );
    const webPackage = JSON.parse(
      readFileSync(resolve(process.cwd(), "package.json"), "utf8"),
    ) as { scripts: { dev: string } };

    expect(webEnvironment).toBe([
      "NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000",
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=",
      "CLERK_SECRET_KEY=",
    ].join("\n"));
    expect(repositoryEnvironment).toMatch(/^COUNTERQ_AUTH_PROVIDER=clerk$/m);
    expect(repositoryEnvironment).toMatch(/^COUNTERQ_CLERK_ISSUER=$/m);
    expect(repositoryEnvironment).toMatch(/^COUNTERQ_CLERK_JWT_VERIFICATION_KEY=$/m);
    expect(repositoryEnvironment).toMatch(/^COUNTERQ_LOCAL_WEB_ORIGIN=http:\/\/localhost:3000$/m);
    expect(repositoryEnvironment).toMatch(
      /^COUNTERQ_ALLOWED_FRONTEND_ORIGINS=http:\/\/localhost:3000,http:\/\/127\.0\.0\.1:3000$/m,
    );
    expect(repositoryEnvironment).not.toMatch(/^NEXT_PUBLIC_API_BASE_URL=/m);
    expect(repositoryEnvironment).not.toMatch(/^NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=/m);
    expect(repositoryEnvironment).not.toMatch(/^CLERK_SECRET_KEY=/m);
    expect(webPackage.scripts.dev).toBe("next dev --hostname localhost --port 3000");
  });

  it("waits for Clerk hydration before loading the profile exactly once", async () => {
    const unreadyGetToken = vi.fn(async () => null);
    const readyGetToken = vi.fn(async () => "hydrated-session-token");
    let resolveProfile!: (response: Response) => void;
    const profileResponse = new Promise<Response>((resolveResponse) => {
      resolveProfile = resolveResponse;
    });
    const fetchFn = vi.fn(
      (input: RequestInfo | URL, init?: RequestInit) => {
        void input;
        void init;
        return profileResponse;
      },
    );
    vi.stubGlobal("fetch", fetchFn);
    clerkMocks.useAuth.mockReturnValue({
      getToken: unreadyGetToken,
      isLoaded: false,
      isSignedIn: undefined,
      sessionId: undefined,
      userId: undefined,
    });

    const { rerender } = render(
      <StrictMode><OnboardingExperience /></StrictMode>,
    );

    expect(screen.getByRole("status")).toHaveTextContent(/Connecting your signed-in workspace/i);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchFn).not.toHaveBeenCalled();
    expect(unreadyGetToken).not.toHaveBeenCalled();

    clerkMocks.useAuth.mockReturnValue(signedInAuth(readyGetToken));
    rerender(<StrictMode><OnboardingExperience /></StrictMode>);

    await waitFor(() => expect(fetchFn).toHaveBeenCalledOnce());
    expect(readyGetToken).toHaveBeenCalledOnce();
    expect(unreadyGetToken).not.toHaveBeenCalled();
    const [requestUrl, requestInit] = fetchFn.mock.calls[0];
    expect(requestUrl).toBe("http://127.0.0.1:8000/api/me");
    expect(new Headers(requestInit?.headers).get("Authorization"))
      .toBe("Bearer hydrated-session-token");
    expect(screen.getByRole("status")).toHaveTextContent(/Loading your profile/i);

    resolveProfile(jsonResponse(currentUser()));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Save interview preferences/i })).toBeEnabled();
    });
    expect(screen.getByLabelText(/Interview level/i)).toBeInTheDocument();
    expect(fetchFn).toHaveBeenCalledOnce();
  });

  it("shows a safe sign-in handoff without contacting CounterQ when signed out", () => {
    const getToken = vi.fn(async () => null);
    const fetchFn = vi.fn();
    vi.stubGlobal("fetch", fetchFn);
    clerkMocks.useAuth.mockReturnValue({
      getToken,
      isLoaded: true,
      isSignedIn: false,
      sessionId: null,
      userId: null,
    });

    render(<OnboardingExperience />);

    expect(screen.getByRole("link", { name: /Continue to sign in/i }))
      .toHaveAttribute("href", "/sign-in");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(getToken).not.toHaveBeenCalled();
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("shows profile failure only after an authenticated CounterQ request fails", async () => {
    const getToken = vi.fn(async () => "authenticated-token");
    const fetchFn = vi.fn(async () => new Response(null, { status: 503 }));
    vi.stubGlobal("fetch", fetchFn);
    clerkMocks.useAuth.mockReturnValue(signedInAuth(getToken));

    render(<OnboardingExperience />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not load your profile.*connection/i,
    );
    expect(getToken).toHaveBeenCalledOnce();
    expect(fetchFn).toHaveBeenCalledOnce();
  });

  it("uses the latest Clerk getToken provider when retrying profile load", async () => {
    const providerDetail = "Clerk provider failed with internal session detail";
    const staleGetToken = vi.fn(async () => {
      throw new Error(providerDetail);
    });
    const currentGetToken = vi.fn(async () => "replacement-session-token");
    const fetchFn = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("Authorization"))
        .toBe("Bearer replacement-session-token");
      return jsonResponse(currentUser());
    });
    vi.stubGlobal("fetch", fetchFn);
    clerkMocks.useAuth.mockReturnValue(signedInAuth(staleGetToken));

    const { rerender } = render(<OnboardingExperience />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not confirm your sign-in/i);
    expect(alert).not.toHaveTextContent(/check your connection/i);
    expect(alert).not.toHaveTextContent(providerDetail);
    expect(fetchFn).not.toHaveBeenCalled();
    const submitButton = screen.getByRole("button", {
      name: /Save interview preferences/i,
    });
    expect(submitButton).toBeDisabled();

    clerkMocks.useAuth.mockReturnValue(signedInAuth(currentGetToken));
    rerender(<OnboardingExperience />);

    expect(fetchFn).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Retry profile load/i }));

    await waitFor(() => expect(fetchFn).toHaveBeenCalledOnce());
    expect(staleGetToken).toHaveBeenCalledOnce();
    expect(currentGetToken).toHaveBeenCalledOnce();
    expect(fetchFn.mock.calls[0]?.[0]).toBe("http://127.0.0.1:8000/api/me");
    await waitFor(() => expect(submitButton).toBeEnabled());
    expect(screen.getByRole("button", { name: /Save interview preferences/i }))
      .toBe(submitButton);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("loads then saves a user-id-free profile through the authenticated lifecycle", async () => {
    const profileGetToken = vi.fn(async () => "profile-load-token");
    const saveGetToken = vi.fn(async () => "profile-save-token");
    const responses = [
      jsonResponse(currentUser()),
      jsonResponse(currentUser({ onboarding_required: false })),
    ];
    const fetchFn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input;
      void init;
      const response = responses.shift();
      if (!response) throw new Error("Unexpected CounterQ request");
      return response;
    });
    vi.stubGlobal("fetch", fetchFn);
    clerkMocks.useAuth.mockReturnValue(signedInAuth(profileGetToken));

    const { rerender } = render(<OnboardingExperience />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Save interview preferences/i })).toBeEnabled();
    });
    clerkMocks.useAuth.mockReturnValue(signedInAuth(saveGetToken));
    rerender(<OnboardingExperience />);
    fireEvent.change(screen.getByLabelText(/Display name/i), {
      target: { value: "Ada" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save interview preferences/i }));

    await waitFor(() => expect(clerkMocks.router.replace).toHaveBeenCalledOnce());
    expect(clerkMocks.router.replace).toHaveBeenCalledWith("/?profile=ready");
    expect(fetchFn).toHaveBeenCalledTimes(2);
    expect(profileGetToken).toHaveBeenCalledOnce();
    expect(saveGetToken).toHaveBeenCalledOnce();
    const [requestUrl, requestInit] = fetchFn.mock.calls[1];
    const requestBody = JSON.parse(String(requestInit?.body));
    expect(requestUrl).toBe("http://127.0.0.1:8000/api/me/profile");
    expect(requestInit?.method).toBe("PUT");
    expect(new Headers(requestInit?.headers).get("Authorization"))
      .toBe("Bearer profile-save-token");
    expect(requestBody).toMatchObject({ display_name: "Ada" });
    expect(requestBody).not.toHaveProperty("user_id");
  });

  it("does not describe an authentication save failure as invalid selections", async () => {
    const api = {
      getMe: vi.fn(async () => currentUser()),
      saveProfile: vi.fn(async () => {
        throw new CounterQApiError("AUTHENTICATION_REQUIRED", 401);
      }),
    } as unknown as CounterQApiClient;

    render(<OnboardingForm api={api} onComplete={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button")).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /Save interview preferences/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/sign-in expired/i);
    expect(alert).not.toHaveTextContent(/Check the selections/i);
  });

  it("retries a failed fetch with a fresh token and clears the error", async () => {
    const getToken = vi.fn(async () => "retry-session-token");
    const fetchFn = vi.fn()
      .mockRejectedValueOnce(new Error("browser fetch detail"))
      .mockResolvedValueOnce(jsonResponse(currentUser()));
    vi.stubGlobal("fetch", fetchFn);
    clerkMocks.useAuth.mockReturnValue(signedInAuth(getToken));

    render(<OnboardingExperience />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not load your profile.*connection/i,
    );
    fireEvent.click(screen.getByRole("button", { name: /Retry profile load/i }));

    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(2));
    expect(getToken).toHaveBeenCalledTimes(2);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Save interview preferences/i }))
        .toBeEnabled();
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("wraps the application and presents managed sign-in and sign-up surfaces", () => {
    const { rerender } = render(
      <CounterQAuthProvider><span>private workspace</span></CounterQAuthProvider>,
    );
    expect(screen.getByTestId("clerk-provider")).toHaveTextContent("private workspace");

    rerender(<SignInPage />);
    expect(screen.getByTestId("clerk-sign-in")).toHaveAttribute("data-redirect", "/onboarding");
    rerender(<SignUpPage />);
    expect(screen.getByTestId("clerk-sign-up")).toHaveAttribute("data-redirect", "/onboarding");
  });

  it("attaches the Clerk bearer token without accepting a browser-selected user id", async () => {
    const fetchFn = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer signed-session-jwt");
      expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
      expect(JSON.parse(String(init?.body))).not.toHaveProperty("user_id");
      return new Response(JSON.stringify(currentUser()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    const api = new CounterQApiClient(
      async () => "signed-session-jwt",
      "https://api.counterq.example/",
      fetchFn as typeof fetch,
    );

    await api.saveProfile({
      display_name: null,
      preferred_language: "python",
      default_interview_mode: "SIMULATION",
      interview_level: "NEW_GRAD",
      target_role: null,
      timezone: "Asia/Calcutta",
    });
    expect(fetchFn).toHaveBeenCalledWith(
      "https://api.counterq.example/api/me/profile",
      expect.objectContaining({ method: "PUT" }),
    );

    await expect(api.request("/api/me/profile", {
      method: "PUT",
      body: JSON.stringify({ user_id: "someone-else" }),
    })).rejects.toThrow("CounterQ user identity cannot be supplied by the browser");
  });

  it("invokes browser fetch with the global receiver", async () => {
    const fetchFn = vi.fn(function (
      this: unknown,
      _input: RequestInfo | URL,
      _init?: RequestInit,
    ) {
      void _input;
      void _init;
      expect(this).toBe(globalThis);
      return Promise.resolve(jsonResponse(currentUser()));
    });
    const api = new CounterQApiClient(
      async () => "receiver-safe-token",
      "https://api.counterq.example",
      fetchFn as typeof fetch,
    );

    await api.getMe();

    expect(fetchFn).toHaveBeenCalledOnce();
  });

  it("normalizes auth failures without leaking bearer material", async () => {
    const secret = "secret-session-token-that-must-not-leak";
    const providerDetail = "ClerkOfflineError with private provider detail";
    const fetchAfterProviderFailure = vi.fn();
    const unauthorized = new CounterQApiClient(
      async () => secret,
      "https://api.counterq.example",
      (async () => new Response("provider details", { status: 401 })) as typeof fetch,
    );
    const missing = new CounterQApiClient(async () => null);
    const providerFailure = new CounterQApiClient(
      async () => {
        throw new Error(providerDetail);
      },
      "https://api.counterq.example",
      fetchAfterProviderFailure as typeof fetch,
    );

    const first = await unauthorized.getMe().catch((error: unknown) => error);
    const second = await missing.getMe().catch((error: unknown) => error);
    const third = await providerFailure.getMe().catch((error: unknown) => error);

    expect(first).toBeInstanceOf(CounterQApiError);
    expect(first).toMatchObject({ category: "AUTHENTICATION_REQUIRED", status: 401 });
    expect(String(first)).not.toContain(secret);
    expect(second).toMatchObject({ category: "AUTHENTICATION_REQUIRED", status: 401 });
    expect(third).toBeInstanceOf(CounterQApiError);
    expect(third).toMatchObject({ category: "AUTHENTICATION_REQUIRED", status: 401 });
    expect(String(third)).not.toContain(providerDetail);
    expect(fetchAfterProviderFailure).not.toHaveBeenCalled();
  });

  it("normalizes thrown fetch failures and emits only safe development stages", async () => {
    const token = "diagnostic-token-that-must-not-be-logged";
    const fetchDetail = "browser network internals that must not be logged";
    vi.stubEnv("NODE_ENV", "development");
    const info = vi.spyOn(console, "info").mockImplementation(() => undefined);
    const api = new CounterQApiClient(
      async () => token,
      "https://api.counterq.example",
      (async () => {
        throw new Error(fetchDetail);
      }) as typeof fetch,
    );

    const failure = await api.getMe().catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(CounterQApiError);
    expect(failure).toMatchObject({
      category: "REQUEST_FAILED",
      status: 0,
      stage: "FETCH",
    });
    const developmentMessages = info.mock.calls.map(([message]) => String(message));
    expect(developmentMessages).toEqual([
      "[CounterQ auth] profile load started",
      "[CounterQ auth] Clerk token acquired",
      "[CounterQ auth] API fetch started",
      "[CounterQ auth] fetch failed",
    ]);
    expect(JSON.stringify(info.mock.calls)).not.toContain(token);
    expect(JSON.stringify(info.mock.calls)).not.toContain(fetchDetail);

    info.mockClear();
    const successApi = new CounterQApiClient(
      async () => token,
      "https://api.counterq.example",
      (async () => jsonResponse(currentUser())) as typeof fetch,
    );
    await successApi.getMe();
    expect(info.mock.calls.map(([message]) => String(message))).toEqual([
      "[CounterQ auth] profile load started",
      "[CounterQ auth] Clerk token acquired",
      "[CounterQ auth] API fetch started",
      "[CounterQ auth] API response 200",
    ]);

    info.mockClear();
    const providerDetail = "private Clerk provider failure";
    const tokenFailureApi = new CounterQApiClient(async () => {
      throw new Error(providerDetail);
    });
    await tokenFailureApi.getMe().catch(() => undefined);
    expect(info.mock.calls.map(([message]) => String(message))).toEqual([
      "[CounterQ auth] profile load started",
      "[CounterQ auth] token acquisition failed",
    ]);
    expect(JSON.stringify(info.mock.calls)).not.toContain(providerDetail);

    info.mockClear();
    vi.stubEnv("NODE_ENV", "production");
    const productionApi = new CounterQApiClient(
      async () => "production-token",
      "https://api.counterq.example",
      (async () => jsonResponse(currentUser())) as typeof fetch,
    );
    await productionApi.getMe();
    expect(info).not.toHaveBeenCalled();
  });

  it("runs the development auth check through the production API client path", async () => {
    const diagnosticToken = "safe-surface-token";
    const getToken = vi.fn(async () => diagnosticToken);
    const fetchFn = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      void _input;
      void _init;
      return jsonResponse(currentUser());
    });
    vi.stubGlobal("fetch", fetchFn);
    clerkMocks.useAuth.mockReturnValue(signedInAuth(getToken));

    render(<DevelopmentAuthCheck />);

    const tokenResult = screen.getByText("Token obtainable").parentElement;
    const apiResult = screen.getByText("CounterQ /api/me").parentElement;
    await waitFor(() => expect(tokenResult).toHaveTextContent(/yes/i));
    await waitFor(() => expect(apiResult).toHaveTextContent(/success.*HTTP 200/i));
    expect(fetchFn).toHaveBeenCalledOnce();
    expect(fetchFn.mock.calls[0]?.[0]).toBe("http://127.0.0.1:8000/api/me");
    expect(new Headers(fetchFn.mock.calls[0]?.[1]?.headers).get("Authorization"))
      .toBe(`Bearer ${diagnosticToken}`);
    expect(document.body).not.toHaveTextContent(diagnosticToken);
    expect(document.body).not.toHaveTextContent(userId);

    fireEvent.click(screen.getByRole("button", { name: /Run auth check again/i }));
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(2));
    expect(getToken).toHaveBeenCalledTimes(2);
  });

  it("collects only interview calibration preferences and never a mastery self-rating", async () => {
    const saveProfile = vi.fn(
      async (profile: Parameters<CounterQApiClient["saveProfile"]>[0]) => {
        void profile;
        return currentUser({ onboarding_required: false });
      },
    );
    const api = {
      getMe: vi.fn(async () => currentUser()),
      saveProfile,
    } as unknown as CounterQApiClient;
    const onComplete = vi.fn();
    render(<OnboardingForm api={api} onComplete={onComplete} />);

    await waitFor(() => expect(screen.getByRole("button")).toBeEnabled());
    fireEvent.change(screen.getByLabelText(/Interview level/i), {
      target: { value: "EARLY_CAREER" },
    });
    fireEvent.change(screen.getByLabelText(/Preferred coding language/i), {
      target: { value: "cpp" },
    });
    fireEvent.change(screen.getByLabelText(/Default interview mode/i), {
      target: { value: "COACH" },
    });
    fireEvent.change(screen.getByLabelText(/Display name/i), {
      target: { value: "Ada" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save interview preferences/i }));

    await waitFor(() => expect(saveProfile).toHaveBeenCalledOnce());
    expect(saveProfile).toHaveBeenCalledWith(expect.objectContaining({
      display_name: "Ada",
      preferred_language: "cpp",
      default_interview_mode: "COACH",
      interview_level: "EARLY_CAREER",
    }));
    expect(saveProfile.mock.calls[0]?.[0]).not.toHaveProperty("user_id");
    expect(screen.queryByText(/rate your mastery/i)).not.toBeInTheDocument();
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it("skips the form when the server says onboarding is complete", async () => {
    const onComplete = vi.fn();
    const api = {
      getMe: vi.fn(async () => currentUser({
        onboarding_required: false,
        profile: {
          display_name: null,
          preferred_language: "java",
          default_interview_mode: "SIMULATION",
          interview_level: "INTERN",
          target_role: null,
          timezone: null,
          profile_version: 1,
          created_at: "2026-09-08T00:00:00Z",
          updated_at: "2026-09-08T00:00:00Z",
        },
      })),
      saveProfile: vi.fn(),
    } as unknown as CounterQApiClient;

    render(<OnboardingForm api={api} onComplete={onComplete} />);

    await waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
    expect(api.saveProfile).not.toHaveBeenCalled();
  });

  it("protects candidate routes in middleware while preserving local demo isolation", () => {
    const middleware = readFileSync("middleware.ts", "utf8");
    const developmentAuthPage = readFileSync(
      "app/development/auth-check/page.tsx",
      "utf8",
    );

    expect(middleware).toContain("await auth.protect()");
    expect(middleware).toContain('process.env.NODE_ENV !== "production"');
    expect(middleware).toContain('pathname === "/interview/demo"');
    expect(middleware).toContain('pathname === "/development/auth-check"');
    expect(middleware).toContain('pathname.startsWith("/sign-in")');
    expect(middleware).toContain('new Response("Not Found", { status: 404 })');
    expect(developmentAuthPage).toContain('process.env.NODE_ENV === "production"');
    expect(developmentAuthPage).toContain("notFound()");
  });
});
