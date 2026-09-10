"use client";

import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useCounterQApi } from "@/features/auth/useCounterQApi";
import type { CounterQApiClient, CurrentUserResponse } from "@/lib/counterq-api";

import { ProductShell } from "./ProductShell";

type ProductPageContext = {
  api: CounterQApiClient;
  me: CurrentUserResponse;
};

export function AuthenticatedProductPage({
  children,
  signedOut,
}: {
  children: (context: ProductPageContext) => React.ReactNode;
  signedOut?: React.ReactNode;
}) {
  const { getToken, isLoaded, isSignedIn, userId } = useAuth();
  if (!isLoaded) return <ProductBoundary message="Confirming your CounterQ session…" />;
  if (!isSignedIn) return signedOut ?? <SignedOutProductBoundary />;
  return (
    <AuthenticatedProductBody key={userId} getToken={getToken}>
      {children}
    </AuthenticatedProductBody>
  );
}

function AuthenticatedProductBody({
  getToken,
  children,
}: {
  getToken: () => Promise<string | null>;
  children: (context: ProductPageContext) => React.ReactNode;
}) {
  const api = useCounterQApi(getToken);
  const router = useRouter();
  const request = useRef<Promise<CurrentUserResponse> | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [me, setMe] = useState<CurrentUserResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    const pending = request.current ?? api.getMe();
    request.current = pending;
    setFailed(false);
    void pending
      .then((current) => {
        if (!active) return;
        if (current.onboarding_required || !current.profile) {
          router.replace("/onboarding");
          return;
        }
        setMe(current);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => { active = false; };
  }, [api, attempt, router]);

  if (failed) {
    return (
      <ProductBoundary message="CounterQ could not load your candidate workspace.">
        <button
          type="button"
          className="product-boundary-action"
          onClick={() => {
            request.current = null;
            setAttempt((value) => value + 1);
          }}
        >
          Try again
        </button>
      </ProductBoundary>
    );
  }
  if (!me) return <ProductBoundary message="Loading your candidate workspace…" />;
  return <ProductShell me={me}>{children({ api, me })}</ProductShell>;
}

function SignedOutProductBoundary() {
  return (
    <ProductBoundary message="Sign in to open your CounterQ workspace.">
      <Link className="product-boundary-action" href="/sign-in">Sign in</Link>
    </ProductBoundary>
  );
}

function ProductBoundary({
  message,
  children,
}: {
  message: string;
  children?: React.ReactNode;
}) {
  return (
    <main className="product-boundary" aria-live="polite">
      <div className="product-wordmark"><span aria-hidden="true">CQ</span><strong>CounterQ</strong></div>
      <p role="status">{message}</p>
      {children}
    </main>
  );
}
