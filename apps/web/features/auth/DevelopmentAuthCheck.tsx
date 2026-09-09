"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useRef, useState } from "react";

import { CounterQApiError } from "@/lib/counterq-api";

import { useCounterQApi } from "./useCounterQApi";

type CheckState = {
  token: "pending" | "yes" | "no" | "unknown";
  api: "pending" | "success" | "failure";
  detail: string | null;
};

const pendingCheck: CheckState = {
  token: "pending",
  api: "pending",
  detail: null,
};

export function DevelopmentAuthCheck() {
  const { getToken, isLoaded, isSignedIn, userId } = useAuth();

  return (
    <main className="development-auth-page">
      <section aria-labelledby="development-auth-title">
        <p className="launcher-kicker">Development diagnostic</p>
        <h1 id="development-auth-title">Authenticated API boundary</h1>
        <p>
          This local-only check reports safe lifecycle stages. It never displays tokens,
          identity claims, provider errors, or profile data.
        </p>
      </section>

      <dl className="development-auth-results" aria-live="polite">
        <DiagnosticResult label="Clerk loaded" value={isLoaded ? "yes" : "no"} />
        <DiagnosticResult
          label="Signed in"
          value={!isLoaded ? "pending" : isSignedIn ? "yes" : "no"}
        />
        {!isLoaded || !isSignedIn ? (
          <>
            <DiagnosticResult
              label="Token obtainable"
              value={!isLoaded ? "pending" : "no"}
            />
            <DiagnosticResult label="CounterQ /api/me" value="pending" />
          </>
        ) : (
          <AuthenticatedCheck key={userId} getToken={getToken} />
        )}
      </dl>
    </main>
  );
}

function AuthenticatedCheck({ getToken }: { getToken: () => Promise<string | null> }) {
  const api = useCounterQApi(getToken);
  const initialCheckStarted = useRef(false);
  const [check, setCheck] = useState<CheckState>(pendingCheck);

  const runCheck = useCallback(async () => {
    setCheck(pendingCheck);
    try {
      await api.getMe();
      setCheck({ token: "yes", api: "success", detail: "HTTP 200" });
    } catch (error: unknown) {
      if (error instanceof CounterQApiError) {
        const token = error.stage === "TOKEN_ACQUISITION" ? "no" : "yes";
        const status = error.status > 0 ? ` · HTTP ${error.status}` : "";
        setCheck({
          token,
          api: "failure",
          detail: `${error.category}${status}`,
        });
        return;
      }
      setCheck({ token: "unknown", api: "failure", detail: "REQUEST_FAILED" });
    }
  }, [api]);

  useEffect(() => {
    if (initialCheckStarted.current) return;
    initialCheckStarted.current = true;
    void runCheck();
  }, [runCheck]);

  return (
    <>
      <DiagnosticResult label="Token obtainable" value={check.token} />
      <DiagnosticResult
        label="CounterQ /api/me"
        value={check.detail ? `${check.api} · ${check.detail}` : check.api}
      />
      <div className="development-auth-action">
        <button type="button" onClick={() => void runCheck()}>
          Run auth check again
        </button>
      </div>
    </>
  );
}

function DiagnosticResult({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
