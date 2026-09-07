"use client";

import type { components } from "@counterq/contracts/openapi";
import { AlertTriangle, Check, Clock3, Lightbulb, X } from "lucide-react";
import { useEffect, useRef } from "react";

type Target = components["schemas"]["CandidateMasteryTarget"];
type Evidence = components["schemas"]["CandidateMasteryEvidenceItem"];

export function MasteryDetailDrawer({ target, onClose }: { target: Target; onClose: () => void }) {
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !drawerRef.current) return;
      const focusable = Array.from(
        drawerRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'),
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
    document.addEventListener("keydown", onKeyDown);
    document.body.classList.add("countermap-drawer-open");
    const frame = window.requestAnimationFrame(() => closeRef.current?.focus());
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
      document.body.classList.remove("countermap-drawer-open");
      previousFocus.current?.focus();
    };
  }, [onClose]);

  return (
    <div className="mastery-drawer-layer">
      <button type="button" className="mastery-drawer-scrim" tabIndex={-1} aria-label="Close mastery detail" onClick={onClose} />
      <aside ref={drawerRef} className="mastery-drawer" role="dialog" aria-modal="true" aria-labelledby="mastery-drawer-title">
        <header className="mastery-drawer-header">
          <div><p>{target.target_type === "SKILL" ? "Interview skill" : target.target_type === "PARENT_SUMMARY" ? "Area summary" : "Technical concept"}</p><h2 id="mastery-drawer-title">{target.display_name}</h2></div>
          <button ref={closeRef} type="button" aria-label="Close detail drawer" onClick={onClose}><X size={18} /></button>
        </header>
        <div className="mastery-drawer-scroll">
          <div className="mastery-detail-status" data-state={target.state.toLowerCase()}>
            <strong>{target.state_label}</strong><span>{target.evidence_sufficiency_label}</span><span>{target.freshness_label}</span>
          </div>
          <section className="mastery-detail-why" aria-labelledby="mastery-why-title">
            <p className="mastery-section-label" id="mastery-why-title">Why this state?</p>
            <blockquote>{target.reason}</blockquote>
            <dl>
              <div><dt>Sessions</dt><dd>{target.distinct_session_count}</dd></div>
              <div><dt>Problems</dt><dd>{target.distinct_problem_count}</dd></div>
              <div><dt>Contexts</dt><dd>{target.distinct_context_count}</dd></div>
            </dl>
          </section>
          {target.unresolved_breakpoint_ids.length ? (
            <div className="mastery-breakpoint-note"><AlertTriangle size={15} /><span>A validated Breakpoint is still unresolved. Assisted success does not close it.</span></div>
          ) : null}
          {target.target_type === "PARENT_SUMMARY" ? (
            <section className="mastery-detail-section"><p className="mastery-section-label">How to read this</p><p>This parent state summarizes child projections for navigation. It creates no synthetic Evidence.</p></section>
          ) : (
            <section className="mastery-detail-section" aria-labelledby="mastery-timeline-title">
              <p className="mastery-section-label" id="mastery-timeline-title">Evidence timeline</p>
              <ol className="mastery-evidence-timeline">
                {target.evidence.map((item) => <EvidenceItem key={item.evidence_id} item={item} />)}
              </ol>
            </section>
          )}
          <section className="mastery-detail-section mastery-next-action">
            <p className="mastery-section-label">Next</p>
            <p><Lightbulb size={15} /> {target.next_action}</p>
            {target.retest_due ? <button type="button" disabled>CounterQ me again · Available in Stage 8B</button> : null}
          </section>
        </div>
      </aside>
    </div>
  );
}

function EvidenceItem({ item }: { item: Evidence }) {
  const assisted = !["INDEPENDENT", "AFTER_PROBE"].includes(item.independence);
  return (
    <li data-contribution={item.contribution.toLowerCase()}>
      <span className="mastery-timeline-icon" aria-hidden="true">{item.polarity === "POSITIVE" ? <Check size={13} /> : item.polarity === "NEGATIVE" ? <AlertTriangle size={13} /> : <Clock3 size={13} />}</span>
      <div>
        <header><strong>{item.problem}</strong><time dateTime={item.recorded_at}>{new Date(item.recorded_at).toLocaleDateString("en", { month: "short", day: "numeric", year: "numeric" })}</time></header>
        <p>{item.finding}</p>
        <small>{titleCase(item.mode)} · {titleCase(item.independence)}{item.retest_linked ? ` · ${retestLabel(item.independence)}` : ""}</small>
        {assisted ? <em>Learning / assisted Evidence</em> : null}
      </div>
    </li>
  );
}

function titleCase(value: string) {
  return value.toLowerCase().split("_").map((word) => `${word[0]?.toUpperCase() ?? ""}${word.slice(1)}`).join(" ");
}

function retestLabel(independence: Evidence["independence"]) {
  if (independence === "INDEPENDENT") return "Independent retest";
  if (independence === "AFTER_PROBE") return "Diagnostic retest after probe";
  return "Retest evidence";
}
