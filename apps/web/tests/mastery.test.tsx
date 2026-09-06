import type { components } from "@counterq/contracts/openapi";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MasteryDemo } from "@/features/mastery/MasteryDemo";
import { MasteryExperience } from "@/features/mastery/MasteryExperience";

type Overview = components["schemas"]["CandidateMasteryOverviewResponse"];
type Target = components["schemas"]["CandidateMasteryTarget"];

const conceptId = "8a000000-0000-4000-8000-000000000101";
const skillId = "8a000000-0000-4000-8000-000000000201";
const evidenceId = "8a000000-0000-4000-8000-000000000301";

function target(overrides: Partial<Target> = {}): Target {
  return {
    target_type: "CONCEPT",
    target_id: conceptId,
    canonical_key: "sliding_window_boundary_monotonicity",
    display_name: "Boundary monotonicity",
    category: "ALGORITHMS",
    state: "STRONG",
    state_label: "Strong",
    evidence_sufficiency: "HIGH",
    evidence_sufficiency_label: "Well supported",
    freshness: "CURRENT",
    freshness_label: "Current",
    reason: "You demonstrated this independently across multiple distinct contexts.",
    evidence_count: 1,
    distinct_session_count: 2,
    distinct_problem_count: 2,
    distinct_context_count: 2,
    retest_due: false,
    recommendation_id: null,
    next_action: "Keep the evidence current through future interviews.",
    unresolved_breakpoint_ids: [],
    evidence: [{
      evidence_id: evidenceId,
      contribution: "SUPPORTING",
      recorded_at: "2026-09-05T12:00:00Z",
      problem: "Longest Substring Without Repeating Characters",
      mode: "SIMULATION",
      candidate_level: "NEW_GRAD",
      polarity: "POSITIVE",
      strength: "STRONG",
      independence: "INDEPENDENT",
      retest_linked: false,
      finding: "Defended why the left boundary never moves backward.",
      source_session_id: "8a000000-0000-4000-8000-000000000401",
    }],
    child_target_ids: [],
    ...overrides,
  };
}

function overview(overrides: Partial<Overview> = {}): Overview {
  const strong = target();
  const developing = target({
    target_id: "8a000000-0000-4000-8000-000000000102",
    canonical_key: "sliding_window_state_maintenance",
    display_name: "State maintenance",
    state: "DEVELOPING",
    state_label: "Developing",
    evidence_sufficiency: "MEDIUM",
    evidence_sufficiency_label: "Some evidence",
    reason: "You demonstrated this independently once. Another context is needed.",
  });
  const weak = target({
    target_id: "8a000000-0000-4000-8000-000000000103",
    canonical_key: "sliding_window_window_validity",
    display_name: "Window validity",
    state: "WEAK",
    state_label: "Needs work",
    freshness: "RETEST_DUE",
    freshness_label: "Retest due",
    reason: "Independent evidence shows a meaningful gap that still needs verification.",
    retest_due: true,
    recommendation_id: "8a000000-0000-4000-8000-000000000501",
    next_action: "Verify the unresolved gap independently in a different context.",
    unresolved_breakpoint_ids: ["8a000000-0000-4000-8000-000000000601"],
    evidence: [{
      ...strong.evidence[0],
      evidence_id: "8a000000-0000-4000-8000-000000000302",
      contribution: "CONTRADICTING",
      polarity: "NEGATIVE",
      finding: "Could not defend the window-validity condition.",
    }],
  });
  const exposedSkill = target({
    target_type: "SKILL",
    target_id: skillId,
    canonical_key: "complexity_reasoning",
    display_name: "Complexity reasoning",
    category: "INTERVIEW_SKILL",
    state: "EXPOSED",
    state_label: "Limited evidence",
    evidence_sufficiency: "LOW",
    evidence_sufficiency_label: "Limited evidence",
    freshness: "AGING",
    freshness_label: "Not tested recently",
    reason: "CounterQ has seen this area, but not enough trustworthy evidence to judge it yet.",
    evidence: [{
      ...strong.evidence[0],
      evidence_id: "8a000000-0000-4000-8000-000000000303",
      contribution: "LEARNING_LIMITED",
      independence: "DIRECTLY_TAUGHT",
      mode: "COACH",
      finding: "Explained the bound after direct teaching.",
    }],
  });
  const parent = target({
    target_type: "PARENT_SUMMARY",
    target_id: "8a000000-0000-4000-8000-000000000100",
    canonical_key: "sliding_window",
    display_name: "Sliding Window",
    state: "DEVELOPING",
    state_label: "Developing",
    reason: "An important child concept still needs work.",
    evidence: [],
    child_target_ids: [strong.target_id, weak.target_id],
  });
  return {
    status: "READY",
    user_id: "8a000000-0000-4000-8000-000000000001",
    mastery_policy_version: "mastery_policy_v1",
    target_level: "NEW_GRAD",
    updated_at: "2026-09-06T12:00:00Z",
    message: "What CounterQ has evidence you can defend independently.",
    technical_concepts: [strong, developing, weak],
    parent_summaries: [parent],
    interview_skills: [exposedSkill],
    retest_recommendations: [{
      recommendation_id: "8a000000-0000-4000-8000-000000000501",
      target_type: "CONCEPT",
      target_id: weak.target_id,
      target_name: weak.display_name,
      status: "PENDING",
      reason: weak.next_action,
      action_label: "CounterQ me again",
      action_enabled: false,
      availability_message: "Quick Drill execution begins in Stage 8B.",
    }],
    ...overrides,
  };
}

