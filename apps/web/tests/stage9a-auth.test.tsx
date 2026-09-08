import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

import type { CurrentUserResponse } from "@/lib/counterq-api";
import { CounterQApiClient, CounterQApiError } from "@/lib/counterq-api";

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
  useAuth: () => ({ getToken: async () => "test-token" }),
}));

import SignInPage from "@/app/sign-in/[[...sign-in]]/page";
import SignUpPage from "@/app/sign-up/[[...sign-up]]/page";
import { CounterQAuthProvider } from "@/features/auth/CounterQAuthProvider";
import { OnboardingForm } from "@/features/auth/OnboardingExperience";

const userId = "01991b74-927a-7000-8000-000000000001";

function currentUser(overrides: Partial<CurrentUserResponse> = {}): CurrentUserResponse {
  return {
    user_id: userId,
    account_status: "ACTIVE",
    onboarding_required: true,
    profile: null,
    ...overrides,
  };
}

describe("Stage 9A authenticated frontend", () => {
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

  it("normalizes auth failures without leaking bearer material", async () => {
    const secret = "secret-session-token-that-must-not-leak";
    const unauthorized = new CounterQApiClient(
      async () => secret,
      "https://api.counterq.example",
      (async () => new Response("provider details", { status: 401 })) as typeof fetch,
    );
    const missing = new CounterQApiClient(async () => null);

    const first = await unauthorized.getMe().catch((error: unknown) => error);
    const second = await missing.getMe().catch((error: unknown) => error);

    expect(first).toBeInstanceOf(CounterQApiError);
    expect(first).toMatchObject({ category: "AUTHENTICATION_REQUIRED", status: 401 });
    expect(String(first)).not.toContain(secret);
    expect(second).toMatchObject({ category: "AUTHENTICATION_REQUIRED", status: 401 });
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

    expect(middleware).toContain("await auth.protect()");
    expect(middleware).toContain('process.env.NODE_ENV !== "production"');
    expect(middleware).toContain('pathname === "/interview/demo"');
    expect(middleware).toContain('pathname.startsWith("/sign-in")');
  });
});
