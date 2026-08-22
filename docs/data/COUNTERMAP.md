# CounterQ — Phase 1 CounterMap

**Document:** `docs/data/COUNTERMAP.md`  
**Status:** Frozen Phase 1 Product + Data Projection Source of Truth  
**Product:** CounterQ  
**Phase:** Phase 1 — Technical Coding Interviews  
**Last Updated:** August 2026

---

# 1. Purpose

CounterMap is CounterQ's single-interview causal reasoning map.

It explains:

> **What the candidate said or did → what CounterQ noticed → why CounterQ asked something → how the candidate responded → what evidence was produced → where understanding held or broke.**

CounterMap is not:

- a transcript with arrows;
- an automatically generated mind map;
- a decorative graph;
- an AI visualization of whatever "seems related";
- the canonical assessment record.

Its purpose is to make CounterQ's interview behavior inspectable.

The governing principle is:

> **CounterMap visualizes causality. It does not invent causality.**

---

# 2. Product purpose

After completing an interview, a candidate should be able to use CounterMap to answer:

- Why did CounterQ ask me that?
- What did I say that triggered the question?
- Was the question based on my explanation, my code, or both?
- What exact code version was CounterQ looking at?
- What did my response demonstrate?
- Where did my reasoning become weak?
- Did I recover independently?
- Did CounterQ guide me?
- Which Breakpoints were genuinely exposed?
- Which parts of my reasoning were strong?
- Which weakness should be retested?

CounterMap turns an opaque outcome such as:

```text
Interview readiness: 68 / 100
```

into something closer to:

```text
I claimed X
    ↓
CounterQ tested assumption Y
    ↓
I couldn't justify Z
    ↓
That produced evidence about concept C
    ↓
A specific Breakpoint was exposed
```

That explanation is more useful than the score itself.

---

# 3. CounterMap vs transcript

A transcript answers:

> **What was said, and in what order?**

CounterMap answers:

> **Why did the interview branch the way it did?**

Transcript is fundamentally chronological.

CounterMap is fundamentally causal.

Example:

```text
Candidate Claim
"unordered_map lookup is always O(1)"
        ↓
CounterQ Challenge
"Is that actually guaranteed?"
        ↓
Candidate Response
"Yes, always."
        ↓
CounterQ Follow-up
"What happens when keys collide?"
        ↓
Candidate Response
Weak collision explanation
        ↓
Validated Evidence
Worst-case complexity misconception
        ↓
Breakpoint
Hash-table worst-case behavior
```

CounterMap should expose this logic directly.

It should not require the candidate to reread a 30-minute transcript and reconstruct the reasoning themselves.

---

# 4. Architectural position

CounterMap follows the frozen CounterQ hierarchy:

```text
Observed Events
        ↓
AI Interpretations
        ↓
Validated Evidence
        ↓
Derived Projections
```

CounterMap belongs entirely to:

```text
Derived Projections
```

It is therefore:

- rebuildable;
- versioned;
- disposable if projection rules improve;
- subordinate to canonical data.

CounterMap must never become the sole source of truth for:

- what was said;
- what code existed;
- why a question was asked;
- what Evidence was validated;
- whether a Breakpoint exists.

If a CounterMap projection is deleted, CounterQ should still be able to reconstruct the interview from canonical persistence.

---

# 5. Canonical inputs

CounterMap may derive from canonical Phase 1 entities including:

- `InterviewSession`;
- interview-stage transitions;
- `InterviewEvent`;
- `TranscriptSegment`;
- `CodeSnapshot`;
- `CodeDiff`;
- `ExecutionRun`;
- `TestResult`;
- `CandidateClaim`;
- `ExaminerDecision`;
- `InterviewerPrompt`;
- `InterviewerInterviewerPromptDelivery`;
- `CandidateResponse`;
- `Assessment`;
- `Evidence`;
- `EvidenceSource`;
- `Breakpoint`;
- Breakpoint/Evidence links;
- Coach assistance metadata;
- relevant RetestRecommendation links.

CounterMap must **not** derive its causal structure solely from:

- SessionReport prose;
- an LLM summary of the entire interview;
- event timestamps;
- semantic similarity.

Report text may link to CounterMap.

It must not define CounterMap truth.

---

# 6. Canonical entities vs visible projection nodes

The canonical persistence chain may contain:

```text
TranscriptSegment
        ↓
CandidateClaim
        ↓
ExaminerDecision
        ↓
InterviewerPrompt
        ↓
InterviewerPromptDelivery
        ↓
CandidateResponse
        ↓
Assessment
        ↓
Evidence
        ↓
Breakpoint
```

Displaying every one of these as an independent graph node would make CounterMap resemble an internal debugging graph.

The candidate-visible projection may compress the same chain to:

```text
Candidate Claim
        ↓
CounterQ Question
        ↓
Candidate Response
        ↓
Evidence
        ↓
Breakpoint
```

The compression is allowed because each visible node keeps canonical provenance.

Clicking a node can reveal deeper source information.

CounterMap may hide implementation plumbing.

It may not alter meaning.

## Candidate-visible projection boundary

CounterMap must distinguish between:

### Internal diagnostic truth

Useful for CounterQ engineers/reviewers:

- stale Examiner decisions;
- suppressed probe candidates;
- target-ranking alternatives;
- model routing;
- policy rejection reasons.

### Candidate-visible interview truth

What may appear in CounterMap:

- candidate actions actually observed;
- interviewer turns actually delivered;
- candidate responses/actions;
- validated Evidence;
- validated Breakpoints;
- delivered Coach assistance;
- grounded causal explanations.

An internal event can be useful for debugging without belonging in the candidate's map.

In particular:

> **CounterMap must never imply that CounterQ "noticed" or "decided" something silently unless that fact is itself necessary, candidate-safe, and represented through validated candidate-visible evidence.**

The candidate-facing map explains the interview and the candidate's performance.

It is not an observability dashboard for CounterQ's hidden cognition.

---

# 7. Phase 1 visible node vocabulary

Phase 1 should use a deliberately small set of visible node types:

- `CLAIM`
- `REASONING`
- `CODE`
- `TEST`
- `QUESTION`
- `RESPONSE`
- `EVIDENCE`
- `BREAKPOINT`
- `ASSISTANCE`
- `MUTATION`

This is enough.

## One delivered interviewer turn maps to one primary visible prompt node

`QUESTION`, `MUTATION`, and `ASSISTANCE` are mutually exclusive candidate-visible representations of a delivered interviewer turn.

A single `InterviewerPromptDelivery` must not appear simultaneously as:

```text
QUESTION
+
MUTATION
```

or:

```text
QUESTION
+
ASSISTANCE
```

Recommended mapping:

- ordinary/base/clarification/probe/final-defense technical prompt → `QUESTION`;
- delivered constraint-mutation/transfer challenge that is intentionally represented as a transfer branch → `MUTATION`;
- delivered Coach help whose purpose is assistance rather than diagnosis → `ASSISTANCE`.

The canonical `InterviewerPrompt.kind`, ProbeStrategy where applicable, assistance metadata, and State Machine context determine the projection type.

If a Coach interaction first diagnoses and later helps, these are separate delivered prompts and may therefore become:

```text
QUESTION
→ RESPONSE
→ EVIDENCE
→ ASSISTANCE
```

This preserves the distinction between testing and helping.

Do not create separate visual node types for:

- CodeDecision;
- CodeCorrection;
- StrongPoint;
- SelfCorrection;
- ComplexityEvidence;
- DebuggingEvidence;
- Probe;
- Clarification;
- ConstraintQuestion.

Those differences should be represented through:

- node subtype;
- label;
- metadata;
- edge semantics.

