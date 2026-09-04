"use client";

import type { components } from "@counterq/contracts/openapi";
import { ChevronDown, FileCheck2, RotateCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { CounterMapExperience } from "@/features/countermap/CounterMapExperience";

type ReportResponse = components["schemas"]["CandidateSessionReportResponse"];
type ReportDocument = components["schemas"]["SessionReportDocument"];
type ReportFinding = components["schemas"]["ReportFinding"];
type ReportSection = components["schemas"]["ReportSection"];
type SourceDetail = components["schemas"]["CandidateSourceDetail"];
type DevelopmentInspection = components["schemas"]["DevelopmentReportInspection"];

type SessionReportExperienceProps = {
  interviewSessionId: string;
  pollIntervalMs?: number;
};

const reasoningSections: Array<{
  key: keyof Pick<
    ReportDocument,
    "claim_defense" | "correctness_implementation" | "complexity" | "edge_cases" | "debugging" | "adaptability"
  >;
  label: string;
}> = [
  { key: "claim_defense", label: "Claim defense" },
  { key: "correctness_implementation", label: "Correctness & implementation" },
  { key: "complexity", label: "Complexity" },
  { key: "edge_cases", label: "Edge cases" },
  { key: "debugging", label: "Debugging" },
  { key: "adaptability", label: "Adaptability" },
];

export function SessionReportExperience({
  interviewSessionId,
  pollIntervalMs = 1600,
}: SessionReportExperienceProps) {
  const [response, setResponse] = useState<ReportResponse | null>(null);
  const [requestFailed, setRequestFailed] = useState(false);

  const load = useCallback(async (signal?: AbortSignal): Promise<ReportResponse["status"] | null> => {
    try {
      const result = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}/api/reports/sessions/${interviewSessionId}`,
        { signal, cache: "no-store" },
      );
      if (!result.ok) throw new Error("Report status unavailable");
      const next = await result.json() as ReportResponse;
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
      const nextStatus = await load(controller.signal);
      if (
        !controller.signal.aborted
        && nextStatus !== "READY"
        && nextStatus !== "FAILED"
      ) timeout = setTimeout(poll, pollIntervalMs);
    };
    void poll();
    return () => {
      controller.abort();
      if (timeout) clearTimeout(timeout);
    };
  }, [load, pollIntervalMs]);

  if (requestFailed && response === null) {
    return (
      <ReportShell interviewSessionId={interviewSessionId}>
        <section className="report-state report-state-failed" aria-labelledby="report-failed-title">
          <p className="report-eyebrow">Interview complete</p>
          <h1 id="report-failed-title">Your report is temporarily out of reach.</h1>
          <p>Your interview remains recorded. Reconnect and try loading this page again.</p>
          <button className="report-retry" type="button" onClick={() => void load()}>
            <RotateCw size={16} aria-hidden="true" /> Try again
          </button>
        </section>
      </ReportShell>
    );
  }

  if (response?.status === "FAILED") {
    return (
      <ReportShell interviewSessionId={interviewSessionId}>
        <section className="report-state report-state-failed" aria-labelledby="report-failed-title">
          <p className="report-eyebrow">Interview complete</p>
          <h1 id="report-failed-title">Your detailed report isn’t ready yet.</h1>
          <p>{response.message}</p>
          <p className="report-safe-note">Your completed interview is preserved.</p>
        </section>
      </ReportShell>
    );
  }

  if (response?.status !== "READY" || !response.report) {
    return (
      <ReportShell interviewSessionId={interviewSessionId}>
        <section className="report-state report-state-preparing" aria-labelledby="report-preparing-title">
          <div className="report-review-mark" aria-hidden="true"><span /></div>
          <p className="report-eyebrow">Interview complete</p>
          <h1 id="report-preparing-title">CounterQ is reviewing what you demonstrated.</h1>
          <p>It is connecting your reasoning, code, corrections, and delivered interview moments.</p>
          <div className="report-preparing-line" aria-hidden="true"><span /></div>
          <p className="report-safe-note" role="status">Preparing your evidence-backed Session Report…</p>
        </section>
      </ReportShell>
    );
  }

  return (
    <ReadyReport
      response={response}
      report={response.report}
      interviewSessionId={interviewSessionId}
    />
  );
}

function ReadyReport({
  response,
  report,
  interviewSessionId,
}: {
  response: ReportResponse;
  report: ReportDocument;
  interviewSessionId: string;
}) {
  const sources = useMemo(
    () => new Map(report.source_details.map((source) => [source.evidence_id, source])),
    [report.source_details],
  );
  return (
    <ReportShell ready interviewSessionId={interviewSessionId}>
      <header className="session-report-header">
        <div>
          <p className="report-eyebrow">Session Report · Complete</p>
          <h1>{response.session.problem_title}</h1>
          <p className="report-deck">What held, what changed under pressure, and what to test next.</p>
        </div>
        <dl className="report-metadata">
          <div><dt>Mode</dt><dd>{titleCase(response.session.mode)}</dd></div>
          <div><dt>Language</dt><dd>{response.session.language.toUpperCase()}</dd></div>
          <div><dt>Duration</dt><dd>{formatDuration(response.session.duration_seconds)}</dd></div>
          <div><dt>Report</dt><dd>v{response.report_version}</dd></div>
        </dl>
      </header>

      <main className="session-report-body">
        <section className="report-summary" aria-labelledby="report-summary-title">
          <div className="report-section-heading">
            <FileCheck2 size={20} aria-hidden="true" />
            <h2 id="report-summary-title">Session summary</h2>
          </div>
          <FindingList findings={report.summary} sources={sources} />
        </section>

        <section className="report-section" aria-labelledby="report-strengths-title">
          <div className="report-section-heading">
            <ShieldCheck size={19} aria-hidden="true" />
            <div><p>What held</p><h2 id="report-strengths-title">Independent strengths</h2></div>
          </div>
          {report.strengths.length ? (
            <FindingList findings={report.strengths} sources={sources} />
          ) : <InsufficientEvidence />}
        </section>

        <section className="report-section report-breakpoints" aria-labelledby="report-breakpoints-title">
          <div className="report-section-heading">
            <span className="report-index">01</span>
            <div><p>What broke under pressure</p><h2 id="report-breakpoints-title">Breakpoints</h2></div>
          </div>
          {report.breakpoints.length ? report.breakpoints.map((item) => (
            <article className="breakpoint-item" key={item.breakpoint_id}>
              <div className="breakpoint-rule" aria-hidden="true" />
              <div>
                <p className="breakpoint-meta">
                  {item.concept_label} · {item.skill_label} · {titleCase(item.status)} · {titleCase(item.severity)}
                </p>
                <h3>{item.title}</h3>
                <p>{item.explanation}</p>
                <EvidenceDisclosure evidenceIds={item.evidence_ids} sources={sources} />
              </div>
            </article>
          )) : <InsufficientEvidence />}
        </section>

        {response.session.mode === "COACH" && report.coach_assistance.length ? (
          <section className="report-section report-coach" aria-labelledby="report-coach-title">
            <div className="report-section-heading">
              <span className="report-index">02</span>
              <div><p>Coach learning</p><h2 id="report-coach-title">Before help → after help</h2></div>
            </div>
            {report.coach_assistance.map((item, index) => (
              <article className="coach-learning-row" key={`${item.title}-${index}`}>
                <h3>{item.title}</h3>
                <p>{item.explanation}</p>
                <div className="coach-learning-contrast">
                  <span className="coach-assistance-label">{item.assistance_label}</span>
                  <div>
                    <strong>Before help</strong>
                    <EvidenceDisclosure evidenceIds={item.before_help_evidence_ids} sources={sources} />
                  </div>
                  <div>
                    <strong>After help</strong>
                    <span>{item.independent_verification_missing
                      ? "Independent verification still needed"
                      : "Later evidence captured"}</span>
                    <EvidenceDisclosure evidenceIds={item.after_help_evidence_ids} sources={sources} />
                  </div>
                </div>
              </article>
            ))}
          </section>
        ) : null}

        <section className="report-section" aria-labelledby="report-reasoning-title">
          <div className="report-section-heading">
            <span className="report-index">03</span>
            <div><p>Reasoning dimensions</p><h2 id="report-reasoning-title">How the solution developed</h2></div>
          </div>
          <div className="reasoning-dimensions">
            {reasoningSections.map(({ key, label }) => (
              <ReasoningDimension key={key} label={label} section={report[key]} sources={sources} />
            ))}
          </div>
        </section>

        <section className="report-section report-next" aria-labelledby="report-next-title">
          <div className="report-section-heading">
            <span className="report-index">04</span>
            <div><p>Next session</p><h2 id="report-next-title">Minimum useful next actions</h2></div>
          </div>
          {report.next_actions.length ? (
            <ol>{report.next_actions.map((item, index) => <li key={`${item.action}-${index}`}>{item.action}</li>)}</ol>
          ) : <InsufficientEvidence />}
        </section>
        <CounterMapExperience interviewSessionId={interviewSessionId} />
      </main>
    </ReportShell>
  );
}

function FindingList({ findings, sources }: { findings: ReportFinding[]; sources: Map<string, SourceDetail> }) {
  return <div className="report-findings">{findings.map((item, index) => (
    <article className="report-finding" key={`${item.title}-${index}`}>
      <h3>{item.title}</h3>
      {item.independence_level ? (
        <p className="report-finding-attribution">{attributionLabel(item.independence_level)}</p>
      ) : null}
      <p>{item.finding}</p>
      <EvidenceDisclosure evidenceIds={item.evidence_ids} sources={sources} />
    </article>
  ))}</div>;
}

function ReasoningDimension({
  label,
  section,
  sources,
}: { label: string; section: ReportSection; sources: Map<string, SourceDetail> }) {
  return (
    <article className="reasoning-dimension">
      <h3>{label}</h3>
      {section.status === "INSUFFICIENT_EVIDENCE" ? (
        <p className="insufficient-evidence">{section.insufficient_evidence_message}</p>
      ) : <FindingList findings={section.items} sources={sources} />}
    </article>
  );
}

function EvidenceDisclosure({
  evidenceIds,
  sources,
}: { evidenceIds: string[]; sources: Map<string, SourceDetail> }) {
  const details = evidenceIds.flatMap((id) => sources.has(id) ? [sources.get(id)!] : []);
  if (!details.length) return null;
  return (
    <details className="evidence-disclosure">
      <summary>Why this?<ChevronDown size={15} aria-hidden="true" /></summary>
      <div className="evidence-source-list">
        {details.map((source, index) => (
          <div key={`${source.evidence_id}-${index}`}>
            <strong>{source.attribution}</strong>
            <span>{source.source_label}</span>
            {source.source_excerpt ? <q>{source.source_excerpt}</q> : null}
          </div>
        ))}
      </div>
    </details>
  );
}

function InsufficientEvidence() {
  return <p className="insufficient-evidence">Not enough evidence from this session.</p>;
}

function ReportShell({
  children,
  ready = false,
  interviewSessionId,
}: {
  children: React.ReactNode;
  ready?: boolean;
  interviewSessionId: string;
}) {
  return (
    <div className={`session-report-shell${ready ? " session-report-shell-ready" : ""}`}>
      <div className="report-wordmark"><span aria-hidden="true">CQ</span> CounterQ</div>
      {children}
      {process.env.NODE_ENV === "development" ? (
        <DevelopmentReportInspector interviewSessionId={interviewSessionId} />
      ) : null}
    </div>
  );
}

function DevelopmentReportInspector({ interviewSessionId }: { interviewSessionId: string }) {
  const [inspection, setInspection] = useState<DevelopmentInspection | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const loadInspection = async () => {
      const result = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}/api/reports/development/sessions/${interviewSessionId}/inspection`,
        { signal: controller.signal, cache: "no-store" },
      );
      if (result.ok) setInspection(await result.json() as DevelopmentInspection);
    };
    void loadInspection().catch(() => undefined);
    return () => controller.abort();
  }, [interviewSessionId]);
  if (!inspection) return null;
  return (
    <details className="development-report-inspector">
      <summary>Development · Session Report pipeline</summary>
      <dl>
        <div><dt>Evidence finalization</dt><dd>{inspection.evidence_finalization_status}</dd></div>
        <div><dt>Report</dt><dd>{inspection.report_status} · v{inspection.report_version ?? "—"}</dd></div>
        <div><dt>Policy</dt><dd>{inspection.generation_policy ?? "—"}</dd></div>
        <div><dt>Invocation</dt><dd>{inspection.ai_invocation_id ?? "—"}</dd></div>
        <div><dt>Sources</dt><dd>{inspection.source_evidence_count} evidence · {inspection.source_breakpoint_count} breakpoints</dd></div>
        <div><dt>Validation</dt><dd>{inspection.report_validation_status ?? "—"}</dd></div>
        <div><dt>Current</dt><dd>{inspection.is_current ? "yes" : "no"}</dd></div>
        <div><dt>Last failure</dt><dd>{inspection.last_failure_category ?? "—"}</dd></div>
      </dl>
      <p>{inspection.outbox.map((event) => `${event.event_type}: ${event.status}`).join(" · ")}</p>
    </details>
  );
}

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

function titleCase(value: string): string {
  return value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function attributionLabel(value: NonNullable<ReportFinding["independence_level"]>): string {
  return {
    INDEPENDENT: "Independently demonstrated",
    AFTER_PROBE: "Demonstrated after interviewer challenge",
    AFTER_LIGHT_GUIDANCE: "Demonstrated after light guidance",
    AFTER_STRONG_HINT: "Demonstrated after a strong hint",
    DIRECTLY_TAUGHT: "Demonstrated after explanation",
  }[value];
}
