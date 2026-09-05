"use client";

import type { components } from "@counterq/contracts/openapi";
import { Network, RotateCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { CounterMapSurface } from "./CounterMapSurface";

type DemoFixture = components["schemas"]["DevelopmentCounterMapFixtureResponse"];

export function CounterMapDemo() {
  const [fixtures, setFixtures] = useState<DemoFixture[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [requestFailed, setRequestFailed] = useState(false);
  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}/api/countermap/development/fixtures`,
        { signal, cache: "no-store" },
      );
      if (!result.ok) throw new Error("CounterMap fixtures unavailable");
      const next = (await result.json()) as DemoFixture[];
      setFixtures(next);
      setSelectedId((current) => current ?? next[0]?.fixture_id ?? null);
      setRequestFailed(false);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setRequestFailed(true);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const selected = fixtures.find((fixture) => fixture.fixture_id === selectedId) ?? fixtures[0];
  return (
    <main className="countermap-demo">
      <header className="countermap-demo-header">
        <div className="report-wordmark">
          <span aria-hidden="true">CQ</span> CounterQ
        </div>
        <p className="countermap-kicker">CounterMap · Interview reconstruction</p>
        <h1>The interview, mapped to the moment.</h1>
        <p>
          {selected?.description
            ?? "Production projection over deterministic canonical development fixtures."}
        </p>
        {fixtures.length ? (
          <nav className="countermap-fixture-switcher" aria-label="CounterMap demo fixtures">
            {fixtures.map((fixture) => (
              <button
                type="button"
                key={fixture.fixture_id}
                aria-pressed={selected?.fixture_id === fixture.fixture_id}
                onClick={() => setSelectedId(fixture.fixture_id)}
              >
                {fixture.label}
              </button>
            ))}
          </nav>
        ) : null}
      </header>
      <section className="countermap-demo-map" aria-labelledby="countermap-demo-title">
        {selected ? (
          <>
            <div className="countermap-demo-summary">
              <p>Evidence-backed causal projection</p>
              <h2 id="countermap-demo-title">{selected.graph.summary.title}</h2>
              <span>{selected.graph.summary.overview}</span>
            </div>
            <CounterMapSurface
              graph={selected.graph}
              detailUrlForNode={(nodeId) => (
                `${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}`
                + `/api/countermap/development/fixtures/${selected.fixture_id}`
                + `/nodes/${encodeURIComponent(nodeId)}`
              )}
            />
          </>
        ) : requestFailed ? (
          <div className="countermap-state countermap-state-failed" role="status">
            <ShieldCheck size={22} aria-hidden="true" />
            <h2 id="countermap-demo-title">The deterministic preview is unavailable.</h2>
            <p>Enable the development spike API, then try again.</p>
            <button type="button" onClick={() => void load()}>
              <RotateCw size={15} /> Try again
            </button>
          </div>
        ) : (
          <div className="countermap-state countermap-state-loading" role="status" aria-live="polite">
            <Network size={22} aria-hidden="true" />
            <h2 id="countermap-demo-title">Projecting canonical fixtures</h2>
            <p>The production projector and validator are reconstructing each reasoning map.</p>
          </div>
        )}
      </section>
    </main>
  );
}