---

# 8. Why there is no separate `STRONG_POINT` node type

Positive and negative assessment outcomes both come from validated Evidence.

Therefore Phase 1 should represent both through:

```text
EVIDENCE
```

with polarity such as:

- positive;
- negative;
- mixed.

Candidate-facing labels can still say:

> **Strong demonstration**

or:

> **Needs work**

without creating two separate data concepts.

This keeps the graph aligned with the frozen data model.

---

# 9. CLAIM node

## Represents

A meaningful candidate technical claim extracted from speech, code context, or combined context.

## Canonical sources

Usually:

- `CandidateClaim`;
- source InterviewEvent;
- relevant TranscriptSegment;
- optional CodeSnapshot.

## Appears when

The claim materially participates in:

- a CounterQ question;
- an Evidence record;
- a Breakpoint;
- a meaningful contradiction;
- a strong demonstration.

A trivial claim should not appear merely because it was extracted.

## Candidate-facing title

Examples:

> `unordered_map lookup is always O(1)`

> `left never needs to move backwards`

> `This greedy choice is always safe`

## Summary

One short sentence describing the claim in context.

## Expandable details

- exact candidate wording;
- normalized interpretation;
- timestamp;
- relevant concept;
- related code version;
- transcript context.

## Actions

- View conversation context;
- View code at this moment where relevant.

---

# 10. REASONING node

## Represents

A grouped portion of candidate explanation that matters causally but is broader than one atomic Claim.

Examples:

- proposed sliding-window approach;
- explanation of DP state;
- debugging hypothesis;
- correctness argument.

## Canonical sources

One or more:

- TranscriptSegments;
- CandidateResponse sources;
- CandidateClaims;
- relevant code/event context.

## Appears when

The reasoning forms a meaningful causal step.

## Candidate-facing title

> **Approach: sliding window with last-seen positions**

or:

> **Debugging hypothesis: duplicate update is causing the failure**

## Expandable details

- supporting transcript segments;
- related concepts;
- stage;
- relevant source events.

## Actions

- View conversation.

---

# 11. CODE node

## Represents

A meaningful code decision or correction.

Use metadata/subtype such as:

- `DECISION`;
- `CORRECTION`;
- `SELF_CORRECTION`.

Do not create separate graph node families.

## Canonical sources

- CodeSnapshot;
- CodeDiff;
- InterviewEvent;
- optional CandidateClaim/Assessment.

## Appears when

The code directly:

- triggered a delivered question;
- supported Evidence;
- exposed a Breakpoint;
- demonstrated independent correction;
- materially contradicted speech.

## Candidate-facing title

Examples:

> **Window boundary update**

> **Corrected boundary update**

> **Changed from set to last-seen index map**

## Preview

A very small code fragment may appear.

Example:

```cpp
left = last[s[right]] + 1;
```

## Details

- exact CodeSnapshot version;
- relevant lines;
- previous/next meaningful diff;
- timestamp;
- why this code matters.

## Actions

- View full code at this moment;
- Compare with next snapshot.

---

# 12. TEST node

## Represents

A causally important execution/test event.

## Canonical sources

- ExecutionRun;
- TestResult;
- InterviewEvent;
- CodeSnapshot.

## Appears when

The test:

- triggered useful debugging;
- produced Evidence;
- caused a CounterQ question;
- exposed a meaningful error;
- demonstrated good candidate-created testing.

Do not show every Run.

## Candidate-facing title

Examples:

> **`abba` failed: expected 2, got 3**

> **Candidate tested empty input independently**

## Details

- exact code version;
- input;
- expected result;
- actual result;
- execution status;
- subsequent candidate action.

---

# 13. QUESTION node

## Represents

A meaningful interviewer prompt actually delivered to the candidate.

Most visible QUESTION nodes will correspond to:

- technical probes;
- important clarification questions;
- constraint mutations;
- final-defense questions.

Routine acknowledgements are excluded.

## Canonical sources

- InterviewerPrompt;
- InterviewerPromptDelivery;
- originating ExaminerDecision where applicable.

## Appears when

The prompt materially contributes to:

- CandidateResponse;
- Evidence;
- a Breakpoint;
- an important reasoning branch.

## Candidate-facing title

Use the actual delivered wording:

> **"What guarantees that `left` never moves backwards?"**

## Detail

- actual delivered wording;
- why CounterQ asked;
- triggering candidate claim/event/code;
- technical concept being tested;
- candidate-safe evidence goal;
- relevant code snapshot.

The candidate-facing **Why this question?** explanation must be generated from structured provenance such as:

- target Claim/Event/CodeSnapshot;
- prompt semantic intent;
- normalized concept;
- observed explanation/code inconsistency where validated;
- candidate-safe technical issue/category.

Do **not** directly render an unrestricted model-generated `ExaminerDecision.rationale` field.

That rationale may contain:

- internal shorthand;
- uncertainty;
- model-specific phrasing;
- implementation detail that should remain internal.

Instead CounterMap builds or generates a concise grounded explanation from the structured facts and validates it against its sources.

Do not expose internal ProbeStrategy enum by default.

## Actions

- View response;
- View triggering context.

---

# 14. RESPONSE node

## Represents

A semantically coherent candidate response to a meaningful CounterQ prompt.

## Canonical sources

- CandidateResponse;
- CandidateResponseSource;
- TranscriptSegments;
- code/run events where response includes action.

## Candidate-facing title

Example:

> **"I think it always moves forward because..."**

or a grounded concise summary:

> **Response: believed the lookup guarantee was worst-case**

## Details

- exact transcript;
- code changes during response;
- execution events;
- assessment arising from the response.

---

# 15. EVIDENCE node

## Represents

Validated CounterQ evidence.

This is the most important outcome node before Breakpoint.

## Canonical source

`Evidence` and its canonical source links.

## Appears when

Evidence materially explains:

- a strong demonstration;
- a weakness;
- a self-correction;
- an assistance-dependent result;
- a Breakpoint.

## Candidate-facing label

Depending on polarity:

> **Strong demonstration**

> **Mixed evidence**

> **Needs work**

The candidate does not need to see raw:

```text
polarity = NEGATIVE
strength = MODERATE
```

unless included in an optional detail section.

## Details

- concept;
- skill dimension;
- finding;
- evidence strength in human-readable form;
- independence level;
- supporting transcript/code/test;
- assistance context.

## Actions

- View sources;
- dispute assessment where enabled.

---

# 16. BREAKPOINT node

## Represents

A validated persistent boundary in candidate understanding.

It is not merely:

> candidate gave one wrong answer.

## Canonical source

`Breakpoint` + `BreakpointEvidence`.

## Candidate-facing title

Examples:

> **Hash-table worst-case complexity**

> **Sliding-window boundary invariant**

> **Dijkstra with negative edges**

## Details

- concept;
- skill dimension;
- what broke;
- why it matters;
- supporting Evidence;
- first exposure;
- current status;
- assistance received;
- retest state.

## Actions

Primary Phase 1 action:

> **CounterQ me again**

Secondary:

> This assessment seems wrong

Do not add a separate `Practice concept` action initially.

One clear action is better.

---

# 17. ASSISTANCE node

## Represents

Meaningful Coach guidance that causally affected subsequent candidate performance.

## Canonical sources

- InterviewerPrompt;
- InterviewerPromptDelivery;
- assistance metadata.

## Appears when

Assistance mattered to later Evidence.

Do not visualize every generic Coach acknowledgement.

## Candidate-facing label

> **Coach guidance**

Possible summary:

> **CounterQ narrowed attention to the previous duplicate position**

Do not show:

> Hint Level 3

unless future expert-mode UX intentionally exposes it.

