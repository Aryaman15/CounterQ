import type { DemoInterviewRoomFixture } from "../models/candidate-visible";

export const demoStarterCode = `class Solution {
public:
    int lengthOfLongestSubstring(string s) {

    }
};`;

export const hiddenInternalFixtureFields = {
  examinerDecisionRationale: "INTERNAL_EXAMINER_REASONING_DO_NOT_RENDER",
  probeStrategy: "PROVE",
  intendedUndeliveredPromptText: "UNDISCLOSED_INTENDED_PROMPT_DO_NOT_RENDER",
};

export const developmentStarterCode = {
  cpp: demoStarterCode,
  python: `class Solution:
    def lengthOfLongestSubstring(self, s: str) -> int:
        pass`,
  java: `class Solution {
    public int lengthOfLongestSubstring(String s) {
        return 0;
    }
}`,
} as const;

export const demoInterviewFixture: DemoInterviewRoomFixture = {
  mode: "SIMULATION",
  language: "cpp",
  languageLabel: "C++17",
  serverNowIso: "2026-08-23T10:08:18.000Z",
  deadlineAtIso: "2026-08-23T10:30:00.000Z",
  persistenceState: "LOCAL_PENDING",
  starterCode: demoStarterCode,
  problem: {
    title: "Longest Substring Without Repeating Characters",
    statement: [
      "Given a string s, return the length of the longest substring that contains no repeated characters.",
      "A substring is a contiguous sequence of characters within the string.",
    ],
    functionSignature: "int lengthOfLongestSubstring(string s)",
    examples: [
      {
        input: 's = "abcabcbb"',
        output: "3",
        explanation: 'The answer is "abc", with length 3.',
      },
      {
        input: 's = "bbbbb"',
        output: "1",
        explanation: 'The answer is "b", with length 1.',
      },
      {
        input: 's = "pwwkew"',
        output: "3",
        explanation: 'The answer is "wke", with length 3. "pwke" is not contiguous.',
      },
    ],
    constraints: [
      "0 <= s.length <= 5 * 10^4",
      "s consists of English letters, digits, symbols, and spaces.",
      "Return only the length of the substring.",
    ],
  },
  currentDeliveredTurn: {
    id: "turn-current-left-invariant",
    speaker: "CounterQ",
    actualText: "What guarantees that `left` never moves backwards?",
    actualTranscriptSegmentId: "transcript-counterq-0003",
    deliveredAtLabel: "just now",
    deliveryState: "DELIVERED",
  },
  recentConversation: [
    {
      id: "turn-counterq-0001",
      speaker: "CounterQ",
      actualText: "Take a moment to read the problem, then tell me how you understand it.",
      actualTranscriptSegmentId: "transcript-counterq-0001",
      deliveredAtLabel: "21:48",
      deliveryState: "DELIVERED",
    },
    {
      id: "turn-candidate-0001",
      speaker: "Candidate",
      actualText: "We need the longest contiguous substring where every character is unique.",
      actualTranscriptSegmentId: "transcript-candidate-0001",
      deliveredAtLabel: "21:39",
    },
    {
      id: "turn-counterq-0002",
      speaker: "CounterQ",
      actualText: "Walk me through how your window changes when a repeated character appears.",
      actualTranscriptSegmentId: "transcript-counterq-0002",
      deliveredAtLabel: "20:57",
      deliveryState: "DELIVERED",
    },
  ],
};
