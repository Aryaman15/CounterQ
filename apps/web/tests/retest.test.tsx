import type { components } from "@counterq/contracts/openapi";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InterviewHeader } from "@/features/interview-room/components/InterviewHeader";
import { MasteryExperience } from "@/features/mastery/MasteryExperience";
import { RetestDemo } from "@/features/mastery/RetestDemo";

type Overview = components["schemas"]["CandidateMasteryOverviewResponse"];
type Launch = components["schemas"]["RetestLaunchResponse"];
type Target = components["schemas"]["CandidateMasteryTarget"];

const recommendationId = "8b000000-0000-4000-8000-000000000001";
const userId = "8b000000-0000-4000-8000-000000000002";
const sessionId = "8b000000-0000-4000-8000-000000000003";

const overview: Overview = {
  status: "READY",
  user_id: userId,
  mastery_policy_version: "mastery_policy_v1",
  target_level: "NEW_GRAD",
  updated_at: "2026-09-08T12:00:00Z",
  message: "What CounterQ has learned from your evidence across interviews.",
  technical_concepts: [],
  parent_summaries: [],
  interview_skills: [],
  retest_recommendations: [{
    recommendation_id: recommendationId,
    target_type: "CONCEPT",
    target_id: "8b000000-0000-4000-8000-000000000004",
    target_name: "Hash Map",
    status: "PENDING",
    reason: "Verify this gap independently in another context.",
    action_label: "CounterQ me again",
    action_enabled: true,
    availability_message: "Ready for a 10-minute Quick Drill.",
  }],
};

const launch: Launch = {
  recommendation_id: recommendationId,
  retest_attempt_id: "8b000000-0000-4000-8000-000000000005",
  interview_session_id: sessionId,
  problem_id: "8b000000-0000-4000-8000-000000000006",
  problem_version_id: "8b000000-0000-4000-8000-000000000007",
  problem_title: "A Different Problem",
  language: "cpp",
  target_level: "NEW_GRAD",
  mode: "SIMULATION",
  template: "QUICK_DRILL",
  configured_duration_seconds: 600,
  resumed: false,
  interview_path: "/interview/demo",
};

function masteryTarget(overrides: Partial<Target> = {}): Target {
  return {
    target_type: "CONCEPT",
    target_id: overview.retest_recommendations[0].target_id,
    canonical_key: "hash_table_complexity",
    display_name: "Hash Map",
    category: "DATA_STRUCTURES",
    state: "WEAK",
    state_label: "Needs work",
    evidence_sufficiency: "MEDIUM",
    evidence_sufficiency_label: "Some evidence",
    freshness: "RETEST_DUE",
    freshness_label: "Retest due",
    reason: "Independent evidence shows a meaningful gap that still needs verification.",
    evidence_count: 1,
    distinct_session_count: 1,
    distinct_problem_count: 1,
    distinct_context_count: 1,
    retest_due: true,
    recommendation_id: recommendationId,
    next_action: "Verify this gap independently in another context.",
    unresolved_breakpoint_ids: ["8b000000-0000-4000-8000-000000000008"],
    evidence: [{
      evidence_id: "8b000000-0000-4000-8000-000000000009",
      contribution: "CONTRADICTING",
      recorded_at: "2026-09-01T12:00:00Z",
      problem: "Prior Hash Map Interview",
      mode: "SIMULATION",
      candidate_level: "NEW_GRAD",
      polarity: "NEGATIVE",
      strength: "STRONG",
      independence: "INDEPENDENT",
      retest_linked: false,
      finding: "Could not defend the worst-case lookup boundary.",
      source_session_id: "8b000000-0000-4000-8000-000000000010",
    }],
    child_target_ids: [],
    ...overrides,
  };
}

function overviewWithTarget(target: Target = masteryTarget()): Overview {
  return {
    ...overview,
    technical_concepts: target.target_type === "CONCEPT" ? [target] : [],
    interview_skills: target.target_type === "SKILL" ? [target] : [],
  };
}

function response(value: unknown, ok = true) {
  return { ok, json: async () => value } as Response;
}

function fixtureFetch(startResponse: Promise<Response> = Promise.resolve(response(launch))) {
  return vi.fn()
    .mockResolvedValueOnce(response({ user_id: userId, recommendation_id: recommendationId }))
    .mockResolvedValueOnce(response(overview))
    .mockReturnValueOnce(startResponse);
}

