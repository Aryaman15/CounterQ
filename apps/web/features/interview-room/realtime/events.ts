export type NormalizedRealtimeEvent =
  | { type: "candidate_speech_started" }
  | { type: "candidate_speech_stopped" }
  | { type: "counterq_response_created"; responseId: string; itemId: string | null }
  | {
      type: "counterq_output_started";
      responseId?: string;
      itemId?: string | null;
      providerEventId?: string | null;
      playbackStarted?: boolean;
    }
  | {
      type: "counterq_output_ended";
      responseId?: string;
      itemId?: string | null;
      playbackComplete?: boolean;
    }
  | {
      type: "counterq_output_interrupted";
      responseId?: string;
      itemId?: string | null;
      confirmedBy?: string;
      audioEndMs?: number | null;
    }
  | {
      type: "counterq_output_transcript_delta";
      text: string;
      responseId: string | null;
      itemId: string | null;
      contentIndex: number | null;
    }
  | {
      type: "counterq_output_transcript_final";
      text: string;
      responseId: string | null;
      itemId: string | null;
      contentIndex: number | null;
    }
  | { type: "transcript_delta"; text: string; itemId: string | null; contentIndex: number | null }
  | { type: "transcript_final"; text: string; itemId: string | null; contentIndex: number | null }
  | { type: "transcript_failed"; itemId: string | null; message: string }
  | {
      type: "realtime_session_observed";
      eventType: "session.created" | "session.updated";
      sessionType: string | null;
      transcriptionModel: string | null;
      turnDetectionType: string | null;
      createResponse: boolean | null;
      interruptResponse: boolean | null;
    }
  | { type: "provider_error"; message: string };

type RawRealtimeEvent = {
  type?: unknown;
  event_id?: unknown;
  delta?: unknown;
  text?: unknown;
  transcript?: unknown;
  error?: unknown;
  response?: unknown;
  reason?: unknown;
  session?: unknown;
  response_id?: unknown;
  item_id?: unknown;
  content_index?: unknown;
  audio_end_ms?: unknown;
};

