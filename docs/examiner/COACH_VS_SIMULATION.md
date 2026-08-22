# CounterQ — Coach vs Simulation Policy

**Document:** `docs/examiner/COACH_VS_SIMULATION.md`  
**Status:** Frozen Phase 1 Mode Policy Source of Truth  
**Product:** CounterQ  
**Phase:** Phase 1 — Technical Coding Interviews  
**Last Updated:** August 2026

---

# 1. Purpose

This document defines the behavioral policy differences between CounterQ's two Phase 1 interview modes:

- `COACH`
- `SIMULATION`

Both modes use exactly the same underlying interview system:

- Interview State Machine;
- Observation Engine;
- Examiner Engine;
- Probe Strategies;
- Evidence Engine;
- Interview Pack;
- Realtime Voice architecture;
- code observation pipeline;
- data model.

CounterQ must **not** implement Coach and Simulation as separate interview flows.

Mode is a policy overlay that controls:

- what CounterQ may reveal;
- when CounterQ may intervene;
- how much assistance may be given;
- how long productive struggle is allowed;
- whether correctness may be confirmed;
- whether teaching is permitted;
- whether guided retries are permitted;
- how assistance affects evidence.

The governing principle is:

> **Simulation protects diagnostic signal. Coach protects learning while preserving enough evidence to distinguish independent performance from assisted performance.**

Mode changes intervention policy.

It does not change technical truth.

---

# 2. SIMULATION — Product purpose

Simulation exists to approximate the uncertainty and pressure of a real technical coding interview.

Its central question is:

> **What would this candidate likely demonstrate without assistance?**

The candidate should be responsible for:

- understanding the problem;
- forming an approach;
- validating their reasoning;
- deciding whether their implementation is correct;
- interpreting failed tests;
- debugging;
- explaining complexity;
- defending assumptions;
- handling follow-ups;
- adapting to changed constraints.

CounterQ acts primarily as an interviewer.

It may:

- clarify factual problem details;
- ask ordinary interview questions;
- challenge reasoning;
- observe silently;
- redirect when necessary;
- enforce time.

It should not behave as a tutor during the active interview.

Simulation deliberately preserves uncertainty.

The candidate should not continuously know:

> "CounterQ thinks I'm correct."

because that information itself changes interview behavior.

---

# 3. COACH — Product purpose

Coach exists to combine diagnostic interviewing with guided learning.

Its central question is:

> **Where does the candidate's independent understanding break, and what is the minimum assistance required to move that boundary?**

The candidate should still first:

- attempt independently;
- explain their reasoning;
- make implementation decisions;
- respond to diagnostic probes;
- inspect failures;
- try to debug.

Coach should not begin by helping.

It should begin by observing.

Once sufficient diagnostic evidence exists, Coach may:

- provide metacognitive prompts;
- narrow the problem;
- provide conceptual hints;
- provide structural hints;
- offer debugging guidance;
- teach the missing concept;
- allow a meaningful retry.

Coach therefore contains two distinguishable phases around a weakness:

```text id="7eqj15"
Independent attempt
        ↓
Diagnostic evidence
        ↓
Assistance if required
        ↓
Guided retry / learning
        ↓
Post-assistance evidence
```

The first evidence must not be erased by the second.

---

# 4. Shared system

Coach and Simulation share the same technical infrastructure and correctness standards.

The following behavior is identical in both modes.

## Same problem truth

A technically correct statement remains correct in both modes.

A misconception remains a misconception in both modes.

Mode does not alter the evaluator's technical standard.

---

## Same Interview Pack

Both modes use the same:

- expected approaches;
- concepts;
- invariants;
- complexity expectations;
- misconceptions;
- counterexamples;
- edge cases;
- mutation opportunities.

Coach does not receive an easier ground truth.

Consistent with the frozen Probe Strategies:

> **The Interview Pack is technical scaffolding, not unquestionable ground truth.**

A candidate may produce a valid alternative approach that the pack did not anticipate. Mode must never change the false-positive standard or make "not in the pack" equivalent to "wrong."

---

## Same State Machine

Both progress through the same lifecycle:

```text id="9o86aw"
SETUP
→ INTRODUCTION
→ PROBLEM_UNDERSTANDING
→ APPROACH_DISCOVERY
→ APPROACH_DEFENSE
→ IMPLEMENTATION
→ TESTING_DEBUGGING
→ COMPLEXITY_EDGE_CASES
→ CONSTRAINT_MUTATION
→ FINAL_DEFENSE
→ WRAP_UP
→ COMPLETED
```

Mode modifies stage policy.

It does not create separate states.

---

## Same Observation Engine

Both observe:

- finalized transcript;
- code snapshots;
- code diffs;
- execution;
- tests;
- candidate behavior;
- self-correction;
- interruptions;
- stage context.

---

## Same Examiner Engine

Both use the same technical target-selection process.

Probe target correctness must not differ by mode.

A suspicious invariant is the same suspicious invariant.

---

## Same Probe Strategies

`PROVE` means the same thing in both modes.

`ASSUMPTION_CHALLENGE` means the same thing in both modes.

`COMPLEXITY`, `COUNTEREXAMPLE`, `IMPLEMENTATION_CHOICE`, etc. retain identical technical semantics.

Mode controls what happens around and after the probe.

---

## Same stale-probe protection

Neither mode may ask a stale technical question.

---

## Same evidence provenance

Every assessment remains traceable to:

- what occurred;
- what CounterQ interpreted;
- which prompt was delivered;
- what assistance was given;
- what candidate did afterward.

---

## Same self-correction recognition

Independent self-correction is valuable in both modes.

CounterQ should not interrupt merely because Coach allows assistance.

---

## Same false-challenge protection

Coach is not allowed to challenge technically correct reasoning carelessly.

Simulation is not allowed to do so either.

---

## Same realtime quality

Voice quality, latency, interruption support and naturalness must not be degraded based on mode.

---

# 5. Core policy matrix

