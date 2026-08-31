"use client";

import type { components } from "@counterq/contracts/openapi";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  DEMO_SPLITTER_STORAGE_KEY,
  type EditorStorageScope,
  readStoredEditorCode,
  readStoredProblemWidth,
  resolveDevelopmentEditorSource,
  writeStoredEditorCode,
  writeStoredProblemWidth,
} from "../hooks/localPersistence";
import { useAuthoritativeDeadlineTimer } from "../hooks/useDemoDeadlineTimer";
import { usePrefersReducedMotion } from "../hooks/usePrefersReducedMotion";
import type { DeliveredConversationRow, DemoInterviewRoomFixture } from "../models/candidate-visible";
import { developmentStarterCode } from "../fixtures/demoInterview";
import { useCodeObservationCollector } from "../realtime/useCodeObservationCollector";
import { useRealtimeVoice } from "../realtime/useRealtimeVoice";
import type { RealtimeVoiceControls } from "../realtime/useRealtimeVoice";
import { EndInterviewDialog } from "./EndInterviewDialog";
import { ExecutionPanel, type ExecutionViewResult } from "./ExecutionPanel";
import { InterviewHeader } from "./InterviewHeader";
import { InterviewerSurface } from "./InterviewerSurface";
import { MonacoInterviewEditor } from "./MonacoInterviewEditor";
import { ProblemPanel } from "./ProblemPanel";
import { RecentConversationDrawer } from "./RecentConversationDrawer";

type InterviewRoomProps = {
  fixture: DemoInterviewRoomFixture;
};

type DevelopmentRunResponse = components["schemas"]["DevelopmentRunResponse"];

