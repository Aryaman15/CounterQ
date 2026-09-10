"use client";

import { ArrowRight, History, Network, Play, RotateCw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import type {
  CandidateInterviewHistoryResponse,
  CandidateMasteryOverviewResponse,
  CounterQApiClient,
  CurrentUserResponse,
} from "@/lib/counterq-api";

import { AuthenticatedProductPage } from "./AuthenticatedProductPage";

type HistoryItem = CandidateInterviewHistoryResponse["items"][number];

export function HomePageExperience() {
  return (
    <AuthenticatedProductPage signedOut={<SignedOutLanding />}>
      {({ api, me }) => <CandidateHome api={api} me={me} />}
    </AuthenticatedProductPage>
  );
}

function SignedOutLanding() {
  return (
    <main className="landing-page">
      <header className="landing-header">
        <div className="product-wordmark"><span aria-hidden="true">CQ</span><strong>CounterQ</strong></div>
        <Link href="/sign-in">Sign in</Link>
      </header>
      <section className="landing-statement" aria-labelledby="landing-title">
        <p className="product-kicker">Adaptive technical examiner</p>
        <h1 id="landing-title">Practice the part after your answer.</h1>
        <p>
          CounterQ listens to your reasoning, observes your code, waits when you are
          making progress, and challenges the decisions that deserve scrutiny.
        </p>
        <div className="landing-actions">
          <Link className="landing-primary-action" href="/sign-up">
            Start practicing <ArrowRight size={17} aria-hidden="true" />
          </Link>
          <Link href="/sign-in">Sign in</Link>
        </div>
        <ol className="landing-sequence" aria-label="How CounterQ interviews">
          <li><span>01</span><strong>You make a claim</strong><small>Speech and code stay in context.</small></li>
          <li><span>02</span><strong>CounterQ observes</strong><small>Not every uncertainty becomes an interruption.</small></li>
          <li><span>03</span><strong>The right question arrives</strong><small>Your reasoning has to survive it.</small></li>
        </ol>
        {process.env.NODE_ENV !== "production" ? (
          <Link className="landing-development-link" href="/interview/demo">
            Development Interview Room preview
          </Link>
        ) : null}
      </section>
    </main>
  );
}

function CandidateHome({ api, me }: { api: CounterQApiClient; me: CurrentUserResponse }) {
  const router = useRouter();
  const [active, setActive] = useState<HistoryItem | null>(null);
  const [completed, setCompleted] = useState<HistoryItem | null>(null);
  const [mastery, setMastery] = useState<CandidateMasteryOverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const retestPending = useRef(false);
  const [retestStarting, setRetestStarting] = useState(false);
  const [retestError, setRetestError] = useState(false);

  const load = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    setFailed(false);
    const [activeResult, completedResult, masteryResult] = await Promise.allSettled([
      api.listInterviews("in_progress", 1, 0, signal),
      api.listInterviews("completed", 1, 0, signal),
      api.getMastery(signal),
    ]);
    if (signal.aborted) return;
    if (activeResult.status === "rejected" && completedResult.status === "rejected") {
      setFailed(true);
    }
    setActive(activeResult.status === "fulfilled"
      ? activeResult.value.items.find((item) => item.can_resume) ?? null
      : null);
    setCompleted(completedResult.status === "fulfilled"
      ? completedResult.value.items.find((item) => item.display_status === "COMPLETED") ?? null
      : null);
    setMastery(masteryResult.status === "fulfilled" ? masteryResult.value : null);
    setLoading(false);
  }, [api]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [attempt, load]);

  const retest = mastery?.retest_recommendations.find((item) => item.action_enabled);
  const name = me.profile?.display_name?.split(/\s+/)[0];

  async function startRetest() {
    if (!retest || retestPending.current) return;
    retestPending.current = true;
    setRetestStarting(true);
    setRetestError(false);
    try {
      const launch = await api.startRetest(retest.recommendation_id);
      router.push(launch.interview_path);
    } catch {
      setRetestError(true);
    } finally {
      retestPending.current = false;
      setRetestStarting(false);
    }
  }

  return (
    <div className="home-page">
      <header className="home-intro">
        <p className="product-kicker">Candidate workspace</p>
        <h1>{name ? `Ready, ${name}?` : "Ready for the next question?"}</h1>
        <p>Choose the next useful action. CounterQ will keep the evidence connected.</p>
      </header>

      {active ? (
        <section className="home-active" aria-labelledby="continue-title">
          <div>
            <p className="home-section-label">Continue where you left off</p>
            <h2 id="continue-title">{active.problem_title}</h2>
            <p>{formatSessionLine(active)}</p>
          </div>
          <Link href={active.interview_path}>Continue interview <Play size={15} /></Link>
        </section>
      ) : null}

      <section className="home-primary-action" aria-labelledby="start-title">
        <div>
          <p className="home-section-label">New session</p>
          <h2 id="start-title">Put your reasoning under pressure.</h2>
          <p>Choose a reviewed problem, mode, language, and a server-timed format.</p>
        </div>
        <Link href="/interview/setup">Start interview <ArrowRight size={16} /></Link>
      </section>

      {retest ? (
        <section className="home-retest" aria-labelledby="retest-title">
          <div><RotateCw size={18} aria-hidden="true" /></div>
          <div>
            <p className="home-section-label">Retest ready</p>
            <h2 id="retest-title">{retest.target_name}</h2>
            <p>{retest.reason}</p>
          </div>
          <button type="button" disabled={retestStarting} onClick={() => void startRetest()}>
            {retestStarting ? "Starting Quick Drill…" : "CounterQ me again"}
          </button>
          {retestError ? <p className="home-retest-error" role="alert">The Quick Drill could not start. Try again.</p> : null}
        </section>
      ) : null}

      <section className="home-recent" aria-labelledby="recent-title">
        <header>
          <div>
            <p className="home-section-label">Recent work</p>
            <h2 id="recent-title">What CounterQ watched carefully</h2>
          </div>
          <Link href="/history"><History size={15} /> All interviews</Link>
        </header>
        {loading ? (
          <p role="status">Loading your recent interviews…</p>
        ) : failed ? (
          <div className="product-inline-error" role="alert">
            <p>Your interview history is temporarily unavailable.</p>
            <button type="button" onClick={() => setAttempt((value) => value + 1)}>Try again</button>
          </div>
        ) : completed ? (
          <article>
            <div>
              <time dateTime={completed.completed_at ?? completed.started_at}>
                {formatDate(completed.completed_at ?? completed.started_at)}
              </time>
              <h3>{completed.problem_title}</h3>
              <p>{formatSessionLine(completed)}</p>
            </div>
            <div className="home-recent-actions">
              <Link href={completed.report_path}>View Report</Link>
              <Link href={completed.countermap_path}><Network size={14} /> CounterMap</Link>
            </div>
          </article>
        ) : (
          <div className="product-empty-state">
            <h3>No completed interviews yet.</h3>
            <p>Your evidence-backed review will appear here after your first session.</p>
          </div>
        )}
      </section>
    </div>
  );
}

function formatSessionLine(item: HistoryItem): string {
  return [
    titleCase(item.mode),
    languageLabel(item.language),
    `${Math.round(item.configured_duration_seconds / 60)} min`,
  ].join(" · ");
}

function titleCase(value: string): string {
  return value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function languageLabel(value: string): string {
  return value === "cpp" ? "C++" : value === "java" ? "Java" : "Python";
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("en", { month: "short", day: "numeric", year: "numeric" });
}
