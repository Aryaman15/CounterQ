"use client";

import type { components } from "@counterq/contracts/openapi";
import { Network, RotateCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { CounterMapSurface } from "./CounterMapSurface";

type CounterMapResponse = components["schemas"]["CandidateCounterMapResponse"];
type CounterMapInspection = components["schemas"]["DevelopmentCounterMapInspection"];
type CounterMapDetail = components["schemas"]["CandidateCounterMapNodeDetailResponse"];

export type CounterMapTransport = {
  loadCounterMap: (signal?: AbortSignal) => Promise<CounterMapResponse>;
  loadNodeDetail: (nodeId: string, signal?: AbortSignal) => Promise<CounterMapDetail>;
  loadInspection?: (signal?: AbortSignal) => Promise<CounterMapInspection>;
};

export function CounterMapExperience({
  interviewSessionId,
  transport,
  pollIntervalMs = 1600,
}: {
  interviewSessionId: string;
  transport: CounterMapTransport;
  pollIntervalMs?: number;
}) {
  const [response, setResponse] = useState<CounterMapResponse | null>(null);
  const [requestFailed, setRequestFailed] = useState(false);
  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await transport.loadCounterMap(signal);
      setResponse(next);
      setRequestFailed(false);
      return next.status;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return null;
      setRequestFailed(true);
      return null;
    }
  }, [transport]);

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
    <section
      className="countermap-experience"
      aria-labelledby="countermap-title"
      data-interview-session-id={interviewSessionId}
    >
      <header className="countermap-header">
        <div>
          <p className="countermap-kicker">CounterMap · Session causality</p>
          <h2 id="countermap-title">How your interview unfolded</h2>
          <p>Only moments that directly shaped the interview appear here.</p>
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
          loadNodeDetail={transport.loadNodeDetail}
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
      {transport.loadInspection ? (
        <DevelopmentCounterMapInspector loadInspection={transport.loadInspection} />
      ) : null}
    </section>
  );
}

function DevelopmentCounterMapInspector({
  loadInspection,
}: {
  loadInspection: (signal?: AbortSignal) => Promise<CounterMapInspection>;
}) {
  const [inspection, setInspection] = useState<CounterMapInspection | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      setInspection(await loadInspection(controller.signal));
    };
    void load().catch(() => undefined);
    return () => controller.abort();
  }, [loadInspection]);
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
