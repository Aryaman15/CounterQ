"use client";

import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useCounterQApi } from "@/features/auth/useCounterQApi";
import type {
  CounterQApiClient,
  CreateInterviewRequest,
  CuratedCatalogItem,
  CustomProblemPreparationResponse,
  CurrentUserResponse,
} from "@/lib/counterq-api";

type Language = CreateInterviewRequest["language"];
type InterviewMode = CreateInterviewRequest["mode"];
type InterviewTemplate = CreateInterviewRequest["template"];
type ProblemSource = "CURATED" | "CUSTOM";

const languageLabels: Record<Language, string> = {
  cpp: "C++17",
  python: "Python 3",
  java: "Java 21",
};

const levelLabels: Record<string, string> = {
  INTERN: "Intern",
  NEW_GRAD: "New graduate",
  EARLY_CAREER: "Early career",
};

const templateDetails: Record<InterviewTemplate, { label: string; duration: string }> = {
  QUICK_DRILL: { label: "Quick drill", duration: "10 minutes" },
  STANDARD_CODING_INTERVIEW: {
    label: "Standard coding interview",
    duration: "30 minutes",
  },
};

export function SelfServeInterviewSetup() {
  const { getToken, isLoaded, isSignedIn, userId } = useAuth();

  if (!isLoaded) {
    return <SetupBoundary status="Confirming your secure CounterQ session…" />;
  }
  if (!isSignedIn) {
    return (
      <SetupBoundary status="Sign in to configure a production interview.">
        <Link className="launcher-link launcher-link-primary" href="/sign-in">
          Continue to sign in
        </Link>
      </SetupBoundary>
    );
  }
  return <AuthenticatedSetup key={userId} getToken={getToken} />;
}

function AuthenticatedSetup({ getToken }: { getToken: () => Promise<string | null> }) {
  const api = useCounterQApi(getToken);
  const router = useRouter();
  const onOnboardingRequired = useCallback(() => router.replace("/onboarding"), [router]);
  const onCreated = useCallback((interviewPath: string) => router.push(interviewPath), [router]);
  return (
    <SelfServeInterviewSetupForm
      api={api}
      onOnboardingRequired={onOnboardingRequired}
      onCreated={onCreated}
    />
  );
}

