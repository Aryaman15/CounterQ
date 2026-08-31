"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, LoaderCircle, Play } from "lucide-react";

export type ExecutionViewResult = {
  runKind: "VISIBLE" | "CUSTOM";
  status: string;
  stdout: string;
  stderr: string;
  compilerOutput: string;
  timedOut: boolean;
  outputTruncated: boolean;
  cases: Array<{
    identifier: string;
    inputJson: Record<string, unknown>;
    expectedOutput: string | null;
    actualOutput: string | null;
    expectedOutputValue?: unknown;
    actualOutputValue: unknown;
    comparisonKind: "EXPECTED" | "NONE";
    status: string;
  }>;
};

type ExecutionPanelProps = {
  expanded: boolean;
  onToggle: () => void;
  onRun: () => void;
  hasAttemptedRun: boolean;
  running?: boolean;
  result?: ExecutionViewResult | null;
  error?: string | null;
  disabled?: boolean;
  customTestSupported?: boolean;
  argumentSchema?: Array<Record<string, unknown>>;
  onRunCustom?: (argumentsValue: Record<string, unknown>) => void;
};

export function ExecutionPanel({
  expanded,
  onToggle,
  onRun,
  hasAttemptedRun,
  running = false,
  result = null,
  error = null,
  disabled = false,
  customTestSupported = false,
  argumentSchema = [],
  onRunCustom,
}: ExecutionPanelProps) {
  const [customJson, setCustomJson] = useState("{}");
  const [customError, setCustomError] = useState<string | null>(null);

  const runCustom = () => {
    try {
      const parsed: unknown = JSON.parse(customJson);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("Custom arguments must be a JSON object.");
      }
      setCustomError(null);
      onRunCustom?.(parsed as Record<string, unknown>);
    } catch (parseError) {
      setCustomError(
        parseError instanceof Error ? parseError.message : "Enter a valid JSON object.",
      );
    }
  };

  return (
    <section className="execution-panel" aria-labelledby="execution-title">
      <div className="execution-bar">
        <div>
          <h2 id="execution-title">Execution</h2>
          <p>{executionSummary({ running, hasAttemptedRun, result })}</p>
        </div>
        <div className="execution-actions">
          <button type="button" className="run-button" onClick={onRun} disabled={disabled || running}>
            {running ? <LoaderCircle className="run-spinner" size={15} aria-hidden="true" /> : <Play size={15} aria-hidden="true" />}
            <span>{running ? "Running" : "Run"}</span>
          </button>
          <button
            type="button"
            className="icon-button"
            onClick={onToggle}
            disabled={disabled}
            aria-expanded={expanded}
            aria-controls="execution-details"
            aria-label={expanded ? "Collapse execution area" : "Expand execution area"}
          >
            {expanded ? <ChevronUp size={17} aria-hidden="true" /> : <ChevronDown size={17} aria-hidden="true" />}
          </button>
        </div>
      </div>
      {expanded ? (
        <div id="execution-details" className="execution-details">
          {error ? <p className="execution-unavailable" role="alert">{error}</p> : null}
          {result ? (
            <div className="execution-result">
              <p><strong>Compile / Run</strong><span data-status={result.status}>{executionStatusLabel(result)}</span></p>
              {result.cases.map((testCase) => (
                <div className="execution-case" key={testCase.identifier}>
                  <span>{result.runKind === "CUSTOM" ? "Custom test" : testCase.identifier.replace("visible-", "Visible case ")}</span>
                  <span>{caseStatusLabel(testCase.comparisonKind, testCase.status)}</span>
                  <code>Input {JSON.stringify(testCase.inputJson)}</code>
                  {testCase.comparisonKind === "NONE" ? (
                    <code>Output {formatJsonValue(testCase.actualOutputValue, testCase.actualOutput)}</code>
                  ) : (
                    <code>
                      Expected {formatJsonValue(testCase.expectedOutputValue, testCase.expectedOutput)} / Actual {formatJsonValue(testCase.actualOutputValue, testCase.actualOutput)}
                    </code>
                  )}
                </div>
              ))}
              {result.compilerOutput ? <pre aria-label="Compiler diagnostics">{result.compilerOutput}</pre> : null}
              {result.stderr ? <pre aria-label="Runtime diagnostics">{result.stderr}</pre> : null}
              {result.outputTruncated ? <p>Output truncated.</p> : null}
            </div>
          ) : running ? (
            <p>No code was compiled, run, or judged yet. Hidden tests are not available here.</p>
          ) : !error ? (
            <p>No code was compiled, run, or judged. Hidden tests are not available here.</p>
          ) : null}
          {customTestSupported ? (
            <div className="custom-test-controls">
              <label htmlFor="custom-test-json">Custom test arguments</label>
              <p>
                JSON object matching: {argumentSchema.map((argument) => `${String(argument.name)}: ${String(argument.type)}`).join(", ")}
              </p>
              <textarea
                id="custom-test-json"
                value={customJson}
                onChange={(event) => setCustomJson(event.currentTarget.value)}
                spellCheck={false}
              />
              {customError ? <p role="alert" className="execution-unavailable">{customError}</p> : null}
              <button type="button" onClick={runCustom} disabled={disabled || running}>
                Run custom test
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function executionStatusLabel(result: ExecutionViewResult): string {
  if (result.timedOut) return "Timed out";
  if (result.status === "COMPILE_ERROR") return "Compile error";
  if (result.status === "RUNTIME_ERROR") return "Runtime error";
  if (result.status === "OUTPUT_LIMIT_EXCEEDED") return "Output limit reached";
  if (result.status === "PROVIDER_ERROR") return "Execution unavailable";
  return "Completed";
}

function executionSummary({
  running,
  hasAttemptedRun,
  result,
}: {
  running: boolean;
  hasAttemptedRun: boolean;
  result: ExecutionViewResult | null;
}): string {
  if (running) return "Running exact canonical source...";
  if (result?.runKind === "CUSTOM") return "Latest custom execution result.";
  if (result?.runKind === "VISIBLE") return "Latest visible execution result.";
  return hasAttemptedRun ? "No execution result." : "Collapsed until needed.";
}

function caseStatusLabel(comparisonKind: "EXPECTED" | "NONE", status: string): string {
  if (comparisonKind === "NONE") {
    if (status === "EXECUTED") return "Executed";
    if (status === "NOT_RUN") return "Not run";
    return "Execution failed";
  }
  if (status === "PASSED") return "Passed";
  if (status === "NOT_RUN") return "Not run";
  return "Failed";
}

function formatJsonValue(value: unknown, encodedFallback: string | null): string {
  if (value !== undefined && value !== null) {
    return JSON.stringify(value);
  }
  return encodedFallback ?? "-";
}
