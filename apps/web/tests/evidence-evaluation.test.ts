import { afterEach, describe, expect, it, vi } from "vitest";

import {
  evaluateDevelopmentEvidence,
  fetchDevelopmentEvidenceSnapshot,
} from "@/features/interview-room/realtime/evidenceEvaluation";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Stage 5 development evidence client", () => {
  it("invokes explicit evaluation and then reads only the canonical snapshot", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            interview_session_id: "00000000-0000-0000-0000-000000000001",
            completed_units: 1,
            skipped_units: 0,
            failed_units: 0,
            units: [],
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            interview_session_id: "00000000-0000-0000-0000-000000000001",
            assessments: [{ status: "VALIDATED" }],
            evidence: [{ independence: "AFTER_PROBE" }],
            breakpoints: [{ status: "OPEN" }],
          }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const evaluation = await evaluateDevelopmentEvidence(
      "00000000-0000-0000-0000-000000000001",
      "http://counterq.test",
    );
    const snapshot = await fetchDevelopmentEvidenceSnapshot(
      "00000000-0000-0000-0000-000000000001",
      "http://counterq.test",
    );

    expect(evaluation.completed_units).toBe(1);
    expect(snapshot.evidence).toEqual([{ independence: "AFTER_PROBE" }]);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://counterq.test/api/evidence/development/session-evaluation",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://counterq.test/api/evidence/development/session-evaluation/00000000-0000-0000-0000-000000000001",
    );
  });
});
