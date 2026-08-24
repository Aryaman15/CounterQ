"use client";

import { useEffect, useRef, useState } from "react";
import { History, Mic, MicOff, PlugZap, Volume2 } from "lucide-react";

import type { DeliveredInterviewerTurn, VoicePresenceState } from "../models/candidate-visible";
import type { CanonicalControlDebug } from "../realtime/RealtimeControlClient";
import type { DevelopmentAnalyzeLatestResponse } from "../realtime/liveExaminer";
import { requestDevelopmentLiveExaminerAnalysis } from "../realtime/liveExaminer";
import type { DevelopmentReasoningSmokeResponse } from "../realtime/reasoningSmoke";
import { requestDevelopmentReasoningSmoke } from "../realtime/reasoningSmoke";
import { CODE_EDIT_BURST_IDLE_MS } from "../realtime/useCodeObservationCollector";
import type { RealtimeSessionDebug } from "../realtime/useRealtimeVoice";
import { renderDeliveredText } from "./deliveredText";
import { VoicePresence } from "./VoicePresence";

type InterviewerSurfaceProps = {
  voiceState: VoicePresenceState;
  isMuted: boolean;
  voiceError: string | null;
  partialTranscript: string;
  lastFinalTranscript: string;
  sessionDebug: RealtimeSessionDebug;
  canonicalDebug: CanonicalControlDebug;
  currentTurn: DeliveredInterviewerTurn;
  onEnableMicrophone: () => Promise<void>;
  onMute: () => void;
  onUnmute: () => void;
  onDisconnectVoice: () => void;
  onSpeakDevelopmentPhrase: () => void;
  onEvaluateExaminerDecision: (examinerDecisionId: string) => void;
  onDeliverAuthorizedPrompt: (promptId: string) => void;
  onOpenConversation: () => void;
};

