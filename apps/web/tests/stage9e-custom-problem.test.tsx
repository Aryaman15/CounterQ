import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

const customProblemText = "Given an array and target, return whether a pair exists. Include examples, constraints, inputs, outputs, and a function signature.";

function storePreparationPointer(
  preparationId: string | null,
  overrides: Record<string, unknown> = {},
) {
  sessionStorage.setItem(
    `counterq:custom-preparation:v1:${profile.user_id}`,
    JSON.stringify({
      version: 1,
      user_id: profile.user_id,
      preparation_id: preparationId,
      idempotency_key: "stable-draft-key",
      draft_text: customProblemText,
      ...overrides,
    }),
  );
}

describe("Stage 9E custom problem setup", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("defaults to reviewed problems and launches only the READY prepared version", async () => {
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

  it("recovers READY server truth on remount and launches that version", async () => {
    storePreparationPointer(pending.preparation_id);
    const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return response(profile);
      if (url.endsWith("/api/problems/curated")) return response(catalog);
      if (url.endsWith(`/preparations/${pending.preparation_id}`)) return response(ready);
      if (url.endsWith("/api/interviews")) {
        return response({
          interview_session_id: "session-restored-custom",
          interview_path: "/interview/session-restored-custom",
        }, 201);
      }
      throw new Error(`Unexpected request: ${url}`);
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

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByDisplayValue(customProblemText)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start interview" }));
    await waitFor(() => {
      expect(onCreated).toHaveBeenCalledWith("/interview/session-restored-custom");
    });
  });

  it.each([
    [pending, "Resume preparation"],
    [{
      ...pending,
      operational_status: "FAILED",
      retryable: true,
      message: "CounterQ could not finish preparing this problem. Retry the preparation.",
    }, "Retry preparation"],
    [{
      ...pending,
      operational_status: "PROCESSING",
      retryable: true,
      message: "The previous preparation attempt can be retried.",
    }, "Retry preparation"],
  ])("resumes retryable server preparation states", async (restored, actionLabel) => {
    storePreparationPointer(pending.preparation_id);
    let prepareCalls = 0;
    const fetchFn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return response(profile);
      if (url.endsWith("/api/problems/curated")) return response(catalog);
      if (url.endsWith(`/preparations/${pending.preparation_id}`)) return response(restored);
      if (url.endsWith("/prepare") && init?.method === "POST") {
        prepareCalls += 1;
        return response(ready);
      }
      throw new Error(`Unexpected request: ${url} ${init?.method}`);
    });
    const api = new CounterQApiClient(async () => "token", "http://api.test", fetchFn as typeof fetch);
    render(
      <SelfServeInterviewSetupForm
        api={api}
        onOnboardingRequired={vi.fn()}
        onCreated={vi.fn()}
      />,
    );

    const action = await screen.findByRole("button", { name: actionLabel });
    fireEvent.click(action);
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(prepareCalls).toBe(1);
  });

  it("recreates an outdated failed preparation with a fresh identity and preserved draft", async () => {
    storePreparationPointer(pending.preparation_id);
    const oldFailed = {
      ...pending,
      operational_status: "FAILED",
      retryable: true,
      message: "CounterQ could not finish preparing this problem. Retry the preparation.",
    };
    const freshPreparationId = "01991b74-927a-7000-8000-000000000040";
    const freshPending = { ...pending, preparation_id: freshPreparationId };
    const freshReady = { ...ready, preparation_id: freshPreparationId };
    let createBody: Record<string, string> | null = null;
    const prepareIds: string[] = [];
    const fetchFn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return response(profile);
      if (url.endsWith("/api/problems/curated")) return response(catalog);
      if (url.endsWith(`/preparations/${pending.preparation_id}`)) {
        return response(oldFailed);
      }
      if (url.endsWith(`/preparations/${pending.preparation_id}/prepare`)) {
        prepareIds.push(pending.preparation_id);
        return response({
          detail: {
            category: "custom_problem_preparation_policy_outdated",
            message: "This saved preparation must be recreated before it can continue.",
          },
        }, 409);
      }
      if (url.endsWith("/api/problems/custom/preparations") && init?.method === "POST") {
        createBody = JSON.parse(String(init.body));
        return response(freshPending, 201);
      }
      if (url.endsWith(`/preparations/${freshPreparationId}/prepare`)) {
        prepareIds.push(freshPreparationId);
        return response(freshReady);
      }
      throw new Error(`Unexpected request: ${url} ${init?.method}`);
    });
    const api = new CounterQApiClient(
      async () => "token",
      "http://api.test",
      fetchFn as typeof fetch,
    );
    render(
      <SelfServeInterviewSetupForm
        api={api}
        onOnboardingRequired={vi.fn()}
        onCreated={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Retry preparation" }));

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByLabelText("Full statement")).toHaveValue(customProblemText);
    expect(prepareIds).toEqual([pending.preparation_id, freshPreparationId]);
    expect(createBody).toEqual({
      problem_text: customProblemText,
      idempotency_key: expect.not.stringMatching(/^stable-draft-key$/),
    });
    const stored = JSON.parse(String(sessionStorage.getItem(
      `counterq:custom-preparation:v1:${profile.user_id}`,
    )));
    expect(stored).toMatchObject({
      preparation_id: freshPreparationId,
      draft_text: customProblemText,
    });
    expect(stored.idempotency_key).not.toBe("stable-draft-key");
  });

  it("restores NEEDS_CORRECTION and requires a new edited draft", async () => {
    storePreparationPointer(pending.preparation_id);
    const needsCorrection = {
      ...pending,
      operational_status: "COMPLETED",
      quality_outcome: "NEEDS_CORRECTION",
      message: "Paste the complete statement.",
    };
    const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return response(profile);
      if (url.endsWith("/api/problems/curated")) return response(catalog);
      if (url.endsWith(`/preparations/${pending.preparation_id}`)) {
        return response(needsCorrection);
      }
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

    expect(await screen.findByText("Needs correction")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit problem to try again" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Full statement"), {
      target: { value: `${customProblemText} Added missing constraints.` },
    });
    expect(screen.getByRole("button", { name: "Prepare problem" })).toBeEnabled();
  });

  it("keeps fresh PROCESSING read-only and does not start duplicate work", async () => {
    storePreparationPointer(pending.preparation_id);
    const processing = {
      ...pending,
      operational_status: "PROCESSING",
      message: "CounterQ is checking this problem.",
    };
    const fetchFn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return response(profile);
      if (url.endsWith("/api/problems/curated")) return response(catalog);
      if (url.endsWith(`/preparations/${pending.preparation_id}`)) return response(processing);
      if (url.endsWith("/prepare") && init?.method === "POST") {
        throw new Error("fresh processing must not be restarted");
      }
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

    expect(await screen.findByText("Preparing")).toBeInTheDocument();
    const action = screen.getByRole("button", { name: "Preparing problem…" });
    expect(action).toBeDisabled();
    expect(screen.getByLabelText("Full statement")).toBeDisabled();
    expect(fetchFn.mock.calls.filter(([url]) => String(url).endsWith("/prepare"))).toHaveLength(0);
  });

  it("clears a missing server pointer instead of trusting local state", async () => {
    storePreparationPointer(pending.preparation_id, {
      quality_outcome: "READY",
      problem_version_id: ready.problem_version_id,
    });
    const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return response(profile);
      if (url.endsWith("/api/problems/curated")) return response(catalog);
      if (url.endsWith(`/preparations/${pending.preparation_id}`)) {
        return response({ detail: "not found" }, 404);
      }
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

    await waitFor(() => expect(screen.getByLabelText("Full statement")).toHaveValue(""));
    expect(screen.queryByText("Ready")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start interview" })).toBeDisabled();
    expect(sessionStorage.getItem(
      `counterq:custom-preparation:v1:${profile.user_id}`,
    )).toBeNull();
  });

  it("does not recover another signed-in user's pointer", async () => {
    sessionStorage.setItem(
      "counterq:custom-preparation:v1:another-user",
      JSON.stringify({
        version: 1,
        user_id: "another-user",
        preparation_id: pending.preparation_id,
        idempotency_key: "other-key",
        draft_text: customProblemText,
      }),
    );
    const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return response(profile);
      if (url.endsWith("/api/problems/curated")) return response(catalog);
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
    expect(screen.queryByLabelText("Full statement")).not.toBeInTheDocument();
    expect(fetchFn.mock.calls.some(([url]) => String(url).includes("/preparations/"))).toBe(false);
  });

  it("reuses the draft idempotency key after an ambiguous create and remount", async () => {
    let firstKey = "";
    const firstFetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return response(profile);
      if (url.endsWith("/api/problems/curated")) return response(catalog);
      if (url.endsWith("/api/problems/custom/preparations")) {
        firstKey = JSON.parse(String(init?.body)).idempotency_key;
        throw new TypeError("connection closed after commit");
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    const firstApi = new CounterQApiClient(
      async () => "token",
      "http://api.test",
      firstFetch as typeof fetch,
    );
    const first = render(
      <SelfServeInterviewSetupForm
        api={firstApi}
        onOnboardingRequired={vi.fn()}
        onCreated={vi.fn()}
      />,
    );
    await screen.findByRole("heading", { name: /Set the conditions/i });
    fireEvent.click(screen.getByLabelText(/Paste your own/i));
    fireEvent.change(screen.getByLabelText("Full statement"), {
      target: { value: customProblemText },
    });
    fireEvent.click(screen.getByRole("button", { name: "Prepare problem" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not prepare/i);
    first.unmount();

    let secondKey = "";
    const secondFetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/me")) return response(profile);
      if (url.endsWith("/api/problems/curated")) return response(catalog);
      if (url.endsWith("/api/problems/custom/preparations")) {
        secondKey = JSON.parse(String(init?.body)).idempotency_key;
        return response(pending, 201);
      }
      if (url.endsWith("/prepare")) return response(ready);
      throw new Error(`Unexpected request: ${url}`);
    });
    const secondApi = new CounterQApiClient(
      async () => "token",
      "http://api.test",
      secondFetch as typeof fetch,
    );
    render(
      <SelfServeInterviewSetupForm
        api={secondApi}
        onOnboardingRequired={vi.fn()}
        onCreated={vi.fn()}
      />,
    );
    expect(await screen.findByDisplayValue(customProblemText)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Prepare problem" }));
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(firstKey).not.toBe("");
    expect(secondKey).toBe(firstKey);
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
