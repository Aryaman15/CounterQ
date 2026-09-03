import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExecutionPanel } from "@/features/interview-room/components/ExecutionPanel";
import { InterviewRoom } from "@/features/interview-room/components/InterviewRoom";
import { InterviewSetup } from "@/features/interview-room/components/InterviewSetup";
import { demoInterviewFixture } from "@/features/interview-room/fixtures/demoInterview";
import {
  readStoredEditorCode,
  resolveDevelopmentEditorSource,
  writeStoredEditorCode,
} from "@/features/interview-room/hooks/localPersistence";
import type { RealtimeVoiceControls } from "@/features/interview-room/realtime/useRealtimeVoice";
import type { DevelopmentBootstrapResponse } from "@/features/interview-room/realtime/RealtimeControlClient";

vi.mock("@monaco-editor/react", () => ({
  default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
    <textarea
      aria-label="Code editor"
      data-testid="mock-monaco-editor"
      value={value}
      onChange={(event) => onChange(event.currentTarget.value)}
    />
  ),
}));

const bootstrap: DevelopmentBootstrapResponse = {
  interview_session_id: "session-two-sum",
  language: "python",
  problem: {
    problem_version_id: "problem-version-two-sum-v1",
    slug: "two-sum",
    title: "Two Sum",
    supported_languages: ["cpp", "python", "java"],
    catalog_order: 1,
    statement: "Return the indices of two values whose sum equals target.",
    constraints: ["2 <= nums.length <= 10^4"],
    examples: [{ input: "nums = [2,7], target = 9", output: "[0,1]", explanation: "2 + 7 = 9" }],
    selected_language: "python",
    display_signature: "def twoSum(self, nums: list[int], target: int) -> list[int]",
    starter_code: "class Solution:\n    def twoSum(self, nums, target):\n        pass",
    argument_schema: [{ name: "nums", type: "int[]" }, { name: "target", type: "int" }],
    return_type: "int[]",
    comparator: "EXACT",
    custom_test_supported: true,
  },
  template: "STANDARD_CODING_INTERVIEW",
  configured_duration_seconds: 1800,
  mode: "SIMULATION",
  current_stage: "IMPLEMENTATION",
  session_status: "ACTIVE",
  state_version: 0,
  deadline_at: "2099-08-31T12:30:00Z",
  time_remaining_seconds: 1800,
  time_pressure: "NORMAL",
  control_websocket_path: "/api/realtime/control/session-two-sum",
  restoration: "CREATED",
  restore_protocol_version: "session.restore.v1",
  started_at: "2099-08-31T12:00:00Z",
  completed_at: null,
  terminal_reason: null,
  latest_code_snapshot: null,
  recent_conversation: [],
  unresolved_prompt: null,
  highest_client_sequence: 0,
  last_server_sequence: 0,
  protocol_version: "counterq.realtime.control.v1",
};

