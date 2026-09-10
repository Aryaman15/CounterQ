"use client";

import { RotateCw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AuthenticatedProductPage } from "@/features/product-shell/AuthenticatedProductPage";
import type {
  CandidateMasteryOverviewResponse,
  CounterQApiClient,
} from "@/lib/counterq-api";

import { MasteryExperience } from "./MasteryExperience";

export function ProductionMasteryPage() {
  return (
    <AuthenticatedProductPage>
      {({ api }) => <CurrentUserMastery api={api} />}
    </AuthenticatedProductPage>
  );
}

function CurrentUserMastery({ api }: { api: CounterQApiClient }) {
  const router = useRouter();
  const [overview, setOverview] = useState<CandidateMasteryOverviewResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const load = useCallback(async (signal: AbortSignal) => {
    setFailed(false);
    try {
      setOverview(await api.getMastery(signal));
    } catch {
      if (!signal.aborted) setFailed(true);
    }
  }, [api]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [attempt, load]);

  return (
    <div className="production-mastery-page">
      <header className="product-page-heading">
        <p className="product-kicker">Cross-session evidence</p>
        <h1>What still holds when the context changes.</h1>
        <p>Technical concepts and interview skills remain separate—and every state stays traceable.</p>
      </header>
      {failed ? (
        <div className="product-inline-error" role="alert">
          <p>Mastery is temporarily unavailable. Your interview evidence is unchanged.</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            <RotateCw size={14} /> Try again
          </button>
        </div>
      ) : overview ? (
        <>
          <MasteryExperience
            overview={overview}
            onStartRetest={(recommendationId) => api.startRetest(recommendationId)}
            onRetestLaunched={(launch) => router.push(launch.interview_path)}
          />
          {overview.status === "EMPTY" ? (
            <Link className="mastery-empty-action" href="/interview/setup">Start an interview</Link>
          ) : null}
        </>
      ) : (
        <p className="product-loading-state" role="status">Loading your evidence-backed Mastery…</p>
      )}
    </div>
  );
}
