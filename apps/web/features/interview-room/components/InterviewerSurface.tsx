"use client";

import { useEffect, useRef, useState } from "react";
import { History, Mic, MicOff, PlugZap, Volume2 } from "lucide-react";

import type { DeliveredInterviewerTurn, VoicePresenceState } from "../models/candidate-visible";
import { renderDeliveredText } from "./deliveredText";
import { VoicePresence } from "./VoicePresence";

type InterviewerSurfaceProps = {
  voiceState: VoicePresenceState;
  isMuted: boolean;
  voiceError: string | null;
  partialTranscript: string;
  lastFinalTranscript: string;
  currentTurn: DeliveredInterviewerTurn;
  onEnableMicrophone: () => Promise<void>;
  onMute: () => void;
  onUnmute: () => void;
  onDisconnectVoice: () => void;
  onSpeakDevelopmentPhrase: () => void;
  onOpenConversation: () => void;
};

export function InterviewerSurface({
  voiceState,
  isMuted,
  voiceError,
  partialTranscript,
  lastFinalTranscript,
  currentTurn,
  onEnableMicrophone,
  onMute,
  onUnmute,
  onDisconnectVoice,
  onSpeakDevelopmentPhrase,
  onOpenConversation,
}: InterviewerSurfaceProps) {
  const connected = voiceState === "Listening" || voiceState === "Speaking" || voiceState === "Muted";
  const showTranscriptInspector =
    process.env.NODE_ENV !== "production" &&
    (connected || partialTranscript.length > 0 || lastFinalTranscript.length > 0);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
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
                      </dl>
                    </div>
                  ) : null}
                </div>
              ) : null}
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
    </section>
  );
}
