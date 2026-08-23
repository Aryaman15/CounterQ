"use client";

import { ChevronDown, ChevronUp, Play } from "lucide-react";

type ExecutionPanelProps = {
  expanded: boolean;
  onToggle: () => void;
  onRun: () => void;
  hasAttemptedRun: boolean;
};

export function ExecutionPanel({ expanded, onToggle, onRun, hasAttemptedRun }: ExecutionPanelProps) {
  return (
    <section className="execution-panel" aria-labelledby="execution-title">
      <div className="execution-bar">
        <div>
          <h2 id="execution-title">Execution</h2>
          <p>{hasAttemptedRun ? "Provider not connected for this visual preview." : "Collapsed until needed."}</p>
        </div>
        <div className="execution-actions">
          <button type="button" className="run-button" onClick={onRun}>
            <Play size={15} aria-hidden="true" />
            <span>Run</span>
          </button>
          <button
            type="button"
            className="icon-button"
            onClick={onToggle}
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
          <p className="execution-unavailable">Execution provider not connected in this Stage 1.3 visual preview.</p>
          <p>No code was compiled, run, or judged. Hidden tests are not available here.</p>
        </div>
      ) : null}
    </section>
  );
}