## Details

- guidance actually delivered;
- concept targeted;
- assistance category;
- resulting retry/response.

---

# 18. MUTATION node

## Represents

A meaningful changed-constraint or transfer challenge.

## Canonical source

Delivered InterviewerPrompt, typically backed by Interview Pack/Examiner provenance.

## Candidate-facing title

Example:

> **Constraint change: input now arrives as a stream**

## Details

- original constraint;
- changed constraint;
- concept being tested;
- candidate response;
- Evidence produced.

A MUTATION is technically still an interviewer prompt, but its visual distinction is useful because it marks an intentional transfer branch.

The same underlying delivered prompt must not also produce a duplicate `QUESTION` node.

Likewise, delivered Coach help represented as `ASSISTANCE` must not also be duplicated as a `QUESTION` node.

---

# 19. Minimal edge vocabulary

Phase 1 should use a small causal edge vocabulary:

- `TRIGGERED`
- `ANSWERED_BY`
- `LED_TO`
- `SUPPORTED`
- `EXPOSED`
- `CORRECTED_BY`
- `ASSISTED`

Seven edge semantics are sufficient.

Do not create dozens of graph relation names.

---

# 20. `TRIGGERED`

Meaning:

> This candidate action/claim/code context directly caused CounterQ to consider and deliver this question.

Typical:

```text
CLAIM → QUESTION
CODE → QUESTION
TEST → QUESTION
```

Requires canonical ExaminerDecision provenance.

Temporal proximity alone is not enough.

---

# 21. `ANSWERED_BY`

Meaning:

> This delivered question received this candidate response.

Typical:

```text
QUESTION → RESPONSE
MUTATION → RESPONSE
```

Requires InterviewerPromptDelivery/CandidateResponse linkage.

---

# 22. `LED_TO`

Generic but controlled causal continuation.

Use when a more specific edge does not apply.

Examples:

```text
TEST → CODE correction
REASONING → CODE decision
RESPONSE → TEST
```

Do not use `LED_TO` as an excuse to invent causal associations.

Canonical relationship must still exist.

---

# 23. `SUPPORTED`

Meaning:

> This response/code/test materially supports this validated Evidence.

Examples:

```text
RESPONSE → EVIDENCE
CODE → EVIDENCE
TEST → EVIDENCE
```

---

# 24. `EXPOSED`

Meaning:

> This validated Evidence created or reinforced this Breakpoint.

```text
EVIDENCE → BREAKPOINT
```

Do not connect a raw wrong answer directly to Breakpoint if validated Evidence is the actual canonical source.

---

# 25. `CORRECTED_BY`

Meaning:

> An earlier reasoning/code issue was independently or subsequently corrected by this later candidate action.

Examples:

```text
CODE → CODE
CLAIM → CLAIM
```

May also lead to positive Evidence.

`CORRECTED_BY` requires more than:

```text
code changed from A to B
```

Code lineage proves that an edit occurred.

It does not by itself prove:

> B corrected the conceptual issue represented by A.

Use `CORRECTED_BY` only when canonical provenance supports correction semantics, for example through:

- candidate verbal self-correction linked to both events;
- Assessment/Evidence explicitly recognizing the correction;
- CandidateResponse sources that contain the before/after action;
- an explicit causal/event link captured by the orchestrator.

If CounterQ only knows that the code changed, use neutral chronological/causal representation rather than claiming it was a correction.

---

# 26. `ASSISTED`

Meaning:

> This Coach assistance materially contributed to this later response/reasoning/code outcome.

Example:

```text
ASSISTANCE → RESPONSE
```

or:

```text
ASSISTANCE → CODE
```

It must be target-scoped.

One assistance node must not connect indiscriminately to all later session Evidence.

---

# 27. Why edges need canonical support

CounterMap may draw:

```text
A → B
```

only when CounterQ can identify the underlying causal relationship.

Example:

A suspicious CodeSnapshot happens at 10:22.

A question happens at 10:25.

That does **not** prove:

```text
CODE → TRIGGERED → QUESTION
```

To draw the edge, CounterQ should have provenance such as:

```text
ExaminerDecision.target_code_snapshot_id = snapshot_17
InterviewerPrompt.examiner_decision_id = decision_52
InterviewerPromptDelivery confirms delivery
```

This is the central integrity rule.

> **Temporal proximity is not causality.**

---

# 28. Why CounterQ asked this

One of the highest-value CounterMap interactions is:

> **Why this question?**

Clicking a QUESTION node should produce a concise explanation such as:

```text
CounterQ asked

“What guarantees that left never moves backwards?”

Why this question?

Your explanation said the window boundary only moves forward,
but the code at that moment could assign an earlier position.

Testing

Sliding-window invariant
```

This explanation must be grounded in persisted:

- target source;
- structured ExaminerDecision fields;
- concept;
- prompt semantic intent;
- actual delivered prompt;
- relevant code snapshot/event.

CounterMap should treat an unrestricted model rationale as **internal provenance**, not candidate-facing copy.

Candidate-facing explanation should be deterministically assembled where possible, or generated from a constrained source bundle and then validated.

---

# 29. What rationale may be exposed

Candidate-visible rationale may include:

- exact triggering claim;
- exact relevant code;
- observable contradiction;
- canonical concept;
- evidence goal;
- concise technical reason.

Example:

> Your complexity claim treated average-case hash-table lookup as a guarantee.

This is acceptable.

---

# 30. What rationale must remain hidden

Do not expose:

- private model chain-of-thought;
- scratchpad;
- hidden reasoning tokens;
- provider reasoning traces;
- raw prompt text;
- internal model debate;
- internal target ranking.

Bad:

```text
I first reasoned that the candidate may have misunderstood hashing,
then considered whether...
```

Good:

> **Why this question?**  
> Your statement treated O(1) lookup as a worst-case guarantee.

CounterMap explains provenance, not private cognition.

---

# 31. Evidence presentation

Evidence details should be human-readable.

Recommended:

```text
What this showed
You correctly explained why the left boundary cannot move backward.

Concept
Sliding-window invariant

Interview skill
Correctness reasoning

How independently?
Independent

Supported by
Approach explanation + code snapshot v18
```

Avoid:

```text
Understanding confidence: 73.48%
```

Phase 1 does not have calibrated psychometric precision.

---

# 32. Strong evidence

CounterMap must give positive Evidence equal visual legitimacy.

Examples:

- independently defended invariant;
- found edge case without prompting;
- self-corrected before CounterQ intervened;
- handled constraint mutation;
- correctly derived amortized complexity;
- debugging hypothesis was accurate.

A candidate should not open CounterMap and see only red weaknesses.

CounterMap is an explanation of performance.

Not a failure map.

---

# 33. Breakpoint vs incorrect answer

Not every error becomes a Breakpoint.

The hierarchy is:

```text
Mistake / behavior
        ↓
Assessment
        ↓
Validated Evidence
        ↓
possibly Breakpoint
```

Examples:

### Syntax error

Candidate misses a semicolon.

Usually:

- execution event;
- perhaps debugging evidence;
- no Breakpoint.

### Temporary verbal slip

Candidate says O(n²), pauses, then independently derives O(n).

Likely:

- mixed Evidence;
- maybe no Breakpoint.

### Repeated misconception

Candidate repeatedly treats average hash-table complexity as guaranteed worst-case despite focused challenge.

Potential:

- validated negative Evidence;
- Breakpoint.

CounterMap must respect frozen Breakpoint policy.

---

# 34. Self-correction

Self-correction is one of CounterMap's most valuable patterns.

Example:

```text
CODE
Potential boundary issue
        ↓ CORRECTED_BY
CODE
Candidate fixes update independently
        ↓ SUPPORTED
EVIDENCE
Independent debugging
```

