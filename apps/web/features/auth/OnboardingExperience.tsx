"use client";

import type { CandidateProfileUpdate, CounterQApiClient } from "@/lib/counterq-api";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useCounterQApi } from "./useCounterQApi";

export function OnboardingExperience() {
  const api = useCounterQApi();
  const router = useRouter();
  return <OnboardingForm api={api} onComplete={() => router.replace("/?profile=ready")} />;
}

export function OnboardingForm({
  api,
  onComplete,
}: {
  api: CounterQApiClient;
  onComplete: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<CandidateProfileUpdate>({
    display_name: null,
    preferred_language: "python",
    default_interview_mode: "SIMULATION",
    interview_level: "NEW_GRAD",
    target_role: null,
    timezone: null,
  });

  useEffect(() => {
    let active = true;
    void api.getMe()
      .then((me) => {
        if (!active) return;
        if (!me.onboarding_required && me.profile) {
          onComplete();
          return;
        }
        const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || null;
        setForm((current) => ({ ...current, timezone }));
      })
      .catch(() => {
        if (active) setError("CounterQ could not load your profile. Try again.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [api, onComplete]);

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.saveProfile(form);
      onComplete();
    } catch {
      setError("Your preferences were not saved. Check the selections and try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="onboarding-page">
      <section className="onboarding-intro" aria-labelledby="onboarding-title">
        <p className="launcher-kicker">Interview calibration</p>
        <h1 id="onboarding-title">Three choices shape how CounterQ interviews you.</h1>
        <p>
          These settings change the questions, coding environment, and intervention policy.
          CounterQ will learn strengths and weaknesses from evidence—not a self-rating form.
        </p>
      </section>
      <form className="onboarding-form" onSubmit={save} aria-busy={loading || saving}>
        <fieldset disabled={loading || saving}>
          <legend>Baseline preferences</legend>
          <label>
            Interview level
            <select
              required
              value={form.interview_level}
              onChange={(event) => setForm({ ...form, interview_level: event.target.value as CandidateProfileUpdate["interview_level"] })}
            >
              <option value="INTERN">Intern</option>
              <option value="NEW_GRAD">New graduate</option>
              <option value="EARLY_CAREER">Early career</option>
            </select>
          </label>
          <label>
            Preferred coding language
            <select
              required
              value={form.preferred_language}
              onChange={(event) => setForm({ ...form, preferred_language: event.target.value as CandidateProfileUpdate["preferred_language"] })}
            >
              <option value="cpp">C++</option>
              <option value="java">Java</option>
              <option value="python">Python</option>
            </select>
          </label>
          <label>
            Default interview mode
            <select
              required
              value={form.default_interview_mode}
              onChange={(event) => setForm({ ...form, default_interview_mode: event.target.value as CandidateProfileUpdate["default_interview_mode"] })}
            >
              <option value="SIMULATION">Simulation</option>
              <option value="COACH">Coach</option>
            </select>
          </label>
          <div className="onboarding-optional">
            <label>
              Display name <span>Optional</span>
              <input
                maxLength={120}
                value={form.display_name ?? ""}
                onChange={(event) => setForm({ ...form, display_name: event.target.value || null })}
              />
            </label>
            <label>
              Target role <span>Optional</span>
              <input
                maxLength={160}
                placeholder="Backend engineer"
                value={form.target_role ?? ""}
                onChange={(event) => setForm({ ...form, target_role: event.target.value || null })}
              />
            </label>
          </div>
        </fieldset>
        {loading ? <p className="onboarding-status" role="status">Loading your profile…</p> : null}
        {error ? <p className="onboarding-error" role="alert">{error}</p> : null}
        <button className="onboarding-submit" type="submit" disabled={loading || saving}>
          {saving ? "Saving preferences…" : "Save interview preferences"}
        </button>
      </form>
    </main>
  );
}