export function InterviewRoom({ fixture }: InterviewRoomProps) {
  const [selectedLanguage, setSelectedLanguage] = useState<"cpp" | "python" | "java">(
    fixture.language,
  );
  const [problemWidth, setProblemWidth] = useState(35);
  const [editorCode, setEditorCode] = useState<string>(developmentStarterCode[fixture.language]);
  const [conversationOpen, setConversationOpen] = useState(false);
  const [endDialogOpen, setEndDialogOpen] = useState(false);
  const [executionExpanded, setExecutionExpanded] = useState(false);
  const [hasAttemptedRun, setHasAttemptedRun] = useState(false);
  const [executionRunning, setExecutionRunning] = useState(false);
  const [executionResult, setExecutionResult] = useState<ExecutionViewResult | null>(null);
  const [executionError, setExecutionError] = useState<string | null>(null);
  const [editorHydrated, setEditorHydrated] = useState(false);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  const previousSelectedLanguageRef = useRef(selectedLanguage);
  const previousSessionIdRef = useRef<string | null>(null);
  const prefersReducedMotion = usePrefersReducedMotion();
  const realtimeVoice = useRealtimeVoice({ developmentLanguage: selectedLanguage });
  const configuredLanguage = realtimeVoice.restoredBootstrap?.language ?? selectedLanguage;
  const editorStorageScope = useMemo<EditorStorageScope>(
    () => ({
      language: configuredLanguage,
      interviewSessionId: realtimeVoice.restoredBootstrap?.interview_session_id,
    }),
    [configuredLanguage, realtimeVoice.restoredBootstrap?.interview_session_id],
  );
  const languageLabel = languageLabelFor(configuredLanguage);
  const remainingLabel = useAuthoritativeDeadlineTimer(
    realtimeVoice.serverDeadlineAt,
    fixture.serverNowIso,
    fixture.deadlineAtIso,
    realtimeVoice.isRestoring,
  );
  const terminal = realtimeVoice.terminalSession;

  useEffect(() => {
    if (
      !terminal &&
      !realtimeVoice.isRestoring &&
      realtimeVoice.serverDeadlineAt &&
      Date.parse(realtimeVoice.serverDeadlineAt) <= Date.now()
    ) {
      realtimeVoice.completeForDeadline();
    }
  }, [realtimeVoice, terminal]);
  const currentDeliveredTurn = useMemo(() => {
      const restoredTurn = realtimeVoice.restoredBootstrap?.recent_conversation
        .filter((turn) => turn.speaker === "COUNTERQ")
        .at(-1);
      if (realtimeVoice.currentCounterQDeliveryText) {
        return { ...fixture.currentDeliveredTurn, actualText: realtimeVoice.currentCounterQDeliveryText };
      }
      if (restoredTurn) {
        const deliveryState: "DELIVERED" | "INTERRUPTED" =
          restoredTurn.delivery_state === "INTERRUPTED" ? "INTERRUPTED" : "DELIVERED";
        return {
          id: restoredTurn.id,
          speaker: "CounterQ" as const,
          actualText: restoredTurn.text,
          actualTranscriptSegmentId: restoredTurn.id,
          deliveredAtLabel: "Restored",
          deliveryState,
        };
      }
      return realtimeVoice.isRestoring || realtimeVoice.restoredBootstrap
        ? null
        : fixture.currentDeliveredTurn;
    },
    [
      fixture.currentDeliveredTurn,
      realtimeVoice.isRestoring,
      realtimeVoice.currentCounterQDeliveryText,
      realtimeVoice.restoredBootstrap,
    ],
  );

  const recentConversation = useMemo(
    (): DeliveredConversationRow[] => {
      if (!realtimeVoice.restoredBootstrap) {
        return fixture.recentConversation;
      }
      return realtimeVoice.restoredBootstrap.recent_conversation.map((turn) => {
        if (turn.speaker === "COUNTERQ") {
          return {
            id: turn.id,
            speaker: "CounterQ",
            actualText: turn.text,
            actualTranscriptSegmentId: turn.id,
            deliveredAtLabel: "Restored",
            deliveryState: turn.delivery_state === "INTERRUPTED" ? "INTERRUPTED" : "DELIVERED",
          };
        }
        return {
          id: turn.id,
          speaker: "Candidate",
          actualText: turn.text,
          actualTranscriptSegmentId: turn.id,
          deliveredAtLabel: "Restored",
        };
      });
    },
    [fixture.recentConversation, realtimeVoice.restoredBootstrap],
  );

  useCodeObservationCollector({
    sourceCode: editorCode,
    controlReady: realtimeVoice.canonicalDebug.controlConnected,
    hydrated: editorHydrated,
    canonicalSourceCode: realtimeVoice.restoredBootstrap?.latest_code_snapshot?.source_code ?? null,
    sendSnapshot: realtimeVoice.observeCodeSnapshot,
    noteActivityStarted: realtimeVoice.noteCodeActivityStarted,
    noteActivityIdle: realtimeVoice.noteCodeActivityIdle,
  });

  useEffect(() => {
    setProblemWidth(readStoredProblemWidth(window.localStorage));
  }, []);

  useEffect(() => {
    if (
      previousSelectedLanguageRef.current !== selectedLanguage &&
      !realtimeVoice.restoredBootstrap &&
      !realtimeVoice.isRestoring
    ) {
      setEditorCode(developmentStarterCode[selectedLanguage]);
      setExecutionResult(null);
      setExecutionError(null);
      setHasAttemptedRun(false);
      setExecutionExpanded(false);
    }
    previousSelectedLanguageRef.current = selectedLanguage;
  }, [realtimeVoice.isRestoring, realtimeVoice.restoredBootstrap, selectedLanguage]);

  useEffect(() => {
    if (realtimeVoice.restoredBootstrap) {
      setSelectedLanguage(realtimeVoice.restoredBootstrap.language);
    }
  }, [realtimeVoice.restoredBootstrap]);

  useEffect(() => {
    if (realtimeVoice.isRestoring) {
      setEditorHydrated(false);
      return;
    }
    const canonicalSource = realtimeVoice.restoredBootstrap?.latest_code_snapshot?.source_code;
    const starterCode = developmentStarterCode[configuredLanguage];
    const localSource = canonicalSource
      ? null
      : readStoredEditorCode(window.localStorage, "", editorStorageScope);
    setEditorCode(
      resolveDevelopmentEditorSource({
        canonicalSourceCode: canonicalSource,
        localSourceCode: localSource,
        starterCode,
      }),
    );
    setEditorHydrated(true);
  }, [configuredLanguage, editorStorageScope, realtimeVoice.isRestoring, realtimeVoice.restoredBootstrap]);

  useEffect(() => {
    const sessionId = realtimeVoice.restoredBootstrap?.interview_session_id ?? null;
    if (sessionId && previousSessionIdRef.current && previousSessionIdRef.current !== sessionId) {
      setExecutionResult(null);
      setExecutionError(null);
      setHasAttemptedRun(false);
      setExecutionExpanded(false);
    }
    previousSessionIdRef.current = sessionId;
  }, [realtimeVoice.restoredBootstrap?.interview_session_id]);

  const persistProblemWidth = useCallback((nextWidth: number) => {
    setProblemWidth(writeStoredProblemWidth(window.localStorage, nextWidth));
  }, []);

  const handleEditorChange = useCallback((nextValue: string) => {
    setEditorCode(nextValue);
    writeStoredEditorCode(window.localStorage, nextValue, editorStorageScope);
  }, [editorStorageScope]);

  const runCurrentCode = useCallback(async () => {
    if (terminal || realtimeVoice.completionPending || executionRunning) return;
    setHasAttemptedRun(true);
    setExecutionExpanded(true);
    setExecutionRunning(true);
    setExecutionError(null);
    try {
      const bootstrap = await realtimeVoice.ensureControlSession();
      const idempotencyKey = globalThis.crypto?.randomUUID?.() ?? `run-${Date.now()}`;
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}/api/execution/development-runs`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            interview_session_id: bootstrap.interview_session_id,
            source_code: editorCode,
            idempotency_key: idempotencyKey,
            client_event_id: `run-${idempotencyKey}`,
            client_instance_id: "interview-room-run",
            client_sequence: Date.now(),
            run_kind: "VISIBLE",
          }),
        },
      );
      if (!response.ok) throw new Error("Code execution is temporarily unavailable.");
      const result = await response.json() as DevelopmentRunResponse;
      setExecutionResult({
        status: result.status,
        stdout: result.stdout,
        stderr: result.stderr,
        compilerOutput: result.compiler_output,
        timedOut: result.timed_out,
        outputTruncated: result.output_truncated,
        cases: result.cases.map((testCase) => ({
          identifier: testCase.identifier,
          inputJson: testCase.input_json,
          expectedOutput: testCase.expected_output,
          actualOutput: testCase.actual_output,
          status: testCase.status,
        })),
      });
    } catch (error) {
      setExecutionError(error instanceof Error ? error.message : "Code execution is unavailable.");
    } finally {
      setExecutionRunning(false);
    }
  }, [editorCode, executionRunning, realtimeVoice, terminal]);

  const updateWidthFromClientX = useCallback(
    (clientX: number) => {
      const bounds = workspaceRef.current?.getBoundingClientRect();
      if (!bounds) {
        return;
      }
      const percent = ((clientX - bounds.left) / bounds.width) * 100;
      persistProblemWidth(percent);
    },
    [persistProblemWidth],
  );

  const handlePointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      draggingRef.current = true;
      event.currentTarget.setPointerCapture(event.pointerId);
      updateWidthFromClientX(event.clientX);
    },
    [updateWidthFromClientX],
  );

  const handlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (draggingRef.current) {
        updateWidthFromClientX(event.clientX);
      }
    },
    [updateWidthFromClientX],
  );

  const handlePointerUp = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    draggingRef.current = false;
    event.currentTarget.releasePointerCapture(event.pointerId);
  }, []);

  const handleSplitterKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        persistProblemWidth(problemWidth - 2);
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        persistProblemWidth(problemWidth + 2);
      }
      if (event.key === "Home") {
        event.preventDefault();
        persistProblemWidth(35);
      }
    },
    [persistProblemWidth, problemWidth],
  );

  const workspaceStyle = useMemo(
    () => ({ "--problem-pane-width": `${problemWidth}%` }) as React.CSSProperties,
    [problemWidth],
  );

  return (
    <main
      className="interview-room"
      data-reduced-motion={prefersReducedMotion ? "reduce" : "no-preference"}
    >
      <InterviewHeader
        mode={fixture.mode}
        remainingLabel={terminal ? "00:00" : remainingLabel}
        voiceState={realtimeVoice.voiceState}
        onEndInterview={() => setEndDialogOpen(true)}
        terminal={Boolean(terminal)}
      />

      <div ref={workspaceRef} className="workspace" style={workspaceStyle} aria-busy={realtimeVoice.isRestoring}>
        <ProblemPanel
          problem={{ ...fixture.problem, functionSignature: functionSignatureFor(configuredLanguage) }}
        />
        <div
          className="workspace-resizer"
          role="separator"
          aria-label="Resize problem and editor panes"
          aria-orientation="vertical"
          aria-valuemin={28}
          aria-valuemax={44}
          aria-valuenow={problemWidth}
          tabIndex={0}
          onKeyDown={handleSplitterKeyDown}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
        >
          <span aria-hidden="true" />
        </div>
        <section className="coding-workspace" aria-labelledby="editor-title">
          <div className="editor-toolbar">
            <div>
              <p className="panel-kicker">Workspace</p>
              <h2 id="editor-title">{sourceFilenameFor(configuredLanguage)}</h2>
            </div>
            <div className="editor-meta">
              {!realtimeVoice.restoredBootstrap && !realtimeVoice.isRestoring ? (
                <label className="development-language-picker">
                  <span className="sr-only">Development execution language</span>
                  <select
                    aria-label="Development execution language"
                    value={selectedLanguage}
                    onChange={(event) =>
                      setSelectedLanguage(event.target.value as "cpp" | "python" | "java")
                    }
                  >
                    <option value="cpp">C++17</option>
                    <option value="python">Python 3</option>
                    <option value="java">Java 21</option>
                  </select>
                </label>
              ) : null}
              <span>{languageLabel}</span>
              <span className="local-state">{codePersistenceState(editorCode, realtimeVoice)}</span>
            </div>
          </div>
          <MonacoInterviewEditor
            value={editorCode}
            onChange={handleEditorChange}
            language={configuredLanguage}
            readOnly={realtimeVoice.isRestoring || Boolean(terminal) || realtimeVoice.completionPending}
          />
          <ExecutionPanel
            expanded={executionExpanded}
            hasAttemptedRun={hasAttemptedRun}
            onRun={runCurrentCode}
            onToggle={() => setExecutionExpanded((current) => !current)}
            running={executionRunning}
            result={executionResult}
            error={executionError}
            disabled={Boolean(terminal) || realtimeVoice.completionPending}
          />
        </section>
      </div>

      <InterviewerSurface
        voiceState={realtimeVoice.voiceState}
        isMuted={realtimeVoice.isMuted}
        voiceError={realtimeVoice.errorMessage}
        partialTranscript={realtimeVoice.partialTranscript}
        lastFinalTranscript={realtimeVoice.lastFinalTranscript}
        sessionDebug={realtimeVoice.sessionDebug}
        canonicalDebug={realtimeVoice.canonicalDebug}
        currentTurn={currentDeliveredTurn}
        onEnableMicrophone={realtimeVoice.enableMicrophone}
        onMute={realtimeVoice.mute}
        onUnmute={realtimeVoice.unmute}
        onDisconnectVoice={realtimeVoice.disconnect}
        onSpeakDevelopmentPhrase={realtimeVoice.speakDevelopmentPhrase}
        onEvaluateExaminerDecision={realtimeVoice.evaluateExaminerDecision}
        onDeliverAuthorizedPrompt={realtimeVoice.deliverAuthorizedPrompt}
        onOpenConversation={() => setConversationOpen(true)}
        terminal={Boolean(terminal) || realtimeVoice.completionPending}
      />
      <RecentConversationDrawer
        open={conversationOpen}
        rows={recentConversation}
        onClose={() => setConversationOpen(false)}
      />
      {realtimeVoice.isRestoring ? (
        <p className="restore-status" role="status">Restoring interview...</p>
      ) : null}
      {terminal ? (
        <p className="restore-status" role="status">
          {terminal.reason === "TIME_EXPIRED" ? "Time's up. This interview has ended." : "Interview ended."}
        </p>
      ) : null}
      <EndInterviewDialog
        open={endDialogOpen}
        onCancel={() => setEndDialogOpen(false)}
        onConfirm={() => {
          setEndDialogOpen(false);
          realtimeVoice.endInterview();
        }}
      />
      <span className="storage-key-marker" data-storage-key={DEMO_SPLITTER_STORAGE_KEY} aria-hidden="true" />
    </main>
  );
}

function languageLabelFor(language: "cpp" | "python" | "java"): "C++17" | "Python 3" | "Java 21" {
  return language === "cpp" ? "C++17" : language === "python" ? "Python 3" : "Java 21";
}

function sourceFilenameFor(language: "cpp" | "python" | "java"): string {
  return language === "cpp" ? "Solution.cpp" : language === "python" ? "solution.py" : "Solution.java";
}

function functionSignatureFor(language: "cpp" | "python" | "java"): string {
  if (language === "python") {
    return "def lengthOfLongestSubstring(self, s: str) -> int";
  }
  if (language === "java") {
    return "int lengthOfLongestSubstring(String s)";
  }
  return "int lengthOfLongestSubstring(string s)";
}

export function codePersistenceState(
  editorCode: string,
  realtimeVoice: Pick<
    RealtimeVoiceControls,
    "acknowledgedCodeSource" | "canonicalDebug" | "isRestoring"
  >,
): "SYNCED" | "LOCAL_PENDING" | "PERSISTENCE_UNCONFIRMED" {
  if (realtimeVoice.isRestoring) {
    return "PERSISTENCE_UNCONFIRMED";
  }
  if (realtimeVoice.acknowledgedCodeSource === editorCode) {
    return "SYNCED";
  }
  if (realtimeVoice.canonicalDebug.controlConnected) {
    return "LOCAL_PENDING";
  }
  return "PERSISTENCE_UNCONFIRMED";
}
