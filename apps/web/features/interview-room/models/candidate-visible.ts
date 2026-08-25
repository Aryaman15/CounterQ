export type VoicePresenceState =
  | "Ready"
  | "Connecting"
  | "Listening"
  | "Speaking"
  | "Reconnecting"
  | "Muted"
  | "Error";

export type DemoPersistenceState = "SYNCED" | "LOCAL_PENDING" | "PERSISTENCE_UNCONFIRMED";

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
  language: "cpp" | "python" | "java";
  languageLabel: "C++17" | "Python 3" | "Java 21";
  serverNowIso: string;
  deadlineAtIso: string;
  persistenceState: DemoPersistenceState;
  problem: DemoProblem;
  starterCode: string;
  currentDeliveredTurn: DeliveredInterviewerTurn;
  recentConversation: DeliveredConversationRow[];
};
