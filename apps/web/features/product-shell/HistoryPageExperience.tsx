"use client";

import { ArrowRight, Network, RotateCw, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

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
  const [deleting, setDeleting] = useState<HistoryItem | null>(null);
  const [deletePending, setDeletePending] = useState(false);
  const [deleteFailed, setDeleteFailed] = useState(false);

  const load = useCallback(async (signal: AbortSignal) => {
    setResponse(null);
    setFailed(false);
    try {
      const next = await api.listInterviews(filter, 50, 0, signal);
      if (!signal.aborted) setResponse(next);
    } catch {
      if (!signal.aborted) setFailed(true);
    }
  }, [api, filter]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [attempt, load]);

  const confirmDeletion = useCallback(async () => {
    if (!deleting || deletePending) return;
    setDeletePending(true);
    setDeleteFailed(false);
    try {
      await api.deleteInterview(deleting.interview_session_id);
      setResponse((current) => current ? {
        ...current,
        items: current.items.filter(
          (item) => item.interview_session_id !== deleting.interview_session_id,
        ),
      } : current);
      setDeleting(null);
    } catch {
      setDeleteFailed(true);
    } finally {
      setDeletePending(false);
    }
  }, [api, deletePending, deleting]);

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
          {response.items.map((item) => (
            <HistoryRow
              key={item.interview_session_id}
              item={item}
              onDelete={() => {
                setDeleteFailed(false);
                setDeleting(item);
              }}
            />
          ))}
        </ol>
      ) : (
        <section className="product-empty-state history-empty" aria-labelledby="history-empty-title">
          <h2 id="history-empty-title">No interviews yet.</h2>
          <p>{filter === "all" ? "Start your first interview and the session will appear here." : `No ${filter.replace("_", " ")} interviews.`}</p>
          <Link href="/interview/setup">Start your first interview <ArrowRight size={15} /></Link>
        </section>
      )}
      {deleting ? (
        <DeleteInterviewDialog
          item={deleting}
          pending={deletePending}
          failed={deleteFailed}
          onCancel={() => {
            if (!deletePending) setDeleting(null);
          }}
          onConfirm={() => void confirmDeletion()}
        />
      ) : null}
    </div>
  );
}

function HistoryRow({ item, onDelete }: { item: HistoryItem; onDelete: () => void }) {
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
          <button className="history-delete-action" type="button" onClick={onDelete}>
            <Trash2 size={14} /> Delete
          </button>
        </div>
      </article>
    </li>
  );
}

function DeleteInterviewDialog({
  item,
  pending,
  failed,
  onCancel,
  onConfirm,
}: {
  item: HistoryItem;
  pending: boolean;
  failed: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);

  useEffect(() => {
    cancelRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pending) onCancel();
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        dialogRef.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [],
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onCancel, pending]);

  return (
    <div className="history-dialog-backdrop">
      <section
        ref={dialogRef}
        className="history-delete-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-interview-title"
        aria-describedby="delete-interview-description"
      >
        <button
          className="history-dialog-close"
          type="button"
          aria-label="Close delete interview dialog"
          disabled={pending}
          onClick={onCancel}
        >
          <X size={16} />
        </button>
        <p className="product-kicker">Delete interview</p>
        <h2 id="delete-interview-title">Remove {item.problem_title}?</h2>
        <p id="delete-interview-description">
          This removes this interview and rebuilds learning conclusions that depended on it.
        </p>
        {failed ? (
          <p className="history-delete-error" role="alert">
            The interview could not be deleted. Nothing was removed; you can try again.
          </p>
        ) : null}
        <div className="history-dialog-actions">
          <button ref={cancelRef} type="button" disabled={pending} onClick={onCancel}>
            Cancel
          </button>
          <button type="button" disabled={pending} onClick={onConfirm}>
            {pending ? "Deleting…" : "Delete interview"}
          </button>
        </div>
      </section>
    </div>
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