If CounterQ prepared a question but never delivered it because the candidate corrected themselves:

the question must not appear.

Candidate-facing CounterMap should show only the grounded candidate path:

```text
earlier claim/code
→ independent correction
→ positive/mixed Evidence
```

Candidate-facing outcome:

> **Corrected independently**

Do **not** add copy such as:

> "CounterQ noticed this but chose to stay silent."

That statement describes hidden Examiner activity and is unnecessary to explain the candidate's evidence.

The internal reviewer view may inspect the stale/suppressed ExaminerDecision.

The candidate map simply gives full credit for independent correction without fabricating a question or narrating hidden cognition.

---

# 35. Stale, rejected, and undelivered questions

Candidate-visible CounterMap must exclude:

- stale ExaminerDecisions;
- rejected ExaminerDecisions;
- unapproved probe candidates;
- authorized but never delivered prompts where no candidate-visible effect occurred;
- internal ranking alternatives.

These are valid internal observability data.

They are not part of the candidate's interview experience.

Candidate-visible truth follows:

```text
InterviewerPromptDelivery
```

not merely:

```text
InterviewerPrompt
```

---

# 36. Interrupted prompts

If CounterQ began a question but was interrupted:

## If almost no meaningful question was delivered

Exclude it.

## If enough was delivered to affect the response

It may appear.

The QUESTION node must use:

> actual delivered wording

not the original intended full wording.

Never reveal the unseen remainder of a partially delivered prompt.

---

# 37. Coach assistance

Coach CounterMap must make assistance visible when it materially affects later evidence.

Example:

```text
EVIDENCE
Initial invariant weakness
        ↓ LED_TO
ASSISTANCE
Coach guidance
        ↓ ASSISTED
RESPONSE
Candidate revises reasoning
        ↓ SUPPORTED
EVIDENCE
Improved reasoning after guidance
```

This tells the learning story accurately.

---

# 38. Assistance tone

Candidate-facing wording should be neutral.

Preferred:

> **Coach guidance**

> **Solved after guidance**

Avoid:

> **FAILED — HINT REQUIRED**

Assistance is information about independence, not a punishment.

---

# 39. Assistance scope

An assistance node should connect only to the outcome it materially influenced.

Example:

Coach gives a strong hint for approach discovery.

Candidate later independently derives complexity.

Do not draw:

```text
ASSISTANCE → complexity Evidence
```

unless the assistance materially contributed to that reasoning.

This aligns with the frozen Evidence independence model.

---

# 40. Improvement after Coach guidance

CounterMap can present:

```text
Initial reasoning
        ↓
Evidence: gap exposed
        ↓
Breakpoint
        ↓
Coach guidance
        ↓
Retry
        ↓
Evidence: improved after guidance
        ↓
Retest recommended
```

Do not conclude:

```text
Mastered
```

immediately after teaching.

Mastery requires separate evidence policy and later independent confirmation.

---

# 41. Simulation map

Simulation usually contains:

- independent candidate reasoning;
- delivered interviewer questions;
- candidate responses;
- code decisions;
- tests;
- Evidence;
- Breakpoints;
- independent self-corrections;
- mutations.

There are normally no ASSISTANCE nodes.

After interview completion, detail drawers may contain:

> **A stronger answer**

or:

> **What this concept means**

because diagnostic secrecy no longer applies.

---

# 42. Code-aware CounterMap

Code-driven causality should be especially visible.

Example:

```text
CODE
left = last[s[right]] + 1
        ↓ TRIGGERED
QUESTION
"What guarantees that left never moves backwards?"
        ↓ ANSWERED_BY
RESPONSE
"It could move backward if..."
        ↓ LED_TO
CODE
left = max(left, last[s[right]] + 1)
        ↓ SUPPORTED
EVIDENCE
Invariant understood after probe
```

This is substantially more differentiated than a transcript visualization.

It proves:

> **CounterQ reacted to the candidate's implementation.**

---

# 43. Code snippets in nodes

Graph nodes may contain at most a small relevant fragment.

Good:

```cpp
left = last[s[right]] + 1;
```

Bad:

20 lines of surrounding function code.

Detailed code belongs in the node drawer.

---

# 44. Exact code provenance

Every CODE node and code-driven QUESTION node must retain:

- CodeSnapshot ID;
- code version;
- content hash;
- relevant range where known;
- relevant CodeDiff where useful;
- source InterviewEvent;
- event watermark.

The candidate should be able to select:

> **View code at this moment**

and see what CounterQ actually saw.

Never resolve this to:

> latest code

after the interview.

---

# 45. Test-event policy

Execution history is not CounterMap.

Only causally meaningful test events appear.

Useful examples:

- failed test triggered debugging branch;
- candidate independently chose a revealing edge case;
- repeated failure exposed misconception;
- execution result triggered a question.

Routine:

```text
Run → Passed
Run → Passed
Run → Passed
```

should normally be compressed or omitted.

---

# 46. Conversation compression

CounterMap should group multiple transcript segments when they form one coherent reasoning step.

Example:

Candidate spends 90 seconds describing an approach across several transcript turns.

Visible node:

> **Approach: sliding window using last-seen positions**

Canonical support:

```text
TranscriptSegments 81–89
CandidateClaims C12, C13
```

The source remains inspectable.

---

# 47. Compression rules

Transcript segments may be grouped when:

- same speaker;
- same reasoning objective;
- no substantive interviewer prompt split them;
- they belong to the same causal branch;
- grouping does not hide a contradiction.

Do not combine:

```text
initial incorrect claim
+
later corrected claim
```

into one bland summary.

That would remove meaningful causality.

---

# 48. Graph granularity

Typical guidance for a normal 25–30 minute interview:

```text
~8–20 visible nodes
```

This is not an application hard limit.

It is a design signal.

If a standard interview consistently produces:

```text
60–80 nodes
```

projection logic is too literal.

If it produces:

```text
2 nodes
```

despite meaningful probing, projection is probably too compressed.

---

# 49. Branching model

CounterMap is not forced into a timeline.

Example:

```text
              ┌── Complexity Claim
              │         ↓
Approach ─────┤    CounterQ Question
              │         ↓
              │      Breakpoint
              │
              └── Code Decision
                        ↓
                  Self-correction
                        ↓
                  Positive Evidence
```

Both branches originate from the same approach but test different things.

---

# 50. Layout direction

Phase 1 should use:

> **left-to-right layered causal flow**

Primary conceptual direction:

```text
Candidate action/reasoning
        →
CounterQ interaction
        →
Candidate response/action
        →
Evidence
        →
Breakpoint / outcome
```

Branches stack vertically.

This is easier to read than:

- radial layout;
- force-directed graph;
- arbitrary network positioning.

CounterMap is a directed causal DAG-like projection.

The layout should communicate that.

---

# 51. React Flow

Use React Flow for Phase 1 rendering.

Reasons:

- mature node/edge interaction;
- pan/zoom;
- custom React nodes;
- fit-view;
- straightforward detail interactions;
- appropriate for small session graphs;
- avoids custom graph-canvas infrastructure.

Do not build a custom SVG graph engine.

---

# 52. Layout engine

Use **Dagre** initially.

Reason:

Phase 1 maps are:

- small;
- mostly directed;
- layered;
- approximately DAG-shaped;
- typically 8–20 nodes.

Dagre provides enough deterministic layered layout without introducing the complexity of ELK.

Configure:

```text
direction = LR
```

with stable node ordering derived from:

- causal rank;
- stage;
- event order;
- canonical node ID as deterministic tie-breaker.

---

# 53. Why not ELK initially

