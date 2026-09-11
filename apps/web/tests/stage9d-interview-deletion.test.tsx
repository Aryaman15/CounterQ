import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  useAuth: vi.fn(),
}));

vi.mock("@clerk/nextjs", () => ({
  UserButton: () => <button type="button">Candidate account menu</button>,
  useAuth: () => mocks.useAuth(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/history",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

import { HistoryPageExperience } from "@/features/product-shell/HistoryPageExperience";

const activeId = "9d000000-0000-4000-8000-000000000001";
const completedId = "9d000000-0000-4000-8000-000000000002";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function pathname(input: RequestInfo | URL): string {
  return new URL(String(input), "http://counterq.test").pathname;
}

function history() {
  return {
    items: [
      {
        interview_session_id: activeId,
        problem_title: "Merge Intervals",
        template: "STANDARD_CODING_INTERVIEW",
        mode: "SIMULATION",
        language: "java",
        candidate_level: "NEW_GRAD",
        status: "ACTIVE",
        display_status: "IN_PROGRESS",
        started_at: "2026-09-11T09:00:00Z",
        completed_at: null,
        deadline_at: "2026-09-11T09:30:00Z",
        configured_duration_seconds: 1800,
        can_resume: true,
        interview_path: `/interview/${activeId}`,
        report_path: `/interview/${activeId}/report`,
        countermap_path: `/interview/${activeId}/countermap`,
      },
      {
        interview_session_id: completedId,
        problem_title: "Two Sum",
        template: "STANDARD_CODING_INTERVIEW",
        mode: "COACH",
        language: "python",
        candidate_level: "NEW_GRAD",
        status: "COMPLETED",
        display_status: "COMPLETED",
        started_at: "2026-09-10T09:00:00Z",
        completed_at: "2026-09-10T09:24:00Z",
        deadline_at: "2026-09-10T09:30:00Z",
        configured_duration_seconds: 1800,
        can_resume: false,
        interview_path: `/interview/${completedId}`,
        report_path: `/interview/${completedId}/report`,
        countermap_path: `/interview/${completedId}/countermap`,
      },
    ],
    limit: 50,
    offset: 0,
    has_more: false,
  };
}

function currentUser() {
  return {
    user_id: "9d000000-0000-4000-8000-000000000003",
    account_status: "ACTIVE",
    onboarding_required: false,
    profile: {
      display_name: "Ada Candidate",
      preferred_language: "python",
      default_interview_mode: "SIMULATION",
      interview_level: "NEW_GRAD",
      target_role: null,
      timezone: "Asia/Kolkata",
      profile_version: 1,
      created_at: "2026-09-10T10:00:00Z",
      updated_at: "2026-09-10T10:00:00Z",
    },
  };
}

beforeEach(() => {
  mocks.useAuth.mockReset();
  mocks.useAuth.mockReturnValue({
    getToken: vi.fn(async () => "stage9d-token"),
    isLoaded: true,
    isSignedIn: true,
    userId: "clerk-stage9d-user",
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Stage 9D interview deletion", () => {
  it("requires explicit confirmation, blocks duplicate submission, and removes only the row", async () => {
    let resolveDeletion: (response: Response) => void = () => undefined;
    const pendingDeletion = new Promise<Response>((resolve) => { resolveDeletion = resolve; });
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathname(input);
      if (path === "/api/me") return json(currentUser());
      if (path === "/api/interviews" && !init?.method) return json(history());
      if (path === `/api/interviews/${completedId}` && init?.method === "DELETE") {
        return pendingDeletion;
      }
      throw new Error(`Unexpected request: ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetch);
    render(<HistoryPageExperience />);

    expect(await screen.findByRole("heading", { name: "Two Sum" })).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[1]);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(
      "This removes this interview and rebuilds learning conclusions that depended on it.",
    );
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();

    const confirm = screen.getByRole("button", { name: "Delete interview" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.getByRole("button", { name: "Deleting…" })).toBeDisabled());
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(1);

    resolveDeletion(json({
      interview_session_id: completedId,
      status: "DELETION_PENDING",
      deletion_request_id: "9d000000-0000-4000-8000-000000000004",
    }, 202));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Two Sum" })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Merge Intervals" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetch.mock.calls.some(([input]) => pathname(input) === "/api/mastery/me")).toBe(false);
  });

  it("cancels without mutation and keeps the row on a retryable failure", async () => {
    let deleteAttempts = 0;
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathname(input);
      if (path === "/api/me") return json(currentUser());
      if (path === "/api/interviews" && !init?.method) return json(history());
      if (path === `/api/interviews/${completedId}` && init?.method === "DELETE") {
        deleteAttempts += 1;
        return json({ detail: "temporarily unavailable" }, 503);
      }
      throw new Error(`Unexpected request: ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetch);
    render(<HistoryPageExperience />);

    await screen.findByRole("heading", { name: "Two Sum" });
    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[1]);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(deleteAttempts).toBe(0);

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[1]);
    fireEvent.click(screen.getByRole("button", { name: "Delete interview" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was removed");
    expect(screen.getByRole("heading", { name: "Two Sum" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete interview" }));
    await waitFor(() => expect(deleteAttempts).toBe(2));
  });

  it("survives Strict Mode and sends an authenticated production DELETE", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathname(input);
      if (path === "/api/me") return json(currentUser());
      if (path === "/api/interviews" && !init?.method) return json(history());
      if (path === `/api/interviews/${activeId}` && init?.method === "DELETE") {
        expect(new Headers(init.headers).get("Authorization")).toBe("Bearer stage9d-token");
        return json({
          interview_session_id: activeId,
          status: "DELETION_PENDING",
          deletion_request_id: "9d000000-0000-4000-8000-000000000005",
        }, 202);
      }
      throw new Error(`Unexpected request: ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetch);
    render(<StrictMode><HistoryPageExperience /></StrictMode>);

    await screen.findByRole("heading", { name: "Merge Intervals" });
    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Delete interview" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Merge Intervals" })).not.toBeInTheDocument());
    const deletionCalls = fetch.mock.calls.filter(([, init]) => init?.method === "DELETE");
    expect(deletionCalls).toHaveLength(1);
    expect(pathname(deletionCalls[0][0])).toBe(`/api/interviews/${activeId}`);
  });
});
