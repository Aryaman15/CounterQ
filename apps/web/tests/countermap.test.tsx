import type { components } from "@counterq/contracts/openapi";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CounterMapDemo } from "@/features/countermap/CounterMapDemo";
import { CounterMapExperience } from "@/features/countermap/CounterMapExperience";
import { ReasoningTimeline } from "@/features/countermap/ReasoningTimeline";
import { counterMapDemoFixtures } from "./counterMapFixtures";

type CounterMapResponse = components["schemas"]["CandidateCounterMapResponse"];
type DevelopmentFixture = components["schemas"]["DevelopmentCounterMapFixtureResponse"];

function apiResponse(value: unknown) {
  return { ok: true, json: async () => value } as Response;
}

function readyResponse(): CounterMapResponse {
  const fixture = counterMapDemoFixtures[0];
  return {
    status: "READY",
    session: {
      problem_title: "Two Sum",
      mode: "SIMULATION",
      language: "python",
      completed_at: "2026-09-05T10:00:00Z",
      duration_seconds: 1200,
    },
    projection_id: "7a000000-0000-4000-8000-000000000901",
    projection_version: 1,
    schema_version: "countermap.graph.v1",
    generated_at: "2026-09-05T10:00:03Z",
    graph: fixture.graph,
    message: "Your evidence-backed reasoning map is ready.",
  };
}

function developmentFixtures(): DevelopmentFixture[] {
  return [
    {
      fixture_id: "simulation-success-and-misconception",
      label: "Simulation",
      description: "An independent defense and a later misconception.",
      graph: counterMapDemoFixtures[0].graph,
    },
    {
      fixture_id: "coach-assisted-improvement-open-breakpoint",
      label: "Coach",
      description: "Guidance materially changed the next response.",
      graph: counterMapDemoFixtures[1].graph,
    },
    {
      fixture_id: "delivery-and-self-correction-integrity",
      label: "Integrity",
      description: "Only delivered wording and structured correction survive.",
      graph: counterMapDemoFixtures[2].graph,
    },
  ];
}

describe("CounterMap Reasoning Timeline", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("renders causal steps and branches from the shared graph contract", () => {
    render(<ReasoningTimeline graph={counterMapDemoFixtures[0].graph} />);
    expect(screen.getByRole("list", { name: /reasoning timeline/i })).toBeInTheDocument();
    expect(screen.getAllByText("Parallel branches").length).toBeGreaterThan(0);
    expect(screen.getByText(/prompted this question/i)).toBeInTheDocument();
    expect(screen.getAllByText("Strong demonstration").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Needs work").length).toBeGreaterThan(0);
  });

  it("loads and switches backend-projected development fixtures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse(developmentFixtures())));
    render(<CounterMapDemo />);
    fireEvent.click(await screen.findByRole("button", { name: "Coach" }));
    expect(screen.getByRole("heading", { name: "Coach guidance" })).toBeInTheDocument();
    expect(screen.getAllByText(/independent verification/i).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Integrity" }));
    expect(screen.getByRole("heading", { name: "Corrected independently" })).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/countermap/development/fixtures"),
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("uses candidate-facing labels and type-specific explanations", () => {
    render(<ReasoningTimeline graph={counterMapDemoFixtures[0].graph} />);
    expect(screen.getAllByText("You said").length).toBeGreaterThan(0);
    expect(screen.getByText("Why this question?")).toBeInTheDocument();
  });

  it("uses mutation and guidance language instead of question-only copy", () => {
    const simulation = counterMapDemoFixtures[0].graph;
    const question = simulation.nodes.find((node) => node.node_type === "QUESTION");
    if (!question) throw new Error("Simulation fixture must include a question");
    const mutationGraph = {
      ...simulation,
      nodes: simulation.nodes.map((node) => node.node_id === question.node_id
        ? { ...node, node_type: "MUTATION" as const }
        : node),
    };
    const { rerender } = render(<ReasoningTimeline graph={mutationGraph} />);
    expect(screen.getByText("Why this constraint change?")).toBeInTheDocument();
    expect(screen.getByText(/prompted this constraint change/i)).toBeInTheDocument();

    const coach = counterMapDemoFixtures[1].graph;
    const assistance = coach.nodes.find((node) => node.node_type === "ASSISTANCE");
    if (!assistance) throw new Error("Coach fixture must include assistance");
    const guidanceGraph = {
      ...coach,
      nodes: coach.nodes.map((node) => node.node_id === assistance.node_id
        ? {
            ...node,
            display_metadata: {
              ...node.display_metadata,
              exact_quote: node.display_metadata?.exact_quote ?? false,
              why: "CounterQ offered this guidance in response to what you said.",
            },
          }
        : node),
    };
    rerender(<ReasoningTimeline graph={guidanceGraph} />);
    expect(screen.getByText("Why this guidance?")).toBeInTheDocument();
  });

  it("shows only actually delivered wording for an interrupted prompt", () => {
    render(<ReasoningTimeline graph={counterMapDemoFixtures[2].graph} />);
    expect(screen.getByText("What invariant")).toBeInTheDocument();
    expect(screen.queryByText(/moves backward/i)).not.toBeInTheDocument();
    expect(screen.getByText(/only the delivered words/i)).toBeInTheDocument();
  });

  it("keeps future retest actions visibly non-operational", () => {
    render(<ReasoningTimeline graph={counterMapDemoFixtures[1].graph} />);
    const retest = screen.getByRole("button", { name: /CounterQ me again/i });
    expect(retest).toBeDisabled();
    expect(retest).toHaveTextContent("Later");
  });

  it("loads a persisted READY projection through the candidate API", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse(readyResponse())));
    render(<CounterMapExperience interviewSessionId="session-7" />);
    expect(await screen.findByRole("heading", { name: /how your interview unfolded/i })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "CounterQ asked" })).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/countermap/sessions/session-7"),
      expect.objectContaining({ cache: "no-store" }),
    ));
  });

  it("uses the durable-safe unavailable copy on projection failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse({
      ...readyResponse(),
      status: "FAILED",
      graph: null,
      generated_at: null,
      message: "CounterMap is unavailable for this interview. Your report and interview evidence are still safe.",
    })));
    render(<CounterMapExperience interviewSessionId="session-8" />);
    expect(await screen.findByRole("heading", { name: /CounterMap is unavailable/i })).toBeInTheDocument();
    expect(screen.getByText(/report and interview evidence are still safe/i)).toBeInTheDocument();
  });

  it("renders not-available as a settled empty state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse({
      ...readyResponse(),
      status: "NOT_AVAILABLE",
      projection_id: null,
      projection_version: null,
      schema_version: null,
      graph: null,
      generated_at: null,
      message: "A reasoning map has not been prepared for this interview yet.",
    })));
    render(<CounterMapExperience interviewSessionId="session-9" />);
    expect(await screen.findByRole("heading", { name: /no reasoning map was prepared/i })).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  });
});