ELK is more capable for:

- large graphs;
- compound graphs;
- sophisticated edge routing.

Phase 1 does not yet need that complexity.

If real interviews produce layout problems Dagre cannot handle, ELK can later replace the projection-layout step without changing canonical CounterMap semantics.

Layout engine is not data architecture.

---

# 54. Visual language

CounterMap should use a restrained semantic visual system.

Differentiate categories through combinations of:

- icon;
- node shape;
- border treatment;
- label;
- color.

Never rely on color alone.

Possible semantic families:

### Candidate

- Claim;
- Reasoning;
- Code;
- Response;
- Test.

### CounterQ

- Question;
- Mutation;
- Assistance.

### Evaluation

- Evidence;
- Breakpoint.

Positive and negative Evidence can use distinct status cues without becoming visually dramatic.

---

# 55. Node density

Each node preview should normally contain:

- type cue;
- short title;
- perhaps one short outcome label.

Example:

```text
QUESTION

Is O(1) guaranteed?
```

Not:

a paragraph explaining the entire interaction.

Details belong in the drawer.

---

# 56. Detail drawer

Clicking a node opens a contextual drawer.

The canvas should remain visible so the candidate retains orientation.

Recommended drawer position:

right-side overlay/panel on desktop.

Drawer contents depend on node type.

---

# 57. Claim detail

Example:

```text
You said

“unordered_map lookup is always O(1).”

Concept
Hash-table complexity

When
Approach discussion · 06:18

Related code
Snapshot v4
```

Actions:

- View conversation;
- View code where applicable.

---

# 58. Question detail

Example:

```text
CounterQ asked

“Is that actually guaranteed?”

Why this question?

Your explanation relied on constant-time lookup
as a guarantee rather than an average-case expectation.

Testing

Hash-table complexity
```

Optional source links:

- View triggering statement;
- View code context;
- View response.

Do not expose raw ExaminerDecision internals.

---

# 59. Response detail

Example:

```text
You answered

“Yes, hash maps are constant time.”

What followed

CounterQ asked about collision behavior.
```

If validated Evidence exists:

show link:

> View what this demonstrated

---

# 60. Evidence detail

Example:

```text
Needs work

Hash-table worst-case complexity

What this showed

You treated expected O(1) lookup as a strict
worst-case guarantee.

Interview skill

Complexity reasoning

How independently?

After probe

Supported by

2 responses
```

Actions:

- View sources;
- This assessment seems wrong.

---

# 61. Breakpoint detail

Example:

```text
Breakpoint

Hash-table worst-case complexity

What broke

You did not initially distinguish expected lookup
cost from worst-case collision behavior.

Why it matters

Interview complexity claims should state the
assumptions behind their guarantees.

Evidence

2 related responses

Status

Needs retest

[ CounterQ me again ]
[ This assessment seems wrong ]
```

This is far more useful than:

> Weak at hashing.

---

# 62. Positive Evidence detail

Example:

```text
Strong demonstration

Sliding-window invariant

What you demonstrated

You independently explained why `left`
cannot move backwards and implemented
that invariant correctly.

Supported by

Approach explanation
Code snapshot v18

How independently?

Independent
```

Keep tone factual rather than celebratory.

---

# 63. Stronger-answer section

For selected technical findings, detail drawer may include:

> **A stronger answer**

Example:

> "`unordered_map` lookup is expected O(1) on average, but its worst case can degrade when collisions create expensive bucket lookup."

Reference content must come from:

- verified Interview Pack;
- reviewed technical content;
- evidence-grounded generation.

Do not imply there is always exactly one acceptable phrasing.

---

# 64. CounterQ me again

`CounterQ me again` is the primary Phase 1 action from an eligible Breakpoint.

Its contract is:

```text
Breakpoint
        ↓
canonical concept + skill gap
        ↓
RetestRecommendation
        ↓
future session/drill chooses
different relevant context where practical
        ↓
independent retest
```

It should **not** simply replay:

> "Is O(1) guaranteed?"

over and over.

The retest system should seek transfer where practical.

---

# 65. Practice concept action

Do **not** add a separate generic:

> Practice concept

action in Phase 1.

Reason:

CounterQ's differentiated action is:

> **CounterQ me again**

A second generic action:

- dilutes the retest loop;
- requires another learning-content destination;
- increases UX choice without clear benefit.

Future Coach curriculum features may add broader practice navigation later.

---

# 66. Map-level summary

CounterMap header may include a concise session summary such as:

```text
CounterMap

3 strong demonstrations
2 Breakpoints
1 independent self-correction
```

No overall numeric score is required.

This summary helps candidates orient before exploring the graph.

Do not turn it into another report dashboard.

---

# 67. CounterMap vs Session Report

Session Report answers:

> **How did I perform overall, and what should I improve?**

CounterMap answers:

> **What happened that supports those conclusions?**

The report may say:

```text
Complexity reasoning: needs work
```

and link:

> View evidence in CounterMap

The map then shows:

```text
Claim
→ Question
→ Response
→ Evidence
→ Breakpoint
```

They are complementary surfaces.

Not duplicates.

---

# 68. CounterMap vs Mastery Map

CounterMap is:

```text
single-session causal history
```

Mastery Map is:

```text
cross-session current understanding
```

CounterMap:

> Hash-table worst-case behavior was exposed here.

Mastery Map:

> Hash-table complexity has remained weak across multiple independent attempts.

CounterMap feeds mastery Evidence.

It does not own the candidate's current mastery state.

---

# 69. Do not mix session and concept graphs

Phase 1 must not create one giant graph containing:

- sessions;
- transcripts;
- code;
- prompts;
- concepts;
- mastery;
- retests;
- reports.

That graph would become incomprehensible.

CounterMap stays session-centered.

Mastery Map stays concept-centered.

---

# 70. Projection persistence

Frozen `DATA_MODEL.md` already establishes:

```text
countermap_projections
```

as the physical Phase 1 persistence mechanism.

Phase 1 should **not** add physical:

- `countermap_nodes`;
- `countermap_edges`;

tables.

The projection row stores a versioned graph JSON structure.

Conceptually:

```text
CounterMapProjection
- interview_session_id
- projection_version
- schema_version
- source_watermark
- status
- generated_at
- graph_json
```

Inside `graph_json`:

```text
nodes[]
edges[]
summary
```

---

# 71. Projection node shape

Conceptually:

```text
node_id
node_type
subtype
canonical_sources[]
title
summary
causal_rank
stage
event_range
display_metadata
available_actions
```

`canonical_sources` must reference persisted source identities.

A node must never exist without canonical provenance.

---

# 72. Projection edge shape

Conceptually:

```text
edge_id
from_node_id
to_node_id
relationship
canonical_relationship_sources[]
```

The source reference may point to:

- ExaminerDecision;
- InterviewerPromptDelivery/CandidateResponse;
- EvidenceSource;
- BreakpointEvidence;
- assistance linkage;
- event causation.

---

# 73. Projection versioning

Persist:

- projection schema version;
- projection generation policy version;
- source event/evidence watermark;
- generated time.

If CounterQ later improves:

- graph compression;
- node titles;
- graph layout;
- summarization;

the map can be regenerated without touching canonical interview history.

---

# 74. Projection generation responsibilities

Deterministic software should own:

- source selection;
- visibility rules;
- causal edge construction;
- question-delivery truth;
- Breakpoint eligibility;
- Evidence linkage;
- code-version references;
- assistance linkage;
- projection validation;
- action enablement.

AI may assist with:

- short titles;
- concise reasoning summaries;
- human-readable explanation;
- grouping transcript segments under deterministic constraints.

AI cannot invent relationships.

