"use client";

import type { components } from "@counterq/contracts/openapi";
import { RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { MasteryExperience } from "./MasteryExperience";

type DemoFixture = components["schemas"]["DevelopmentMasteryFixtureResponse"];

export function MasteryDemo() {
  const [fixtures, setFixtures] = useState<DemoFixture[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}/api/mastery/development/fixtures`,
        { signal, cache: "no-store" },
      );
      if (!response.ok) throw new Error("Mastery fixtures unavailable");
      const result = (await response.json()) as DemoFixture[];
      setFixtures(result);
      setSelectedId((current) => current ?? result[0]?.fixture_id ?? null);
      setFailed(false);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const selected = fixtures.find((fixture) => fixture.fixture_id === selectedId) ?? fixtures[0];
  return (
    <main className="mastery-demo">
      <header className="mastery-demo-header">
        <div className="report-wordmark"><span aria-hidden="true">CQ</span> CounterQ</div>
        <p className="mastery-kicker">Mastery · Cross-session evidence</p>
        <h1>What holds up when the context changes.</h1>
        <p>{selected?.description ?? "A deterministic projection of canonical evidence across interviews."}</p>
        {fixtures.length ? (
          <nav className="mastery-fixture-switcher" aria-label="Mastery demo scenarios">
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
      {selected ? (
        <MasteryExperience overview={selected.overview} />
      ) : failed ? (
        <section className="mastery-system-state" role="status">
          <ShieldCheck size={22} aria-hidden="true" />
          <h2>Mastery is temporarily unavailable.</h2>
          <p>Your canonical Evidence, reports, and CounterMaps remain unchanged.</p>
          <button type="button" onClick={() => void load()}><RefreshCw size={14} /> Try again</button>
        </section>
      ) : (
        <section className="mastery-system-state" role="status" aria-live="polite">
          <span className="mastery-loading-mark" aria-hidden="true" />
          <h2>Recalculating from canonical Evidence</h2>
          <p>CounterQ is applying the deterministic Mastery policy across sessions.</p>
        </section>
      )}
    </main>
  );
}
