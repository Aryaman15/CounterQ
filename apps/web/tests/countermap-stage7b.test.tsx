import type { components } from "@counterq/contracts/openapi";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CounterMapSurface } from "@/features/countermap/CounterMapSurface";
import { ReasoningTimeline } from "@/features/countermap/ReasoningTimeline";
import type { CounterMapNode } from "@/features/countermap/counterMapPresentation";
import { counterMapUiSamples } from "./counterMapUiSamples";

type Detail = components["schemas"]["CandidateCounterMapNodeDetailResponse"];

function apiResponse(value: unknown, ok = true) {
  return { ok, json: async () => value } as Response;
}

function setNarrowViewport(narrow: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: query.includes("max-width") ? narrow : false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

function baseDetail(node: CounterMapNode): Detail {
  return {
    node_id: node.node_id,
    node_type: node.node_type,
    title: node.title,
    summary: node.summary,
    stage: node.stage ?? null,
    source_status: "AVAILABLE",
  };
}

function renderSurface(graph = counterMapUiSamples[0]) {
  return render(
    <CounterMapSurface
      graph={graph}
      detailUrlForNode={(nodeId) => `/candidate-detail/${nodeId}`}
    />,
  );
}

function openTimelineNode(name: RegExp | string) {
  fireEvent.click(screen.getByRole("button", { name: "Timeline" }));
  fireEvent.click(screen.getByRole("button", { name }));
}

describe("Stage 7B interactive CounterMap", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    setNarrowViewport(false);
  });

  it("defaults desktop and tablet presentation to Graph", async () => {
    renderSurface();

    expect(screen.getByRole("button", { name: "Graph" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("region", { name: /interactive countermap graph/i })).toBeInTheDocument();
  });

  it("defaults a narrow viewport to the equivalent Timeline", async () => {
    setNarrowViewport(true);
    renderSurface();

    await waitFor(() => expect(screen.getByRole("button", { name: "Timeline" })).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByRole("list", { name: /reasoning timeline/i })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /interactive countermap graph/i })).not.toBeInTheDocument();
  });

  it("switches Graph and Timeline without fetching or regenerating projection data", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    renderSurface();

    fireEvent.click(screen.getByRole("button", { name: "Timeline" }));
    expect(screen.getByRole("list", { name: /reasoning timeline/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Graph" }));
    expect(screen.getByRole("region", { name: /interactive countermap graph/i })).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("keeps the selected view when the production-backed graph fixture changes", () => {
    const { rerender } = renderSurface(counterMapUiSamples[0]);
    fireEvent.click(screen.getByRole("button", { name: "Timeline" }));

    rerender(
      <CounterMapSurface
        graph={counterMapUiSamples[1]}
        detailUrlForNode={(nodeId) => `/candidate-detail/${nodeId}`}
      />,
    );

    expect(screen.getByRole("button", { name: "Timeline" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/reasoning improved after guidance/i)).toBeInTheDocument();
  });

  it("provides pan/zoom/fit controls with semantic labels", () => {
    renderSurface();

    expect(screen.getByRole("button", { name: "Zoom in" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zoom out" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fit causal map" })).toBeInTheDocument();
    expect(screen.getByText(/drag to pan/i)).toBeInTheDocument();
  });

  it("explains all three non-color visual families", () => {
    renderSurface();

    const legend = screen.getByLabelText("Node visual families");
    expect(within(legend).getByText("Your moments")).toHaveAttribute("data-family", "candidate");
    expect(within(legend).getByText("CounterQ")).toHaveAttribute("data-family", "counterq");
    expect(within(legend).getByText("What this showed")).toHaveAttribute("data-family", "evaluation");
  });

  it("renders every visible node kind in the semantic Timeline equivalent", () => {
    const nodes = [
      ...counterMapUiSamples[0].nodes,
      ...counterMapUiSamples[1].nodes,
      ...counterMapUiSamples[2].nodes,
    ];
    const byType = new Map(nodes.map((node) => [node.node_type, node]));
    const claim = byType.get("CLAIM");
    if (!claim) throw new Error("Samples need a claim node");
    byType.set("REASONING", { ...claim, node_id: "reasoning-node", node_type: "REASONING", title: "Your reasoning" });
    byType.set("TEST", { ...claim, node_id: "test-node", node_type: "TEST", title: "You tested it" });
    byType.set("MUTATION", { ...claim, node_id: "mutation-node", node_type: "MUTATION", title: "Constraint change" });
    const graph = { ...counterMapUiSamples[0], nodes: [...byType.values()], edges: [] };

    render(<ReasoningTimeline graph={graph} />);

    for (const type of ["CLAIM", "REASONING", "CODE", "TEST", "QUESTION", "RESPONSE", "EVIDENCE", "BREAKPOINT", "ASSISTANCE", "MUTATION"]) {
      expect(document.querySelector(`.countermap-node-${type.toLowerCase()}`)).toBeInTheDocument();
    }
  });

  it("opens a source-backed drawer when a Timeline node is selected", async () => {
    const question = counterMapUiSamples[0].nodes.find((node) => node.node_type === "QUESTION");
    if (!question) throw new Error("Sample needs a question");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse({
      ...baseDetail(question),
      delivered_prompt: {
        text: question.summary,
        delivery_state: "DELIVERED",
        why: question.display_metadata?.why,
        assistance_label: null,
        concepts: ["Hash tables"],
        skills: ["Complexity reasoning"],
      },
    })));
    renderSurface();

    openTimelineNode(/Inspect this moment: CounterQ asked/i);

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("Actually delivered")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      `/candidate-detail/${question.node_id}`,
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("closes the drawer with Escape and restores focus to the selected node control", async () => {
    const question = counterMapUiSamples[0].nodes.find((node) => node.node_type === "QUESTION");
    if (!question) throw new Error("Sample needs a question");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse(baseDetail(question))));
    renderSurface();
    fireEvent.click(screen.getByRole("button", { name: "Timeline" }));
    const trigger = screen.getByRole("button", { name: /Inspect this moment: CounterQ asked/i });
    trigger.focus();
    fireEvent.click(trigger);
    const drawer = await screen.findByRole("dialog");
    const close = within(drawer).getByRole("button", { name: "Close detail drawer" });
    await waitFor(() => expect(close).toHaveFocus());

    fireEvent.keyDown(document, { key: "Tab" });
    expect(close).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it("shows only actual interrupted wording in the question drawer", async () => {
    const question = counterMapUiSamples[2].nodes.find((node) => node.node_type === "QUESTION");
    if (!question) throw new Error("Integrity sample needs a question");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse({
      ...baseDetail(question),
      delivered_prompt: {
        text: "What invariant",
        delivery_state: "INTERRUPTED",
        why: "CounterQ asked this in response to your code.",
        assistance_label: null,
        concepts: [],
        skills: [],
      },
    })));
    renderSurface(counterMapUiSamples[2]);

    openTimelineNode(/Inspect this moment: CounterQ asked/i);

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getAllByText("What invariant").length).toBeGreaterThan(0);
    expect(within(drawer).getByText(/only the words delivered before interruption/i)).toBeInTheDocument();
    expect(within(drawer).queryByText(/moves backward/i)).not.toBeInTheDocument();
  });

  it("renders the deterministic why explanation and structured target labels", async () => {
    const question = counterMapUiSamples[0].nodes.find((node) => node.node_type === "QUESTION");
    if (!question) throw new Error("Sample needs a question");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse({
      ...baseDetail(question),
      delivered_prompt: {
        text: question.summary,
        delivery_state: "DELIVERED",
        why: question.display_metadata?.why,
        assistance_label: null,
        concepts: ["Hash tables"],
        skills: ["Complexity reasoning"],
      },
    })));
    renderSurface();

    openTimelineNode(/Inspect this moment: CounterQ asked/i);

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("Why this question?")).toBeInTheDocument();
    expect(within(drawer).getByText("Hash tables")).toBeInTheDocument();
    expect(within(drawer).getByText("Complexity reasoning")).toBeInTheDocument();
  });

  it("shows exact historical snapshot v2 without substituting later v5 code", async () => {
    const code = counterMapUiSamples[2].nodes.find((node) => node.display_metadata?.code_version === 2);
    if (!code) throw new Error("Integrity sample needs snapshot v2");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse({
      ...baseDetail(code),
      code: {
        snapshot_id: code.display_metadata?.code_snapshot_id,
        version: 2,
        language: "python",
        source_code: "left = max(left, last[char] + 1)",
        context: "This is the code CounterQ was reacting to.",
        diff: null,
      },
    })));
    renderSurface(counterMapUiSamples[2]);

    openTimelineNode(/View code at this moment: Corrected independently/i);

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getAllByText(/Snapshot v2/i).length).toBeGreaterThan(0);
    expect(within(drawer).getByText(/left = max/)).toBeInTheDocument();
    expect(within(drawer).queryByText(/last\.get/)).not.toBeInTheDocument();
  });

  it("fails safely when the API cannot verify code hash or version", async () => {
    const code = counterMapUiSamples[2].nodes.find((node) => node.node_type === "CODE");
    if (!code) throw new Error("Integrity sample needs code");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse({
      ...baseDetail(code),
      source_status: "UNAVAILABLE",
      message: "The exact historical code could not be verified, so no code is shown.",
    })));
    renderSurface(counterMapUiSamples[2]);

    openTimelineNode(/View code at this moment: Your code/i);

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("Exact source unavailable")).toBeInTheDocument();
    expect(within(drawer).queryByRole("code")).not.toBeInTheDocument();
  });

  it("preserves assisted Evidence instead of labeling it independent", async () => {
    const evidence = counterMapUiSamples[1].nodes.find((node) => (
      node.node_type === "EVIDENCE" && node.display_metadata?.polarity === "POSITIVE"
    ));
    if (!evidence) throw new Error("Coach sample needs positive evidence");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse({
      ...baseDetail(evidence),
      evidence: {
        finding: evidence.summary,
        polarity: "POSITIVE",
        strength: "MODERATE",
        independence_level: "AFTER_LIGHT_GUIDANCE",
        concepts: ["Hash tables"],
        skills: ["Complexity reasoning"],
        supporting_moments: [3],
      },
    })));
    renderSurface(counterMapUiSamples[1]);

    openTimelineNode(/Inspect this moment: What this showed/i);

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("After light guidance")).toBeInTheDocument();
    expect(within(drawer).getByText(/not presented as independent proof/i)).toBeInTheDocument();
  });

  it("shows an open Breakpoint as needing independent verification", async () => {
    const breakpoint = counterMapUiSamples[1].nodes.find((node) => node.node_type === "BREAKPOINT");
    if (!breakpoint) throw new Error("Coach sample needs breakpoint");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse({
      ...baseDetail(breakpoint),
      breakpoint: {
        summary: breakpoint.summary,
        status: "OPEN",
        severity: "MEDIUM",
        concept: "Hash tables",
        skill: "Complexity reasoning",
        independent_verification_required: true,
        evidence: [{
          relationship: "RESOLUTION_SUPPORT",
          finding: "Improved after guidance.",
          polarity: "POSITIVE",
          independence_level: "AFTER_LIGHT_GUIDANCE",
        }],
      },
    })));
    renderSurface(counterMapUiSamples[1]);

    openTimelineNode(/Inspect this moment: Breakpoint/i);

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText(/independent verification is still missing/i)).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "CounterQ me again" })).toBeDisabled();
  });

  it("never renders hidden test details in the TEST drawer", async () => {
    const source = counterMapUiSamples[0].nodes[0];
    const testNode: CounterMapNode = {
      ...source,
      node_id: "test-detail-node",
      node_type: "TEST",
      title: "You tested it",
      summary: "3 visible tests passed.",
    };
    const graph = { ...counterMapUiSamples[0], nodes: [testNode], edges: [] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse({
      ...baseDetail(testNode),
      execution: {
        run_id: "7a000000-0000-4000-8000-000000000777",
        status: "SUCCEEDED",
        language: "python",
        code_snapshot_version: 2,
        visible_passed: 3,
        visible_failed: 0,
        visible_tests: [],
      },
    })));
    renderSurface(graph);

    openTimelineNode(/Inspect this moment: You tested it/i);

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText(/3 passed · 0 failed/i)).toBeInTheDocument();
    expect(within(drawer).queryByText(/hidden/i)).not.toBeInTheDocument();
  });

  it("renders detail loading and network failure states without losing the map", async () => {
    let rejectFetch: (reason?: unknown) => void = () => undefined;
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise((_resolve, reject) => {
      rejectFetch = reject;
    })));
    renderSurface();
    openTimelineNode(/Inspect this moment: CounterQ asked/i);

    expect(await screen.findByText(/opening the exact source/i)).toBeInTheDocument();
    rejectFetch(new Error("offline"));
    expect(await screen.findByText(/source could not be loaded/i)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: /reasoning timeline/i })).toBeInTheDocument();
  });

  it("renders a calm empty projection state", () => {
    const graph = {
      ...counterMapUiSamples[0],
      nodes: [],
      edges: [],
      summary: { ...counterMapUiSamples[0].summary, node_counts: {}, relationship_counts: {} },
    };
    renderSurface(graph);

    expect(screen.getByRole("heading", { name: /no material causal moments/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Graph" })).not.toBeInTheDocument();
  });
});