describe("Stage 8B retest experience", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("enables CounterQ me again only for a persisted actionable recommendation", () => {
    render(<MasteryExperience overview={overview} onStartRetest={async () => launch} />);
    expect(screen.getByRole("button", { name: "CounterQ me again" })).toBeEnabled();
  });

  it("shows starting state and blocks double submit", async () => {
    let resolveStart: (value: Launch) => void = () => undefined;
    const start = vi.fn(() => new Promise<Launch>((resolve) => { resolveStart = resolve; }));
    render(<MasteryExperience overview={overview} onStartRetest={start} />);
    const button = screen.getByRole("button", { name: "CounterQ me again" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(screen.getByRole("button", { name: /Starting Quick Drill/ })).toBeDisabled();
    expect(start).toHaveBeenCalledTimes(1);
    resolveStart(launch);
    await waitFor(() => expect(screen.getByRole("button", { name: "Quick Drill ready" })).toBeDisabled());
  });

  it("launches through the durable backend and navigates into the existing Interview Room", async () => {
    vi.stubGlobal("fetch", fixtureFetch());
    const navigate = vi.fn();
    render(<RetestDemo navigate={navigate} />);
    fireEvent.click(await screen.findByRole("button", { name: "CounterQ me again" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/interview/demo"));
    expect(sessionStorage.getItem("counterq:realtime-control:development-session-id")).toBe(sessionId);
  });

  it("surfaces a safe unavailable message without navigation", async () => {
    vi.stubGlobal("fetch", fixtureFetch(Promise.resolve(response({
      detail: { message: "No suitable retest is available yet." },
    }, false))));
    const navigate = vi.fn();
    render(<RetestDemo navigate={navigate} />);
    fireEvent.click(await screen.findByRole("button", { name: "CounterQ me again" }));
    expect(await screen.findByText("No suitable retest is available yet.")).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("has a recoverable backend-fixture failure state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({}, false)));
    render(<RetestDemo navigate={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: /temporarily unavailable/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("does not create an action merely because a target is weak", () => {
    render(<MasteryExperience overview={{ ...overview, retest_recommendations: [] }} onStartRetest={async () => launch} />);
    expect(screen.queryByRole("button", { name: "CounterQ me again" })).not.toBeInTheDocument();
  });

  it("keeps skill-only recommendations unavailable", () => {
    render(<MasteryExperience overview={{
      ...overview,
      retest_recommendations: [{
        ...overview.retest_recommendations[0],
        target_type: "SKILL",
        action_enabled: false,
      }],
    }} onStartRetest={async () => launch} />);
    expect(screen.getByRole("button", { name: "CounterQ me again" })).toBeDisabled();
  });

  it("launches an actionable concept retest from the mastery detail drawer", async () => {
    const start = vi.fn(async () => launch);
    render(<MasteryExperience overview={overviewWithTarget()} onStartRetest={start} />);
    fireEvent.click(screen.getByRole("button", { name: /Open Hash Map mastery detail/i }));
    const drawer = screen.getByRole("dialog");
    fireEvent.click(within(drawer).getByRole("button", { name: "CounterQ me again" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith(recommendationId));
    expect(within(drawer).getByRole("button", { name: "Quick Drill ready" })).toBeDisabled();
  });

  it("shares double-submit protection between overview and drawer actions", () => {
    const start = vi.fn(() => new Promise<Launch>(() => undefined));
    render(<MasteryExperience overview={overviewWithTarget()} onStartRetest={start} />);
    fireEvent.click(screen.getByRole("button", { name: /Open Hash Map mastery detail/i }));
    const actions = screen.getAllByRole("button", { name: "CounterQ me again" });
    expect(actions).toHaveLength(2);
    fireEvent.click(actions[0]);
    fireEvent.click(actions[1]);
    expect(start).toHaveBeenCalledTimes(1);
    expect(screen.getAllByRole("button", { name: /Starting Quick Drill/ })).toHaveLength(2);
  });

  it("does not invent a drawer action for WEAK without a persisted recommendation", () => {
    const weak = masteryTarget({ recommendation_id: null });
    render(
      <MasteryExperience
        overview={{ ...overviewWithTarget(weak), retest_recommendations: [] }}
        onStartRetest={async () => launch}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Open Hash Map mastery detail/i }));
    expect(within(screen.getByRole("dialog")).queryByRole("button", {
      name: "CounterQ me again",
    })).not.toBeInTheDocument();
  });

  it("does not expose a drill action for a skill-only target", () => {
    const skill = masteryTarget({
      target_type: "SKILL",
      target_id: "8b000000-0000-4000-8000-000000000011",
      canonical_key: "correctness",
      display_name: "Correctness",
    });
    render(
      <MasteryExperience
        overview={{
          ...overviewWithTarget(skill),
          retest_recommendations: [{
            ...overview.retest_recommendations[0],
            target_type: "SKILL",
            target_id: skill.target_id,
            action_enabled: false,
          }],
        }}
        onStartRetest={async () => launch}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Open Correctness mastery detail/i }));
    expect(within(screen.getByRole("dialog")).queryByRole("button", {
      name: "CounterQ me again",
    })).not.toBeInTheDocument();
  });

  it("shows safe launch errors in the drawer's shared action state", async () => {
    render(
      <MasteryExperience
        overview={overviewWithTarget()}
        onStartRetest={async () => {
          throw new Error("No suitable retest is available yet.");
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Open Hash Map mastery detail/i }));
    const drawer = screen.getByRole("dialog");
    fireEvent.click(within(drawer).getByRole("button", { name: "CounterQ me again" }));
    expect(await within(drawer).findByText("No suitable retest is available yet.")).toBeInTheDocument();
  });

  it("contains no stale Stage 8B availability wording", () => {
    render(<MasteryExperience overview={overviewWithTarget()} onStartRetest={async () => launch} />);
    fireEvent.click(screen.getByRole("button", { name: /Open Hash Map mastery detail/i }));
    expect(document.body.textContent).not.toContain("Available in Stage 8B");
  });

  it("shows only fresh Quick Drill context in the Interview Room header", () => {
    render(<InterviewHeader
      mode="SIMULATION"
      template="QUICK_DRILL"
      remainingLabel="10:00"
      voiceState="Ready"
      onEndInterview={() => undefined}
    />);
    expect(screen.getByText("Quick Drill")).toBeInTheDocument();
    expect(screen.getByText("10 min")).toBeInTheDocument();
    expect(screen.getByText("SIMULATION")).toBeInTheDocument();
    expect(document.body.textContent?.toLowerCase()).not.toMatch(/last time|got this wrong|remember/);
  });

  it("keeps the retest action intentional on mobile and keyboard focusable", () => {
    const css = readFileSync(`${process.cwd()}/app/globals.css`, "utf8");
    expect(css).toMatch(/@media \(max-width: 720px\)[\s\S]*\.mastery-retest li/);
    render(<MasteryExperience overview={overview} onStartRetest={async () => launch} />);
    const button = screen.getByRole("button", { name: "CounterQ me again" });
    button.focus();
    expect(button).toHaveFocus();
  });
});
