import type { components } from "@counterq/contracts/openapi";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionReportExperience } from "@/features/interview-room/components/SessionReportExperience";

type ReportResponse = components["schemas"]["CandidateSessionReportResponse"];

const evidenceId = "018f1d54-7b2a-7000-8000-000000000001";
const beforeHelpEvidenceId = "018f1d54-7b2a-7000-8000-000000000006";
const breakpointId = "018f1d54-7b2a-7000-8000-000000000002";

function reportResponse(mode: "COACH" | "SIMULATION" = "SIMULATION"): ReportResponse {
  const finding = {
    title: "The core invariant held",
    finding: "You kept the complement lookup ahead of insertion and defended why order matters.",
    evidence_ids: [evidenceId],
    breakpoint_id: null,
    independence_level: mode === "COACH" ? "AFTER_LIGHT_GUIDANCE" as const : "INDEPENDENT" as const,
    based_on_insufficient_evidence: false,
  };
  const supported = {
    status: "SUPPORTED" as const,
    items: [finding],
    insufficient_evidence_message: null,
  };
  const insufficient = {
    status: "INSUFFICIENT_EVIDENCE" as const,
    items: [],
    insufficient_evidence_message: "Not enough evidence from this session.",
  };
  return {
    status: "READY",
    report_id: "018f1d54-7b2a-7000-8000-000000000010",
    report_version: 1,
    generated_at: "2026-09-04T10:31:00Z",
    message: "Your evidence-backed Session Report is ready.",
    session: {
      problem_title: "Two Sum",
      mode,
      language: "python",
      completed_at: "2026-09-04T10:30:00Z",
      duration_seconds: 1532,
    },
    report: {
      contract_version: "session-report-output.v1",
      metadata: {
        interview_session_id: "018f1d54-7b2a-7000-8000-000000000020",
        mode,
        level: "MID",
        language: "python",
        problem_version_id: "018f1d54-7b2a-7000-8000-000000000021",
        problem_title: "Two Sum",
        started_at: "2026-09-04T10:04:28Z",
        completed_at: "2026-09-04T10:30:00Z",
        duration_seconds: 1532,
        source_watermark: 18,
      },
      summary: [finding],
      strengths: mode === "COACH" ? [] : [finding],
      breakpoints: [{
        breakpoint_id: breakpointId,
        concept_id: "018f1d54-7b2a-7000-8000-000000000003",
        skill_dimension_id: "018f1d54-7b2a-7000-8000-000000000004",
        concept_label: "Hash Table Complexity",
        skill_label: "Complexity Reasoning",
        title: "Worst-case lookup remained uncertain",
        explanation: "You corrected the average-case claim, but did not independently defend the worst case.",
        status: "OPEN",
        severity: "MEDIUM",
        evidence_ids: [evidenceId],
      }],
      claim_defense: supported,
      correctness_implementation: supported,
      complexity: supported,
      edge_cases: insufficient,
      debugging: insufficient,
      adaptability: supported,
      coach_assistance: mode === "COACH" ? [{
        title: "Complexity reasoning restarted after a hint",
        explanation: "Before help, the worst case was missing. After light guidance, you revised the claim.",
        delivery_ids: ["018f1d54-7b2a-7000-8000-000000000005"],
        assistance_type: "CONCEPTUAL_HINT",
        hint_level: "CONCEPTUAL_HINT",
        assistance_label: "Conceptual hint",
        before_help_evidence_ids: [beforeHelpEvidenceId],
        after_help_evidence_ids: [evidenceId],
        later_independence_level: "AFTER_LIGHT_GUIDANCE",
        independent_verification_missing: true,
      }] : [],
      next_actions: [{
        action: "Practice distinguishing average-case from worst-case hash lookup.",
        evidence_ids: [evidenceId],
        breakpoint_ids: [breakpointId],
        based_on_insufficient_evidence: false,
      }],
      source_details: [{
        evidence_id: evidenceId,
        finding: "The candidate defended the complement-before-insertion invariant.",
        attribution: mode === "COACH" ? "Demonstrated after light guidance" : "Independently demonstrated",
        source_label: "Conversation from this interview",
        source_excerpt: "I check the complement first so one index cannot match itself.",
      }, ...(mode === "COACH" ? [{
        evidence_id: beforeHelpEvidenceId,
        finding: "The initial complexity claim omitted the worst-case boundary.",
        attribution: "Independently demonstrated",
        source_label: "Conversation from this interview",
        source_excerpt: "Hash lookup is always constant time.",
      }] : [])],
    },
  };
}

function response(value: ReportResponse) {
  return { ok: true, json: async () => value } as Response;
}

describe("post-interview Session Report", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("recovers the preparing state from backend truth on mount", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      ...reportResponse(), status: "PREPARING", report: null, report_id: null,
      report_version: null, generated_at: null,
    })));
    render(<SessionReportExperience interviewSessionId="session-1" pollIntervalMs={60_000} />);

    expect(screen.getByRole("heading", { name: /reviewing what you demonstrated/i })).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/reports/sessions/session-1"),
      expect.objectContaining({ cache: "no-store" }),
    ));
  });

  it("moves from report preparation to a structured READY report", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response({
        ...reportResponse(), status: "PREPARING", report: null, report_id: null,
        report_version: null, generated_at: null,
      }))
      .mockResolvedValueOnce(response(reportResponse())));
    render(<SessionReportExperience interviewSessionId="session-2" pollIntervalMs={1} />);

    expect(screen.getByRole("heading", { name: /reviewing what you demonstrated/i })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Two Sum" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Independent strengths" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Breakpoints" })).toBeInTheDocument();
    expect(screen.getAllByText("Not enough evidence from this session.")).toHaveLength(2);
  });

  it("keeps Simulation free of a fabricated Coach section", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(reportResponse("SIMULATION"))));
    render(<SessionReportExperience interviewSessionId="session-3" />);

    await screen.findByRole("heading", { name: "Two Sum" });
    expect(screen.queryByRole("heading", { name: /Before help/i })).not.toBeInTheDocument();
  });

  it("separates Coach before-help and assisted outcomes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(reportResponse("COACH"))));
    render(<SessionReportExperience interviewSessionId="session-4" />);

    expect(await screen.findByRole("heading", { name: "Before help → after help" })).toBeInTheDocument();
    expect(screen.getByText("Before help")).toBeInTheDocument();
    expect(screen.getByText("After help")).toBeInTheDocument();
    expect(screen.getByText("Independent verification still needed")).toBeInTheDocument();
  });

  it("reveals candidate-safe source context without exposing internal IDs", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(reportResponse())));
    render(<SessionReportExperience interviewSessionId="session-5" />);

    const disclosure = (await screen.findAllByText("Why this?"))[0];
    fireEvent.click(disclosure);
    expect(screen.getAllByText("Independently demonstrated").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/check the complement first/i).length).toBeGreaterThan(0);
    expect(screen.queryByText(evidenceId)).not.toBeInTheDocument();
  });

  it("shows a durable-safe failed state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      ...reportResponse(), status: "FAILED", report: null, generated_at: null,
      message: "Your interview is saved, but the detailed report could not be generated yet.",
    })));
    render(<SessionReportExperience interviewSessionId="session-6" />);

    expect(await screen.findByRole("heading", { name: /isn’t ready yet/i })).toBeInTheDocument();
    expect(screen.getByText("Your completed interview is preserved.")).toBeInTheDocument();
  });
});