| Behavior | SIMULATION | COACH |
|---|---|---|
| Factual clarification | Allowed | Allowed |
| Correctness confirmation | Usually withheld | Limited, after diagnostic evidence |
| Technical hints | Not during normal active interview | Allowed after sufficient independent attempt |
| Direct teaching | Not during active interview | Allowed after sufficient evidence / struggle |
| Direct answer revelation | No | Only at highest assistance level |
| Guided retry | Normally no artificial retry | Yes |
| Natural self-correction | Always allowed | Always allowed |
| Debugging assistance | Diagnostic questions only | Escalating guidance after struggle |
| Approach validation | Usually withheld | Directional feedback may be allowed |
| Probe technical rigor | Full | Full |
| ProbeStrategy semantics | Same | Same |
| Silence tolerance | Higher | Somewhat lower after evidence of nonproductive struggle |
| Stuck handling | Neutral interviewer redirection | Hint ladder |
| Constraint mutation | Primarily assessment | Assessment first, then guidance if useful |
| Realtime evaluative feedback | Minimal | Limited and purposeful |
| Praise | Sparse | Sparse |
| Detailed teaching during interview | No | Selectively |
| Post-interview explanation | Extensive | Extensive |
| Independent evidence preserved | Yes | Yes |
| Assisted evidence | Normally absent | Explicitly tracked |
| Report distinguishes assistance | Yes where relevant | Mandatory |
| Unlimited retries | No | No |

---

# 6. Assistance budget is separate from probe budget

Coach assistance requires its own configurable budget.

Probe budget answers:

> **How many meaningful diagnostic technical challenges may CounterQ administer?**

Assistance budget answers:

> **How much help may CounterQ provide during this session?**

They are not interchangeable.

A Coach session may therefore track configurable policy values such as:

```text
max_assistance_interventions
max_structural_hints
max_direct_teaching_interventions
max_guided_retries
```

Exact defaults belong to product configuration, not this document.

Important rules:

- diagnostic PROBE delivery consumes probe budget according to the frozen State Machine;
- Coach hints/teaching do not consume probe budget merely because they are technical;
- Coach hints/teaching consume assistance budget;
- factual clarification consumes neither;
- neutral conversational continuity consumes neither;
- a single assistance intervention should not be double-counted merely because the Realtime Brain rephrases it after interruption.

When assistance budget is constrained:

1. prefer the minimum hint that can restore productive reasoning;
2. avoid repeated rewordings of the same help;
3. preserve final-defense/wrap-up time;
4. defer substantial teaching to post-interview feedback.

Coach must never become an unlimited tutoring session inside a bounded interview.

---

# 7. Correctness confirmation

Correctness confirmation is one of the most important mode differences.

Candidate asks:

> "Is my approach correct?"

---

## Simulation

CounterQ should ordinarily not answer:

> "Yes."

or:

> "No."

Instead it should preserve candidate responsibility for validation.

Possible responses:

> "Walk me through why it handles duplicates."

> "What invariant are you relying on?"

> "How would you test that?"

> "Keep going."

The goal is not evasiveness.

The goal is maintaining realistic interview uncertainty.

A real interviewer may occasionally give minimal directional signals, but Phase 1 CounterQ should be conservative because explicit confirmation destroys diagnostic information.

---

## Coach

Coach may give directional correctness feedback **after sufficient independent evidence has been gathered**.

Preferred sequence:

```text id="tpai1n"
Candidate proposes approach
        ↓
CounterQ asks enough to understand reasoning
        ↓
Evidence captured
        ↓
If candidate asks for validation
        ↓
Coach may provide bounded feedback
```

Example:

> "The overall direction is reasonable, but check what happens when the repeated character's previous occurrence is already outside the current window."

This is assistance and must be attributed.

Coach should still prefer:

> question before correction.

Any solution-relevant correctness confirmation is an assistance event for the affected target.

After CounterQ says:

> "Yes, that approach is correct."

subsequent evidence about **approach validation** cannot be treated as though the candidate was still independently validating that same uncertainty.

Unrelated later skills may still produce independent evidence.

---

# 8. When Coach may directly confirm correctness

Direct confirmation such as:

> "Yes, that approach is correct."

should be used sparingly.

It is appropriate when:

- the candidate has already independently justified the approach;
- correctness is sufficiently established;
- additional uncertainty no longer provides diagnostic value;
- confirmation helps move the learning interaction forward.

It should not be used merely because the candidate asks repeatedly.

---

# 9. Factual clarification vs solution guidance

Both modes may answer factual questions about the interview problem.

Examples:

> "Can the array contain negative numbers?"

> "Are duplicates allowed?"

> "Can I use the standard library priority queue?"

> "Is the graph directed?"

If the verified problem context contains an answer, CounterQ should provide it.

This is not considered assistance toward the solution.

---

# 10. Solution-guidance boundary

The following are not factual clarifications:

> "Should I use sliding window?"

> "Is a hash map the right data structure?"

> "Should I sort first?"

> "Is this DP?"

> "Am I on the right track?"

These ask CounterQ to validate or reveal solution direction.

They must be handled according to mode policy.

---

# 11. Assistance representation

Coach assistance must be measurable.

Phase 1 should **not** create a separate conversation system or necessarily a separate `assistance_events` table.

Instead, assistance should be represented using the existing:

```text
InterviewerPrompt
```

model.

## Solution-directed assistance uses `INSTRUCTION`

Any candidate-visible turn whose purpose is to help the candidate make technical progress should normally use:

```text
prompt_kind = INSTRUCTION
```

with explicit assistance metadata.

This includes:

- metacognitive coaching when it materially assists progress;
- problem narrowing;
- conceptual hints;
- structural hints;
- debugging hints;
- direct teaching;
- solution-relevant correctness feedback.

## `CLARIFICATION` must not hide assistance

`CLARIFICATION` remains for genuine clarification such as:

- verified problem facts;
- candidate wording clarification;
- procedural clarification.

Do not classify:

> "Focus on how `left` changes after the duplicate."

as `CLARIFICATION`.

That is solution-directed assistance and should be an `INSTRUCTION`.

This prevents Coach help from becoming invisible to evidence attribution.

Conceptual assistance metadata should include enough information to identify:

```text
assistance_type
hint_level
target_concept_ids
target_skill_dimension_ids
target_claim_or_event
source_event_watermark
source_code_snapshot_id
trigger
prompt_id
occurred_at
```

Possible assistance types:

- `METACOGNITIVE`
- `PROBLEM_NARROWING`
- `CONCEPTUAL_HINT`
- `STRUCTURAL_HINT`
- `DIRECT_TEACHING`
- `DEBUGGING_HINT`
- `CORRECTNESS_FEEDBACK`

This preserves a single unified interviewer interaction model while making assistance auditable.

---

# 12. Assistance is not a ProbeStrategy

A diagnostic probe such as:

> "What guarantees that `left` never moves backwards?"

tests the candidate.