---

# 75. AI summarization rule

Suppose five transcript segments support one approach explanation.

AI may produce:

> **Approach: sliding window with last-seen positions**

but the projection must retain:

```text
source_segments = [81,82,83,84,85]
```

If the summary is disputed or later improves:

the canonical transcript remains authoritative.

---

# 76. CounterMap generation algorithm

Recommended Phase 1 flow:

```text
1. Load canonical session data through the final source watermark.

2. Load:
   - delivered interviewer prompts;
   - candidate responses;
   - meaningful claims;
   - relevant code snapshots/diffs;
   - causally relevant tests;
   - validated Evidence;
   - Breakpoints;
   - Coach assistance.

3. Exclude:
   - stale Examiner decisions;
   - rejected decisions;
   - undelivered prompts;
   - insignificant execution events;
   - ordinary acknowledgements.

4. Identify candidate-visible causal anchors:
   - Claim / reasoning;
   - Code;
   - Test;
   - delivered Question;
   - Response;
   - Evidence;
   - Breakpoint;
   - Assistance;
   - Mutation.

5. Group low-level transcript segments into coherent reasoning nodes.

6. Materialize exactly one primary visible prompt node per meaningful delivered interviewer turn:
   - QUESTION;
   - MUTATION;
   - or ASSISTANCE.

7. Build candidate-safe "Why this question?" explanations from structured provenance; never copy raw private/internal rationale blindly.

8. Construct causal edges only from canonical provenance.

9. Add validated EVIDENCE nodes.

10. Add BREAKPOINT nodes only where canonical Breakpoint/Evidence links exist.

11. Add positive Evidence/self-correction branches.

12. Add Coach ASSISTANCE only where it materially contributed.

13. Compress structurally redundant internal steps.

14. Generate grounded titles/summaries.

15. Validate projection integrity.

16. Assign deterministic causal rank/order.

17. Persist versioned graph JSON.

18. Render with deterministic layered layout.
```

---

# 77. Projection validation rules

Before a projection may become `READY`:

### Node provenance

Every visible node must reference at least one canonical source.

### Edge provenance

Every edge must be justified by a persisted relationship.

### Question truth

QUESTION/MUTATION/ASSISTANCE wording must derive from actual `InterviewerPromptDelivery`.

A single delivered interviewer turn may map to only one primary candidate-visible prompt node.

### Candidate-safe rationale

"Why this question?" explanations must be reconstructable from structured canonical provenance and must not expose raw model chain-of-thought or unrestricted internal rationale text.

### Stale exclusion

No stale/rejected/undelivered prompt may appear as candidate-visible question.

### Interrupted prompt integrity

Undelivered portions of interrupted prompts cannot appear.

### Breakpoint integrity

Every BREAKPOINT must have valid non-invalidated supporting Evidence.

### Evidence integrity

Every visible Evidence node must correspond to validated Evidence.

### Assistance integrity

Every ASSISTANCE node must correspond to assistance actually delivered.

### Code integrity

Every code-related node must reference an exact CodeSnapshot/version.

A `CORRECTED_BY` relationship requires explicit correction semantics from canonical Assessment/Evidence/response/event provenance; code change alone is insufficient.

### Quote integrity

Candidate quotes must be exact finalized transcript text.

Do not generate quotation marks around paraphrases.

### No chain-of-thought

No hidden reasoning traces may appear.

### No orphan causal nodes

Every non-root visible node should participate in a meaningful branch, except intentionally independent positive Evidence such as an unprompted self-correction.

---

# 78. Projection failure

If validation fails:

do not publish a graph that "mostly looks right."

CounterMap is a trust feature.

Safe failure:

> **CounterMap is unavailable for this interview. Your report and interview evidence are still safe.**

Projection can be retried.

Canonical data remains unaffected.

---

# 79. Evidence invalidation

If Evidence is later invalidated:

1. canonical Evidence status changes;
2. dependent Breakpoint may be recalculated;
3. CounterMap projection becomes stale;
4. rebuild occurs;
5. unsupported Evidence/Breakpoint nodes disappear or change.

The existing graph cannot preserve a conclusion merely because it was previously displayed.

---

# 80. Interview deletion

Deleting an interview deletes its CounterMap projection.

No graph should survive after its canonical interview sources have been removed.

Cross-session mastery then recalculates according to `DATA_MODEL.md`.

---

# 81. Performance

A Phase 1 CounterMap should be cheap to open.

Normal request path:

```text
load countermap_projections
        ↓
parse graph_json
        ↓
render React Flow
```

Do not rebuild the graph on every page view.

Do not require recursive graph-database traversal.

Generation occurs asynchronously after the session or when a projection becomes stale.

---

# 82. Progressive generation

CounterMap should not require expensive natural-language enrichment before useful causal structure can be produced.

However, `READY` must have a clear meaning.

Recommended projection status semantics:

```text
BUILDING
READY
FAILED
STALE
```

A `READY` projection is internally self-consistent and safe for candidate display.

If richer grounded summaries are generated later, prefer one of:

### Option A — New projection version

```text
projection v1 READY
        ↓
grounded enrichment completes
        ↓
projection v2 READY
```

The UI atomically moves from one valid projection to another.

### Option B — Non-semantic lazy detail

Load source-backed details on demand without mutating causal graph truth.

Do not continually mutate a candidate-visible `READY` graph in place while:

- node meaning changes;
- Breakpoint explanations change;
- edge semantics change.

And never show incomplete/placeholder conclusions merely to make the map appear faster.

The principle is:

> **Progressive UX is allowed. Progressive truth is not.**

---

# 83. Small / low-evidence maps

A strong candidate may produce a simple map.

Example:

```text
Approach
    ↓
CounterQ proof question
    ↓
Strong response
    ↓
Correct implementation
    ↓
Complexity evidence
```

This is acceptable.

Do not invent:

- extra questions;
- artificial Breakpoints;
- meaningless branches;

to make the graph visually impressive.

A small CounterMap may itself indicate a clean interview.

---

# 84. Large-map behavior

If a session produces a larger graph:

Phase 1 supports:

- pan;
- zoom;
- fit view;
- detail drawer;
- branch collapsing only if real user testing requires it.

Do not initially build:

- minimap;
- graph search;
- saved manual layouts;
- complex filters;
- edge toggles.

Normal graphs should remain small enough that those features are unnecessary.

---

# 85. Accessibility

A canvas graph cannot be the only representation.

CounterMap must provide:

> **Reasoning Timeline**

The timeline uses the same projection and presents causal branches as accessible cards.

Example:

```text
1. You claimed...
2. CounterQ asked...
3. You answered...
4. This showed...
5. Breakpoint exposed...
```

Branch relationships should remain clearly labeled.

Screen-reader users must be able to access equivalent meaning without interacting with React Flow.

---

# 86. Mobile behavior

CounterMap may be viewed on mobile even though interviews are desktop-first.

On narrow displays:

default to:

> **Reasoning Timeline**

with an optional graph view where technically usable.

Do not invest Phase 1 effort into advanced touch graph navigation.

---

# 87. Map interactions

Phase 1 supports:

- click node;
- open detail drawer;
- pan;
- zoom;
- fit view;
- switch Graph / Reasoning Timeline;
- open transcript source;
- open code source;
- `CounterQ me again`;
- assessment dispute.

Do not support:

- editing nodes;
- moving and saving nodes;
- manually creating edges;
- changing Evidence;
- annotating other candidates.

CounterMap is an explanation surface.

Not a graph editor.

---

# 88. Source navigation

Details should provide actions such as:

> **View conversation context**

> **View code at this moment**

> **View test result**

This is important for candidate trust.

If CounterQ says:

