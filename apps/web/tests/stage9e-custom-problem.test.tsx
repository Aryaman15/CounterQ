import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { describe, expect, it, vi } from "vitest";

import { SelfServeInterviewSetupForm } from "@/features/interview-room/components/SelfServeInterviewSetup";
import { CounterQApiClient } from "@/lib/counterq-api";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const profile = {
  user_id: "01991b74-927a-7000-8000-000000000001",
  account_status: "ACTIVE",
  onboarding_required: false,
  profile: {
    display_name: null,
    preferred_language: "python",
    default_interview_mode: "SIMULATION",
    interview_level: "NEW_GRAD",
    target_role: null,
    timezone: "Asia/Calcutta",
    profile_version: 1,
    created_at: "2026-09-11T00:00:00Z",
    updated_at: "2026-09-11T00:00:00Z",
  },
};

const catalog = [{
  problem_version_id: "01991b74-927a-7000-8000-000000000010",
  slug: "reviewed-problem",
  title: "Reviewed Problem",
  supported_languages: ["cpp", "python", "java"],
  catalog_order: 1,
}];

const pending = {
  preparation_id: "01991b74-927a-7000-8000-000000000020",
  operational_status: "PENDING",
  quality_outcome: null,
  retryable: false,
  title: null,
  statement_preview: null,
  constraints: [],
  examples: [],
  supported_languages: [],
  concept_labels: [],
  problem_version_id: null,
  message: "Custom problem preparation is pending.",
};