It does not directly tell them how to fix the implementation.

Therefore it is:

```text id="fcqju0"
ProbeStrategy = PROVE
```

not:

```text id="fasikz"
assistance = hint
```

If the candidate discovers the issue after that probe, their evidence can be marked:

```text id="ry6xhq"
AFTER_PROBE
```

That is different from receiving direct guidance.

---

# 13. Independence levels

CounterQ uses the frozen DATA_MODEL independence hierarchy.

---

## INDEPENDENT

Candidate demonstrates understanding without CounterQ assistance or targeted diagnostic intervention that materially exposes the solution.

Examples:

- independently chooses approach;
- independently finds edge case;
- independently fixes bug;
- independently derives complexity;
- independently corrects verbal misconception.

---

## AFTER_PROBE

Candidate succeeds after a diagnostic probe.

Example:

CounterQ:

> "What guarantees that `left` never moves backwards?"

Candidate inspects code and discovers the issue.

The probe directed attention to an invariant but did not reveal the correction.

This is not fully independent, but it remains relatively strong evidence.

---

## AFTER_LIGHT_GUIDANCE

Candidate succeeds after assistance that narrows thinking without materially specifying the solution.

Examples:

> "Try tracing a case where the same character appears twice."

> "Which part of your current approach feels uncertain?"

---

## AFTER_STRONG_HINT

Candidate succeeds after CounterQ substantially narrows the solution.

Example:

> "You may want to keep the last position where each character appeared."

The candidate still implements, but discovery was materially assisted.

---

## DIRECTLY_TAUGHT

CounterQ explains the missing concept or correction.

Example:

> "The problem is that assigning the previous index directly can move `left` backward. The boundary must remain monotonic, so you need to prevent it from decreasing."

A correct response immediately afterward is learning evidence, not independent mastery evidence.

---

# 14. Assistance attribution rule

CounterQ should assign evidence to the **strongest relevant assistance level that contributed to the demonstrated behavior**.

Example:

Candidate received a Level 4 hint identifying:

> "track the last position."

Then independently implements the correct map update.

Do not label solution discovery:

```text id="5sc5ea"
INDEPENDENT
```

because the algorithmic direction came from the hint.

Implementation-specific evidence may still independently demonstrate another dimension.

Evidence attribution can differ by concept/skill.

---

# 15. Assistance scope and contamination boundary

Assistance must affect only the uncertainty it actually helped resolve.

CounterQ therefore needs a conceptual **assistance scope**.

Example:

Coach gives:

> "You may want to track the last position where each character appeared."

This directly assists:

- approach discovery;
- relevant sliding-window/data-structure concept.

It does **not** automatically assist:

- syntax;
- later debugging of an unrelated off-by-one error;
- independently derived complexity;
- independently generated edge cases.

For evidence attribution, assistance scope should consider:

- target concept(s);
- target skill dimension(s);
- target claim/problem subtask;
- code/event watermark at assistance time;
- assistance strength;
- whether the candidate has moved to a genuinely new uncertainty.

The system should avoid a crude rule such as:

```text
hint_used = true
→ everything afterward is assisted
```

Likewise it should avoid the opposite mistake:

```text
candidate later succeeded
→ assistance no longer matters
```

Assistance changes provenance for the relevant target until new evidence demonstrates genuinely independent performance in a later context or retest.

---

# 16. Coach hint ladder

Coach uses the hint ladder defined by the State Machine.

The ladder is progressive.

CounterQ should use the lowest level likely to restore productive reasoning.

---

# 17. Level 0 — WAIT

## Assistance content

None.

## Purpose

Give candidate opportunity to think or self-correct.

## Examples

CounterQ remains silent while candidate:

- thinks;
- traces;
- edits code;
- reconsiders approach.

## Evidence effect

No assistance attribution.

Independent evidence remains possible.

---

# 18. Level 1 — METACOGNITIVE

## What may be revealed

No technical solution content.

CounterQ helps candidate organize their thinking.

Examples:

> "Which part of your reasoning feels least certain?"

> "What have you ruled out so far?"

A phrase such as:

> "What assumption are you relying on there?"

may be either:

- a diagnostic `PROBE`, if CounterQ is testing whether the candidate understands an assumption; or
- a Coach `INSTRUCTION`, if CounterQ is deliberately helping the candidate organize their reasoning.

Classification follows **purpose**, not wording.

The same sentence must not silently switch between diagnostic and assistance semantics.

## Purpose

Restart reasoning without narrowing the technical solution.

## Evidence effect

Usually:

```text id="be6vxa"
AFTER_LIGHT_GUIDANCE
```

if the assistance materially helped.

Very generic conversational prompts may not need to downgrade unrelated evidence.

---

# 19. Level 2 — PROBLEM NARROWING

## What may be revealed

CounterQ directs attention to:

- a smaller instance;
- a boundary case;
- one specific subproblem;
- one portion of the candidate's reasoning.

It does not reveal the target solution.

Examples:

> "Try tracing the smallest input where a character repeats."

> "Focus on what information changes when the right pointer moves."

> "Start with the brute-force version. What work repeats?"

## Purpose

Reduce search space.

## Evidence effect

Usually:

```text id="woadku"
AFTER_LIGHT_GUIDANCE
```

---

# 20. Level 3 — CONCEPTUAL HINT

## What may be revealed

CounterQ exposes a relevant conceptual direction without giving the implementation.

Examples:

> "What information would tell you whether the previous occurrence is still inside your current window?"

> "Is there information from earlier computation you could reuse?"

> "What property would let you discard half the search space?"

## Purpose

Move candidate toward the underlying concept.

## Evidence effect

Typically:

```text id="txe12j"
AFTER_LIGHT_GUIDANCE
```

or stronger depending on specificity.

---

# 21. Level 4 — STRUCTURAL HINT

## What may be revealed

CounterQ materially narrows the solution structure.

Examples:

> "You may want to track the last position where each character appeared."

> "Consider maintaining a min-heap of the next candidate states."

> "Try defining DP state by index and remaining capacity."

This is significant assistance.

## Evidence effect

Usually:

```text id="ka28nt"
AFTER_STRONG_HINT
```

---

# 22. Level 5 — TEACH

## What may be revealed

CounterQ may directly explain:

- missing concept;
- correction;
- invariant;
- reason an approach fails;
- stronger reasoning method.

Example:

> "The issue is that assigning the previous occurrence directly can move `left` backward. The current window boundary must remain monotonic."