> "Your implementation allowed the pointer to move backwards."

the candidate should be able to inspect the exact snapshot.

---

# 89. Filters

Do not add graph filters in initial Phase 1.

With a target graph size around 8–20 nodes:

```text
All / Breakpoints / Strong
```

adds unnecessary interface complexity.

If actual maps consistently become larger, filtering can be introduced later.

---

# 90. Candidate-facing terminology

Recommended terminology:

- You said;
- Your reasoning;
- Your code;
- CounterQ asked;
- You answered;
- What this showed;
- Strong demonstration;
- Breakpoint;
- Coach guidance;
- Constraint change;
- Corrected independently.

Keep internal terms hidden:

- ExaminerDecision;
- ProbeStrategy;
- source watermark;
- state version;
- AIInvocation;
- model confidence;
- target rank.

`Breakpoint` should remain a product-facing CounterQ term.

---

# 91. Ordering

Causality is primary.

When two nodes share no direct causal dependency, deterministic ordering may use:

1. causal depth;
2. interview stage;
3. server sequence;
4. canonical node ID as stable tie-breaker.

This ensures projection does not move around arbitrarily between renders.

---

# 92. Stable layout

Given the same projection version and viewport class, node positioning should remain substantially stable.

Random graph movement damages:

- orientation;
- screenshots;
- debugging;
- user trust.

Store:

- causal ranks;
- deterministic ordering inputs.

The client may recompute coordinates using the same deterministic rules.

Coordinates themselves do not need to become canonical data.

---

# 93. CounterMap and numeric scores

CounterMap must work perfectly even if CounterQ never launches a single overall numeric interview score.

Its value is:

- provenance;
- causal explanation;
- actionable diagnosis.

It must not become:

> a fancy score visualization.

---

# 94. Candidate trust and disagreement

CounterMap should make disagreement inspectable.

If the candidate believes CounterQ made a bad conclusion, they should be able to inspect:

- their exact wording;
- code version;
- CounterQ question;
- their response;
- Evidence interpretation.

This is far stronger than:

> AI confidence = 82%.

---

# 95. Assessment dispute

Phase 1 should include a lightweight action:

> **This assessment seems wrong**

on:

- negative Evidence;
- mixed Evidence;
- Breakpoint details.

This is worth including because false technical challenges are especially damaging to CounterQ trust.

---

# 96. Dispute behavior

Clicking should persist a factual user-action event using the existing interview-event infrastructure rather than requiring a new Phase 1 assessment-feedback table.

Conceptually:

```text
InterviewEvent
event_type = assessment_disputed
source = CLIENT
payload = {
  projection_node_id,
  evidence_id / breakpoint_id,
  assessment_or_policy_version,
  optional_comment
}
```

Analytics may separately emit a content-free `assessment_disputed` telemetry event.

The durable interview event records:

- interview/session identity;
- projection node ID;
- Evidence/Breakpoint ID;
- assessment/policy version;
- optional user comment;
- timestamp and provenance.

It must **not** automatically:

- delete Evidence;
- change Breakpoint;
- increase mastery;
- regenerate the report.

It creates product/evaluation feedback for later review.

---

# 97. Internal reviewer view

Candidate CounterMap remains clean.

Architecture should nevertheless preserve an internal reviewer/debug surface capable of inspecting:

- canonical IDs;
- ExaminerDecision;
- ProbeStrategy;
- AIInvocation;
- source event watermark;
- code version;
- stale/rejected decisions;
- projection rules;
- model/policy versions.

This internal view is valuable for improving Examiner quality.

It is not Phase 1 candidate UI.

---

# 98. CounterMap telemetry

Useful events include:

- `countermap_opened`;
- `countermap_node_opened`;
- `breakpoint_opened`;
- `positive_evidence_opened`;
- `transcript_source_opened`;
- `code_source_opened`;
- `counterq_me_again_clicked`;
- `assessment_disputed`;
- `reasoning_timeline_used`;
- `countermap_fit_view_used`.

Analytics must not contain:

- transcript text;
- candidate code;
- hidden assessment rationale.

Use canonical IDs.

---

# 99. Product-quality metrics

Relevant metrics include:

- percentage of reports where CounterMap is opened;
- percentage of Breakpoints inspected;
- percentage of code-driven nodes where source code is opened;
- `CounterQ me again` conversion;
- assessment dispute rate;
- projection validation failure rate;
- false-causality defect rate;
- candidate agreement with Breakpoints;
- candidate understanding of why questions were asked.

A particularly important qualitative metric is:

> **Causal trust**

Question:

> "Did CounterMap make it clear why CounterQ challenged you?"

---

# 100. CounterMap overview wireframe

```text
┌─────────────────────────────────────────────────────────────────┐
│ CounterMap                                      [Reasoning List] │
│ See how your interview reasoning unfolded.                      │
│                                                                 │
│ 3 strong demonstrations    2 Breakpoints    1 self-correction  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ [Approach: Sliding Window]                                      │
│          │                                                      │
│          ├───────────────┐                                      │
│          ▼               ▼                                      │
│ [Complexity Claim]   [Code: boundary update]                    │
│          │               │                                      │
│          ▼               ▼                                      │
│ [CounterQ asked]    [Corrected independently]                   │
│          │               │                                      │
│          ▼               ▼                                      │
│ [Your response]     [Strong evidence]                           │
│          │                                                      │
│          ▼                                                      │
│ [Breakpoint]                                                 │
│ Hash-table worst-case behavior                                  │
│                                                                 │
│             Click any node to inspect its evidence.             │
└─────────────────────────────────────────────────────────────────┘
```

---

# 101. Detail drawer wireframe

```text
┌──────────────────────────────────────────────┐
│ Breakpoint                              [×]  │
│                                              │
│ Hash-table worst-case complexity             │
│                                              │
│ What happened                                │
│ You described unordered_map lookup as a      │
│ guaranteed O(1) operation.                   │
│                                              │
│ CounterQ asked                               │
│ “Is that actually guaranteed?”               │
│                                              │
│ You answered                                 │
│ “Yes.”                                       │
│                                              │
│ What this showed                             │
│ Your explanation did not distinguish         │
│ average-case lookup from worst-case           │
│ collision behavior.                          │
│                                              │
│ Why it matters                               │
│ Complexity guarantees should state the       │
│ assumptions behind them.                     │
│                                              │
│ Evidence                                     │
│ [ View conversation ]                        │
│ [ View code at this moment ]                 │
│                                              │
│ Status                                       │
│ Needs retest                                 │
│                                              │
│ [ CounterQ me again ]                        │
│                                              │
│ This assessment seems wrong                  │
└──────────────────────────────────────────────┘
```

---

# 102. Self-correction wireframe

```text
┌─────────────────────────┐
│ CODE                    │
│ Window boundary update  │
│                         │
│ left = last[...] + 1    │
└────────────┬────────────┘
             │
      CORRECTED_BY
             │
             ▼
┌─────────────────────────┐
│ CODE                    │
│ Corrected independently │
│                         │
│ left = max(left, ...)   │
└────────────┬────────────┘
             │
         SUPPORTED
             │
             ▼
┌─────────────────────────┐
│ STRONG DEMONSTRATION    │
│ Independent debugging   │
│                         │
│ You noticed and fixed   │
│ the invariant yourself. │
└─────────────────────────┘
```

There is intentionally **no CounterQ Question node** because no question was delivered.

This communicates the candidate-facing truth:

> The candidate recognized and corrected the issue independently.

Whether CounterQ internally prepared and suppressed a probe belongs to internal reviewer/debug tooling, not to the candidate's causal map.

---

# 103. Component hierarchy

Conceptual React structure:

