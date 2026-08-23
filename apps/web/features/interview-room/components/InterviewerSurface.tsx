"use client";

import { History } from "lucide-react";

import type { DeliveredInterviewerTurn, VoicePresenceState } from "../models/candidate-visible";
import { renderDeliveredText } from "./deliveredText";
import { VoicePresence } from "./VoicePresence";

type InterviewerSurfaceProps = {
  voiceState: VoicePresenceState;
  currentTurn: DeliveredInterviewerTurn;
  onOpenConversation: () => void;
};

export function InterviewerSurface({
  voiceState,
  currentTurn,
  onOpenConversation,
}: InterviewerSurfaceProps) {
  return (
    <section className="interviewer-surface" aria-labelledby="current-question-title">
      <div className="interviewer-presence">
        <VoicePresence state={voiceState} />
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