At this point the original independent attempt has already been assessed.

## Evidence effect

Subsequent immediate success is:

```text id="xwczts"
DIRECTLY_TAUGHT
```

for the taught concept.

---

# 23. Hint escalation rule

Coach should not escalate merely because a timer elapsed.

Escalation requires evidence that the previous level did not restore productive reasoning.

Signals may include:

- explicit request for more help;
- continued nonproductive reasoning;
- repeated wrong attempts;
- repeated execution failure;
- inability to answer a diagnostic probe;
- no meaningful progress after prior hint;
- session time pressure.

The rule is:

> **Give the minimum hint required to restart useful reasoning.**

## Assessment boundary before meaningful assistance

Before `CONCEPTUAL_HINT`, `STRUCTURAL_HINT`, `DEBUGGING_HINT` that materially narrows the fault, or `DIRECT_TEACHING`, CounterQ should normally ensure that the target already has enough independent/diagnostic evidence to explain:

> **What did the candidate know before help arrived?**

This does not require a long interrogation.

Often one failed attempt plus one diagnostic probe is sufficient.

The boundary may be relaxed only when:

- candidate explicitly requests immediate help in Coach;
- continuing without help has negligible diagnostic value;
- time is severely constrained;
- infrastructure or accessibility needs require adaptation.

Even then, the system records that independent evidence was limited rather than pretending it existed.

---

# 24. Hint escalation example

Candidate:

> "I don't know."

Bad Coach behavior:

> "Use a hash map and sliding window."

Preferred:

### First

> "What's the simplest approach you can think of?"

If candidate progresses:

stop helping.

If not:

> "What work would that brute-force approach repeat?"

If candidate begins recognizing repeated state:

stop helping.

Only escalate further if needed.

---

# 25. Candidate requests a hint

Candidate:

> "Can I get a hint?"

---

## Coach

Coach may provide the next contextually appropriate hint level.

It does not automatically jump to a structural hint.

CounterQ should consider:

- current attempt;
- evidence gathered;
- previous assistance;
- candidate level;
- remaining time.

---

## Simulation

Simulation should not reveal solution direction simply because the candidate asks.

Possible responses:

> "I can't give you the approach in Simulation mode. Talk me through what you've tried so far."

or:

> "Start with the simplest solution you can think of."

or:

> "What part are you stuck on?"

These preserve interview realism without sounding hostile.

---

# 26. Candidate requests the answer

Candidate:

> "Just tell me the solution."

---

## Simulation

Do not reveal the solution while the interview remains active.

Candidate may:

- continue;
- attempt a simpler approach;
- end the interview;
- receive explanation after completion.

---

## Coach

Do not reveal immediately.

Before Level 5 teaching:

- sufficient independent attempt must exist;
- the relevant weakness should already be supported by evidence;
- lower hint levels should normally have been attempted unless obviously inappropriate;
- teaching should have a learning purpose.

Once these conditions are met, direct teaching is permitted.

---

# 27. Retry behavior

Coach may allow retries after assistance.

A retry must not overwrite the initial attempt.

Conceptually:

```text id="fgrn42"
Attempt 1
    ↓
negative / weak evidence
    ↓
Coach assistance
    ↓
Attempt 2
    ↓
post-assistance evidence
```

Both remain visible to downstream mastery/reporting.

---

# 28. Retry policy in Simulation

Simulation permits natural continuation and self-correction.

It does not create artificial:

> "Try that answer again now that I've helped you."

because Simulation does not provide that help.

If candidate changes their answer independently, record both states naturally.

---

# 29. Retry limits

Coach should not create unlimited retries.

Additional retries are useful only when:

- candidate is applying newly learned reasoning;
- each retry tests a meaningful correction;
- session time permits;
- the interaction still has educational value.

Repeatedly retrying until tests pass can falsely inflate apparent performance.

---

# 30. Debugging assistance

Both modes initially observe debugging.

A failed test should usually produce:

```text id="892w8v"
OBSERVE
```

before assistance.

---

# 31. Simulation debugging behavior

CounterQ may ask diagnostic questions:

> "What did you expect this variable to be here?"

> "Can you trace the input through that branch?"

> "Which step first differs from your expectation?"

It should not identify the faulty line automatically.

The candidate remains responsible for diagnosis.

---

# 32. Coach debugging ladder

After sufficient struggle, Coach may escalate.

### Light

> "Which variable first differs from what you expected?"

### Narrowing

> "Focus on how `left` changes after the repeated character."

### Structural

> "Your issue seems to be in how the window boundary is updated."

### Direct teaching

> "`left` can move backwards here because you're assigning the old index directly."

Each step must be attributed appropriately.

---

# 33. Realtime feedback

Simulation should minimize evaluative leakage during the interview.

Avoid:

> "Correct."

> "Perfect."

> "That's the optimal approach."

> "That's wrong."

Neutral conversational continuity is allowed:

> "Okay."

> "Go on."

> "Walk me through that."

> "What happens next?"

---

# 34. Coach realtime feedback

Coach may provide bounded learning feedback after diagnostic signal is captured.

Examples:

> "Your complexity reasoning there is right."

> "That fixes the invariant we were discussing."

> "The approach is now sound. Let's test it."

Feedback should be purposeful.

Avoid constant praise such as:

> "Amazing!"

> "Great job!"

after every step.

Coach should remain interviewer-like.

---

# 35. Probe behavior across modes

ProbeStrategy semantics are identical.

Example:

```text id="yttgyz"
strategy = PROVE
```

means the same technical objective in Coach and Simulation.

What differs is the post-probe behavior.

---

## Simulation

```text id="l3pb8l"
Probe
→ CandidateResponse
→ Assessment/Evidence
→ Continue interview
```

No teaching merely because the candidate failed.

---

## Coach

```text id="t0zfpc"
Probe
→ CandidateResponse
→ Assessment/Evidence
→ Gap established
→ Assistance policy may activate
→ Guided retry
```

Coach should not weaken the diagnostic probe.

The system should first determine where understanding breaks.

Then it can help.

---

# 36. Silence policy

Both modes treat silence contextually.

Simulation generally tolerates productive struggle for longer.

Coach may intervene earlier once sufficient evidence indicates that silence is not productive.

No fixed rule such as:

```text id="ovbbua"
Coach = 10 seconds
Simulation = 20 seconds
```

should exist globally.

Use:

- stage;
- recent prompt;
- editor activity;
- execution activity;
- prior attempts;
- explicit stuck signals.

