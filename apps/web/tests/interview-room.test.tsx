import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Home from "../app/page";
import InterviewDemoPage from "../app/interview/demo/page";
import {
  codePersistenceState,
  InterviewRoom,
} from "../features/interview-room/components/InterviewRoom";
import { InterviewerSurface } from "../features/interview-room/components/InterviewerSurface";
import {
  demoInterviewFixture,
  hiddenInternalFixtureFields,
} from "../features/interview-room/fixtures/demoInterview";
import {
  DEMO_EDITOR_STORAGE_KEY,
  DEMO_SPLITTER_STORAGE_KEY,
  clampProblemWidth,
  readStoredEditorCode,
  readStoredProblemWidth,
  writeStoredEditorCode,
  writeStoredProblemWidth,
} from "../features/interview-room/hooks/localPersistence";
import { reducedMotionQuery } from "../features/interview-room/hooks/usePrefersReducedMotion";

const observedRealtimeSession = {
  eventType: "session.created" as const,
  sessionType: "realtime",
  transcriptionModel: "gpt-live-transcribe",
  turnDetectionType: "semantic_vad",
  createResponse: false,
  interruptResponse: true,
};

const observedCanonicalSession = {
  sessionId: "session-1",
  controlConnected: true,
  pendingDurableMessages: 1,
  lastServerSequence: 17,
  stateVersion: 3,
  probeBudgetUsed: 1,
  probeBudgetMax: 6,
  lastCandidateFinal: {
    providerItemId: "item-1",
    eventId: "event-1",
    transcriptSegmentId: "segment-1",
    persistence: "ACKNOWLEDGED" as const,
  },
  lastDelivery: {
    promptId: "prompt-1",
    deliveryId: "delivery-1",
    deliveryState: "DELIVERED",
    providerResponseId: "response-1",
    actualTranscriptId: "segment-2",
    localPlaybackState: "COMPLETED" as const,
    canonicalState: "DELIVERED" as const,
    outputTranscriptState: "FINAL" as const,
    pendingTerminalEvent: "NONE" as const,
    lifecycleEvents: [
      "response.created",
      "playback_start_observed",
      "delivery_started_ack",
      "delivery_delivered_ack",
    ],
  },
  lastObservation: {
    kind: "CANDIDATE_TRANSCRIPT_FINALIZED",
    sourceEventId: "event-1",
    sourceEventWatermark: 17,
    stateVersion: 3,
    stage: "IMPLEMENTATION",
    triggerClass: "VOICE_TURN_COMPLETED",
  },
  lastCode: {
    snapshotId: "snapshot-2",
    version: 2,
    hashPrefix: "abc123def456",
    diffId: "diff-1",
    persistence: "ACKNOWLEDGED" as const,
  },
  lastVoice: {
    transcriptSegmentId: "segment-1",
    associatedCodeSnapshotId: "snapshot-2",
    associatedCodeSnapshotVersion: 2,
  },
  lastPolicyGate: {
    decisionId: "decision-1",
    disposition: "AUTHORIZED",
    decisionStatus: "AUTHORIZED",
    policyGateOutcome: "AUTHORIZED",
    promptId: "prompt-1",
    promptKind: "PROBE",
  },
  lastDeliveryPermit: {
    promptId: "prompt-1",
    status: "PERMITTED",
    reason: "Authorized prompt is valid for delivery.",
  },
};

vi.mock("@monaco-editor/react", () => ({
  default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
    <textarea
      aria-label="C++ code editor"
      data-testid="mock-monaco-editor"
      value={value}
      onChange={(event) => onChange(event.currentTarget.value)}
    />
  ),
}));

