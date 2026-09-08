"use client";

import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { CounterQApiError } from "@/lib/counterq-api";
import type {
  CandidateProfileUpdate,
  CounterQApiClient,
  CurrentUserResponse,
} from "@/lib/counterq-api";

import { useCounterQApi } from "./useCounterQApi";

export function OnboardingExperience() {
  const { getToken, isLoaded, isSignedIn, sessionId, userId } = useAuth();

  if (!isLoaded) return <AuthLoadingState />;
  if (!isSignedIn) return <SignedOutState />;
  return (
    <AuthenticatedOnboarding
      key={sessionId ?? userId ?? "authenticated-session"}
      getToken={getToken}
    />
  );
}

function AuthenticatedOnboarding({ getToken }: { getToken: () => Promise<string | null> }) {
  const api = useCounterQApi(getToken);
  const router = useRouter();
  const onComplete = useCallback(() => router.replace("/?profile=ready"), [router]);
  return <OnboardingForm api={api} onComplete={onComplete} />;
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
  const profileLoad = useRef<{
    api: CounterQApiClient;
    request: Promise<CurrentUserResponse>;
  } | null>(null);
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
    if (profileLoad.current?.api !== api) {
      profileLoad.current = { api, request: api.getMe() };
    }
    void profileLoad.current.request
      .then((me) => {
        if (!active) return;
        if (!me.onboarding_required && me.profile) {
          onComplete();
          return;
        }
        const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || null;
        setForm((current) => ({ ...current, timezone }));
      })
      .catch((requestError: unknown) => {
        if (active) setError(profileLoadFailureMessage(requestError));
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
    } catch (requestError: unknown) {
      setError(profileSaveFailureMessage(requestError));
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

function AuthLoadingState() {
  return (
    <OnboardingBoundary
      title="Preparing your interview settings."
      description="Confirming your secure CounterQ session before loading your profile."
    >
      <p className="onboarding-status" role="status">Connecting your signed-in workspace…</p>
    </OnboardingBoundary>
  );
}

function SignedOutState() {
  return (
    <OnboardingBoundary
      title="Sign in to continue your setup."
      description="Your interview settings stay connected to your authenticated CounterQ account."
    >
      <Link className="launcher-link launcher-link-primary" href="/sign-in">
        Continue to sign in
      </Link>
    </OnboardingBoundary>
  );
}

function OnboardingBoundary({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <main className="onboarding-page">
      <section className="onboarding-intro" aria-labelledby="onboarding-boundary-title">
        <p className="launcher-kicker">Interview calibration</p>
        <h1 id="onboarding-boundary-title">{title}</h1>
        <p>{description}</p>
      </section>
      <section className="onboarding-form" aria-live="polite">
        {children}
      </section>
    </main>
  );
}

function profileLoadFailureMessage(error: unknown): string {
  if (error instanceof CounterQApiError) {
    if (error.category === "AUTHENTICATION_REQUIRED") {
      return "CounterQ could not confirm your sign-in. Sign in again and retry.";
    }
    if (error.category === "ACCESS_DENIED") {
      return "CounterQ could not access this profile. Sign in again or contact support.";
    }
  }
  return "CounterQ could not load your profile. Check your connection and try again.";
}

function profileSaveFailureMessage(error: unknown): string {
  if (error instanceof CounterQApiError) {
    if (error.category === "AUTHENTICATION_REQUIRED") {
      return "Your sign-in expired before these preferences were saved. Sign in again and retry.";
    }
    if (error.category === "ACCESS_DENIED") {
      return "CounterQ could not update this profile. Sign in again or contact support.";
    }
    if (error.status === 400 || error.status === 422) {
      return "Your preferences were not saved. Check the selections and try again.";
    }
  }
  return "Your preferences were not saved. Check your connection and try again.";
}
