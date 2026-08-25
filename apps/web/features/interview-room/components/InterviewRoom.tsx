"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  DEMO_SPLITTER_STORAGE_KEY,
  readStoredEditorCode,
  readStoredProblemWidth,
  writeStoredEditorCode,
  writeStoredProblemWidth,
} from "../hooks/localPersistence";
import { useAuthoritativeDeadlineTimer } from "../hooks/useDemoDeadlineTimer";
import { usePrefersReducedMotion } from "../hooks/usePrefersReducedMotion";
import type { DeliveredConversationRow, DemoInterviewRoomFixture } from "../models/candidate-visible";
import { useCodeObservationCollector } from "../realtime/useCodeObservationCollector";
import { useRealtimeVoice } from "../realtime/useRealtimeVoice";
import { EndInterviewDialog } from "./EndInterviewDialog";
import { ExecutionPanel } from "./ExecutionPanel";
import { InterviewHeader } from "./InterviewHeader";
import { InterviewerSurface } from "./InterviewerSurface";
import { MonacoInterviewEditor } from "./MonacoInterviewEditor";
import { ProblemPanel } from "./ProblemPanel";
import { RecentConversationDrawer } from "./RecentConversationDrawer";

type InterviewRoomProps = {
  fixture: DemoInterviewRoomFixture;
};

export function InterviewRoom({ fixture }: InterviewRoomProps) {
  const [problemWidth, setProblemWidth] = useState(35);
  const [editorCode, setEditorCode] = useState(fixture.starterCode);
  const [conversationOpen, setConversationOpen] = useState(false);
  const [endDialogOpen, setEndDialogOpen] = useState(false);
  const [executionExpanded, setExecutionExpanded] = useState(false);
  const [hasAttemptedRun, setHasAttemptedRun] = useState(false);
  const [editorHydrated, setEditorHydrated] = useState(false);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  const prefersReducedMotion = usePrefersReducedMotion();
  const realtimeVoice = useRealtimeVoice();
  const remainingLabel = useAuthoritativeDeadlineTimer(
    realtimeVoice.serverDeadlineAt,
    fixture.serverNowIso,
    fixture.deadlineAtIso,
  );
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
      return realtimeVoice.restoredBootstrap ? null : fixture.currentDeliveredTurn;
    },
    [
      fixture.currentDeliveredTurn,
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
    setEditorCode(readStoredEditorCode(window.localStorage, fixture.starterCode));
  }, [fixture.starterCode]);

  useEffect(() => {
    if (realtimeVoice.restoredBootstrap) {
      setEditorCode(
        realtimeVoice.restoredBootstrap.latest_code_snapshot?.source_code ?? fixture.starterCode,
      );
      setEditorHydrated(true);
      return;
    }
    if (realtimeVoice.isRestoring) {
      setEditorHydrated(false);
    }
  }, [fixture.starterCode, realtimeVoice.isRestoring, realtimeVoice.restoredBootstrap]);

  const persistProblemWidth = useCallback((nextWidth: number) => {
    setProblemWidth(writeStoredProblemWidth(window.localStorage, nextWidth));
  }, []);

  const handleEditorChange = useCallback((nextValue: string) => {
    setEditorCode(nextValue);
    writeStoredEditorCode(window.localStorage, nextValue);
  }, []);

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
        remainingLabel={remainingLabel}
        voiceState={realtimeVoice.voiceState}
        onEndInterview={() => setEndDialogOpen(true)}
      />

      <div ref={workspaceRef} className="workspace" style={workspaceStyle} aria-busy={realtimeVoice.isRestoring}>
        <ProblemPanel problem={fixture.problem} />
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
              <h2 id="editor-title">Solution.cpp</h2>
            </div>
            <div className="editor-meta">
              <span>{fixture.languageLabel}</span>
              <span className="local-state">{fixture.persistenceState}</span>
            </div>
          </div>
          <MonacoInterviewEditor
            value={editorCode}
            onChange={handleEditorChange}
            readOnly={realtimeVoice.isRestoring}
          />
          <ExecutionPanel
            expanded={executionExpanded}
            hasAttemptedRun={hasAttemptedRun}
            onRun={() => {
              setHasAttemptedRun(true);
              setExecutionExpanded(true);
            }}
            onToggle={() => setExecutionExpanded((current) => !current)}
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
      />
      <RecentConversationDrawer
        open={conversationOpen}
        rows={recentConversation}
        onClose={() => setConversationOpen(false)}
      />
      {realtimeVoice.isRestoring ? (
        <p className="restore-status" role="status">Restoring interview...</p>
      ) : null}
      <EndInterviewDialog
        open={endDialogOpen}
        onCancel={() => setEndDialogOpen(false)}
        onConfirm={() => setEndDialogOpen(false)}
      />
      <span className="storage-key-marker" data-storage-key={DEMO_SPLITTER_STORAGE_KEY} aria-hidden="true" />
    </main>
  );
}