describe("Interview Room demo", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
  });

  it("renders a development launcher to the preview route", () => {
    render(<Home />);

    expect(screen.getByRole("heading", { name: "CounterQ Interview Room" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Interview Room Preview" })).toHaveAttribute(
      "href",
      "/interview/demo",
    );
  });

  it("derives the editor persistence badge from canonical acknowledgement", () => {
    const base = {
      canonicalDebug: observedCanonicalSession,
      isRestoring: false,
    };

    expect(
      codePersistenceState("class Solution {};", {
        ...base,
        acknowledgedCodeSource: "class Solution {};",
      }),
    ).toBe("SYNCED");
    expect(
      codePersistenceState("class Solution { int x; };", {
        ...base,
        acknowledgedCodeSource: "class Solution {};",
      }),
    ).toBe("LOCAL_PENDING");
    expect(
      codePersistenceState("class Solution {};", {
        ...base,
        isRestoring: true,
        acknowledgedCodeSource: "class Solution {};",
      }),
    ).toBe("PERSISTENCE_UNCONFIRMED");
  });

  it("renders the Interview Room route and Monaco surface", async () => {
    render(<InterviewDemoPage />);

    expect(screen.getByTestId("monaco-editor-surface")).toBeInTheDocument();
    expect(await screen.findByLabelText("C++ code editor")).toBeInTheDocument();
  });

  it("selects a development language before creating an interview session", () => {
    render(<InterviewRoom fixture={demoInterviewFixture} />);

    const selector = screen.getByRole("combobox", { name: "Development execution language" });
    fireEvent.change(selector, { target: { value: "python" } });

    expect(selector).toHaveValue("python");
    expect(screen.getByRole("heading", { name: "solution.py" })).toBeInTheDocument();
    expect(screen.getAllByText("Python 3").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("C++ code editor")).toHaveValue(
      "class Solution:\n    def lengthOfLongestSubstring(self, s: str) -> int:\n        pass",
    );
  });

  it("shows the required header state without durable stage labels", () => {
    render(<InterviewRoom fixture={demoInterviewFixture} />);

    expect(screen.getByLabelText("CounterQ Interview Room")).toHaveTextContent("CounterQ");
    expect(screen.getByText("SIMULATION")).toBeInTheDocument();
    expect(screen.getByText("21:42")).toBeInTheDocument();
    expect(screen.getAllByText("Ready").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Enable microphone" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "End Interview" })).toBeInTheDocument();

    expect(screen.queryByText("IMPLEMENTATION")).not.toBeInTheDocument();
    expect(screen.queryByText("APPROACH_DEFENSE")).not.toBeInTheDocument();
    expect(screen.queryByText("CONSTRAINT_MUTATION")).not.toBeInTheDocument();
  });

  it("shows the deterministic problem statement, examples, constraints, and signature", () => {
    render(<InterviewRoom fixture={demoInterviewFixture} />);

    expect(
      screen.getByRole("heading", {
        name: "Longest Substring Without Repeating Characters",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/return the length of the longest substring/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Examples" })).toBeInTheDocument();
    expect(screen.getByText('s = "abcabcbb"')).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Constraints" })).toBeInTheDocument();
    expect(screen.getByText("0 <= s.length <= 5 * 10^4")).toBeInTheDocument();
    expect(screen.getByText("int lengthOfLongestSubstring(string s)")).toBeInTheDocument();
  });

  it("renders only candidate-safe delivered interviewer text", () => {
    render(<InterviewRoom fixture={demoInterviewFixture} />);

    expect(screen.getByText((_, node) => node?.textContent === "What guarantees that left never moves backwards?"))
      .toBeInTheDocument();
    expect(screen.queryByText(hiddenInternalFixtureFields.examinerDecisionRationale)).not.toBeInTheDocument();
    expect(screen.queryByText(hiddenInternalFixtureFields.probeStrategy)).not.toBeInTheDocument();
    expect(screen.queryByText(hiddenInternalFixtureFields.intendedUndeliveredPromptText)).not.toBeInTheDocument();
  });

  it("opens, updates, and closes the development transcript popover without changing voice state", () => {
    const noop = vi.fn();
    const { rerender } = render(
      <InterviewerSurface
        voiceState="Listening"
        isMuted={false}
        voiceError={null}
        partialTranscript="I am thinking about"
        lastFinalTranscript="I am thinking about the window."
        sessionDebug={observedRealtimeSession}
        canonicalDebug={observedCanonicalSession}
        currentTurn={demoInterviewFixture.currentDeliveredTurn}
        onEnableMicrophone={noop}
        onMute={noop}
        onUnmute={noop}
        onDisconnectVoice={noop}
        onSpeakDevelopmentPhrase={noop}
        onEvaluateExaminerDecision={noop}
        onDeliverAuthorizedPrompt={noop}
        onOpenConversation={noop}
      />,
    );

    const transcriptButton = screen.getByRole("button", { name: "Dev transcript" });
    expect(transcriptButton).toBeInTheDocument();
    expect(screen.getByText("Listening")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "DEVELOPMENT TRANSCRIPT" })).not.toBeInTheDocument();

    fireEvent.click(transcriptButton);
    const popover = screen.getByRole("dialog", { name: "DEVELOPMENT TRANSCRIPT" });
    expect(within(popover).getByText("DEVELOPMENT TRANSCRIPT")).toBeInTheDocument();
    expect(screen.getByText("I am thinking about")).toBeInTheDocument();
    expect(screen.getByText("I am thinking about the window.")).toBeInTheDocument();
    expect(screen.getByText("realtime / gpt-live-transcribe")).toBeInTheDocument();
    expect(screen.getByText(/semantic_vad; auto response disabled; interruption enabled/i)).toBeInTheDocument();
    expect(screen.getByText(/session-1; control connected; pending 1/i)).toBeInTheDocument();
    expect(screen.getByText(/server sequence 17; state version 3/i)).toBeInTheDocument();
    expect(screen.getByText(/ACKNOWLEDGED; item item-1; event event-1; segment segment-1/i)).toBeInTheDocument();
    expect(screen.getByText(/CANDIDATE_TRANSCRIPT_FINALIZED; event event-1; watermark 17/i)).toBeInTheDocument();
    expect(screen.getByText(/ACKNOWLEDGED; snapshot snapshot-2; version 2; hash abc123def456; diff diff-1/i)).toBeInTheDocument();
    expect(screen.getByText(/acknowledged; idle threshold 2500 ms/i)).toBeInTheDocument();
    expect(screen.getByText(/segment segment-1; code snapshot snapshot-2; version 2/i)).toBeInTheDocument();
    expect(screen.getByText(/PERMITTED; prompt prompt-1/i)).toBeInTheDocument();
    expect(screen.getByText("Authorized prompt is valid for delivery.")).toBeInTheDocument();
    expect(screen.getByText(/prompt prompt-1; delivery delivery-1; state DELIVERED/i)).toBeInTheDocument();
    expect(screen.getByText("Listening")).toBeInTheDocument();

    rerender(
      <InterviewerSurface
        voiceState="Listening"
        isMuted={false}
        voiceError={null}
        partialTranscript=""
        lastFinalTranscript="Final transcript arrived."
        sessionDebug={observedRealtimeSession}
        canonicalDebug={observedCanonicalSession}
        currentTurn={demoInterviewFixture.currentDeliveredTurn}
        onEnableMicrophone={noop}
        onMute={noop}
        onUnmute={noop}
        onDisconnectVoice={noop}
        onSpeakDevelopmentPhrase={noop}
        onEvaluateExaminerDecision={noop}
        onDeliverAuthorizedPrompt={noop}
        onOpenConversation={noop}
      />,
    );

    expect(screen.getByText("No partial transcript")).toBeInTheDocument();
    expect(screen.getByText("Final transcript arrived.")).toBeInTheDocument();
    expect(screen.getByText("Listening")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "DEVELOPMENT TRANSCRIPT" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Dev transcript" }));
    expect(screen.getByRole("dialog", { name: "DEVELOPMENT TRANSCRIPT" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dev transcript" }));
    expect(screen.queryByRole("dialog", { name: "DEVELOPMENT TRANSCRIPT" })).not.toBeInTheDocument();
  });

  it("runs the development-only reasoning smoke control without changing prompt or speaking", async () => {
    const speakDevelopmentPhrase = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        invocation_id: "invocation-1",
        status: "SUCCEEDED",
        provider: "fake",
        model: "gpt-5.6-terra",
        capability: "STANDARD_REASONING",
        verdict: "NOT_GUARANTEED",
        technical_note: "Average lookup is expected constant time; worst-case is not guaranteed.",
        confidence: 0.91,
        latency_ms: 42,
        input_tokens: 100,
        cached_input_tokens: 20,
        output_tokens: 30,
        estimated_cost: "0.000520",
        currency: "USD",
        reasoning_budget_used: 1,
        reasoning_budget_remaining: 7,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <InterviewerSurface
        voiceState="Listening"
        isMuted={false}
        voiceError={null}
        partialTranscript="partial"
        lastFinalTranscript="final"
        sessionDebug={observedRealtimeSession}
        canonicalDebug={observedCanonicalSession}
        currentTurn={demoInterviewFixture.currentDeliveredTurn}
        onEnableMicrophone={vi.fn()}
        onMute={vi.fn()}
        onUnmute={vi.fn()}
        onDisconnectVoice={vi.fn()}
        onSpeakDevelopmentPhrase={speakDevelopmentPhrase}
        onEvaluateExaminerDecision={vi.fn()}
        onDeliverAuthorizedPrompt={vi.fn()}
        onOpenConversation={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Dev transcript" }));
    fireEvent.click(screen.getByRole("button", { name: "Reasoning smoke" }));

    await waitFor(() => {
      expect(screen.getByText("AI GATEWAY")).toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/ai/development-reasoning-smoke",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ interview_session_id: "session-1" }),
      }),
    );
    expect(screen.getByText(/SUCCEEDED; invocation invocation-1; fake\/gpt-5.6-terra/i)).toBeInTheDocument();
    expect(screen.getByText("RESULT")).toBeInTheDocument();
    expect(screen.getByText(/NOT_GUARANTEED; confidence 0.91/i)).toBeInTheDocument();
    expect(screen.getByText(/Average lookup is expected constant time/i)).toBeInTheDocument();
    expect(screen.getByText((_, node) => node?.textContent === "What guarantees that left never moves backwards?"))
      .toBeInTheDocument();
    expect(screen.queryByText(/raw provider/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/OPENAI_API_KEY/i)).not.toBeInTheDocument();
    expect(speakDevelopmentPhrase).not.toHaveBeenCalled();
  });

  it("shows development delivery-permit diagnostics distinctly", () => {
    const noop = vi.fn();
    const diagnostics = {
      ...observedCanonicalSession,
      lastDeliveryPermit: {
        promptId: "prompt-expired",
        status: "EXPIRED",
        reason: "Authorized prompt delivery window expired.",
      },
    };

    render(
      <InterviewerSurface
        voiceState="Listening"
        isMuted={false}
        voiceError={null}
        partialTranscript="partial"
        lastFinalTranscript="final"
        sessionDebug={observedRealtimeSession}
        canonicalDebug={diagnostics}
        currentTurn={demoInterviewFixture.currentDeliveredTurn}
        onEnableMicrophone={noop}
        onMute={noop}
        onUnmute={noop}
        onDisconnectVoice={noop}
        onSpeakDevelopmentPhrase={noop}
        onEvaluateExaminerDecision={noop}
        onDeliverAuthorizedPrompt={noop}
        onOpenConversation={noop}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Dev transcript" }));
    expect(screen.getByText(/EXPIRED; prompt prompt-expired/i)).toBeInTheDocument();
    expect(screen.getByText("Authorized prompt delivery window expired.")).toBeInTheDocument();
  });


  it("runs the development-only Live Examiner analysis without changing prompt or speaking", async () => {
    const speakDevelopmentPhrase = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "PROPOSED",
        source_kind: "CANDIDATE_TRANSCRIPT_FINALIZED",
        source_event_id: "event-1",
        source_event_watermark: 17,
        source_state_version: 3,
        code_snapshot_id: "snapshot-2",
        code_snapshot_version: 2,
        ai_invocation_id: "invocation-live-1",
        provider: "fake",
        model: "gpt-5.6-terra",
        latency_ms: 37,
        input_tokens: 120,
        cached_input_tokens: 12,
        output_tokens: 40,
        estimated_cost: "0.000700",
        currency: "USD",
        claims: [
          {
            id: "claim-1",
            normalized_claim: "unordered_map lookup has guaranteed O(1) time complexity",
            claim_type: "COMPLEXITY",
            verbatim_excerpt: "lookup is always O(1)",
            confidence: 0.92,
          },
        ],
        decision: {
          id: "decision-1",
          action: "PROBE",
          target_kind: "CLAIM",
          target_claim_id: "claim-1",
          target_code_snapshot_id: null,
          proposed_probe_strategy: "ASSUMPTION_CHALLENGE",
          technical_rationale: "The candidate made an absolute hash-table complexity claim.",
          confidence: 0.9,
          priority: 4,
          urgency: 3,
          status: "PROPOSED",
          policy_gate_outcome: null,
          policy_gate_reason: null,
          deadline_at: "2026-08-24T00:00:08Z",
        },
        message: null,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const diagnostics = {
      ...observedCanonicalSession,
      lastPolicyGate: {
        decisionId: null,
        disposition: null,
        decisionStatus: null,
        policyGateOutcome: null,
        promptId: null,
        promptKind: null,
      },
    };

    render(
      <InterviewerSurface
        voiceState="Listening"
        isMuted={false}
        voiceError={null}
        partialTranscript="partial"
        lastFinalTranscript="final"
        sessionDebug={observedRealtimeSession}
        canonicalDebug={diagnostics}
        currentTurn={demoInterviewFixture.currentDeliveredTurn}
        onEnableMicrophone={vi.fn()}
        onMute={vi.fn()}
        onUnmute={vi.fn()}
        onDisconnectVoice={vi.fn()}
        onSpeakDevelopmentPhrase={speakDevelopmentPhrase}
        onEvaluateExaminerDecision={vi.fn()}
        onDeliverAuthorizedPrompt={vi.fn()}
        onOpenConversation={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Dev transcript" }));
    fireEvent.click(screen.getByRole("button", { name: "Analyze latest observation" }));

    await waitFor(() => {
      expect(screen.getByText("LIVE EXAMINER RESULT")).toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/examiner/development-analyze-latest",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ interview_session_id: "session-1" }),
      }),
    );
    expect(screen.getByText(/PROPOSED; source CANDIDATE_TRANSCRIPT_FINALIZED; watermark 17/i))
      .toBeInTheDocument();
    expect(screen.getByText(/COMPLEXITY: unordered_map lookup has guaranteed O\(1\)/i))
      .toBeInTheDocument();
    expect(screen.getByText(/PROPOSED; PROBE; strategy ASSUMPTION_CHALLENGE/i))
      .toBeInTheDocument();
    expect(screen.getByText(/absolute hash-table complexity claim/i)).toBeInTheDocument();
    expect(screen.getByText((_, node) => node?.textContent === "What guarantees that left never moves backwards?"))
      .toBeInTheDocument();
    expect(speakDevelopmentPhrase).not.toHaveBeenCalled();
  });

  it("runs Analyze + authorize and displays examiner and gate diagnostics without speaking", async () => {
    const speakDevelopmentPhrase = vi.fn();
    const deliverAuthorizedPrompt = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        analysis: {
          status: "PROPOSED",
          source_kind: "CANDIDATE_TRANSCRIPT_FINALIZED",
          source_event_id: "event-1",
          source_event_watermark: 17,
          source_state_version: 3,
          code_snapshot_id: "snapshot-2",
          code_snapshot_version: 2,
          ai_invocation_id: "invocation-live-1",
          provider: "fake",
          model: "gpt-5.6-terra",
          latency_ms: 4000,
          input_tokens: 120,
          cached_input_tokens: 12,
          output_tokens: 40,
          estimated_cost: "0.000700",
          currency: "USD",
          claims: [
            {
              id: "claim-1",
              normalized_claim: "unordered_map lookup has guaranteed O(1) time complexity",
              claim_type: "COMPLEXITY",
              verbatim_excerpt: "lookup is always O(1)",
              confidence: 0.92,
            },
          ],
          decision: {
            id: "decision-1",
            action: "PROBE",
            target_kind: "CLAIM",
            target_claim_id: "claim-1",
            target_code_snapshot_id: null,
            proposed_probe_strategy: "ASSUMPTION_CHALLENGE",
            technical_rationale: "The candidate made an absolute hash-table complexity claim.",
            confidence: 0.9,
            priority: 4,
            urgency: 3,
            status: "PROPOSED",
            policy_gate_outcome: null,
            policy_gate_reason: null,
            deadline_at: "2026-08-24T00:00:08Z",
          },
          message: null,
        },
        policy_gate: {
          decision_id: "decision-1",
          disposition: "AUTHORIZED",
          decision_status: "AUTHORIZED",
          policy_gate_outcome: "AUTHORIZED",
          reason: "Policy gate authorized candidate-safe prompt intent.",
          interviewer_prompt_id: "prompt-1",
          prompt_kind: "PROBE",
          probe_strategy: "ASSUMPTION_CHALLENGE",
          candidate_safe_text: "You said always. Is that actually guaranteed?",
        },
        timing: {
          analysis_completed_at: "2026-08-24T00:00:04Z",
          gate_evaluated_at: "2026-08-24T00:00:04Z",
          decision_deadline_at: "2026-08-24T00:00:08Z",
          remaining_usefulness_seconds_at_analysis: 4,
          remaining_usefulness_seconds_at_gate: 4,
          authorized_at: "2026-08-24T00:00:04Z",
          delivery_window_expires_at: "2026-08-24T00:00:16Z",
          delivery_window_seconds: 12,
          delivery_window_state: "OPEN",
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const analyzeAuthorizeDiagnostics = {
      ...observedCanonicalSession,
      lastPolicyGate: {
        decisionId: null,
        disposition: null,
        decisionStatus: null,
        policyGateOutcome: null,
        promptId: null,
        promptKind: null,
      },
    };

    render(
      <InterviewerSurface
        voiceState="Listening"
        isMuted={false}
        voiceError={null}
        partialTranscript="partial"
        lastFinalTranscript="final"
        sessionDebug={observedRealtimeSession}
        canonicalDebug={analyzeAuthorizeDiagnostics}
        currentTurn={demoInterviewFixture.currentDeliveredTurn}
        onEnableMicrophone={vi.fn()}
        onMute={vi.fn()}
        onUnmute={vi.fn()}
        onDisconnectVoice={vi.fn()}
        onSpeakDevelopmentPhrase={speakDevelopmentPhrase}
        onEvaluateExaminerDecision={vi.fn()}
        onDeliverAuthorizedPrompt={deliverAuthorizedPrompt}
        onOpenConversation={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Dev transcript" }));
    fireEvent.click(screen.getByRole("button", { name: "Analyze + authorize" }));

    expect(screen.getByRole("button", { name: "Running..." })).toBeDisabled();
    await waitFor(() => {
      expect(screen.getByText("POLICY GATE RESULT")).toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/examiner/development-analyze-and-authorize",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ interview_session_id: "session-1" }),
      }),
    );
    expect(screen.getByText("LIVE EXAMINER RESULT")).toBeInTheDocument();
    expect(screen.getByText(/AUTHORIZED; decision AUTHORIZED; outcome AUTHORIZED/i))
      .toBeInTheDocument();
    expect(screen.getByText(/usefulness at analysis 4.0s; at gate 4.0s/i)).toBeInTheDocument();
    expect(screen.getByText(/delivery window OPEN until 2026-08-24T00:00:16Z/i))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Deliver authorized prompt" }));
    expect(deliverAuthorizedPrompt).toHaveBeenCalledWith("prompt-1");
    expect(speakDevelopmentPhrase).not.toHaveBeenCalled();
  });

  it("shows a safe Live Examiner structured-output failure without retaining a decision", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              category: "STRUCTURED_OUTPUT_INVALID",
              message: "Examiner returned an invalid structured decision. No decision was persisted.",
              retryable: false,
            },
          }),
          { status: 502 },
        ),
      ),
    );

    render(
      <InterviewerSurface
        voiceState="Listening"
        isMuted={false}
        voiceError={null}
        partialTranscript="partial"
        lastFinalTranscript="final"
        sessionDebug={observedRealtimeSession}
        canonicalDebug={observedCanonicalSession}
        currentTurn={demoInterviewFixture.currentDeliveredTurn}
        onEnableMicrophone={vi.fn()}
        onMute={vi.fn()}
        onUnmute={vi.fn()}
        onDisconnectVoice={vi.fn()}
        onSpeakDevelopmentPhrase={vi.fn()}
        onEvaluateExaminerDecision={vi.fn()}
        onDeliverAuthorizedPrompt={vi.fn()}
        onOpenConversation={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Dev transcript" }));
    fireEvent.click(screen.getByRole("button", { name: "Analyze latest observation" }));

    expect(await screen.findByText(/LIVE EXAMINER FAILED/i)).toHaveTextContent(
      "Structured Examiner output was invalid. No decision was created.",
    );
    expect(screen.queryByRole("button", { name: "Policy gate" })).not.toBeInTheDocument();
  });

  it("disables the reasoning smoke button while pending", async () => {
    let resolveRequest: (value: unknown) => void = () => undefined;
    const fetchPromise = new Promise((resolve) => {
      resolveRequest = resolve;
    });
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(fetchPromise));

    render(
      <InterviewerSurface
        voiceState="Listening"
        isMuted={false}
        voiceError={null}
        partialTranscript="partial"
        lastFinalTranscript="final"
        sessionDebug={observedRealtimeSession}
        canonicalDebug={observedCanonicalSession}
        currentTurn={demoInterviewFixture.currentDeliveredTurn}
        onEnableMicrophone={vi.fn()}
        onMute={vi.fn()}
        onUnmute={vi.fn()}
        onDisconnectVoice={vi.fn()}
        onSpeakDevelopmentPhrase={vi.fn()}
        onEvaluateExaminerDecision={vi.fn()}
        onDeliverAuthorizedPrompt={vi.fn()}
        onOpenConversation={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Dev transcript" }));
    fireEvent.click(screen.getByRole("button", { name: "Reasoning smoke" }));

    expect(screen.getByRole("button", { name: "Reasoning..." })).toBeDisabled();

    resolveRequest({
      ok: true,
      json: async () => ({
        invocation_id: "invocation-2",
        status: "SUCCEEDED",
        provider: "fake",
        model: "gpt-5.6-terra",
        capability: "STANDARD_REASONING",
        verdict: "UNCERTAIN",
        technical_note: "Smoke complete.",
        confidence: 0.5,
        latency_ms: 12,
        input_tokens: 1,
        cached_input_tokens: 0,
        output_tokens: 1,
        estimated_cost: null,
        currency: null,
        reasoning_budget_used: 1,
        reasoning_budget_remaining: 7,
      }),
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Reasoning smoke" })).not.toBeDisabled();
    });
  });

  it("does not call the reasoning endpoint during ordinary Interview Room render", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<InterviewRoom fixture={demoInterviewFixture} />);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("opens and closes recent conversation accessibly", () => {
    render(<InterviewRoom fixture={demoInterviewFixture} />);

    fireEvent.click(screen.getByRole("button", { name: "Recent conversation" }));
    const drawer = screen.getByRole("dialog", { name: "Recent conversation" });
    expect(within(drawer).getByText("Delivered turns")).toBeInTheDocument();
    expect(within(drawer).getByText(/Take a moment to read the problem/i)).toBeInTheDocument();
    expect(screen.queryByText(hiddenInternalFixtureFields.probeStrategy)).not.toBeInTheDocument();

    fireEvent.click(within(drawer).getByRole("button", { name: "Close recent conversation" }));
    expect(screen.queryByRole("dialog", { name: "Recent conversation" })).not.toBeInTheDocument();
  });

  it("opens and dismisses the End Interview confirmation", () => {
    render(<InterviewRoom fixture={demoInterviewFixture} />);

    fireEvent.click(screen.getByRole("button", { name: "End Interview" }));
    const dialog = screen.getByRole("dialog", { name: "End this interview?" });
    expect(within(dialog).getByText("Your current demo session will stop.")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Continue interview" }));
    expect(screen.queryByRole("dialog", { name: "End this interview?" })).not.toBeInTheDocument();
  });

  it("expands and collapses the execution placeholder without pretending to run code", () => {
    render(<InterviewRoom fixture={demoInterviewFixture} />);

    expect(screen.queryByText(/No code was compiled/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(screen.getByText(/No code was compiled, run, or judged/i)).toBeInTheDocument();
    expect(screen.getByText(/Hidden tests are not available here/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse execution area" }));
    expect(screen.queryByText(/No code was compiled/i)).not.toBeInTheDocument();
  });

  it("keeps splitter preference deterministic and bounded", () => {
    expect(clampProblemWidth(10)).toBe(28);
    expect(clampProblemWidth(99)).toBe(44);
    expect(writeStoredProblemWidth(window.localStorage, 41)).toBe(41);
    expect(window.localStorage.getItem(DEMO_SPLITTER_STORAGE_KEY)).toBe("41");
    expect(readStoredProblemWidth(window.localStorage)).toBe(41);
  });

  it("persists editor content only in the local demo layer", () => {
    const editedCode = "class Solution { public: int lengthOfLongestSubstring(string s) { return 0; } };";

    expect(readStoredEditorCode(window.localStorage, demoInterviewFixture.starterCode)).toBe(
      demoInterviewFixture.starterCode,
    );
    writeStoredEditorCode(window.localStorage, editedCode);
    expect(window.localStorage.getItem(DEMO_EDITOR_STORAGE_KEY)).toBe(editedCode);
    expect(readStoredEditorCode(window.localStorage, demoInterviewFixture.starterCode)).toBe(editedCode);
  });

  it("implements reduced-motion detection for the room", async () => {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query === reducedMotionQuery,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));

    render(<InterviewRoom fixture={demoInterviewFixture} />);

    await waitFor(() => {
      expect(screen.getByRole("main")).toHaveAttribute("data-reduced-motion", "reduce");
    });
  });
});
