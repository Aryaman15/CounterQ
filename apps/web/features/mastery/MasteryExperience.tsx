"use client";

import type { components } from "@counterq/contracts/openapi";
import { ArrowUpRight, Clock3, Layers3, RotateCcw } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { MasteryDetailDrawer } from "./MasteryDetailDrawer";

type Overview = components["schemas"]["CandidateMasteryOverviewResponse"];
type Target = components["schemas"]["CandidateMasteryTarget"];
type RetestLaunch = components["schemas"]["RetestLaunchResponse"];
type RetestState = "idle" | "starting" | "launched" | "error";

const stateOrder = ["STRONG", "DEVELOPING", "WEAK", "EXPOSED"] as const;

export function MasteryExperience({
  overview,
  onStartRetest,
  onRetestLaunched,
}: {
  overview: Overview;
  onStartRetest?: (recommendationId: string) => Promise<RetestLaunch>;
  onRetestLaunched?: (launch: RetestLaunch) => void;
}) {
  const [selected, setSelected] = useState<Target | null>(null);
  const [retestStates, setRetestStates] = useState<Record<string, RetestState>>({});
  const [retestErrors, setRetestErrors] = useState<Record<string, string>>({});
  const pendingRetests = useRef(new Set<string>());
  const concepts = useMemo(() => groupTargets(overview.technical_concepts), [overview]);
  const skills = useMemo(() => groupTargets(overview.interview_skills), [overview]);

  const startRetest = async (recommendationId: string) => {
    if (!onStartRetest || pendingRetests.current.has(recommendationId)) return;
    pendingRetests.current.add(recommendationId);
    setRetestStates((current) => ({ ...current, [recommendationId]: "starting" }));
    setRetestErrors((current) => ({ ...current, [recommendationId]: "" }));
    try {
      const launch = await onStartRetest(recommendationId);
      setRetestStates((current) => ({ ...current, [recommendationId]: "launched" }));
      onRetestLaunched?.(launch);
    } catch (error) {
      setRetestStates((current) => ({ ...current, [recommendationId]: "error" }));
      setRetestErrors((current) => ({
        ...current,
        [recommendationId]: error instanceof Error
          ? error.message
          : "No suitable retest is available yet.",
      }));
    } finally {
      pendingRetests.current.delete(recommendationId);
    }
  };

  if (overview.status === "EMPTY") {
    return (
      <section className="mastery-empty" aria-labelledby="mastery-empty-title">
        <div className="mastery-empty-axis" aria-hidden="true"><span /><span /><span /></div>
        <div>
          <p className="mastery-section-label">Evidence horizon</p>
          <h2 id="mastery-empty-title">No conclusions before the evidence.</h2>
          <p>{overview.message}</p>
        </div>
      </section>
    );
  }

  return (
    <section className="mastery-experience" aria-label="Mastery overview">
      {overview.status === "UPDATING" ? (
        <div className="mastery-update-note" role="status"><RotateCcw size={14} /> {overview.message}</div>
      ) : overview.status === "STALE" ? (
        <div className="mastery-update-note" role="status">{overview.message}</div>
      ) : overview.status === "FAILED" ? (
        <div className="mastery-update-note mastery-update-failed" role="status">{overview.message}</div>
      ) : null}

      <div className="mastery-overview-intro">
        <div>
          <p className="mastery-section-label">Current evidence model</p>
          <h2>Mastery</h2>
          <p>{overview.message}</p>
        </div>
        <dl>
          <div><dt>Target</dt><dd>{titleCase(overview.target_level)}</dd></div>
        </dl>
      </div>

      {overview.parent_summaries.length ? (
        <section className="mastery-parent-band" aria-labelledby="mastery-area-title">
          <div className="mastery-section-heading">
            <Layers3 size={16} aria-hidden="true" />
            <div><p>Area summaries</p><h3 id="mastery-area-title">The wider pattern</h3></div>
          </div>
          <div className="mastery-parent-list">
            {overview.parent_summaries.map((target) => (
              <MasteryRow key={target.target_id} target={target} onOpen={setSelected} />
            ))}
          </div>
        </section>
      ) : null}

      <MasterySection
        eyebrow="Technical concepts"
        title="Understanding that survived the interview"
        groups={concepts}
        onOpen={setSelected}
      />

      {overview.retest_recommendations.length ? (
        <section className="mastery-retest" aria-labelledby="mastery-retest-title">
          <div>
            <p className="mastery-section-label">Retest due / ready</p>
            <h3 id="mastery-retest-title">Where another context would be useful</h3>
          </div>
          <ul>
            {overview.retest_recommendations.map((item) => (
              <li key={item.recommendation_id}>
                <div><strong>{item.target_name}</strong><span>{item.reason}</span></div>
                <button
                  type="button"
                  disabled={
                    !item.action_enabled ||
                    !onStartRetest ||
                    ["starting", "launched"].includes(
                      retestStates[item.recommendation_id] ?? "idle",
                    )
                  }
                  aria-describedby={`retest-${item.recommendation_id}`}
                  onClick={() => void startRetest(item.recommendation_id)}
                >
                  {retestStates[item.recommendation_id] === "starting"
                    ? "Starting Quick Drill…"
                    : retestStates[item.recommendation_id] === "launched"
                      ? "Quick Drill ready"
                      : item.action_label}
                </button>
                <small id={`retest-${item.recommendation_id}`} aria-live="polite">
                  {retestErrors[item.recommendation_id] || item.availability_message}
                </small>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <MasterySection
        eyebrow="Interview skills"
        title="How consistently you reason across topics"
        groups={skills}
        onOpen={setSelected}
      />

      {selected ? <MasteryDetailDrawer target={selected} onClose={() => setSelected(null)} /> : null}
    </section>
  );
}

function MasterySection({
  eyebrow,
  title,
  groups,
  onOpen,
}: {
  eyebrow: string;
  title: string;
  groups: Map<string, Target[]>;
  onOpen: (target: Target) => void;
}) {
  if (![...groups.values()].some((items) => items.length)) return null;
  return (
    <section className="mastery-domain" aria-label={eyebrow}>
      <div className="mastery-domain-heading">
        <p className="mastery-section-label">{eyebrow}</p>
        <h3>{title}</h3>
      </div>
      <div className="mastery-state-columns">
        {stateOrder.map((state) => {
          const items = groups.get(state) ?? [];
          if (!items.length) return null;
          return (
            <section key={state} className="mastery-state-group" data-state={state.toLowerCase()}>
              <header><span aria-hidden="true" /><h4>{items[0].state_label}</h4><small>{items.length}</small></header>
              <div>{items.map((target) => <MasteryRow key={target.target_id} target={target} onOpen={onOpen} />)}</div>
            </section>
          );
        })}
      </div>
    </section>
  );
}

function MasteryRow({ target, onOpen }: { target: Target; onOpen: (target: Target) => void }) {
  return (
    <button
      type="button"
      className="mastery-row"
      data-state={target.state.toLowerCase()}
      onClick={() => onOpen(target)}
      aria-label={`Open ${target.display_name} mastery detail, ${target.state_label}`}
    >
      <span className="mastery-row-marker" aria-hidden="true" />
      <span className="mastery-row-copy">
        <strong>{target.display_name}</strong>
        <span>{target.reason}</span>
      </span>
      <span className="mastery-row-meta">
        {target.retest_due ? <em><Clock3 size={11} /> Retest due</em> : null}
        <small>{target.evidence_sufficiency_label}</small>
      </span>
      <ArrowUpRight className="mastery-row-arrow" size={15} aria-hidden="true" />
    </button>
  );
}

function groupTargets(targets: Target[]) {
  const result = new Map<string, Target[]>();
  for (const state of stateOrder) result.set(state, []);
  for (const target of targets) result.get(target.state)?.push(target);
  return result;
}

function titleCase(value: string) {
  return value.toLowerCase().split("_").map((word) => `${word[0]?.toUpperCase() ?? ""}${word.slice(1)}`).join(" ");
}