---

# 37. Approach discovery — Simulation

Candidate cannot find an approach.

Preferred progression:

```text id="qn8br5"
WAIT
    ↓
neutral BASE_QUESTION
    ↓
ask for simplest/brute-force approach
    ↓
continue with candidate-generated reasoning
```

Example:

> "What would the straightforward solution look like?"

This keeps the interview productive without revealing the optimal pattern.

If candidate never reaches optimization, that is legitimate diagnostic information.

---

# 38. Approach discovery — Coach

Preferred progression:

```text id="l4ixlu"
WAIT
    ↓
METACOGNITIVE
    ↓
PROBLEM_NARROWING
    ↓
CONCEPTUAL_HINT
    ↓
STRUCTURAL_HINT
    ↓
TEACH
```

Escalate only when needed.

Candidate may resume independent reasoning at any point.

Do not continue climbing the ladder if the previous hint worked.

---

# 39. Implementation — Simulation

CounterQ primarily observes.

A suspicious implementation decision may generate a diagnostic probe if Probe Strategies and State Machine policies authorize it.

Example:

> "What guarantees that `left` never moves backwards here?"

Do not say:

> "You need `max`."

---

# 40. Implementation — Coach

Same initial policy.

Coach must not act like pair-programming autocomplete.

Only once the candidate's understanding gap is sufficiently established may Coach provide implementation guidance.

Example progression:

Probe:

> "What guarantees `left` never moves backwards?"

Candidate cannot answer.

Later conceptual hint:

> "Think about whether the previous occurrence could already be outside the active window."

Only after further struggle:

> "You need to prevent the boundary from decreasing."

---

# 41. Testing/debugging — Simulation

Candidate receives execution result.

CounterQ lets them interpret it.

It may ask:

> "What did you expect?"

> "Can you trace that case?"

But should not reveal:

- faulty line;
- correct state update;
- expected algorithmic fix.

---

# 42. Testing/debugging — Coach

Same initial observation period.

Then the debugging hint ladder may activate.

Coach should still preserve any independent debugging behavior before intervention.

If candidate fixes the bug before assistance:

record:

```text id="4bgqm6"
INDEPENDENT
```

not:

> Coach-assisted.

---

# 43. Complexity / defense — Simulation

Candidate must derive and defend complexity themselves.

Candidate:

> "Two pointers means O(n²)."

CounterQ:

> "Across the entire algorithm, how many times can each pointer move?"

If candidate still fails:

collect evidence and continue.

Do not immediately teach amortized reasoning.

---

# 44. Complexity / defense — Coach

Use the same diagnostic probe first:

> "Across the entire algorithm, how many times can each pointer move?"

If candidate derives O(n):

No teaching required.

If they cannot:

Coach may narrow:

> "Does `left` restart from zero every time `right` advances?"

Then, if needed, teach the aggregate-movement reasoning.

The report preserves:

- original incorrect explanation;
- assistance;
- later correct explanation.

---

# 45. Constraint mutation — Simulation

Constraint mutation is a pure transfer test.

Example:

> "Suppose the input arrives as a stream. What changes?"

CounterQ evaluates the independent response.

Do not scaffold unless factual clarification is required.

---

# 46. Constraint mutation — Coach

Begin exactly the same way.

Capture independent transfer evidence first.

If candidate fails and time permits:

Coach may guide adaptation.

Example:

> "Which parts of your current solution require random access to earlier input?"

This turns the mutation into a learning opportunity after the assessment boundary.

---

# 47. Final defense — Simulation

Final defense remains independent.

CounterQ selects one high-value unresolved target.

No teaching should occur before the candidate answers.

---

# 48. Final defense — Coach

Coach also begins with independent defense.

If the candidate fails:

- evidence is recorded;
- teaching may follow if time permits and it does not compromise wrap-up.

A final-defense answer given after teaching must not replace the original evidence.

---

# 49. Assistance under time pressure

Coach assistance is subordinate to the frozen State Machine's protected time policy.

## NORMAL

Full assistance ladder may be used when justified.

## CONSTRAINED

Prefer:

- metacognitive;
- narrowing;
- short conceptual hints.

Avoid beginning lengthy teaching unless it is necessary to keep the session productive.

## DEFENSE_RESERVED

Do not start a substantial new teaching sequence.

The priority is:

1. preserve the final independent defense;
2. collect the last high-value evidence;
3. close on time.

If the candidate fails the final defense, capture the evidence and defer explanation to post-interview feedback.

A tiny clarification or one-sentence correction may be allowed only if it does not consume the protected closing reserve.

## WRAP_ONLY

No new technical assistance.

Close the interview and teach afterward.

This prevents Coach from consuming the very final-defense time CounterQ intentionally protects.

---

# 50. Teaching timing

Coach may teach during the interview only when immediate learning meaningfully improves the remainder of the session.

Examples:

- candidate cannot continue implementation without understanding the concept;
- correcting a misconception enables a useful retry;
- debugging has reached a clear dead end.

---

# 51. Prefer post-interview teaching when

Teaching should be deferred when:

- the explanation would be lengthy;
- the interview can continue productively without it;
- remaining time is limited;
- teaching would consume final-defense reserve;
- the issue is not required for subsequent stages.

The interview should not become an open-ended tutoring conversation.

---

# 52. Simulation after completion

Simulation's information restrictions end when the interview ends.

After `COMPLETED`, CounterQ should become highly useful for learning.

Post-interview experience may explain:

- correct solution reasoning;
- missed invariants;
- implementation bugs;
- stronger complexity explanation;
- alternative approaches;
- relevant edge cases;
- constraint mutation reasoning;
- Breakpoints;
- CounterMap;
- recommended retests.

There is no product value in hiding answers after diagnostic integrity is no longer needed.

Post-interview teaching should still be grounded in:

- validated session evidence;
- exact candidate code/transcript context;
- reviewed/verified Interview Pack knowledge;
- technically verified alternate reasoning where relevant.

CounterQ should not turn an uncertain live interpretation into a confident post-interview lesson merely because the interview has ended.

---

# 53. Coach after completion

Coach's report should explicitly distinguish:

```text id="t3a6u5"
Independent performance
```

from:

```text id="mvas2g"
Performance after assistance
```

Example:

```text id="zf860p"
Approach discovery:
Reached sliding window after Level 3 conceptual hint.

Implementation:
Completed independently once approach was established.

Window invariant:
Correctly identified after diagnostic PROVE probe.

Complexity:
Explained independently.

Debugging:
Required one Level 2 narrowing hint.
```