function controls(value: DevelopmentBootstrapResponse = bootstrap): RealtimeVoiceControls {
  return {
    voiceState: "Ready",
    isMuted: false,
    errorMessage: null,
    partialTranscript: "",
    lastFinalTranscript: "",
    currentCounterQDeliveryText: "",
    sessionDebug: {
      eventType: null,
      sessionType: null,
      transcriptionModel: null,
      turnDetectionType: null,
      createResponse: null,
      interruptResponse: null,
    },
    canonicalDebug: {
      sessionId: value.interview_session_id,
      controlConnected: true,
      pendingDurableMessages: 0,
      lastServerSequence: 0,
      stateVersion: 0,
      probeBudgetUsed: 0,
      probeBudgetMax: 5,
      lastCandidateFinal: { providerItemId: null, eventId: null, transcriptSegmentId: null, persistence: "PENDING" },
      lastDelivery: { promptId: null, deliveryId: null, deliveryState: null, providerResponseId: null, actualTranscriptId: null, localPlaybackState: "NOT_STARTED", canonicalState: null, outputTranscriptState: "NONE", pendingTerminalEvent: "NONE", lifecycleEvents: [] },
      lastObservation: { kind: null, sourceEventId: null, sourceEventWatermark: null, stateVersion: null, stage: null, triggerClass: null },
      lastCode: { snapshotId: null, version: null, hashPrefix: null, diffId: null, persistence: "PENDING" },
      lastVoice: { transcriptSegmentId: null, associatedCodeSnapshotId: null, associatedCodeSnapshotVersion: null },
      lastPolicyGate: { decisionId: null, disposition: null, decisionStatus: null, policyGateOutcome: null, promptId: null, promptKind: null },
      lastDeliveryPermit: { promptId: null, status: null, reason: null },
    },
    serverDeadlineAt: value.deadline_at,
    restoredBootstrap: value,
    isRestoring: false,
    controlReconnecting: false,
    acknowledgedCodeSource: value.latest_code_snapshot?.source_code ?? null,
    terminalSession: null,
    completionPending: false,
    endInterview: vi.fn(),
    completeForDeadline: vi.fn(),
    ensureControlSession: vi.fn(async () => value),
    startInterview: vi.fn(async () => value),
    enableMicrophone: vi.fn(async () => undefined),
    mute: vi.fn(),
    unmute: vi.fn(),
    disconnect: vi.fn(),
    speakDevelopmentPhrase: vi.fn(),
    evaluateExaminerDecision: vi.fn(),
    deliverAuthorizedPrompt: vi.fn(),
    observeCodeSnapshot: vi.fn(),
    noteCodeActivityStarted: vi.fn(),
    noteCodeActivityIdle: vi.fn(),
  };
}