export function SelfServeInterviewSetupForm({
  api,
  onOnboardingRequired,
  onCreated,
}: {
  api: CounterQApiClient;
  onOnboardingRequired: () => void;
  onCreated: (interviewPath: string) => void;
}) {
  const [me, setMe] = useState<CurrentUserResponse | null>(null);
  const [catalog, setCatalog] = useState<CuratedCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [template, setTemplate] = useState<InterviewTemplate>("STANDARD_CODING_INTERVIEW");
  const [mode, setMode] = useState<InterviewMode>("SIMULATION");
  const [language, setLanguage] = useState<Language>("python");
  const [problemVersionId, setProblemVersionId] = useState("");
  const [problemSource, setProblemSource] = useState<ProblemSource>("CURATED");
  const [customProblemText, setCustomProblemText] = useState("");
  const [customPreparation, setCustomPreparation] = useState<CustomProblemPreparationResponse | null>(null);
  const [preparingCustom, setPreparingCustom] = useState(false);
  const [customError, setCustomError] = useState<string | null>(null);
  const submissionInFlight = useRef(false);
  const preparationInFlight = useRef(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void api.getMe()
      .then(async (currentUser) => {
        if (!active) return;
        if (currentUser.onboarding_required || !currentUser.profile) {
          onOnboardingRequired();
          return;
        }
        const items = [...await api.getCuratedCatalog()]
          .sort((left, right) => left.catalog_order - right.catalog_order);
        if (!active) return;
        setMe(currentUser);
        setMode(currentUser.profile.default_interview_mode);
        setLanguage(currentUser.profile.preferred_language);
        setCatalog(items);
        setProblemVersionId(
          items.find((item) => item.supported_languages.includes(
            currentUser.profile!.preferred_language,
          ))?.problem_version_id ?? "",
        );
      })
      .catch(() => {
        if (active) setError("CounterQ could not load interview setup right now.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [api, onOnboardingRequired]);

  const selectedProblem = useMemo(
    () => catalog.find((item) => item.problem_version_id === problemVersionId) ?? null,
    [catalog, problemVersionId],
  );
  const recommendedProblemId = catalog.find(
    (item) => item.supported_languages.includes(language),
  )?.problem_version_id;

  function selectLanguage(nextLanguage: Language) {
    setLanguage(nextLanguage);
    if (!selectedProblem?.supported_languages.includes(nextLanguage)) {
      setProblemVersionId(
        catalog.find((item) => item.supported_languages.includes(nextLanguage))
          ?.problem_version_id ?? "",
      );
    }
  }

  async function prepareCustomProblem() {
    if (preparationInFlight.current || customProblemText.trim().length === 0) return;
    preparationInFlight.current = true;
    setPreparingCustom(true);
    setCustomError(null);
    try {
      let preparation = customPreparation;
      if (!preparation) {
        const idempotencyKey = globalThis.crypto?.randomUUID?.() ?? `custom-${Date.now()}`;
        preparation = await api.createCustomProblemPreparation({
          problem_text: customProblemText,
          idempotency_key: idempotencyKey,
        });
        setCustomPreparation(preparation);
      }
      const prepared = await api.prepareCustomProblem(preparation.preparation_id);
      setCustomPreparation(prepared);
    } catch {
      setCustomError("CounterQ could not prepare this problem right now. Try again.");
    } finally {
      preparationInFlight.current = false;
      setPreparingCustom(false);
    }
  }

  const customReady = customPreparation?.quality_outcome === "READY"
    && customPreparation.problem_version_id !== null
    && customPreparation.supported_languages.includes(language);
  const launchProblemVersionId = problemSource === "CURATED"
    ? problemVersionId
    : customReady ? customPreparation.problem_version_id : null;

  async function startInterview() {
    if (submissionInFlight.current || !launchProblemVersionId) return;
    submissionInFlight.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const created = await api.createInterview({
        problem_version_id: launchProblemVersionId,
        template,
        mode,
        language,
      });
      onCreated(created.interview_path);
    } catch {
      submissionInFlight.current = false;
      setSubmitting(false);
      setError("CounterQ could not start this interview. Review the selection and try again.");
    }
  }

  const profile = me?.profile;
  const selectedTemplate = templateDetails[template];
  return (
    <main className="interview-setup self-serve-setup">
      <section className="setup-shell" aria-labelledby="self-serve-setup-title">
        <div className="setup-heading">
          <p className="panel-kicker">New CounterQ interview</p>
          <h1 id="self-serve-setup-title">Set the conditions. Then defend your reasoning.</h1>
          <p>A focused session with reviewed or prepared problems, live voice, and a server-owned clock.</p>
        </div>

        {loading ? <p role="status">Loading your profile and curated catalog…</p> : null}
        {!loading && profile ? (
          <div className="self-serve-layout">
            <form className="self-serve-form" onSubmit={(event) => {
              event.preventDefault();
              void startInterview();
            }}>
              <fieldset>
                <legend>Format</legend>
                <div className="setup-choice-row">
                  {(Object.keys(templateDetails) as InterviewTemplate[]).map((item) => (
                    <label className="setup-choice" key={item}>
                      <input
                        type="radio"
                        name="template"
                        checked={template === item}
                        onChange={() => setTemplate(item)}
                      />
                      <span><strong>{templateDetails[item].label}</strong><small>{templateDetails[item].duration}</small></span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <fieldset>
                <legend>Problem source</legend>
                <div className="setup-choice-row">
                  <label className="setup-choice">
                    <input
                      type="radio"
                      name="problem-source"
                      checked={problemSource === "CURATED"}
                      onChange={() => setProblemSource("CURATED")}
                    />
                    <span><strong>Reviewed problems</strong><small>CounterQ&apos;s prepared catalog</small></span>
                  </label>
                  <label className="setup-choice">
                    <input
                      type="radio"
                      name="problem-source"
                      checked={problemSource === "CUSTOM"}
                      onChange={() => setProblemSource("CUSTOM")}
                    />
                    <span><strong>Paste your own</strong><small>Prepared before it can launch</small></span>
                  </label>
                </div>
              </fieldset>

              <div className="setup-compact-fields">
                <label>
                  Mode
                  <select value={mode} onChange={(event) => setMode(event.target.value as InterviewMode)}>
                    <option value="SIMULATION">Simulation</option>
                    <option value="COACH">Coach</option>
                  </select>
                </label>
                <label>
                  Coding language
                  <select value={language} onChange={(event) => selectLanguage(event.target.value as Language)}>
                    <option value="cpp">C++17</option>
                    <option value="python">Python 3</option>
                    <option value="java">Java 21</option>
                  </select>
                </label>
                <label>
                  Interview level
                  <output aria-label="Interview level inherited from profile">
                    {levelLabels[profile.interview_level] ?? profile.interview_level}
                  </output>
                </label>
              </div>

              {problemSource === "CURATED" ? (
                <fieldset className="problem-selector setup-catalog" disabled={submitting}>
                  <legend>Reviewed problem</legend>
                  {catalog.map((item) => {
                  const compatible = item.supported_languages.includes(language);
                  return (
                    <label
                      key={item.problem_version_id}
                      className={`problem-option${compatible ? "" : " problem-option-disabled"}`}
                    >
                      <input
                        type="radio"
                        name="curated-problem"
                        value={item.problem_version_id}
                        checked={problemVersionId === item.problem_version_id}
                        disabled={!compatible}
                        onChange={() => setProblemVersionId(item.problem_version_id)}
                      />
                      <span className="problem-order">{String(item.catalog_order).padStart(2, "0")}</span>
                      <span>
                        <strong>{item.title}{item.problem_version_id === recommendedProblemId ? <em>CounterQ pick</em> : null}</strong>
                        <small>{compatible ? `Ready for ${languageLabels[language]}` : `Not available in ${languageLabels[language]}`}</small>
                      </span>
                    </label>
                  );
                  })}
                  {!catalog.length ? <p>No reviewed problems are available right now.</p> : null}
                </fieldset>
              ) : (
                <fieldset className="custom-problem-intake" disabled={submitting || preparingCustom}>
                  <legend>Paste a coding problem</legend>
                  <label htmlFor="custom-problem-text">
                    Full statement
                    <textarea
                      id="custom-problem-text"
                      rows={12}
                      maxLength={20_000}
                      value={customProblemText}
                      placeholder="Include the statement, constraints, examples, expected outputs, and function signature."
                      onChange={(event) => {
                        setCustomProblemText(event.target.value);
                        setCustomPreparation(null);
                        setCustomError(null);
                      }}
                    />
                  </label>
                  <div className="custom-problem-actions">
                    <small>{customProblemText.length.toLocaleString()} / 20,000 characters</small>
                    <button
                      type="button"
                      className="prepare-problem-button"
                      disabled={!customProblemText.trim() || preparingCustom}
                      onClick={() => void prepareCustomProblem()}
                    >
                      {preparingCustom ? "Preparing problem…" : customPreparation?.retryable ? "Retry preparation" : "Prepare problem"}
                    </button>
                  </div>
                  {customPreparation ? (
                    <CustomPreparationStatus preparation={customPreparation} />
                  ) : null}
                  {customError ? <p className="setup-error" role="alert">{customError}</p> : null}
                </fieldset>
              )}

              {error ? <p className="setup-error" role="alert">{error}</p> : null}
              <button
                type="submit"
                className="start-interview-button"
                disabled={submitting || !launchProblemVersionId || Boolean(error)}
              >
                {submitting ? "Starting interview…" : "Start interview"}
              </button>
            </form>

            <aside className="setup-summary" aria-label="Interview summary">
              <p className="panel-kicker">Session brief</p>
              <strong>{selectedTemplate.label}</strong>
              <dl>
                <div><dt>Duration</dt><dd>{selectedTemplate.duration}</dd></div>
                <div><dt>Mode</dt><dd>{mode === "SIMULATION" ? "Simulation" : "Coach"}</dd></div>
                <div><dt>Language</dt><dd>{languageLabels[language]}</dd></div>
                <div><dt>Level</dt><dd>{levelLabels[profile.interview_level] ?? profile.interview_level}</dd></div>
                <div><dt>Problem</dt><dd>{problemSource === "CURATED" ? selectedProblem?.title ?? "Choose a problem" : customPreparation?.title ?? "Awaiting preparation"}</dd></div>
              </dl>
              <p>The timer begins when you start. Refreshing the room never resets it.</p>
            </aside>
          </div>
        ) : null}
      </section>
    </main>
  );
}

function CustomPreparationStatus({
  preparation,
}: {
  preparation: CustomProblemPreparationResponse;
}) {
  const ready = preparation.quality_outcome === "READY";
  const label = preparation.operational_status === "FAILED"
    ? "Preparation interrupted"
    : preparation.quality_outcome === "NEEDS_CORRECTION"
      ? "Needs correction"
      : preparation.quality_outcome === "REJECTED"
        ? "Not supported"
        : ready ? "Ready" : "Preparing";
  return (
    <section
      className={`custom-preparation-status custom-preparation-status-${ready ? "ready" : "attention"}`}
      aria-live="polite"
    >
      <p className="panel-kicker">{label}</p>
      {preparation.title ? <h2>{preparation.title}</h2> : null}
      <p>{preparation.message}</p>
      {preparation.statement_preview ? <p>{preparation.statement_preview}</p> : null}
      {preparation.concept_labels.length ? (
        <p><strong>Focus:</strong> {preparation.concept_labels.join(" · ")}</p>
      ) : null}
    </section>
  );
}

function SetupBoundary({ status, children }: { status: string; children?: React.ReactNode }) {
  return (
    <main className="interview-setup self-serve-setup">
      <section className="setup-shell">
        <p className="panel-kicker">New CounterQ interview</p>
        <h1>Preparing your interview workspace.</h1>
        <p role="status">{status}</p>
        {children}
      </section>
    </main>
  );
}
