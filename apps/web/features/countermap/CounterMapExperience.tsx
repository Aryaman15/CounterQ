"use client";

import type { components } from "@counterq/contracts/openapi";
import { Network, RotateCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { CounterMapSurface } from "./CounterMapSurface";

type CounterMapResponse = components["schemas"]["CandidateCounterMapResponse"];
type CounterMapInspection = components["schemas"]["DevelopmentCounterMapInspection"];

export function CounterMapExperience({
  interviewSessionId,
  pollIntervalMs = 1600,
}: {
  interviewSessionId: string;
  pollIntervalMs?: number;
}) {
  const [response, setResponse] = useState<CounterMapResponse | null>(null);
  const [requestFailed, setRequestFailed] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}/api/countermap/sessions/${interviewSessionId}`,
        { signal, cache: "no-store" },
      );
      if (!result.ok) throw new Error("CounterMap status unavailable");
      const next = await result.json() as CounterMapResponse;
      setResponse(next);
      setRequestFailed(false);
      return next.status;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return null;
      setRequestFailed(true);
      return null;
    }
  }, [interviewSessionId]);

  useEffect(() => {
    const controller = new AbortController();
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      const next = await load(controller.signal);
      if (!controller.signal.aborted && (next === "BUILDING" || next === "STALE")) {
        timeout = setTimeout(poll, pollIntervalMs);
      }
    };
    void poll();
    return () => {
      controller.abort();
      if (timeout) clearTimeout(timeout);
    };
  }, [load, pollIntervalMs]);

  const state = requestFailed ? "FAILED" : response?.status;
  return (
    <section className="countermap-experience" aria-labelledby="countermap-title">
      <header className="countermap-header">
        <div>
          <p className="countermap-kicker">CounterMap · Session causality</p>
          <h2 id="countermap-title">How your interview unfolded</h2>
          <p>Only material moments with canonical causal support appear here.</p>
        </div>
        <Network size={28} aria-hidden="true" />
      </header>
      {state === "FAILED" ? (
        <div className="countermap-state countermap-state-failed" role="status">
          <ShieldCheck size={22} aria-hidden="true" />
          <h3>CounterMap is unavailable for this interview.</h3>
          <p>{response?.message ?? "Your report and interview evidence are still safe."}</p>
          {requestFailed ? (
            <button type="button" onClick={() => void load()}><RotateCw size={15} /> Try again</button>
          ) : null}
        </div>
      ) : response?.status === "READY" && response.graph ? (
        <CounterMapSurface
          graph={response.graph}
          detailUrlForNode={(nodeId) => (
            `${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}`
            + `/api/countermap/sessions/${interviewSessionId}/nodes/${encodeURIComponent(nodeId)}`
          )}
        />
      ) : response?.status === "NOT_AVAILABLE" ? (
        <div className="countermap-state countermap-state-empty" role="status">
          <Network size={22} aria-hidden="true" />
          <h3>No reasoning map was prepared.</h3>
          <p>{response.message}</p>
        </div>
      ) : (
        <div className="countermap-state countermap-state-loading" role="status" aria-live="polite">
          <div className="countermap-skeleton" aria-hidden="true"><span /><span /><span /></div>
          <h3>{response?.status === "STALE" ? "Updating your reasoning map" : "Tracing the evidence-backed story"}</h3>
          <p>{response?.message ?? "CounterQ is connecting what you said, built, tested, and demonstrated."}</p>
        </div>
      )}
      {process.env.NODE_ENV === "development" ? (
        <DevelopmentCounterMapInspector interviewSessionId={interviewSessionId} />
      ) : null}
    </section>
  );
}

function DevelopmentCounterMapInspector({ interviewSessionId }: { interviewSessionId: string }) {
  const [inspection, setInspection] = useState<CounterMapInspection | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      const result = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}/api/countermap/development/sessions/${interviewSessionId}/inspection`,
        { signal: controller.signal, cache: "no-store" },
      );
      if (result.ok) setInspection(await result.json() as CounterMapInspection);
    };
    void load().catch(() => undefined);
    return () => controller.abort();
  }, [interviewSessionId]);
  if (!inspection) return null;
  return (
    <details className="development-countermap-inspector">
      <summary>Development · CounterMap pipeline</summary>
      <dl>
        <div><dt>Projection</dt><dd>{inspection.projection_status} · v{inspection.projection_version ?? "—"}</dd></div>
        <div><dt>Schema</dt><dd>{inspection.schema_version ?? "—"}</dd></div>
        <div><dt>Policy</dt><dd>{inspection.generation_policy_version ?? "—"}</dd></div>
        <div><dt>Source watermark</dt><dd>{inspection.source_watermark ?? "—"}</dd></div>
        <div><dt>Graph</dt><dd>{inspection.node_count} nodes · {inspection.edge_count} edges</dd></div>
        <div><dt>Validation</dt><dd>{inspection.validation_outcome}</dd></div>
        <div><dt>Outbox</dt><dd>{inspection.outbox_generation_state}</dd></div>
        <div><dt>Failure</dt><dd>{inspection.last_failure_category ?? "—"}</dd></div>
      </dl>
    </details>
  );
}
