export type NormalizedRealtimeEvent =
  | { type: "candidate_speech_started" }
  | { type: "candidate_speech_stopped" }
  | { type: "counterq_output_started" }
  | { type: "counterq_output_ended" }
  | { type: "counterq_output_interrupted" }
  | { type: "transcript_delta"; text: string }
  | { type: "transcript_final"; text: string }
  | { type: "provider_error"; message: string };

type RawRealtimeEvent = {
  type?: unknown;
  delta?: unknown;
  text?: unknown;
  transcript?: unknown;
  error?: unknown;
};

function stringField(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export function normalizeRealtimeEvent(raw: unknown): NormalizedRealtimeEvent[] {
  if (!raw || typeof raw !== "object") {
    return [{ type: "provider_error", message: "Malformed realtime event" }];
  }

  const event = raw as RawRealtimeEvent;
  const rawType = stringField(event.type);
  if (!rawType) {
    return [{ type: "provider_error", message: "Malformed realtime event" }];
  }

  if (rawType === "input_audio_buffer.speech_started") {
    return [{ type: "candidate_speech_started" }];
  }

  if (rawType === "input_audio_buffer.speech_stopped") {
    return [{ type: "candidate_speech_stopped" }];
  }

  if (rawType === "response.output_audio.delta" || rawType === "response.audio.delta") {
    return [{ type: "counterq_output_started" }];
  }

  if (
    rawType === "response.output_audio.done" ||
    rawType === "response.audio.done" ||
    rawType === "response.done"
  ) {
    return [{ type: "counterq_output_ended" }];
  }

  if (rawType === "response.cancelled" || rawType === "response.output_audio.cancelled") {
    return [{ type: "counterq_output_interrupted" }];
  }

  if (rawType.includes("transcription") && rawType.endsWith(".delta")) {
    const text = stringField(event.delta) ?? stringField(event.text) ?? stringField(event.transcript);
    return text ? [{ type: "transcript_delta", text }] : [];
  }

  if (
    rawType.includes("transcription") &&
    (rawType.endsWith(".completed") || rawType.endsWith(".done"))
  ) {
    const text = stringField(event.transcript) ?? stringField(event.text) ?? stringField(event.delta);
    return text ? [{ type: "transcript_final", text }] : [];
  }

  if (rawType === "error") {
    return [{ type: "provider_error", message: "Realtime provider error" }];
  }

  return [];
}
