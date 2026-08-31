"use client";

import type { components } from "@counterq/contracts/openapi";
import { useEffect, useMemo, useState } from "react";

type CatalogItem = components["schemas"]["CuratedCatalogItem"];
type Language = "cpp" | "python" | "java";

type InterviewSetupProps = {
  busy: boolean;
  error: string | null;
  onStart: (problemVersionId: string, language: Language) => Promise<void>;
};

const languageLabels: Record<Language, string> = {
  cpp: "C++17",
  python: "Python 3",
  java: "Java 21",
};

export function InterviewSetup({ busy, error, onStart }: InterviewSetupProps) {
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [problemVersionId, setProblemVersionId] = useState("");
  const [language, setLanguage] = useState<Language | "">("");

  useEffect(() => {
    const controller = new AbortController();
    void fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}/api/problems/curated`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("The curated problem catalog is unavailable.");
        const items = (await response.json()) as CatalogItem[];
        setCatalog([...items].sort((left, right) => left.catalog_order - right.catalog_order));
        setCatalogError(items.length ? null : "No reviewed curated problems are available.");
      })
      .catch((requestError: unknown) => {
        if ((requestError as Error).name !== "AbortError") {
          setCatalogError("The curated problem catalog is unavailable.");
        }
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const selectedProblem = useMemo(
    () => catalog.find((item) => item.problem_version_id === problemVersionId) ?? null,
    [catalog, problemVersionId],
  );

  useEffect(() => {
    if (language && !selectedProblem?.supported_languages.includes(language)) setLanguage("");
  }, [language, selectedProblem]);

  return (
    <main className="interview-setup">
      <section className="setup-shell" aria-labelledby="setup-title">
        <div className="setup-heading">
          <p className="panel-kicker">CounterQ interview setup</p>
          <h1 id="setup-title">Choose the problem you want to defend.</h1>
          <p>Simulation · New Grad · reviewed curated content</p>
        </div>

        {loading ? <p role="status">Loading curated problems…</p> : null}
        {catalogError ? <p className="setup-error" role="alert">{catalogError}</p> : null}
        {!loading && catalog.length ? (
          <div className="setup-grid">
            <fieldset className="problem-selector">
              <legend>Problem</legend>
              {catalog.map((item) => (
                <label key={item.problem_version_id} className="problem-option">
                  <input
                    type="radio"
                    name="curated-problem"
                    value={item.problem_version_id}
                    checked={problemVersionId === item.problem_version_id}
                    onChange={() => setProblemVersionId(item.problem_version_id)}
                  />
                  <span className="problem-order">{String(item.catalog_order).padStart(2, "0")}</span>
                  <span>
                    <strong>{item.title}</strong>
                    <small>{item.supported_languages.map((itemLanguage) => languageLabels[itemLanguage]).join(" · ")}</small>
                  </span>
                </label>
              ))}
            </fieldset>

            <fieldset className="language-selector" disabled={!selectedProblem}>
              <legend>Language</legend>
              {(["cpp", "python", "java"] as const).map((itemLanguage) => (
                <label key={itemLanguage}>
                  <input
                    type="radio"
                    name="interview-language"
                    value={itemLanguage}
                    checked={language === itemLanguage}
                    disabled={!selectedProblem?.supported_languages.includes(itemLanguage)}
                    onChange={() => setLanguage(itemLanguage)}
                  />
                  <span>{languageLabels[itemLanguage]}</span>
                </label>
              ))}
              <button
                type="button"
                className="start-interview-button"
                disabled={!problemVersionId || !language || busy}
                onClick={() => void onStart(problemVersionId, language as Language)}
              >
                {busy ? "Starting…" : "Start Interview"}
              </button>
              {error ? <p className="setup-error" role="alert">{error}</p> : null}
            </fieldset>
          </div>
        ) : null}
      </section>
    </main>
  );
}
