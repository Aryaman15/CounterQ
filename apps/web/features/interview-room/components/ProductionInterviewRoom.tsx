"use client";

import { useAuth } from "@clerk/nextjs";
import Link from "next/link";

import { useCounterQApi } from "@/features/auth/useCounterQApi";

import { demoInterviewFixture } from "../fixtures/demoInterview";
import { InterviewRoom } from "./InterviewRoom";

export function ProductionInterviewRoom({ interviewSessionId }: { interviewSessionId: string }) {
  const { getToken, isLoaded, isSignedIn, userId } = useAuth();
  if (!isLoaded) {
    return <RoomBoundary message="Confirming your secure CounterQ session…" />;
  }
  if (!isSignedIn) {
    return (
      <RoomBoundary message="Sign in to restore this interview.">
        <Link className="launcher-link launcher-link-primary" href="/sign-in">
          Continue to sign in
        </Link>
      </RoomBoundary>
    );
  }
  return (
    <AuthenticatedProductionInterviewRoom
      key={`${userId}:${interviewSessionId}`}
      interviewSessionId={interviewSessionId}
      getToken={getToken}
    />
  );
}

function AuthenticatedProductionInterviewRoom({
  interviewSessionId,
  getToken,
}: {
  interviewSessionId: string;
  getToken: () => Promise<string | null>;
}) {
  const api = useCounterQApi(getToken);
  return (
    <InterviewRoom
      fixture={demoInterviewFixture}
      allowFixturePreview={false}
      runtime={{ kind: "production", interviewSessionId, api }}
    />
  );
}

function RoomBoundary({ message, children }: { message: string; children?: React.ReactNode }) {
  return (
    <main className="interview-setup">
      <p className="panel-kicker">Authenticated interview</p>
      <h1>Restoring your workspace.</h1>
      <p role="status">{message}</p>
      {children}
    </main>
  );
}