function response(value: unknown, ok = true) {
  return { ok, json: async () => value } as Response;
}

describe("Stage 8A Mastery Map", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("renders a sparse cold start without enumerating untested ontology", () => {
    render(<MasteryExperience overview={overview({
      status: "EMPTY",
      message: "CounterQ is still learning where your interview strengths are.",
      technical_concepts: [],
      parent_summaries: [],
      interview_skills: [],
      retest_recommendations: [],
    })} />);

    expect(screen.getByRole("heading", { name: "No conclusions before the evidence." })).toBeInTheDocument();
    expect(screen.queryByText("Boundary monotonicity")).not.toBeInTheDocument();
  });

  it("groups canonical states and keeps concepts separate from skills", () => {
    render(<MasteryExperience overview={overview()} />);

    for (const label of ["Strong", "Developing", "Needs work", "Limited evidence"])
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    expect(screen.getByRole("region", { name: "Technical concepts" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Interview skills" })).toBeInTheDocument();
    expect(screen.getByText("Complexity reasoning")).toBeInTheDocument();
  });

  it("shows freshness and an honest disabled Stage 8B retest action", () => {
    render(<MasteryExperience overview={overview()} />);

    expect(screen.getAllByText("Retest due").length).toBeGreaterThan(0);
    fireEvent.click(
      screen.getByRole("button", { name: /open complexity reasoning mastery detail/i }),
    );
    expect(screen.getByText("Not tested recently")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "CounterQ me again" })).toBeDisabled();
    expect(screen.getByText("Quick Drill execution begins in Stage 8B.")).toBeInTheDocument();
  });

  it("contains no percentage, score, or gamified progress copy", () => {
    const { container } = render(<MasteryExperience overview={overview()} />);
    expect(container.textContent?.toLowerCase()).not.toMatch(/percent|percentage|score|\d+%/);
  });

  it("opens an evidence drawer with rationale, contradiction, and assisted attribution", () => {
    render(<MasteryExperience overview={overview()} />);
    fireEvent.click(screen.getByRole("button", { name: /open window validity mastery detail/i }));

    expect(screen.getByRole("dialog", { name: "Window validity" })).toBeInTheDocument();
    expect(screen.getByText("Why this state?")).toBeInTheDocument();
    expect(screen.getByText("Evidence timeline")).toBeInTheDocument();
    expect(screen.getByText("Could not defend the window-validity condition.")).toBeInTheDocument();
    expect(screen.getByText(/validated Breakpoint is still unresolved/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close detail drawer" }));
    fireEvent.click(screen.getByRole("button", { name: /open complexity reasoning mastery detail/i }));
    expect(screen.getByText("Learning / assisted Evidence")).toBeInTheDocument();
  });

  it("explains parent aggregation without fabricating parent Evidence", () => {
    render(<MasteryExperience overview={overview()} />);
    fireEvent.click(screen.getByRole("button", { name: /open sliding window mastery detail/i }));

    expect(screen.getByText(/summarizes child projections/i)).toBeInTheDocument();
    expect(screen.getByText(/creates no synthetic Evidence/i)).toBeInTheDocument();
    expect(screen.queryByText("Evidence timeline")).not.toBeInTheDocument();
  });

  it("closes the drawer with Escape and restores keyboard focus", async () => {
    render(<MasteryExperience overview={overview()} />);
    const trigger = screen.getByRole("button", { name: /open boundary monotonicity/i });
    trigger.focus();
    fireEvent.click(trigger);
    await waitFor(() => expect(screen.getByRole("button", { name: "Close detail drawer" })).toHaveFocus());

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("renders updating and failure states without overstating persistence", () => {
    const { rerender } = render(<MasteryExperience overview={overview({
      status: "UPDATING",
      message: "CounterQ is recalculating this view from your canonical evidence.",
    })} />);
    expect(screen.getByRole("status")).toHaveTextContent("recalculating");

    rerender(<MasteryExperience overview={overview({
      status: "FAILED",
      message: "Mastery is temporarily unavailable. Your interview evidence is unchanged.",
    })} />);
    expect(screen.getByRole("status")).toHaveTextContent("temporarily unavailable");
    expect(screen.queryByText(/everything is saved/i)).not.toBeInTheDocument();
  });

  it("loads production-shaped backend fixtures and has safe loading/error recovery", async () => {
    const fixture = {
      fixture_id: "backend-fixture",
      label: "Backend fixture",
      description: "Evaluated by the production deterministic policy.",
      overview: overview(),
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([fixture])));
    render(<MasteryDemo />);
    expect(screen.getByRole("heading", { name: /recalculating from canonical evidence/i })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Backend fixture" })).toHaveAttribute("aria-pressed", "true");
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/mastery/development/fixtures"), expect.objectContaining({ cache: "no-store" }));
  });

  it("offers retry after a fixture request fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({}, false)));
    render(<MasteryDemo />);

    expect(await screen.findByRole("heading", { name: /temporarily unavailable/i })).toBeInTheDocument();
    expect(screen.getByText(/canonical Evidence, reports, and CounterMaps remain unchanged/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("ships an intentional 390px layout and reduced-motion fallback", () => {
    const css = readFileSync(`${process.cwd()}/app/globals.css`, "utf8");
    expect(css).toContain("@media (max-width: 720px)");
    expect(css).toContain(".mastery-drawer { width: 100%");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toMatch(/\.mastery-loading-mark[\s\S]*animation: none/);
  });
});