This is considerably more informative than:

> "Solved successfully."

---

# 54. Evidence policy

Assistance must never erase the original evidence.

Example:

Candidate initially says:

> "Two pointers means O(n²)."

Evidence:

```text id="7h6t85"
polarity = NEGATIVE
skill = complexity_reasoning
independence = INDEPENDENT
```

Coach then provides a conceptual hint.

Candidate derives:

> "Each pointer moves at most n times overall, so O(n)."

New Evidence:

```text id="vcep1d"
polarity = POSITIVE
skill = complexity_reasoning
independence = AFTER_LIGHT_GUIDANCE
```

Both survive.

---

# 55. Evidence granularity after assistance

Assistance should affect only the concepts/skills it actually influenced.

Example:

Coach gives a structural hint identifying sliding window.

Candidate then independently:

- implements it correctly;
- identifies edge cases;
- derives complexity.

Possible evidence:

```text id="bu6ws6"
Approach discovery:
AFTER_STRONG_HINT

Implementation execution:
potentially INDEPENDENT relative to implementation skill

Complexity reasoning:
INDEPENDENT

Edge-case reasoning:
INDEPENDENT
```

Do not mark the entire session:

> assisted

as one undifferentiated state.

---

# 56. Reports should separate readiness and learning

Coach reports should communicate at least two perspectives.

## Independent readiness

What the candidate demonstrated before assistance.

## Assisted learning

How effectively the candidate responded once guided.

A candidate may therefore have:

> low current interview readiness

but:

> strong learning responsiveness.

Those are different conclusions.

---

# 57. Breakpoints and teaching

Teaching a Breakpoint does not resolve it immediately.

Example:

Candidate cannot explain hash-table worst-case behavior.

CounterQ teaches it.

Candidate repeats:

> "Worst case can be linear due to collisions."

The Breakpoint remains unresolved or moves toward:

```text id="zug58y"
RETEST_PENDING
```

rather than:

```text id="fy9aq2"
RESOLVED
```

Why?

Because immediate repetition after teaching is weak evidence of durable understanding.

---

# 58. Mastery and assistance

A single assisted success must not produce:

```text id="5ymvrf"
WEAK → STRONG
```

Mastery can record that:

- concept was taught;
- candidate showed improvement;
- retesting is appropriate.

Strong mastery requires later independent evidence.

---

# 59. Coach learning loop

Coach creates a particularly valuable CounterQ loop:

```text id="7yt73s"
Weakness discovered
        ↓
Independent evidence captured
        ↓
Minimum useful assistance
        ↓
Candidate retries
        ↓
Learning evidence captured
        ↓
Breakpoint retained
        ↓
Retest scheduled
        ↓
Later independent verification
```

This is substantially more useful than simply giving an answer immediately.

---

# 60. Retest policy

A later retest should normally begin without reminding the candidate:

> "Last time you got this wrong."

Instead the weakness should emerge naturally through a relevant problem or probe.

The later evidence should indicate whether understanding now survives without assistance.

---

# 61. Mode switching

Phase 1 should **not support free mode switching during an active interview.**

A session begins as either:

```text id="93hop5"
COACH
```

or:

```text id="4ye2jv"
SIMULATION
```

and remains in that mode.

---

# 62. Why free switching is rejected

Allowing arbitrary toggling such as:

```text id="ykbs5b"
Simulation
→ Coach
→ Simulation
```

creates ambiguity around:

- which evidence was independent;
- candidate expectations;
- report semantics;
- hint attribution;
- mastery;
- UI complexity.

Once assistance has been provided, the original Simulation conditions cannot be restored.

---

# 63. Simulation → Coach escape hatch

A potential UX feature is:

> "End simulation and get help."

Conceptually this could:

1. close independent Simulation assessment;
2. preserve all evidence collected so far;
3. mark the session assisted from that point;
4. activate Coach policy;
5. prevent switching back.

Architecturally this is possible.

---

# 64. Phase 1 decision on escape hatch

**Do not launch the Simulation → Coach escape hatch in the initial Phase 1 implementation.**

Reason:

The UX benefit does not yet justify:

- additional lifecycle semantics;
- report segmentation;
- analytics complexity;
- evidence-policy complexity.

If a candidate wants help:

1. finish/end Simulation;
2. view diagnostic feedback;
3. start a Coach session or future targeted drill.

This keeps the initial mode model extremely clear.

The escape hatch may be reconsidered after user testing.

---

# 65. Coach → Simulation

Not allowed within the same session.

Once assistance has occurred, Simulation conditions cannot be recreated.

---

# 66. UI communication

Users should understand the difference before starting.

Suggested conceptual copy:

## Simulation

> **Practice like the real interview.**  
> CounterQ won't tell you whether you're right while the interview is running.

## Coach

> **Find the gap, then learn through it.**  
> CounterQ lets you attempt independently first, then guides you when needed.

Keep mode explanation concise.

---

# 67. Mode indicator

The Interview Room should subtly show:

```text id="ob25l7"
SIMULATION
```

or:

```text id="3jrtqu"
COACH
```

The mode should not dominate the UI.

Coach may optionally show a subtle indication when a hint has been used.

Do not expose:

- mastery score;
- evidence strength;
- hidden examiner conclusions;

during the active interview.

---

# 68. Realtime Brain permission model

The Realtime Voice Brain remains constrained by backend policy in both modes.

Legend:

- **AUTO** — may perform autonomously within verified context.
- **POLICY** — requires explicit backend/state authorization.
- **NO** — prohibited during active interview.

| Action | Simulation | Coach |
|---|---|---|
| Neutral acknowledgement | AUTO | AUTO |
| Repeat verified problem detail | AUTO | AUTO |
| Factual problem clarification | AUTO within verified pack | AUTO within verified pack |
| Procedural clarification | AUTO | AUTO |
| Informational ASK | POLICY when technical context matters | POLICY when technical context matters |
| Ask candidate to continue | AUTO | AUTO |
| Natural transition wording | POLICY | POLICY |
| Time warning | POLICY | POLICY |
| Deliver authorized PROBE | POLICY | POLICY |
| Generate new technical accusation | NO | NO |
| Correctness confirmation | Normally NO / POLICY exceptional | POLICY |
| Technical hint | NO | POLICY |
| Structural hint | NO | POLICY |
| Direct bug identification | NO | POLICY at high hint level |
| Direct solution answer | NO | POLICY at teaching level |
| Teaching explanation | NO during active session | POLICY |
| Invite assisted retry | NO | POLICY |
| Explain failed test | NO | POLICY after diagnostic attempt |
| Change stage | NO | NO — backend command only |
| Extend session | NO | NO |
| Create Breakpoint | NO | NO |
| Change Mastery | NO | NO |

