export type VoicePresenceState = "Ready" | "Listening" | "Speaking" | "Reconnecting" | "Muted";

export type DemoPersistenceState = "LOCAL_PENDING";

export type DeliveredInterviewerTurn = {
  id: string;
  speaker: "CounterQ";
  actualText: string;
  actualTranscriptSegmentId: string;
  deliveredAtLabel: string;
  deliveryState: "DELIVERED" | "INTERRUPTED";
};

export type DeliveredConversationRow =
  | DeliveredInterviewerTurn
  | {
      id: string;
      speaker: "Candidate";
      actualText: string;
      actualTranscriptSegmentId: string;
      deliveredAtLabel: string;
    };

export type ProblemExample = {
  input: string;
  output: string;
  explanation: string;
};

export type DemoProblem = {
  title: string;
  statement: string[];
  examples: ProblemExample[];
  constraints: string[];
  functionSignature: string;
};

export type DemoInterviewRoomFixture = {
  mode: "SIMULATION";
  languageLabel: "C++17";
  serverNowIso: string;
  deadlineAtIso: string;
  voiceState: VoicePresenceState;
  persistenceState: DemoPersistenceState;
  problem: DemoProblem;
  starterCode: string;
  currentDeliveredTurn: DeliveredInterviewerTurn;
  recentConversation: DeliveredConversationRow[];
};
