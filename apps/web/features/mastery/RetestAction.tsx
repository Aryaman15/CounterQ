"use client";

import type { components } from "@counterq/contracts/openapi";

export type RetestState = "idle" | "starting" | "launched" | "error";

type Recommendation = components["schemas"]["CandidateRetestRecommendation"];

export function RetestAction({
  recommendation,
  state = "idle",
  error = "",
  descriptionId,
  onStart,
}: {
  recommendation: Recommendation;
  state?: RetestState;
  error?: string;
  descriptionId: string;
  onStart?: (recommendationId: string) => void;
}) {
  return (
    <>
      <button
        type="button"
        disabled={
          !recommendation.action_enabled ||
          !onStart ||
          state === "starting" ||
          state === "launched"
        }
        aria-describedby={descriptionId}
        onClick={() => onStart?.(recommendation.recommendation_id)}
      >
        {state === "starting"
          ? "Starting Quick Drill…"
          : state === "launched"
            ? "Quick Drill ready"
            : recommendation.action_label}
      </button>
      <small id={descriptionId} aria-live="polite">
        {error || recommendation.availability_message}
      </small>
    </>
  );
}