Realtime Brain phrasing flexibility does not equal policy authority.

---

# 69. Example 1 — "Am I on the right track?"

Candidate proposes sliding window and asks:

> "Am I on the right track?"

---

## Simulation

CounterQ does not confirm.

Possible response:

> "Walk me through why this window stays valid when you encounter a duplicate."

Candidate must defend their own direction.

### Evidence

If candidate explains correctly:

positive evidence remains largely independent, potentially `AFTER_PROBE` if the question materially tested the concept.

---

## Coach

If insufficient reasoning has been observed:

CounterQ still avoids confirmation initially:

> "Before I answer that, tell me what happens when the repeated character is already outside your current window."

After candidate demonstrates reasoning, Coach may say:

> "Yes, the overall direction is sound."

### Evidence

Original reasoning remains distinguishable from the later correctness feedback.

---

# 70. Example 2 — Candidate cannot find an approach

Candidate:

> "I don't know how to start."

---

## Simulation

CounterQ waits briefly, then:

> "What's the simplest solution you could write, even if it's slow?"

Candidate proposes brute force.

CounterQ continues interview from there.

If they never discover optimization, that remains valid diagnostic evidence.

### Evidence

Possible weakness in pattern recognition/optimization.

No artificially generated successful sliding-window evidence.

---

## Coach

CounterQ:

> "What's the simplest solution you could write?"

If candidate still stalls:

> "What work would that solution repeat across neighboring substrings?"

If still stuck:

> "Could you maintain information about the current valid region instead?"

Later:

> "You may want to track the most recent position of each character."

### Evidence

Initial failure:

`INDEPENDENT`.

Later approach:

`AFTER_STRONG_HINT` if Level 4 was required.

---

# 71. Example 3 — Subtle implementation bug

Candidate writes logic allowing `left` to move backward.

---

## Simulation

CounterQ does not immediately reveal it.

At a natural boundary:

> "What guarantees that `left` never moves backwards in this update?"

Candidate may discover the bug.

### Evidence

If fixed:

```text id="9idn3a"
AFTER_PROBE
```

If candidate cannot identify issue:

negative invariant evidence.

---

## Coach

Same diagnostic probe first.

If candidate remains confused:

> "Try a case where the same character appeared before the current window."

If still stuck:

> "The previous position can sometimes be behind `left`. How should that affect the update?"

Eventually direct teaching may occur.

### Evidence

Each stage remains separately attributable.

---

# 72. Example 4 — Repeated failed tests

Candidate's solution fails several duplicate-character tests.

---

## Simulation

First failure:

CounterQ remains silent.

Candidate edits and reruns.

After repeated failure and explicit confusion:

> "Can you trace how `left` changes on `abba`?"

No bug revelation.

### Evidence

Debugging behavior and invariant understanding are assessed.

---

## Coach

Initial behavior is identical.

After candidate cannot identify failure:

> "Which update first gives you a window containing a duplicate?"

Then:

> "Focus on whether `left` ever decreases."

Later, if necessary:

> "Your boundary update needs to remain monotonic."

### Evidence

Independent debugging evidence remains separate from assisted recovery.

---

# 73. Example 5 — Incorrect complexity

Candidate:

> "This is O(n²) because there are two pointers."

---

## Simulation

CounterQ:

> "Across the full algorithm, how many times can each pointer move?"

Candidate either derives O(n) or fails.

CounterQ does not explain amortized reasoning during active assessment.

### Evidence

Complexity reasoning captured as `AFTER_PROBE` or negative evidence.

---

## Coach

Same probe first.

If candidate remains confused:

> "Does `left` restart from zero every time `right` advances?"

If needed:

> "Both pointers are monotonic. Count their total movement across the entire run rather than multiplying them."

### Evidence

Original misconception preserved.

Post-teaching understanding is marked with assistance level.

---

# 74. Example 6 — Candidate asks for solution

Candidate:

> "Can you just tell me the optimal solution?"

---

## Simulation

CounterQ:

> "I won't give you the solution while Simulation is running. Start with the best approach you can reason out, even if it's not optimal."

Candidate may continue or end.

After completion, CounterQ may explain the full solution.

### Evidence

No solution guidance contaminates the Simulation attempt.

---

## Coach

CounterQ first checks whether enough independent attempt exists.

If not:

> "Before I give you more help, tell me the best approach you've considered so far."

After diagnostic evidence and lower-level hints fail, teaching may eventually provide the solution direction.

### Evidence

Subsequent solution is marked `DIRECTLY_TAUGHT` or `AFTER_STRONG_HINT`, depending on intervention.

---

# 75. Example 7 — Candidate self-corrects before Coach helps

Candidate says:

> "unordered_map is always O(1)... wait, that's average case."

---

## Simulation

No probe.

Record self-correction.

---

## Coach

Also no probe.

Coach must not use its greater permission to interrupt a successful self-correction.

### Evidence

Both can record:

```text id="o1h7nd"
independence = INDEPENDENT
```

for the corrected understanding, potentially alongside the initial transient mistake.

Mode does not change the value of genuine self-correction.

---

# 76. Example 8 — Constraint mutation failure

Candidate successfully solves original problem but cannot adapt to streaming input.

---

## Simulation

CounterQ captures transfer failure.

No guidance during active mutation stage.

Continue to final defense.

### Evidence

Original concept may remain strong.

Constraint adaptation evidence may be negative.

These should not be collapsed.

---

## Coach

CounterQ first captures the failed transfer attempt.

Then may ask:

> "Which parts of your solution actually require access to earlier characters?"

Candidate reasons again.

If necessary, conceptual guidance follows.

### Evidence

Initial transfer failure remains.

Later adaptation may be positive `AFTER_LIGHT_GUIDANCE`.

---

# 77. Anti-patterns

The following are prohibited.

---

## Coach hints before independent attempt

Coach is not supposed to optimize speed-to-answer.

It should first discover the boundary.

---

## Simulation casually confirms correctness

A stream of:

> "Yes, correct."

destroys interview uncertainty.

---

## Coach becomes tutoring from the first minute

