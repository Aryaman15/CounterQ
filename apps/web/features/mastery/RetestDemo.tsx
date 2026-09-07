"use client";

import type { components } from "@counterq/contracts/openapi";
import { RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { storeDevelopmentInterviewSession } from "../interview-room/realtime/RealtimeControlClient";
import { MasteryExperience } from "./MasteryExperience";

type Fixture = components["schemas"]["DevelopmentRetestFixtureResponse"];
type Launch = components["schemas"]["RetestLaunchResponse"];
type Overview = components["schemas"]["CandidateMasteryOverviewResponse"];

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export function RetestDemo({
  navigate = (path) => window.location.assign(path),
}: {
  navigate?: (path: string) => void;
}) {
  const [fixture, setFixture] = useState<Fixture | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const fixtureResponse = await fetch(`${apiBase}/api/retests/development/fixture`, {
        method: "POST",
        signal,
      });
      if (!fixtureResponse.ok) throw new Error("Retest fixture unavailable");
      const durableFixture = (await fixtureResponse.json()) as Fixture;
      const masteryResponse = await fetch(
        `${apiBase}/api/mastery/development/users/${durableFixture.user_id}`,
        { cache: "no-store", signal },
      );
      if (!masteryResponse.ok) throw new Error("Mastery unavailable");
      setFixture(durableFixture);
      setOverview((await masteryResponse.json()) as Overview);
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

  const startRetest = async (recommendationId: string): Promise<Launch> => {
    const response = await fetch(
      `${apiBase}/api/retests/development/recommendations/${recommendationId}/start`,
      { method: "POST" },
    );
    if (!response.ok) {
      const payload = await response.json().catch(() => null) as {
        detail?: { message?: string } | string;
      } | null;
      const message = typeof payload?.detail === "object"
        ? payload.detail.message
        : payload?.detail;
      throw new Error(message || "No suitable retest is available yet.");
    }
    return response.json() as Promise<Launch>;
  };

  const launchRetest = (launch: Launch) => {
    storeDevelopmentInterviewSession(launch.interview_session_id);
    navigate(launch.interview_path);
  };

  return (
    <main className="mastery-demo retest-demo">
      <header className="mastery-demo-header">
        <div className="report-wordmark"><span aria-hidden="true">CQ</span> CounterQ</div>
        <p className="mastery-kicker">Quick Drill · Retrieval + transfer</p>
        <h1>CounterQ me again.</h1>
        <p>Test whether your reasoning holds up in a fresh interview context.</p>
      </header>
      {overview && fixture ? (
        <MasteryExperience
          overview={overview}
          onStartRetest={startRetest}
          onRetestLaunched={launchRetest}
        />
      ) : failed ? (
        <section className="mastery-system-state" role="status">
          <ShieldCheck size={22} aria-hidden="true" />
          <h2>The retest path is temporarily unavailable.</h2>
          <p>Your existing Evidence and Mastery history are unchanged.</p>
          <button type="button" onClick={() => void load()}><RefreshCw size={14} /> Try again</button>
        </section>
      ) : (
        <section className="mastery-system-state" role="status" aria-live="polite">
          <span className="mastery-loading-mark" aria-hidden="true" />
          <h2>Preparing your evidence-backed retest</h2>
          <p>CounterQ is finding a different reviewed interview context.</p>
        </section>
      )}
    </main>
  );
}