```text
CounterMapPage
├── CounterMapHeader
│   ├── CounterMapTitle
│   ├── MapSummary
│   └── CounterMapViewToggle
│
├── CounterMapContent
│   ├── CounterMapCanvas
│   │   ├── ClaimNode
│   │   ├── ReasoningNode
│   │   ├── CodeNode
│   │   ├── TestNode
│   │   ├── QuestionNode
│   │   ├── ResponseNode
│   │   ├── EvidenceNode
│   │   ├── BreakpointNode
│   │   ├── AssistanceNode
│   │   ├── MutationNode
│   │   ├── CounterMapEdge
│   │   └── MapControls
│   │
│   └── ReasoningTimeline
│       ├── CausalBranch
│       └── TimelineNodeCard
│
└── CounterMapDetailDrawer
    ├── NodeHeader
    ├── NodeSummary
    ├── CausalExplanation
    ├── EvidenceDetail
    ├── AssistanceDetail
    ├── SourceLinks
    ├── StrongerAnswer
    └── NodeActions
        ├── CounterQAgainButton
        └── DisputeAssessmentButton
```

Do not write custom graph infrastructure outside React Flow without strong evidence it is necessary.

---

# 104. Mobile component behavior

On narrow displays:

```text
CounterMapViewToggle
```

defaults to:

```text
Reasoning Timeline
```

The graph remains optional.

Detail content can open as full-screen mobile sheet rather than side drawer.

No separate mobile causal model is required.

---

# 105. Phase 1 implementation order

CounterMap should be built in this order.

## Phase A — Causal persistence first

Before any graph:

ensure interviews persist:

```text
Candidate claim / code
→ ExaminerDecision
→ InterviewerPrompt
→ InterviewerPromptDelivery
→ CandidateResponse
→ Evidence
```

This starts during the Core Interaction Spike.

---

## Phase B — Reasoning Timeline

Build a simple post-session causal list first.

Why:

- validates projection semantics;
- easier to debug;
- easier to inspect provenance;
- immediately useful;
- accessible by default.

If the timeline is wrong, a graph will also be wrong.

---

## Phase C — CounterMap graph

Add React Flow projection once causal selection/compression rules are stable.

---

## Phase D — Detail drawer and source navigation

Add:

- exact transcript;
- code snapshots;
- "Why this question?";
- Evidence details.

---

## Phase E — Retest action

Add:

> **CounterQ me again**

once RetestRecommendation integration exists.

This ordering protects the product from spending time polishing a graph before causal truth is reliable.

---

# 106. Technical spike requirement

CounterMap UI is **not required** for the first Core Interaction Spike.

The spike must, however, persist enough provenance to reconstruct:

```text
CandidateClaim / source behavior
        ↓
ExaminerDecision
        ↓
InterviewerPrompt
        ↓
InterviewerPromptDelivery
        ↓
CandidateResponse
```

For code-driven interaction:

```text
CodeSnapshot
        ↓
ExaminerDecision
        ↓
InterviewerPromptDelivery
```

must reference the exact code version.

If the spike cannot reconstruct this chain afterward, the CounterQ core architecture is incomplete even if the live question sounded good.

---

# 107. Acceptance criteria

CounterMap Phase 1 is acceptable only when:

### Causality

- Every visible node references canonical source data.
- Every edge has canonical causal support.
- Temporal proximity alone never creates an edge.
- Candidate can understand why each visible CounterQ technical question was asked.

### Prompt truth

- Stale Examiner decisions never appear.
- Rejected questions never appear.
- Undelivered questions never appear.
- Interrupted prompts expose only what was actually delivered.
- One delivered interviewer turn maps to only one primary prompt-derived node (`QUESTION`, `MUTATION`, or `ASSISTANCE`).
- Candidate-facing "Why this question?" never renders raw model rationale/chain-of-thought.

### Code provenance

- Code-driven questions reference exact CodeSnapshot versions.
- Candidate can inspect code at the time of the question.
- Self-correction uses actual before/after snapshots.
- `CORRECTED_BY` is not inferred from code change alone.

### Evidence

- Breakpoints always reference validated Evidence.
- Positive Evidence is represented as well as negative Evidence.
- Invalidated Evidence disappears from rebuilt projections.
- No graph conclusion exists only because a report said so.

### Assistance

- Coach guidance appears only where materially relevant.
- Assistance does not contaminate unrelated branches.
- Assisted success is distinguishable from independent success.

### Projection quality

- Typical maps remain understandable without zoom gymnastics.
- Normal session graph density stays deliberately low.
- Same projection renders with stable ordering.
- CounterMap can be fully rebuilt from canonical data.

### Accessibility

- Reasoning Timeline communicates equivalent causal meaning.
- Important information does not depend on color.
- Node details are keyboard accessible.

### Trust

- Candidate can inspect transcript/code sources.
- Candidate can dispute an assessment.
- No chain-of-thought is exposed.
- No unsupported causal relationship is displayed.

### Resilience

- CounterMap generation failure does not affect SessionReport or canonical interview data.
- Deleted interviews cannot retain orphan CounterMaps.
- Projection schema can be upgraded without rewriting canonical evidence.

### Product usefulness

A candidate inspecting the map should be able to answer:

> "What specifically did CounterQ learn about me here?"

and:

> "Why did the interviewer choose this branch?"

without requiring knowledge of CounterQ's internal architecture.

---

# 108. Phase 1 scope cuts

Explicitly defer:

- Neo4j or another graph database;
- giant cross-session reasoning graph;
- user-editable graph;
- drag-and-save layouts;
- animated interview playback;
- collaborative annotation;
- social/shareable CounterMaps;
- complex graph filtering;
- graph search;
- graph minimap unless later needed;
- dozens of custom node shapes;
- mobile-first graph interaction;
- AI-created unsupported relationships;
- personality analysis;
- emotion/body-language nodes;
- leaderboard comparisons;
- recruiter-facing graph views.

CounterMap remains focused on one candidate understanding one interview.

---

# 109. Final CounterMap principles

1. **CounterMap explains causality, not chronology alone.**

2. **Every visible node must have canonical provenance.**

3. **Every edge requires causal support.**

4. **Temporal proximity is not causality.**

5. **CounterMap is derived, never authoritative.**

6. **Delivered conversation is visible; discarded internal reasoning is not.**

7. **Strong reasoning matters as much as Breakpoints.**

8. **Self-correction should remain visible even when CounterQ correctly stayed silent.**

9. **Code questions must point to exact code context.**

10. **Coach assistance must be visible only where it mattered.**

11. **Breakpoints require validated Evidence.**

12. **Do not expose chain-of-thought.**

13. **Use fewer meaningful nodes rather than graph noise.**

14. **CounterMap and Mastery Map solve different problems.**

15. **Graph layout should be deterministic.**

16. **CounterMap does not require a numeric score.**

17. **The candidate should be able to inspect the source behind an assessment.**

18. **A small graph is acceptable when the interview was straightforward.**

19. **A question that was never spoken did not happen from the candidate's perspective.**

20. **If CounterQ cannot explain why it concluded something, it should not show the conclusion confidently.**

21. **Candidate-visible CounterMap explains candidate-visible causality, not hidden Examiner cognition.**

22. **One delivered interviewer turn becomes one primary prompt-derived node.**

23. **"Why this question?" comes from structured provenance, never raw private reasoning.**

24. **A code change is not automatically a correction.**

25. **A READY projection is internally consistent; progressive UX must not mean progressive truth.**

The governing product rule is:

> **A CounterMap should let the candidate replay the logic of the interview—not merely replay the conversation.**

And every branch should answer:

> **"Why did the interview go this way?"**
