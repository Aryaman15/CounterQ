"use client";

import { ArrowRight, Network, RotateCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import type {
  CandidateInterviewHistoryResponse,
  CounterQApiClient,
} from "@/lib/counterq-api";

import { AuthenticatedProductPage } from "./AuthenticatedProductPage";

type Filter = "all" | "in_progress" | "completed";
type HistoryItem = CandidateInterviewHistoryResponse["items"][number];

const filters: Array<{ value: Filter; label: string }> = [
  { value: "all", label: "All" },
  { value: "in_progress", label: "In progress" },
  { value: "completed", label: "Completed" },
];

export function HistoryPageExperience() {
  return (
    <AuthenticatedProductPage>
      {({ api }) => <InterviewHistory api={api} />}
    </AuthenticatedProductPage>
  );
}

function InterviewHistory({ api }: { api: CounterQApiClient }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [response, setResponse] = useState<CandidateInterviewHistoryResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const load = useCallback(async (signal: AbortSignal) => {
    setResponse(null);
    setFailed(false);
    try {
      setResponse(await api.listInterviews(filter, 50, 0, signal));
    } catch {
      if (!signal.aborted) setFailed(true);
    }
  }, [api, filter]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [attempt, load]);

  return (
    <div className="history-page">
      <header className="product-page-heading">
        <p className="product-kicker">Interview history</p>
        <h1>The sessions behind your evidence.</h1>
        <p>Return to active work or inspect what a completed interview actually demonstrated.</p>
      </header>
      <nav className="history-filters" aria-label="Interview history filters">
        {filters.map((item) => (
          <button
            key={item.value}
            type="button"
            aria-pressed={filter === item.value}
            onClick={() => setFilter(item.value)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      {!response && !failed ? (
        <p className="product-loading-state" role="status">Loading interview history…</p>
      ) : failed ? (
        <div className="product-inline-error" role="alert">
          <p>Interview history is temporarily unavailable.</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            <RotateCw size={14} /> Try again
          </button>
        </div>
      ) : response && response.items.length ? (
        <ol className="history-list">
          {response.items.map((item) => <HistoryRow key={item.interview_session_id} item={item} />)}
        </ol>
      ) : (
        <section className="product-empty-state history-empty" aria-labelledby="history-empty-title">
          <h2 id="history-empty-title">No interviews yet.</h2>
          <p>{filter === "all" ? "Start your first interview and the session will appear here." : `No ${filter.replace("_", " ")} interviews.`}</p>
          <Link href="/interview/setup">Start your first interview <ArrowRight size={15} /></Link>
        </section>
      )}
    </div>
  );
}

function HistoryRow({ item }: { item: HistoryItem }) {
  return (
    <li>
      <article className="history-row">
        <div className="history-date">
          <time dateTime={item.completed_at ?? item.started_at}>
            {formatDate(item.completed_at ?? item.started_at)}
          </time>
          <span data-status={item.display_status.toLowerCase()}>{statusLabel(item)}</span>
        </div>
        <div className="history-copy">
          <h2>{item.problem_title}</h2>
          <p>{titleCase(item.mode)} · {languageLabel(item.language)} · {templateLabel(item.template)} · {Math.round(item.configured_duration_seconds / 60)} min</p>
        </div>
        <div className="history-actions">
          {item.can_resume ? (
            <Link className="history-primary-action" href={item.interview_path}>Continue interview</Link>
          ) : item.display_status === "COMPLETED" ? (
            <>
              <Link className="history-primary-action" href={item.report_path}>View Report</Link>
              <Link href={item.countermap_path}><Network size={14} /> CounterMap</Link>
            </>
          ) : null}
        </div>
      </article>
    </li>
  );
}

function statusLabel(item: HistoryItem): string {
  if (item.display_status === "COMPLETED") return "Completed";
  if (item.can_resume) return "In progress";
  return "Ended";
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("en", { month: "short", day: "numeric", year: "numeric" });
}

function titleCase(value: string): string {
  return value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function languageLabel(value: string): string {
  return value === "cpp" ? "C++" : value === "java" ? "Java" : "Python";
}

function templateLabel(value: string): string {
  return titleCase(value).replace("Coding Interview", "interview");
}
