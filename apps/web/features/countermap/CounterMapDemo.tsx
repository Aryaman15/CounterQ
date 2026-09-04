"use client";

import { useState } from "react";

import { ReasoningTimeline } from "./ReasoningTimeline";
import { counterMapDemoFixtures } from "./demoFixtures";

export function CounterMapDemo() {
  const [selectedId, setSelectedId] = useState(counterMapDemoFixtures[0].id);
  const selected = counterMapDemoFixtures.find((fixture) => fixture.id === selectedId) ?? counterMapDemoFixtures[0];
  return (
    <main className="countermap-demo">
      <header className="countermap-demo-header">
        <div className="report-wordmark"><span aria-hidden="true">CQ</span> CounterQ</div>
        <p className="countermap-kicker">Stage 7A · Deterministic preview</p>
        <h1>Reasoning, reconstructed from evidence.</h1>
        <p>{selected.description}</p>
        <nav className="countermap-fixture-switcher" aria-label="CounterMap demo fixtures">
          {counterMapDemoFixtures.map((fixture) => (
            <button
              type="button"
              key={fixture.id}
              aria-pressed={selected.id === fixture.id}
              onClick={() => setSelectedId(fixture.id)}
            >
              {fixture.label}
            </button>
          ))}
        </nav>
      </header>
      <section className="countermap-demo-map" aria-labelledby="countermap-demo-title">
        <div className="countermap-demo-summary">
          <p>Evidence-backed causal projection</p>
          <h2 id="countermap-demo-title">{selected.graph.summary.title}</h2>
          <span>{selected.graph.summary.overview}</span>
        </div>
        <ReasoningTimeline graph={selected.graph} />
      </section>
    </main>
  );
}