describe("curated interview journey", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("renders catalog order and starts with the exact selected version and language", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        { problem_version_id: "v2", slug: "binary-search", title: "Binary Search", supported_languages: ["cpp", "python", "java"], catalog_order: 2 },
        { problem_version_id: "v1", slug: "two-sum", title: "Two Sum", supported_languages: ["cpp", "python", "java"], catalog_order: 1 },
      ],
    }));
    const onStart = vi.fn(async () => undefined);
    render(<InterviewSetup busy={false} error={null} onStart={onStart} />);

    await screen.findByText("Two Sum");
    const labels = screen.getAllByRole("radio", { name: /Sum|Search/ });
    expect(labels[0]).toHaveAccessibleName(/Two Sum/);
    fireEvent.click(screen.getByRole("radio", { name: /Two Sum/ }));
    fireEvent.click(screen.getByRole("radio", { name: "Python 3" }));
    expect(screen.getByRole("radio", { name: "Simulation" })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Start Interview" }));
    expect(onStart).toHaveBeenLastCalledWith("v1", "python", "SIMULATION");
    fireEvent.click(screen.getByRole("radio", { name: "Coach" }));
    fireEvent.click(screen.getByRole("button", { name: "Start Interview" }));
    expect(onStart).toHaveBeenLastCalledWith("v1", "python", "COACH");
  });

  it("renders exact bootstrap problem and starter, with immutable room language", async () => {
    render(
      <InterviewRoom
        fixture={demoInterviewFixture}
        allowFixturePreview={false}
        realtimeVoiceOverride={controls()}
      />,
    );
    expect(screen.getByRole("heading", { name: "Two Sum" })).toBeInTheDocument();
    expect(screen.getByText(/Return the indices of two values/i)).toBeInTheDocument();
    expect(screen.getByText(bootstrap.problem.display_signature)).toBeInTheDocument();
    expect(await screen.findByTestId("mock-monaco-editor")).toHaveValue(bootstrap.problem.starter_code);
    expect(screen.queryByRole("combobox", { name: /language/i })).not.toBeInTheDocument();
    expect(screen.getByText("Python 3")).toBeInTheDocument();
  });

  it("prefers canonical restored code and sends visible/custom executions correctly", async () => {
    const restored = {
      ...bootstrap,
      restoration: "RESTORED" as const,
      latest_code_snapshot: {
        id: "snapshot-4",
        version_number: 4,
        language: "python",
        source_code: "class Solution:\n    def twoSum(self, nums, target):\n        return [0, 1]",
        content_hash: "hash-4",
      },
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        execution_run_id: "run-1",
        code_snapshot_id: "snapshot-5",
        code_snapshot_version: 5,
        run_kind: "CUSTOM",
        status: "SUCCEEDED",
        stdout: "",
        stderr: "",
        compiler_output: "",
        exit_code: 0,
        timed_out: false,
        output_truncated: false,
        duration_ms: 4,
        cases: [{ identifier: "custom-1", input_json: { nums: [2, 7], target: 9 }, expected_output: null, actual_output: "[0,1]", expected_output_value: null, actual_output_value: [0, 1], comparison_kind: "NONE", status: "EXECUTED", duration_ms: 1, failure_classification: null }],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<InterviewRoom fixture={demoInterviewFixture} allowFixturePreview={false} realtimeVoiceOverride={controls(restored)} />);

    expect(await screen.findByTestId("mock-monaco-editor")).toHaveValue(restored.latest_code_snapshot.source_code);
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({ run_kind: "VISIBLE" });

    fireEvent.change(screen.getByLabelText("Custom test arguments"), {
      target: { value: '{"nums":[2,7],"target":9}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run custom test" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toMatchObject({
      run_kind: "CUSTOM",
      custom_arguments: { nums: [2, 7], target: 9 },
    });
    expect(await screen.findByText("Executed")).toBeInTheDocument();
    expect(screen.getByText("Latest custom execution result.")).toBeInTheDocument();
    expect(screen.queryByText("Latest visible execution result.")).not.toBeInTheDocument();
    expect(screen.getByText(/Output \[0,1\]/)).toBeInTheDocument();
    expect(screen.queryByText("Passed")).not.toBeInTheDocument();
    expect(screen.queryByText(/Expected/)).not.toBeInTheDocument();
  });

  it("does not send invalid custom JSON and hides unsupported controls", () => {
    const onRunCustom = vi.fn();
    const { rerender } = render(
      <ExecutionPanel expanded hasAttemptedRun={false} onRun={vi.fn()} onToggle={vi.fn()} customTestSupported argumentSchema={bootstrap.problem.argument_schema} onRunCustom={onRunCustom} />,
    );
    fireEvent.change(screen.getByLabelText("Custom test arguments"), { target: { value: "[]" } });
    fireEvent.click(screen.getByRole("button", { name: "Run custom test" }));
    expect(onRunCustom).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("JSON object");

    rerender(<ExecutionPanel expanded hasAttemptedRun={false} onRun={vi.fn()} onToggle={vi.fn()} customTestSupported={false} />);
    expect(screen.queryByLabelText("Custom test arguments")).not.toBeInTheDocument();
  });

  it("surfaces safe custom validation detail and keeps infrastructure failures generic", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: async () => ({ detail: "missing arguments: target" }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        json: async () => ({ detail: "SQL connection refused at internal-host" }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <InterviewRoom
        fixture={demoInterviewFixture}
        allowFixturePreview={false}
        realtimeVoiceOverride={controls()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Expand execution area" }));
    fireEvent.change(screen.getByLabelText("Custom test arguments"), {
      target: { value: '{"nums":[2,7]}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run custom test" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("missing arguments: target");

    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Code execution is temporarily unavailable.",
    );
    expect(screen.queryByText(/SQL connection refused/i)).not.toBeInTheDocument();
  });

  it("never hydrates one session's pending source into another session", () => {
    writeStoredEditorCode(window.localStorage, "problem A pending source", {
      language: "python",
      interviewSessionId: "problem-a-session",
    });

    const problemBLocalSource = readStoredEditorCode(window.localStorage, "", {
      language: "python",
      interviewSessionId: "problem-b-session",
    });
    expect(
      resolveDevelopmentEditorSource({
        canonicalSourceCode: null,
        localSourceCode: problemBLocalSource,
        starterCode: "problem B exact starter",
      }),
    ).toBe("problem B exact starter");
  });
});
