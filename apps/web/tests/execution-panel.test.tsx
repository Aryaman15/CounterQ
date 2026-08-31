import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ExecutionPanel } from "@/features/interview-room/components/ExecutionPanel";

describe("ExecutionPanel", () => {
  it("shows bounded visible-result truth without implying hidden-test judgment", () => {
    render(
      <ExecutionPanel
        expanded
        hasAttemptedRun
        onRun={vi.fn()}
        onToggle={vi.fn()}
        result={{
          runKind: "VISIBLE",
          status: "SUCCEEDED",
          stdout: "",
          stderr: "",
          compilerOutput: "",
          timedOut: false,
          outputTruncated: false,
          cases: [
            {
              identifier: "visible-1",
              inputJson: { s: "abcabcbb" },
              expectedOutput: "3",
              actualOutput: "3",
              actualOutputValue: 3,
              comparisonKind: "EXPECTED",
              status: "PASSED",
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("Visible case 1")).toBeInTheDocument();
    expect(screen.getByText('Input {"s":"abcabcbb"}')).toBeInTheDocument();
    expect(screen.getByText("Expected 3 / Actual 3")).toBeInTheDocument();
    expect(screen.queryByText(/hidden tests passed/i)).not.toBeInTheDocument();
  });

  it("prevents duplicate run requests while one canonical execution is pending", () => {
    const onRun = vi.fn();
    render(
      <ExecutionPanel
        expanded={false}
        hasAttemptedRun={false}
        onRun={onRun}
        onToggle={vi.fn()}
        running
      />,
    );

    const run = screen.getByRole("button", { name: "Running" });
    expect(run).toBeDisabled();
    fireEvent.click(run);
    expect(onRun).not.toHaveBeenCalled();
  });

  it("renders compile diagnostics and output-boundary truth", () => {
    render(
      <ExecutionPanel
        expanded
        hasAttemptedRun
        onRun={vi.fn()}
        onToggle={vi.fn()}
        result={{
          runKind: "VISIBLE",
          status: "COMPILE_ERROR",
          stdout: "",
          stderr: "",
          compilerOutput: "candidate.cpp: error",
          timedOut: false,
          outputTruncated: true,
          cases: [],
        }}
      />,
    );

    expect(screen.getByText("Compile error")).toBeInTheDocument();
    expect(screen.getByLabelText("Compiler diagnostics")).toHaveTextContent("candidate.cpp: error");
    expect(screen.getByText("Output truncated.")).toBeInTheDocument();
  });

  it.each([
    ["int", 42],
    ["bool", true],
    ["string", "quoted \"value\""],
    ["int[]", [1, -2, 3]],
    ["string[]", ["a", "b"]],
    ["int[][]", [[1, 2], [], [3]]],
    ["string[][]", [["a"], [], ["b", "c"]]],
  ])("renders a custom %s result as JSON-safe execution truth", (_semanticType, value) => {
    render(
      <ExecutionPanel
        expanded
        hasAttemptedRun
        onRun={vi.fn()}
        onToggle={vi.fn()}
        result={{
          runKind: "CUSTOM",
          status: "SUCCEEDED",
          stdout: "",
          stderr: "",
          compilerOutput: "",
          timedOut: false,
          outputTruncated: false,
          cases: [
            {
              identifier: "custom-1",
              inputJson: { first: [1, 2], second: "value" },
              expectedOutput: null,
              actualOutput: JSON.stringify(value),
              actualOutputValue: value,
              comparisonKind: "NONE",
              status: "EXECUTED",
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("Latest custom execution result.")).toBeInTheDocument();
    expect(screen.getByText("Executed")).toBeInTheDocument();
    expect(screen.getByText(`Input ${JSON.stringify({ first: [1, 2], second: "value" })}`)).toBeInTheDocument();
    expect(screen.getByText(`Output ${JSON.stringify(value)}`)).toBeInTheDocument();
    expect(screen.queryByText("Passed")).not.toBeInTheDocument();
    expect(screen.queryByText(/Expected/)).not.toBeInTheDocument();
  });

  it("does not turn an unvalidated custom output into a correctness verdict", () => {
    render(
      <ExecutionPanel
        expanded
        hasAttemptedRun
        onRun={vi.fn()}
        onToggle={vi.fn()}
        result={{
          runKind: "CUSTOM",
          status: "SUCCEEDED",
          stdout: "",
          stderr: "",
          compilerOutput: "",
          timedOut: false,
          outputTruncated: false,
          cases: [
            {
              identifier: "custom-1",
              inputJson: { value: 1 },
              expectedOutput: null,
              actualOutput: "true",
              actualOutputValue: true,
              comparisonKind: "NONE",
              status: "FAILED",
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("Execution failed")).toBeInTheDocument();
    expect(screen.queryByText("Passed")).not.toBeInTheDocument();
    expect(screen.queryByText("Failed")).not.toBeInTheDocument();
    expect(screen.queryByText(/Expected/)).not.toBeInTheDocument();
  });
});
