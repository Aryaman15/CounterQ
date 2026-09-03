"use client";

import { Clock, Power } from "lucide-react";

import type { VoicePresenceState } from "../models/candidate-visible";
import { VoicePresence } from "./VoicePresence";

type InterviewHeaderProps = {
  mode: "COACH" | "SIMULATION";
  remainingLabel: string;
  voiceState: VoicePresenceState;
  onEndInterview: () => void;
  terminal?: boolean;
};

export function InterviewHeader({
  mode,
  remainingLabel,
  voiceState,
  onEndInterview,
  terminal = false,
}: InterviewHeaderProps) {
  return (
    <header className="interview-header">
      <div className="header-brand" aria-label="CounterQ Interview Room">
        <span className="brand-mark" aria-hidden="true" />
        <span className="brand-name">CounterQ</span>
      </div>
      <div className="header-status">
        <span className="mode-badge">{mode}</span>
        <span className="timer-pill" aria-label={`Time remaining ${remainingLabel}`}>
          <Clock size={15} aria-hidden="true" />
          <span>{remainingLabel}</span>
        </span>
        <VoicePresence state={voiceState} compact />
      </div>
      {!terminal ? (
        <button type="button" className="end-button" onClick={onEndInterview}>
          <Power size={16} aria-hidden="true" />
          <span>End Interview</span>
        </button>
      ) : (
        <span className="mode-badge">ENDED</span>
      )}
    </header>
  );
}