describe("Stage 9E custom problem setup", () => {
  it("defaults to reviewed problems and launches only the READY prepared version", async () => {
    const ready = {
      ...pending,
      operational_status: "COMPLETED",
      quality_outcome: "READY",
      title: "Pair Difference",
      statement_preview: "Return whether a pair has the requested difference.",
      constraints: ["2 <= nums.length <= 100000"],
      examples: [{ input: "[1, 4], 3", output: "true", explanation: "4 - 1 = 3" }],
      supported_languages: ["cpp", "python", "java"],
      concept_labels: ["Hash map"],
      problem_version_id: "01991b74-927a-7000-8000-000000000030",
      message: "This problem passed CounterQ's preparation checks and is ready to interview.",
    };
    const fetchFn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return response(profile);
      if (url.endsWith("/api/problems/curated")) return response(catalog);
      if (url.endsWith("/api/problems/custom/preparations")) return response(pending, 201);
      if (url.endsWith("/prepare")) return response(ready);
      if (url.endsWith("/api/interviews")) {
        return response({
          interview_session_id: "session-custom",
          interview_path: "/interview/session-custom",
        }, 201);
      }
      throw new Error(`Unexpected request: ${url} ${init?.method}`);
    });
    const onCreated = vi.fn();
    const api = new CounterQApiClient(async () => "token", "http://api.test", fetchFn as typeof fetch);
    const { container } = render(
      <SelfServeInterviewSetupForm
        api={api}
        onOnboardingRequired={vi.fn()}
        onCreated={onCreated}
      />,
    );

    await screen.findByRole("heading", { name: /Set the conditions/i });
    expect(container.querySelector('input[name="curated-problem"]')).toBeChecked();
    fireEvent.click(screen.getByLabelText(/Paste your own/i));
    expect(screen.getByRole("button", { name: "Start interview" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Full statement"), {
      target: {
        value: "Given an array and target, return whether a pair exists. Include examples, constraints, inputs, outputs, and a function signature.",
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Prepare problem" }));

    expect(await screen.findAllByText("Pair Difference")).not.toHaveLength(0);
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText(/Hash map/)).toBeInTheDocument();
    const start = screen.getByRole("button", { name: "Start interview" });
    expect(start).toBeEnabled();
    fireEvent.click(start);

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("/interview/session-custom"));
    const interviewCall = fetchFn.mock.calls.find(([url]) => String(url).endsWith("/api/interviews"));
    expect(interviewCall).toBeDefined();
    const body = JSON.parse(String(interviewCall?.[1]?.body));
    expect(body).toEqual({
      problem_version_id: ready.problem_version_id,
      template: "STANDARD_CODING_INTERVIEW",
      mode: "SIMULATION",
      language: "python",
    });
    expect(JSON.stringify(body)).not.toMatch(/preparation|pack|owner|source|user_id/);
  });

  it.each([
    [
      { ...pending, operational_status: "COMPLETED", quality_outcome: "NEEDS_CORRECTION", message: "Paste the complete statement." },
      "Needs correction",
    ],
    [
      { ...pending, operational_status: "COMPLETED", quality_outcome: "REJECTED", message: "This is not a supported coding problem." },
      "Not supported",
    ],
    [
      { ...pending, operational_status: "FAILED", retryable: true, message: "CounterQ could not finish preparing this problem. Retry the preparation." },
      "Preparation interrupted",
    ],
  ])("keeps a non-ready preparation from launching", async (prepared, label) => {
    const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return response(profile);
      if (url.endsWith("/api/problems/curated")) return response(catalog);
      if (url.endsWith("/api/problems/custom/preparations")) return response(pending, 201);
      if (url.endsWith("/prepare")) return response(prepared);
      throw new Error(`Unexpected request: ${url}`);
    });
    const api = new CounterQApiClient(async () => "token", "http://api.test", fetchFn as typeof fetch);
    render(
      <SelfServeInterviewSetupForm
        api={api}
        onOnboardingRequired={vi.fn()}
        onCreated={vi.fn()}
      />,
    );
    await screen.findByRole("heading", { name: /Set the conditions/i });
    fireEvent.click(screen.getByLabelText(/Paste your own/i));
    fireEvent.change(screen.getByLabelText("Full statement"), {
      target: { value: "A complete-looking pasted problem statement with examples and constraints." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Prepare problem" }));
    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start interview" })).toBeDisabled();
  });

  it("bounds input and blocks duplicate preparation in StrictMode", async () => {
    let releaseCreate: (value: Response) => void = () => undefined;
    const deferredCreate = new Promise<Response>((resolve) => {
      releaseCreate = resolve;
    });
    let createCalls = 0;
    let prepareCalls = 0;
    const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return response(profile);
      if (url.endsWith("/api/problems/curated")) return response(catalog);
      if (url.endsWith("/api/problems/custom/preparations")) {
        createCalls += 1;
        return deferredCreate;
      }
      if (url.endsWith("/prepare")) {
        prepareCalls += 1;
        return response({
          ...pending,
          operational_status: "FAILED",
          retryable: true,
          message: "CounterQ could not finish preparing this problem. Retry the preparation.",
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    const getToken = vi.fn(async () => "candidate-token");
    const api = new CounterQApiClient(
      getToken,
      "http://api.test",
      fetchFn as typeof fetch,
    );
    render(
      <StrictMode>
        <SelfServeInterviewSetupForm
          api={api}
          onOnboardingRequired={vi.fn()}
          onCreated={vi.fn()}
        />
      </StrictMode>,
    );

    await screen.findByRole("heading", { name: /Set the conditions/i });
    fireEvent.click(screen.getByLabelText(/Paste your own/i));
    const textarea = screen.getByLabelText("Full statement");
    expect(textarea).toHaveAttribute("maxlength", "20000");
    fireEvent.change(textarea, {
      target: {
        value: "Given an array and target, return whether a pair exists with examples and constraints.",
      },
    });
    const prepare = screen.getByRole("button", { name: "Prepare problem" });
    fireEvent.click(prepare);
    fireEvent.click(prepare);

    expect(await screen.findByRole("button", { name: "Preparing problem…" })).toBeDisabled();
    expect(createCalls).toBe(1);
    releaseCreate(response(pending, 201));
    expect(await screen.findByText("Preparation interrupted")).toBeInTheDocument();
    expect(prepareCalls).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "Retry preparation" }));
    await waitFor(() => expect(prepareCalls).toBe(2));
    expect(createCalls).toBe(1);
    expect(getToken).toHaveBeenCalled();
  });
});
