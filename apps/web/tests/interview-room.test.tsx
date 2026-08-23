import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Home from "../app/page";
import InterviewDemoPage from "../app/interview/demo/page";
import { InterviewRoom } from "../features/interview-room/components/InterviewRoom";
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

  it("renders the Interview Room route and Monaco surface", async () => {
    render(<InterviewDemoPage />);

    expect(screen.getByTestId("monaco-editor-surface")).toBeInTheDocument();
    expect(await screen.findByLabelText("C++ code editor")).toBeInTheDocument();
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
        currentTurn={demoInterviewFixture.currentDeliveredTurn}
        onEnableMicrophone={noop}
        onMute={noop}
        onUnmute={noop}
        onDisconnectVoice={noop}
        onSpeakDevelopmentPhrase={noop}
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
    expect(screen.getByText("Listening")).toBeInTheDocument();

    rerender(
      <InterviewerSurface
        voiceState="Listening"
        isMuted={false}
        voiceError={null}
        partialTranscript=""
        lastFinalTranscript="Final transcript arrived."
        currentTurn={demoInterviewFixture.currentDeliveredTurn}
        onEnableMicrophone={noop}
        onMute={noop}
        onUnmute={noop}
        onDisconnectVoice={noop}
        onSpeakDevelopmentPhrase={noop}
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
