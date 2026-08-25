"use client";

import { ChevronDown, ChevronUp, LoaderCircle, Play } from "lucide-react";

export type ExecutionViewResult = {
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
}: ExecutionPanelProps) {
  return (
    <section className="execution-panel" aria-labelledby="execution-title">
      <div className="execution-bar">
        <div>
          <h2 id="execution-title">Execution</h2>
          <p>{running ? "Running exact canonical source..." : hasAttemptedRun ? "Latest visible execution result." : "Collapsed until needed."}</p>
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
                  <span>{testCase.identifier.replace("visible-", "Visible case ")}</span>
                  <span>{testCase.status === "PASSED" ? "Passed" : "Failed"}</span>
                  <code>Input {JSON.stringify(testCase.inputJson)}</code>
                  <code>Expected {testCase.expectedOutput ?? "-"} / Actual {testCase.actualOutput ?? "-"}</code>
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