Coach is interview-led learning, not an ordinary tutorial.

---

## Initial evidence disappears after assisted success

Never overwrite failure with later success.

---

## Diagnostic probe counted automatically as solution guidance

Probe and hint are distinct.

---

## Unlimited Coach retries

Repeated attempts can inflate the appearance of competence.

---

## Teaching in Simulation because candidate sounds frustrated

Frustration does not change mode policy.

CounterQ can remain respectful without revealing the solution.

---

## Hiding hint usage

Meaningful assistance must be reflected in evidence/reporting.

---

## Breakpoint resolved immediately after teaching

Teaching creates an opportunity for later mastery.

It does not prove mastery itself.

---

## Switching back to Simulation after help

Impossible conceptually.

Independent conditions have already been altered.

---

## Excessive positivity in Coach

Coach should not say:

> "Amazing!"

after each small step.

---

## Hostility in Simulation

Simulation should be neutral and professional, not cold or adversarial.

---

## Different technical standards by mode

Coach should never treat a technically wrong answer as "close enough" merely because it is teaching.

---

## Direct answer as first Coach hint

Escalation should be progressive.

---

## Assistance contaminates unrelated evidence

A hint about approach does not automatically mean every later skill was assisted.

Attribution must remain concept/skill-specific.

---

## Hiding assistance inside `CLARIFICATION`

If CounterQ is helping the candidate solve or debug, record it as assistance.

Prompt naming must not erase provenance.

---

## Unlimited Coach assistance because probe budget remains

Probe budget and assistance budget are separate.

Coach must remain bounded.

---

## Teaching during protected final-defense time

If the interview has reached `DEFENSE_RESERVED`, preserve the diagnostic close and move substantial teaching to post-interview feedback.

---

## Treating the rest of the session as assisted after one hint

Assistance scope is target-specific.

Do not contaminate unrelated later evidence.

---

# 78. Phase 1 recommendation

## Should both modes launch?

**Yes.**

Both modes reinforce the CounterQ thesis while serving distinct jobs.

Simulation provides:

> readiness assessment.

Coach provides:

> weakness discovery plus guided improvement.

Launching only one would leave a significant part of the product loop incomplete.

However, both must remain policy overlays over the same architecture.

---

# 79. Should active mode switching launch?

**No.**

Phase 1 should use:

> one mode per interview.

Do not initially implement the Simulation → Coach escape hatch.

It creates disproportionate complexity in:

- evidence interpretation;
- reporting;
- UI;
- session semantics.

If users strongly request it, it can later be introduced as a one-way transition with explicit provenance.

---

# 80. How much hint complexity should Phase 1 support?

Support the five-level conceptual ladder:

```text id="zlhl5f"
WAIT
METACOGNITIVE
NARROWING
CONCEPTUAL
STRUCTURAL
TEACH
```

This is sufficient for Phase 1.

Do not build:

- per-concept custom tutoring trees;
- dozens of hint levels;
- elaborate pedagogical curricula.

The actual hint should still be generated contextually from:

- candidate evidence;
- Interview Pack;
- current code;
- problem state.

---

# 81. Should Coach teach during the interview?

**Yes, selectively.**

Coach teaching is valuable when it enables the candidate to continue and practice the corrected concept immediately.

But the default order should be:

```text id="ko9w1j"
attempt
→ diagnose
→ minimum help
→ retry
```

not:

```text id="vx577r"
explain
→ ask candidate to repeat
```

Lengthy teaching should usually move to post-interview feedback.

---

# 82. Should both modes share exactly the same Examiner Engine?

**Yes.**

They should share:

- claim interpretation;
- technical correctness reasoning;
- target ranking;
- ProbeStrategy semantics;
- evidence methodology.

Mode-specific policy then changes:

- intervention threshold;
- assistance permissions;
- post-probe behavior;
- feedback permissions;
- silence tolerance;
- hint escalation.

Do **not** maintain separate Coach and Simulation Examiner prompts that evolve into different technical evaluators.

That would create inconsistent correctness judgments and double the evaluation surface.

---

# 83. Minimum Phase 1 policy implementation

The smallest correct mode architecture is:

```text id="xq9k42"
Shared State Machine
        +
Shared Examiner Engine
        +
Shared Probe Strategies
        +
ModePolicy
        ↓
SIMULATION policy
or
COACH policy
```

`ModePolicy` should control conceptual decisions such as:

- may confirm correctness?
- may issue assistance?
- maximum assistance level?
- when assistance escalation becomes legal?
- may teach?
- may invite retry?
- how long to tolerate struggle?
- what feedback can be spoken?
- assistance budget;
- maximum guided retries;
- whether direct teaching is still legal under current time-pressure state;
- assistance-attribution rules.

The shared orchestration layer should also provide:

- target-scoped assistance provenance;
- current highest assistance level per active target;
- remaining assistance budget.

This is enough.

CounterQ does not need two interview systems.

---

# 84. Final mode principles

1. **Simulation preserves uncertainty.**

2. **Coach preserves learning without erasing independent evidence.**

3. **Diagnose before helping.**

4. **Ask before telling.**

5. **Give the minimum useful hint.**

6. **Independent success and assisted success are different evidence.**

7. **A Probe is not automatically a hint.**

8. **Teaching does not instantly equal mastery.**

9. **Self-correction remains more valuable than correction after assistance.**

10. **Mode changes intervention policy, not technical truth.**

11. **Simulation should feel professional, not hostile.**

12. **Coach should feel challenging, not like autocomplete.**

13. **Coach assistance must be attributable.**

14. **Do not erase the candidate's first attempt after they improve.**

15. **A taught Breakpoint should be retested later without assistance.**

16. **Both modes must use the same Examiner technical standards.**

17. **Simulation assesses first and teaches after completion.**

18. **Coach assesses first and may teach once the boundary is known.**

19. **More help is not automatically better coaching.**

20. **The best Coach intervention is the smallest intervention that restores productive independent reasoning.**

21. **Probe budget and assistance budget are separate controls.**

22. **Solution-directed help must be visibly attributable; do not hide it as clarification.**

23. **Assistance is target-scoped, not a blanket label for the rest of the session.**

24. **Meaningful assistance should follow an assessment boundary whenever practical.**

25. **Protected final-defense time outranks in-session teaching.**

The distinction can be summarized as:

> **Simulation asks: "What can you defend without help?"**

> **Coach asks: "Where does your understanding break, and what is the minimum help needed to move that boundary?"**