export function InterviewerSurface({
  voiceState,
  isMuted,
  voiceError,
  partialTranscript,
  lastFinalTranscript,
  sessionDebug,
  canonicalDebug,
  currentTurn,
  onEnableMicrophone,
  onMute,
  onUnmute,
  onDisconnectVoice,
  onSpeakDevelopmentPhrase,
  onEvaluateExaminerDecision,
  onDeliverAuthorizedPrompt,
  onOpenConversation,
}: InterviewerSurfaceProps) {
  const connected = voiceState === "Listening" || voiceState === "Speaking" || voiceState === "Muted";
  const showTranscriptInspector =
    process.env.NODE_ENV !== "production" &&
    (connected || partialTranscript.length > 0 || lastFinalTranscript.length > 0);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [reasoningSmokePending, setReasoningSmokePending] = useState(false);
  const [reasoningSmokeResult, setReasoningSmokeResult] =
    useState<DevelopmentReasoningSmokeResponse | null>(null);
  const [reasoningSmokeError, setReasoningSmokeError] = useState<string | null>(null);
  const [liveExaminerPending, setLiveExaminerPending] = useState(false);
  const [liveExaminerResult, setLiveExaminerResult] =
    useState<DevelopmentAnalyzeLatestResponse | null>(null);
  const [liveExaminerError, setLiveExaminerError] = useState<string | null>(null);
  const transcriptPopoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!showTranscriptInspector) {
      setTranscriptOpen(false);
    }
  }, [showTranscriptInspector]);

  useEffect(() => {
    if (!transcriptOpen) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setTranscriptOpen(false);
      }
    };
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && !transcriptPopoverRef.current?.contains(target)) {
        setTranscriptOpen(false);
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [transcriptOpen]);

  const handleReasoningSmoke = async () => {
    if (!canonicalDebug.sessionId || reasoningSmokePending) {
      return;
    }
    setReasoningSmokePending(true);
    setReasoningSmokeError(null);
    try {
      const result = await requestDevelopmentReasoningSmoke(canonicalDebug.sessionId);
      setReasoningSmokeResult(result);
    } catch {
      setReasoningSmokeError("AI Gateway smoke request failed");
    } finally {
      setReasoningSmokePending(false);
    }
  };

  const handleLiveExaminerAnalysis = async () => {
    if (!canonicalDebug.sessionId || liveExaminerPending) {
      return;
    }
    setLiveExaminerPending(true);
    setLiveExaminerError(null);
    try {
      const result = await requestDevelopmentLiveExaminerAnalysis(canonicalDebug.sessionId);
      setLiveExaminerResult(result);
    } catch {
      setLiveExaminerError("Live Examiner analysis request failed");
    } finally {
      setLiveExaminerPending(false);
    }
  };

  return (
    <section className="interviewer-surface" aria-labelledby="current-question-title">
      <div className="interviewer-presence">
        <VoicePresence state={voiceState} />
        <div className="voice-controls" aria-label="Realtime voice controls">
          {(voiceState === "Ready" || voiceState === "Error") && (
            <button type="button" className="voice-control-button" onClick={onEnableMicrophone}>
              <Mic size={14} aria-hidden="true" />
              <span>Enable microphone</span>
            </button>
          )}
          {voiceState === "Connecting" && (
            <span className="voice-control-note" role="status">
              Connecting
            </span>
          )}
          {connected && (
            <>
              {isMuted ? (
                <button type="button" className="voice-control-button" onClick={onUnmute}>
                  <Mic size={14} aria-hidden="true" />
                  <span>Unmute</span>
                </button>
              ) : (
                <button type="button" className="voice-control-button" onClick={onMute}>
                  <MicOff size={14} aria-hidden="true" />
                  <span>Mute</span>
                </button>
              )}
              <button type="button" className="voice-icon-button" onClick={onDisconnectVoice} aria-label="Disconnect voice">
                <PlugZap size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="voice-control-button voice-dev-button"
                onClick={onSpeakDevelopmentPhrase}
              >
                <Volume2 size={14} aria-hidden="true" />
                <span>Dev phrase</span>
              </button>
            </>
          )}
        </div>
        {voiceError ? <p className="voice-error">{voiceError}</p> : null}
      </div>
      <div className="active-prompt">
        <p id="current-question-title" className="active-prompt-label">
          CounterQ
        </p>
        <p className="active-prompt-text">{renderDeliveredText(currentTurn.actualText)}</p>
      </div>
      <button type="button" className="conversation-button" onClick={onOpenConversation}>
        <History size={16} aria-hidden="true" />
        <span>Recent conversation</span>
      </button>
      {showTranscriptInspector ? (
        <div className="voice-dev-transcript-anchor" ref={transcriptPopoverRef}>
          <button
            type="button"
            className="voice-control-button voice-dev-button"
            aria-expanded={transcriptOpen}
            aria-controls="development-transcript-popover"
            onClick={() => setTranscriptOpen((current) => !current)}
          >
            <span>Dev transcript</span>
          </button>
          {transcriptOpen ? (
            <div
              id="development-transcript-popover"
              className="voice-dev-transcript-popover"
              role="dialog"
              aria-labelledby="development-transcript-title"
            >
              <div className="voice-dev-transcript-header">
                <h2 id="development-transcript-title">DEVELOPMENT TRANSCRIPT</h2>
                <button
                  type="button"
                  className="voice-dev-transcript-close"
                  onClick={() => setTranscriptOpen(false)}
                >
                  Close
                </button>
              </div>
              <dl>
                <div>
                  <dt>Partial</dt>
                  <dd>{partialTranscript || "No partial transcript"}</dd>
                </div>
                <div>
                  <dt>Final</dt>
                  <dd>{lastFinalTranscript || "No final transcript"}</dd>
                </div>
                <div>
                  <dt>Session</dt>
                  <dd>
                    {sessionDebug.transcriptionModel
                      ? `${sessionDebug.sessionType ?? "session"} / ${sessionDebug.transcriptionModel}`
                      : "Session transcription not observed"}
                  </dd>
                </div>
                <div>
                  <dt>Turn detection</dt>
                  <dd>
                    {sessionDebug.turnDetectionType
                      ? `${sessionDebug.turnDetectionType}; auto response ${
                          sessionDebug.createResponse === false ? "disabled" : "not confirmed"
                        }; interruption ${
                          sessionDebug.interruptResponse === true ? "enabled" : "not confirmed"
                        }`
                      : "Turn detection not observed"}
                  </dd>
                </div>
                <div>
                  <dt>CounterQ session</dt>
                  <dd>
                    {canonicalDebug.sessionId
                      ? `${canonicalDebug.sessionId}; control ${
                          canonicalDebug.controlConnected ? "connected" : "disconnected"
                        }; pending ${canonicalDebug.pendingDurableMessages}`
                      : "No canonical session yet"}
                  </dd>
                </div>
                <div>
                  <dt>Server ordering</dt>
                  <dd>
                    {canonicalDebug.lastServerSequence
                      ? `server sequence ${canonicalDebug.lastServerSequence}; state version ${
                          canonicalDebug.stateVersion ?? "unknown"
                        }`
                      : "No durable event acknowledged"}
                  </dd>
                </div>
                <div>
                  <dt>Last candidate final</dt>
                  <dd>
                    {canonicalDebug.lastCandidateFinal.providerItemId
                      ? `${canonicalDebug.lastCandidateFinal.persistence}; item ${
                          canonicalDebug.lastCandidateFinal.providerItemId
                        }; event ${
                          canonicalDebug.lastCandidateFinal.eventId ?? "pending"
                        }; segment ${
                          canonicalDebug.lastCandidateFinal.transcriptSegmentId ?? "pending"
                        }`
                      : "No candidate final persisted"}
                  </dd>
                </div>
                <div>
                  <dt>Last observation</dt>
                  <dd>
                    {canonicalDebug.lastObservation.kind
                      ? `${canonicalDebug.lastObservation.kind}; event ${
                          canonicalDebug.lastObservation.sourceEventId ?? "pending"
                        }; watermark ${
                          canonicalDebug.lastObservation.sourceEventWatermark ?? "pending"
                        }; state ${
                          canonicalDebug.lastObservation.stateVersion ?? "unknown"
                        }; stage ${
                          canonicalDebug.lastObservation.stage ?? "unknown"
                        }; trigger ${
                          canonicalDebug.lastObservation.triggerClass ?? "unknown"
                        }`
                      : "No structured observation acknowledged"}
                  </dd>
                </div>
                <div>
                  <dt>Code</dt>
                  <dd>
                    {canonicalDebug.lastCode.snapshotId
                      ? `${canonicalDebug.lastCode.persistence}; snapshot ${
                          canonicalDebug.lastCode.snapshotId
                        }; version ${
                          canonicalDebug.lastCode.version ?? "unknown"
                        }; hash ${
                          canonicalDebug.lastCode.hashPrefix ?? "unknown"
                        }; diff ${canonicalDebug.lastCode.diffId ?? "none"}`
                      : `No canonical code snapshot yet; ${canonicalDebug.lastCode.persistence}`}
                  </dd>
                </div>
                <div>
                  <dt>Edit observation</dt>
                  <dd>
                    {canonicalDebug.lastCode.persistence.toLowerCase()}; idle threshold{" "}
                    {CODE_EDIT_BURST_IDLE_MS} ms
                  </dd>
                </div>
                <div className="voice-dev-ai-gateway">
                  <dt>AI Gateway</dt>
                  <dd>
                    <button
                      type="button"
                      className="voice-control-button voice-dev-button"
                      onClick={handleReasoningSmoke}
                      disabled={!canonicalDebug.sessionId || reasoningSmokePending}
                    >
                      {reasoningSmokePending ? "Reasoning..." : "Reasoning smoke"}
                    </button>
                    {reasoningSmokeError ? (
                      <span className="voice-dev-inline-error">{reasoningSmokeError}</span>
                    ) : null}
                    {reasoningSmokeResult ? (
                      <div className="voice-dev-reasoning-result" aria-live="polite">
                        <p>AI GATEWAY</p>
                        <span>
                          {reasoningSmokeResult.status}; invocation{" "}
                          {reasoningSmokeResult.invocation_id}; {reasoningSmokeResult.provider}/
                          {reasoningSmokeResult.model}
                        </span>
                        <span>
                          latency {reasoningSmokeResult.latency_ms ?? "unknown"} ms; tokens in{" "}
                          {reasoningSmokeResult.input_tokens ?? "unknown"} out{" "}
                          {reasoningSmokeResult.output_tokens ?? "unknown"}; cost{" "}
                          {reasoningSmokeResult.estimated_cost
                            ? `${reasoningSmokeResult.estimated_cost} ${
                                reasoningSmokeResult.currency ?? ""
                              }`
                            : "unknown"}
                        </span>
                        <span>
                          budget {reasoningSmokeResult.reasoning_budget_used} used /{" "}
                          {reasoningSmokeResult.reasoning_budget_remaining} remaining
                        </span>
                        <p>RESULT</p>
                        <span>
                          {reasoningSmokeResult.verdict}; confidence{" "}
                          {reasoningSmokeResult.confidence}
                        </span>
                        <span>{reasoningSmokeResult.technical_note}</span>
                      </div>
                    ) : null}
                  </dd>
                </div>
                <div className="voice-dev-ai-gateway">
                  <dt>LIVE EXAMINER</dt>
                  <dd>
                    <span>Autostart OFF by default in development</span>
                    <button
                      type="button"
                      className="voice-control-button voice-dev-button"
                      onClick={handleLiveExaminerAnalysis}
                      disabled={!canonicalDebug.sessionId || liveExaminerPending}
                    >
                      {liveExaminerPending ? "Analyzing..." : "Analyze latest observation"}
                    </button>
                    {liveExaminerError ? (
                      <span className="voice-dev-inline-error">{liveExaminerError}</span>
                    ) : null}
                    {liveExaminerResult ? (
                      <div className="voice-dev-reasoning-result" aria-live="polite">
                        <p>LIVE EXAMINER RESULT</p>
                        <span>
                          {liveExaminerResult.status}; source{" "}
                          {liveExaminerResult.source_kind ?? "none"}; watermark{" "}
                          {liveExaminerResult.source_event_watermark ?? "none"}
                        </span>
                        <span>
                          invocation {liveExaminerResult.ai_invocation_id ?? "none"};{" "}
                          {liveExaminerResult.provider ?? "no provider"}/
                          {liveExaminerResult.model ?? "no model"}; latency{" "}
                          {liveExaminerResult.latency_ms ?? "unknown"} ms
                        </span>
                        <span>
                          code snapshot {liveExaminerResult.code_snapshot_id ?? "none"}; version{" "}
                          {liveExaminerResult.code_snapshot_version ?? "none"}
                        </span>
                        <p>Claims</p>
                        {liveExaminerResult.claims.length ? (
                          liveExaminerResult.claims.map((claim) => (
                            <span key={claim.id}>
                              {claim.claim_type}: {claim.normalized_claim}
                            </span>
                          ))
                        ) : (
                          <span>No claims persisted</span>
                        )}
                        <p>Decision</p>
                        {liveExaminerResult.decision ? (
                          <>
                            <span>
                              {liveExaminerResult.decision.status};{" "}
                              {liveExaminerResult.decision.action}; strategy{" "}
                              {liveExaminerResult.decision.proposed_probe_strategy ?? "none"}
                            </span>
                            <span>{liveExaminerResult.decision.technical_rationale}</span>
                            <button
                              type="button"
                              className="voice-control-button voice-dev-button"
                              onClick={() =>
                                onEvaluateExaminerDecision(liveExaminerResult.decision!.id)
                              }
                              disabled={liveExaminerResult.decision.status !== "PROPOSED"}
                            >
                              Policy gate
                            </button>
                          </>
                        ) : (
                          <span>{liveExaminerResult.message ?? "No decision persisted"}</span>
                        )}
                      </div>
                    ) : null}
                  </dd>
                </div>
                <div>
                  <dt>Voice code context</dt>
                  <dd>
                    {canonicalDebug.lastVoice.transcriptSegmentId
                      ? `segment ${canonicalDebug.lastVoice.transcriptSegmentId}; code snapshot ${
                          canonicalDebug.lastVoice.associatedCodeSnapshotId ?? "none"
                        }; version ${
                          canonicalDebug.lastVoice.associatedCodeSnapshotVersion ?? "none"
                        }`
                      : "No finalized voice observation yet"}
                  </dd>
                </div>
                <div>
                  <dt>Policy gate</dt>
                  <dd>
                    {canonicalDebug.lastPolicyGate.decisionId ? (
                      <>
                        <span>
                          {canonicalDebug.lastPolicyGate.disposition}; decision{" "}
                          {canonicalDebug.lastPolicyGate.decisionStatus}; outcome{" "}
                          {canonicalDebug.lastPolicyGate.policyGateOutcome ?? "transient"}
                        </span>
                        {canonicalDebug.lastPolicyGate.promptId ? (
                          <button
                            type="button"
                            className="voice-control-button voice-dev-button"
                            onClick={() =>
                              onDeliverAuthorizedPrompt(canonicalDebug.lastPolicyGate.promptId!)
                            }
                            disabled={voiceState !== "Listening" && voiceState !== "Muted"}
                          >
                            Deliver authorized prompt
                          </button>
                        ) : null}
                      </>
                    ) : (
                      "No policy-gate result yet"
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Last delivery</dt>
                  <dd>
                    {canonicalDebug.lastDelivery.promptId
                      ? `prompt ${canonicalDebug.lastDelivery.promptId}; delivery ${
                          canonicalDebug.lastDelivery.deliveryId ?? "pending"
                        }; state ${
                          canonicalDebug.lastDelivery.deliveryState ?? "pending"
                        }; response ${
                          canonicalDebug.lastDelivery.providerResponseId ?? "pending"
                        }; actual transcript ${
                          canonicalDebug.lastDelivery.actualTranscriptId ?? "none"
                        }`
                      : "No canonical delivery yet"}
                  </dd>
                </div>
              </dl>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