function stringField(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function objectField(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function numberField(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function booleanField(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
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
    return [
      { type: "candidate_speech_started" },
      {
        type: "counterq_output_interrupted",
        itemId: stringField(event.item_id),
        confirmedBy: "input_audio_buffer.speech_started",
        audioEndMs: numberField(event.audio_end_ms),
      },
    ];
  }

  if (rawType === "input_audio_buffer.speech_stopped") {
    return [{ type: "candidate_speech_stopped" }];
  }

  if (rawType === "response.created") {
    const response = objectField(event.response);
    const responseId = stringField(response?.id);
    if (!responseId) {
      return [];
    }
    return [
      {
        type: "counterq_response_created",
        responseId,
        itemId: firstResponseItemId(response),
      },
    ];
  }

  if (rawType === "output_audio_buffer.started") {
    return [
      {
        type: "counterq_output_started",
        ...providerFields(event),
        playbackStarted: true,
      },
    ];
  }

  if (rawType === "response.output_audio.delta" || rawType === "response.audio.delta") {
    return [
      {
        type: "counterq_output_started",
        ...providerFields(event),
      },
    ];
  }

  if (rawType === "output_audio_buffer.stopped") {
    return [
      {
        type: "counterq_output_ended",
        ...providerFields(event),
        playbackComplete: true,
      },
    ];
  }

  if (rawType === "response.output_audio.done" || rawType === "response.audio.done") {
    return [
      {
        type: "counterq_output_ended",
        ...providerFields(event),
      },
    ];
  }

  if (
    rawType === "response.cancelled" ||
    rawType === "response.output_audio.cancelled" ||
    rawType === "output_audio_buffer.cleared" ||
    rawType === "output_audio_buffer.clear"
  ) {
    return [
      {
        type: "counterq_output_interrupted",
        ...providerFields(event),
        confirmedBy: rawType,
        audioEndMs: numberField(event.audio_end_ms),
      },
    ];
  }

  if (rawType === "response.done") {
    const response = objectField(event.response);
    const status = stringField(response?.status);
    if (status === "cancelled" || status === "incomplete" || status === "failed") {
      return [
        {
          type: "counterq_output_interrupted",
          ...(stringField(response?.id) ? { responseId: stringField(response?.id) ?? undefined } : {}),
          ...(firstResponseItemId(response) ? { itemId: firstResponseItemId(response) } : {}),
          confirmedBy: `response.done:${status}`,
        },
      ];
    }
    return [
      {
        type: "counterq_output_ended",
        ...(stringField(response?.id) ? { responseId: stringField(response?.id) ?? undefined } : {}),
        ...(firstResponseItemId(response) ? { itemId: firstResponseItemId(response) } : {}),
      },
    ];
  }

  if (rawType === "session.created" || rawType === "session.updated") {
    const session = objectField(event.session);
    const audio = objectField(session?.audio);
    const input = objectField(audio?.input);
    const transcription = objectField(input?.transcription);
    const turnDetection = objectField(input?.turn_detection);
    return [
      {
        type: "realtime_session_observed",
        eventType: rawType,
        sessionType: stringField(session?.type),
        transcriptionModel: stringField(transcription?.model),
        turnDetectionType: stringField(turnDetection?.type),
        createResponse: booleanField(turnDetection?.create_response),
        interruptResponse: booleanField(turnDetection?.interrupt_response),
      },
    ];
  }

  if (
    rawType === "response.output_audio_transcript.delta" ||
    rawType === "response.audio_transcript.delta"
  ) {
    const text = stringField(event.delta) ?? stringField(event.text) ?? stringField(event.transcript);
    return text
      ? [
          {
            type: "counterq_output_transcript_delta",
            text,
            responseId: stringField(event.response_id),
            itemId: stringField(event.item_id),
            contentIndex: numberField(event.content_index),
          },
        ]
      : [];
  }

  if (
    rawType === "response.output_audio_transcript.done" ||
    rawType === "response.audio_transcript.done"
  ) {
    const text = stringField(event.transcript) ?? stringField(event.text) ?? stringField(event.delta);
    return text
      ? [
          {
            type: "counterq_output_transcript_final",
            text,
            responseId: stringField(event.response_id),
            itemId: stringField(event.item_id),
            contentIndex: numberField(event.content_index),
          },
        ]
      : [];
  }

  if (rawType.includes("input_audio_transcription") && rawType.endsWith(".delta")) {
    const text = stringField(event.delta) ?? stringField(event.text) ?? stringField(event.transcript);
    return text
      ? [
          {
            type: "transcript_delta",
            text,
            itemId: stringField(event.item_id),
            contentIndex: numberField(event.content_index),
          },
        ]
      : [];
  }

  if (
    rawType.includes("input_audio_transcription") &&
    (rawType.endsWith(".completed") || rawType.endsWith(".done"))
  ) {
    const text = stringField(event.transcript) ?? stringField(event.text) ?? stringField(event.delta);
    return text
      ? [
          {
            type: "transcript_final",
            text,
            itemId: stringField(event.item_id),
            contentIndex: numberField(event.content_index),
          },
        ]
      : [];
  }

  if (rawType.includes("input_audio_transcription") && rawType.endsWith(".failed")) {
    return [
      {
        type: "transcript_failed",
        itemId: stringField(event.item_id),
        message: "Realtime transcription failed for the current audio turn.",
      },
    ];
  }

  if (rawType === "error") {
    return [{ type: "provider_error", message: "Realtime provider error" }];
  }

  return [];
}

function firstResponseItemId(response: Record<string, unknown> | null): string | null {
  const output = response?.output;
  if (!Array.isArray(output)) {
    return null;
  }
  const first = output[0];
  return objectField(first)?.id ? stringField(objectField(first)?.id) : null;
}

function providerFields(event: RawRealtimeEvent): {
  responseId?: string;
  itemId?: string | null;
  providerEventId?: string | null;
} {
  const fields: {
    responseId?: string;
    itemId?: string | null;
    providerEventId?: string | null;
  } = {};
  const responseId = stringField(event.response_id);
  const itemId = stringField(event.item_id);
  const providerEventId = stringField(event.event_id);
  if (responseId) {
    fields.responseId = responseId;
  }
  if (itemId) {
    fields.itemId = itemId;
  }
  if (providerEventId) {
    fields.providerEventId = providerEventId;
  }
  return fields;
}
