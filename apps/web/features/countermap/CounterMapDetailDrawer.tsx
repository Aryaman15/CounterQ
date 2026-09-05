"use client";

import {
  ArrowDownLeft,
  ArrowUpRight,
  Braces,
  Check,
  CircleAlert,
  Clock3,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  eyebrowForNode,
  independenceLabel,
  relationshipLabel,
  titleCase,
  whyLabel,
  type CounterMapDetail,
  type CounterMapGraph,
  type CounterMapNode,
} from "./counterMapPresentation";

export function CounterMapDetailDrawer({
  graph,
  node,
  detailUrl,
  onClose,
}: {
  graph: CounterMapGraph;
  node: CounterMapNode;
  detailUrl: string;
  onClose: () => void;
}) {
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const [detail, setDetail] = useState<CounterMapDetail | null>(null);
  const [failed, setFailed] = useState(false);
  const connections = useMemo(() => connectionDetails(graph, node), [graph, node]);

  useEffect(() => {
    previousFocus.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const frame = window.requestAnimationFrame(() => closeRef.current?.focus());
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !drawerRef.current) return;
      const focusable = Array.from(
        drawerRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], summary, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute("hidden"));
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
    document.addEventListener("keydown", handleKey);
    document.body.classList.add("countermap-drawer-open");
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", handleKey);
      document.body.classList.remove("countermap-drawer-open");
      previousFocus.current?.focus();
    };
  }, [onClose]);

  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setFailed(false);
    void fetch(detailUrl, { signal: controller.signal, cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("CounterMap source detail unavailable");
        return response.json() as Promise<CounterMapDetail>;
      })
      .then(setDetail)
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) setFailed(true);
      });
    return () => controller.abort();
  }, [detailUrl]);

  return (
    <div className="countermap-drawer-layer">
      <button
        type="button"
        className="countermap-drawer-scrim"
        tabIndex={-1}
        aria-label="Close moment detail"
        onClick={onClose}
      />
      <aside
        ref={drawerRef}
        className="countermap-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="countermap-drawer-title"
      >
        <header className="countermap-drawer-header">
          <div>
            <p>{eyebrowForNode(node)}</p>
            <h2 id="countermap-drawer-title">{node.title}</h2>
          </div>
          <button ref={closeRef} type="button" aria-label="Close detail drawer" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>
        <div className="countermap-drawer-scroll">
          <p className="countermap-drawer-summary">{node.summary}</p>
          <div className="countermap-drawer-meta" aria-label="Moment context">
            <span>{node.stage ? titleCase(node.stage) : "Interview review"}</span>
            {node.event_range ? (
              <span><Clock3 size={12} aria-hidden="true" /> Moment {node.event_range.start_sequence}</span>
            ) : null}
          </div>

          {failed ? (
            <SourceUnavailable message="This source could not be loaded. The map itself remains available." />
          ) : !detail ? (
            <div className="countermap-detail-loading" role="status" aria-live="polite">
              <span aria-hidden="true" />
              <p>Opening the exact source for this moment…</p>
            </div>
          ) : detail.source_status === "UNAVAILABLE" ? (
            <SourceUnavailable message={detail.message ?? "The exact source is unavailable."} />
          ) : (
            <CandidateDetail detail={detail} node={node} />
          )}

          {connections.length ? (
            <section className="countermap-detail-section countermap-causal-context" aria-labelledby="causal-context-title">
              <p className="countermap-detail-label" id="causal-context-title">Causal context</p>
              <ul>
                {connections.map((connection) => (
                  <li key={connection.id}>
                    {connection.direction === "incoming"
                      ? <ArrowDownLeft size={14} aria-hidden="true" />
                      : <ArrowUpRight size={14} aria-hidden="true" />}
                    <span>
                      <strong>{connection.title}</strong>
                      {connection.label}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          <DeferredActions node={node} />
        </div>
      </aside>
    </div>
  );
}

function CandidateDetail({ detail, node }: { detail: CounterMapDetail; node: CounterMapNode }) {
  return (
    <>
      {detail.statement ? (
        <section className="countermap-detail-section">
          <p className="countermap-detail-label">
            {detail.statement.exact_quote ? "Your exact words" : "Candidate-safe source"}
          </p>
          <blockquote>{detail.statement.text}</blockquote>
        </section>
      ) : null}
      {detail.delivered_prompt ? (
        <section className="countermap-detail-section">
          <p className="countermap-detail-label">Actually delivered</p>
          <blockquote>{detail.delivered_prompt.text}</blockquote>
          <p className="countermap-delivery-state">
            {detail.delivered_prompt.delivery_state === "DELIVERED"
              ? "Delivered in full"
              : "Only the words delivered before interruption are shown"}
          </p>
          {detail.delivered_prompt.why ? (
            <div className="countermap-why-panel">
              <strong>{whyLabel(node.node_type)}</strong>
              <p>{detail.delivered_prompt.why}</p>
            </div>
          ) : null}
          {detail.delivered_prompt.concepts.length || detail.delivered_prompt.skills.length ? (
            <DetailTerms
              concepts={detail.delivered_prompt.concepts}
              skills={detail.delivered_prompt.skills}
            />
          ) : null}
        </section>
      ) : null}
      {detail.code ? <CodeDetail code={detail.code} /> : null}
      {detail.execution ? <ExecutionDetail execution={detail.execution} /> : null}
      {detail.evidence ? <EvidenceDetail evidence={detail.evidence} /> : null}
      {detail.breakpoint ? <BreakpointDetail breakpoint={detail.breakpoint} /> : null}
    </>
  );
}

function CodeDetail({ code }: { code: NonNullable<CounterMapDetail["code"]> }) {
  return (
    <section className="countermap-detail-section countermap-code-detail">
      <div className="countermap-code-heading">
        <div>
          <p className="countermap-detail-label">View code at this moment</p>
          <strong>{titleCase(code.language)} · Snapshot v{code.version}</strong>
        </div>
        <Braces size={18} aria-hidden="true" />
      </div>
      <p>{code.context}</p>
      <pre aria-label={`Read-only ${code.language} code snapshot version ${code.version}`}>
        <code>{code.source_code}</code>
      </pre>
      {code.diff ? (
        <details className="countermap-code-diff">
          <summary>Change from v{code.diff.from_version} to v{code.diff.to_version}</summary>
          {code.diff.change_summary ? <p>{code.diff.change_summary}</p> : null}
          <pre><code>{code.diff.diff_content}</code></pre>
        </details>
      ) : null}
    </section>
  );
}

function ExecutionDetail({ execution }: { execution: NonNullable<CounterMapDetail["execution"]> }) {
  return (
    <section className="countermap-detail-section">
      <p className="countermap-detail-label">Visible run at this moment</p>
      <div className="countermap-run-summary">
        <strong>{titleCase(execution.status)}</strong>
        <span>Code snapshot v{execution.code_snapshot_version}</span>
        <span>{execution.visible_passed} passed · {execution.visible_failed} failed</span>
      </div>
      {execution.visible_tests.length ? (
        <ul className="countermap-visible-tests">
          {execution.visible_tests.map((test) => (
            <li key={test.test_identifier}>
              <Check size={13} aria-hidden="true" />
              <span>{test.test_identifier}</span>
              <strong>{titleCase(test.status)}</strong>
            </li>
          ))}
        </ul>
      ) : (
        <p className="countermap-safe-note">Only candidate-visible test totals are available for this run.</p>
      )}
    </section>
  );
}

function EvidenceDetail({ evidence }: { evidence: NonNullable<CounterMapDetail["evidence"]> }) {
  const assisted = evidence.independence_level !== "INDEPENDENT";
  return (
    <section
      className="countermap-detail-section countermap-evidence-detail"
      data-polarity={evidence.polarity.toLowerCase()}
    >
      <p className="countermap-detail-label">What this showed</p>
      <h3>{evidence.finding}</h3>
      <dl className="countermap-detail-facts">
        <div><dt>Finding</dt><dd>{titleCase(evidence.polarity)}</dd></div>
        <div><dt>Strength</dt><dd>{titleCase(evidence.strength)}</dd></div>
        <div><dt>Independence</dt><dd>{independenceLabel(evidence.independence_level)}</dd></div>
      </dl>
      {assisted ? (
        <p className="countermap-assisted-note">
          This improvement followed guidance, so it is not presented as independent proof.
        </p>
      ) : null}
      <DetailTerms concepts={evidence.concepts} skills={evidence.skills} />
    </section>
  );
}

function BreakpointDetail({ breakpoint }: { breakpoint: NonNullable<CounterMapDetail["breakpoint"]> }) {
  return (
    <section className="countermap-detail-section countermap-breakpoint-detail">
      <p className="countermap-detail-label">Breakpoint</p>
      <h3>{breakpoint.summary}</h3>
      <dl className="countermap-detail-facts">
        <div><dt>Status</dt><dd>{titleCase(breakpoint.status)}</dd></div>
        <div><dt>Importance</dt><dd>{titleCase(breakpoint.severity)}</dd></div>
      </dl>
      <DetailTerms concepts={[breakpoint.concept]} skills={[breakpoint.skill]} />
      {breakpoint.independent_verification_required ? (
        <p className="countermap-verification-note">
          <CircleAlert size={15} aria-hidden="true" />
          Improvement is visible, but independent verification is still missing.
        </p>
      ) : null}
      {breakpoint.evidence.length ? (
        <ol className="countermap-breakpoint-evidence">
          {breakpoint.evidence.map((item, index) => (
            <li key={`${item.relationship}-${index}`}>
              <span>{titleCase(item.relationship)}</span>
              <p>{item.finding}</p>
              <small>{independenceLabel(item.independence_level)}</small>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

function DetailTerms({ concepts, skills }: { concepts: string[]; skills: string[] }) {
  return (
    <dl className="countermap-detail-terms">
      {concepts.length ? <div><dt>Concept</dt><dd>{concepts.join(", ")}</dd></div> : null}
      {skills.length ? <div><dt>Skill</dt><dd>{skills.join(", ")}</dd></div> : null}
    </dl>
  );
}

function SourceUnavailable({ message }: { message: string }) {
  return (
    <div className="countermap-source-unavailable" role="status">
      <CircleAlert size={18} aria-hidden="true" />
      <div><strong>Exact source unavailable</strong><p>{message}</p></div>
    </div>
  );
}

function DeferredActions({ node }: { node: CounterMapNode }) {
  const actions = (node.available_actions ?? []).filter((action) => action.action !== "VIEW_SOURCE");
  if (!actions.length) return null;
  return (
    <section className="countermap-detail-section countermap-deferred-actions" aria-label="Follow-up actions">
      {actions.map((action) => (
        <div key={action.action}>
          <button type="button" disabled>
            {action.label}
          </button>
          <p>{action.reason ?? "This action is not available yet."}</p>
        </div>
      ))}
    </section>
  );
}

type ConnectionDetail = {
  id: string;
  direction: "incoming" | "outgoing";
  title: string;
  label: string;
};

function connectionDetails(graph: CounterMapGraph, node: CounterMapNode): ConnectionDetail[] {
  const nodes = new Map(graph.nodes.map((item) => [item.node_id, item]));
  const result: ConnectionDetail[] = [];
  for (const edge of graph.edges) {
    if (edge.to_node_id === node.node_id) {
      const source = nodes.get(edge.from_node_id);
      if (source) result.push({
        id: edge.edge_id,
        direction: "incoming",
        title: source.title,
        label: relationshipLabel(edge.relationship, node.node_type),
      });
    }
    if (edge.from_node_id === node.node_id) {
      const target = nodes.get(edge.to_node_id);
      if (target) result.push({
        id: edge.edge_id,
        direction: "outgoing",
        title: target.title,
        label: relationshipLabel(edge.relationship, target.node_type),
      });
    }
  }
  return result;
}
