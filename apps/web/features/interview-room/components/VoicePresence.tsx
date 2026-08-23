import type { VoicePresenceState } from "../models/candidate-visible";

type VoicePresenceProps = {
  state: VoicePresenceState;
  compact?: boolean;
};

export function VoicePresence({ state, compact = false }: VoicePresenceProps) {
  return (
    <div className={compact ? "voice-presence voice-presence-compact" : "voice-presence"}>
      <span className={`voice-dot voice-dot-${state.toLowerCase()}`} aria-hidden="true" />
      <span className="voice-label">{state}</span>
    </div>
  );
}
