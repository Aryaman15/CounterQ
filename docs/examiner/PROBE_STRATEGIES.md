# CounterQ — Examiner Probe Strategies

**Document:** `docs/examiner/PROBE_STRATEGIES.md`  
**Status:** Frozen Phase 1 Examiner Policy Source of Truth  
**Product:** CounterQ  
**Phase:** Phase 1 — Technical Coding Interviews  
**Last Updated:** August 2026

---

# 1. Purpose

This document defines the technical interrogation policy used by the CounterQ Examiner Engine.

The frozen State Machine defines:

> **When a probe is behaviorally allowed.**

This document defines:

> **What CounterQ should test, which probing strategy it should use, how deeply it should investigate, and when it should deliberately choose not to probe.**

CounterQ must not behave like a chatbot that automatically generates another question after every candidate response.

Every candidate-visible technical probe must have an explicit evidence purpose.

---

# 2. Core objective

CounterQ exists to find the boundary between:

> **“The candidate can state the answer.”**

and:

> **“The candidate actually understands and can defend it.”**

A probe exists to reduce uncertainty about candidate understanding.

It does **not** exist merely because another technically valid question can be generated.

The optimization objective is:

> **Minimum number of high-information questions required to determine the candidate's level of understanding.**

CounterQ should prefer:

```text
3 excellent probes
```

over:

```text
12 mediocre follow-ups
```

Probe count is not a success metric.

Evidence quality is.

---

# 3. Probe definition

A `PROBE` is an interviewer prompt whose purpose is to deliberately test:

- the validity of a candidate claim;
- the reasoning behind a decision;
- the correctness of an invariant;
- the depth of conceptual understanding;
- the implications of an implementation choice;
- the candidate's ability to adapt or transfer understanding.

A good probe has:

- a specific target;
- relevant concept(s);
- a diagnostic uncertainty;
- a ProbeStrategy;
- an evidence goal;
- a reason it matters now;
- an expiry condition;
- a stopping condition.

Example:

```text
Target:
Candidate claim that unordered_map lookup is always O(1)

Concept:
Hash-table complexity

Uncertainty:
Does the candidate distinguish average-case behavior from a worst-case guarantee?

Strategy:
ASSUMPTION_CHALLENGE

Evidence goal:
Determine whether the complexity claim reflects genuine understanding

Stopping condition:
Candidate correctly distinguishes average and worst case and can explain why
```

---

# 4. Examiner action vocabulary

The Examiner Engine recommends one of four actions:

- `WAIT`
- `OBSERVE`
- `ASK`
- `PROBE`

These actions are semantically different.

## WAIT

CounterQ deliberately allows the candidate to continue.

Typical reasons:

- candidate is developing an idea;
- candidate is likely to self-correct;
- candidate is in productive coding flow;
- sufficient evidence already exists;
- intervention would reduce diagnostic value.

`WAIT` does not mean the Examiner failed to notice something.

It may represent the highest-quality interviewing decision.

---

## OBSERVE

CounterQ has identified something potentially meaningful but does not yet have enough confidence or evidence to intervene.

Examples:

- suspicious code may simply be incomplete;
- candidate statement is ambiguous;
- an execution result may naturally expose the issue;
- transcription is uncertain;
- candidate appears to be reconsidering their own claim.

---

## ASK

CounterQ needs information without deliberately challenging the candidate.

Examples:

> "What's the complexity you're targeting?"

> "What does `left` represent here?"

> "Which case are you testing next?"

ASK is useful when the evidence gap is informational rather than diagnostic.

---

## PROBE

CounterQ deliberately tests a candidate's reasoning.

Every PROBE requires:

- a target;
- a ProbeStrategy;
- an evidence goal.

---

# 5. Examiner action → interviewer prompt mapping

`ExaminerDecision.action` and `InterviewerPrompt.kind` are related but are not the same vocabulary.

CounterQ must map them deliberately.

## WAIT

Produces no candidate-visible prompt.

The candidate keeps the conversational floor.

## OBSERVE

Produces no candidate-visible prompt.

CounterQ records or continues analysis without speaking.

## ASK

Produces an informational/non-adversarial interviewer prompt, normally one of:

- `BASE_QUESTION`
- `CLARIFICATION`
- occasionally `INSTRUCTION` when the State Machine permits a neutral process instruction.

ASK does **not** use a `ProbeStrategy`.

ASK should be used when CounterQ needs missing information rather than when it is deliberately testing a suspected weakness.

## PROBE

Produces:

```text
InterviewerPrompt.kind = PROBE
```

and requires exactly one **primary ProbeStrategy**.

## State-machine prompts

Some candidate-visible turns do not originate from Examiner actions at all.

Examples:

- `TIME_WARNING`
- `TRANSITION`
- procedural `INSTRUCTION`

These may be created deterministically by the State Machine.

This separation prevents the Examiner Engine from becoming the owner of ordinary interview choreography.

## ASK must not become a probe-budget loophole

CounterQ must not label a technically adversarial question as `ASK` merely to avoid:

- probe-budget consumption;
- probe cooldown;
- probe-chain limits;
- Simulation-mode restrictions.

The semantic test is:

> **Is this turn merely requesting missing information, or is it deliberately testing whether the candidate's reasoning survives scrutiny?**

If the second is true, it is a `PROBE`.

---

# 6. Examiner reasoning pipeline

The Phase 1 Examiner should conceptually follow this process:

```text
Observed Event(s)
        ↓
Interpret candidate behavior
        ↓
Extract / update candidate claims or behavioral signals
        ↓
Normalize relevant concepts and skill dimensions
        ↓
Identify possible diagnostic targets
        ↓
For each target:
    determine target type
    estimate technical correctness
    estimate interpretation confidence
    estimate technical importance
    estimate diagnostic value
    estimate candidate commitment/confidence
    estimate self-correction likelihood
    inspect current evidence
    inspect current code/event freshness
        ↓
Rank candidate targets
        ↓
Apply interview constraints:
    stage
    mode
    time
    probe budget
    cooldown
    candidate speaking state
    code version
    reasoning deadline
        ↓
Choose:
WAIT / OBSERVE / ASK / PROBE
        ↓
If PROBE:
    select strategy
    define probe intent
    define desired evidence
    define expiry
    define stopping condition
        ↓
State Machine Policy Gate
        ↓
InterviewerPrompt(kind=PROBE)
        ↓
Realtime Voice Brain phrases it naturally
```

The critical separation is:

```text
notice something
≠
ask about it
```

---

# 7. Candidate target types

A diagnostic target may originate from voice, code, execution behavior, or combined context.

Potential targets include:

- verbal technical claim;
- algorithm choice;
- data-structure choice;
- complexity claim;
- correctness claim;
- causal explanation;
- assumption;
- invariant;
- implementation decision;
- edge-case reasoning;
- explanation/code mismatch;
- debugging hypothesis;
- test-selection behavior;
- trade-off claim;
- constraint assumption;
- contradiction with an earlier answer;
- uncertainty statement;
- prior known weakness naturally relevant to the current problem.

A target should reference the exact underlying:

- event;
- claim;
- transcript;
- code snapshot;
- execution result;

where applicable.

---

# 8. Claim / behavior classification

Before probing, CounterQ should classify the target's semantic role.

Useful categories include:

- `FACTUAL`
- `CAUSAL`
- `COMPLEXITY`
- `CORRECTNESS`
- `ASSUMPTION`
- `IMPLEMENTATION_CHOICE`
- `TRADE_OFF`
- `INVARIANT`
- `EDGE_CASE`
- `DEBUGGING_HYPOTHESIS`
- `UNCERTAINTY`
- `CONTRADICTION`

This classification helps select candidate strategies.

It must not mechanically determine the final strategy.

---

# 9. Probe-value factors

CounterQ should eventually be able to estimate a conceptual ProbeValue.

A useful mental model is:

```text
ProbeValue increases with:

technical importance
diagnostic uncertainty
evidence value
context relevance
freshness
candidate commitment
transfer value
centrality to the problem
```

and decreases with:

```text
interruption cost
duplicate evidence
time pressure
probe fatigue
staleness risk
answer-leak risk
low model confidence
low candidate level relevance
```

No mathematical formula is frozen in Phase 1.

The architecture must preserve the factors required to build one later.

---

# 10. Technical importance

Technical importance asks:

> If this issue remains unresolved, how much does it matter?

High importance examples:

- incorrect algorithmic invariant;
- fundamental complexity misconception;
- incorrect graph assumption;
- code behavior that breaks correctness.

Low importance examples:

- harmless variable naming;
- minor implementation style;
- obscure language detail irrelevant to the interview objective.

A high-confidence observation may still have low probe value if its technical importance is low.

---

# 11. Diagnostic value

Diagnostic value asks:

> If the candidate answers this question, how much new information will we learn?

High diagnostic value:

> "What guarantees that `left` never moves backwards?"

when monotonicity is central and not yet demonstrated.

Low diagnostic value:

> "What's the complexity?"

after the candidate already derived the complexity accurately and defended it.

---

# 12. Freshness

A target loses value as the candidate moves on.

Freshness depends on:

- source event watermark;
- current code version;
- current stage;
- intervening candidate statements;
- self-correction;
- later evidence.

A technically interesting question about code from three versions ago may have effectively zero current ProbeValue.

---

# 13. Candidate commitment

CounterQ should distinguish:

> "Maybe this is O(1)?"

from:

> "This is always O(1)."

Commitment affects diagnostic priority because a confidently asserted misconception is stronger evidence than exploratory uncertainty.

Candidate confidence is **not** something to punish.

It merely affects how informative a challenge might be.

---

# 14. Evidence gap analysis

Before asking any probe, the Examiner should ask:

> **What do we still not know?**

Example:

Already established:

- candidate selected sliding window;
- implementation is broadly correct;
- candidate knows expected complexity.

Still unknown:

- candidate has not demonstrated why the left boundary cannot move backward.

Then the invariant becomes a high-value target.

Evidence-gap analysis prevents CounterQ from becoming a reaction stream.

---

# 15. Existing evidence

Current-session evidence always matters.

If the candidate has already convincingly demonstrated a concept:

do not probe it again merely because a familiar trigger appears.

Example:

Earlier the candidate correctly explains:

> "Hash-table access is average O(1), but collisions can make the worst case linear."

Later they use `unordered_map`.

Do not automatically ask:

> "Is lookup really O(1)?"

There is already sufficient evidence.

Only contradictory later behavior should reopen the uncertainty.

## Semantic target identity and deduplication

Probe duplication must be detected semantically, not only by exact wording.

Two questions may be different strings but test the same uncertainty.

Example:

> "Why is `max` needed there?"

and:

> "What prevents `left` from moving backwards?"

may both target:

```text
sliding_window_left_monotonicity
```

The Examiner should maintain an ephemeral/persisted target identity using available canonical information such as:

- concept;
- misconception/failure pattern;
- claim or code region;
- evidence goal;
- current code/problem context.

This target identity informs:

- same-concept cooldown;
- duplicate-evidence risk;
- probe-chain continuation;
- final-defense selection.

Do not invent a new persisted `ProbeCandidate` table solely for this.

`ExaminerDecision` plus canonical target/evidence references remain sufficient for Phase 1.

---

# 16. Cross-session mastery

Historical mastery may change target priority.

Example:

A previous session exposed weakness around:

```text
Dijkstra + negative edges
```

A later interview naturally uses Dijkstra.

A relevant assumption challenge now has higher diagnostic value because it also acts as a legitimate retest.

However:

> **Prior weakness may increase relevance. It may not manufacture relevance.**

CounterQ must not force a negative-edge question into an unrelated problem merely to retest history.

Current problem relevance comes first.

## Historical mastery is a prior, not a verdict

Historical weakness may increase the value of checking a concept.

It must **not**:

- cause CounterQ to assume the candidate is currently wrong;
- lower the technical confidence threshold required for a challenge;
- override strong current-session evidence;
- force the same wording or same old question;
- be exposed to the candidate unless the product explicitly enters a disclosed retest workflow.

For a natural retest, the Examiner should evaluate the current response/code as if it must stand on its own.

Historical mastery may influence:

```text
target priority
```

but not:

```text
technical correctness judgment
```

This reduces confirmation bias and prevents CounterQ from repeatedly "proving" an old weakness.

---

# 17. When NOT to probe

This section is a core product policy.

> **No probe is a valid Examiner decision.**

CounterQ should choose WAIT or OBSERVE even after noticing something interesting when any of the following applies.

---

## Candidate is still developing the thought

Exploratory reasoning frequently contains provisional statements.

Example:

> "Maybe I can use a hash map... actually, a set might be enough."

Do not interrogate the first half of an unfinished thought.

Choose:

```text
WAIT
```

---

## Candidate is actively correcting the issue

If the candidate appears to have noticed the problem:

wait.

Independent correction is stronger evidence than correction after interviewer intervention.

---

## Candidate has not committed to the claim

Candidate:

> "I think it might be O(n log n), but I need to check."

This is uncertainty, not a confident misconception.

Possible action:

```text
OBSERVE
```

or later ASK.

---

## Issue is cosmetic

Do not probe:

- variable naming;
- formatting;
- harmless syntax style;
- implementation preferences without conceptual consequence.

CounterQ is not a code-style reviewer.

---

## Issue is below the configured level's diagnostic value

A technically obscure detail may be true but irrelevant.

Do not interrogate intern candidates about implementation internals that are not reasonably part of the coding interview.

---

## Existing evidence already answers the question

Do not ask for proof twice because another opportunity appeared.

---

## Similar probe was recently asked

Respect same-concept cooldown.

---

## Candidate is in productive coding flow

Even a useful target may be better delayed.

Implementation interruption carries real cost.

---

## Question would reveal too much

A probe that essentially states the bug destroys diagnostic value.

Prefer waiting for testing or reframing the question.

---

## Testing will naturally surface it

If the candidate is about to run a relevant case, observe first.

---

## Final defense is a better moment

Some implementation questions are more natural once coding is complete.

---

## Examiner confidence is insufficient

If CounterQ is unsure whether the candidate's reasoning is actually wrong:

- clarify;
- observe;
- escalate analysis if worthwhile;
- or do nothing.

Never issue a confident technical challenge on weak reasoning confidence.

---

## Transcript confidence is weak

If speech recognition may have converted:

> "average O(1)"

into:

> "always O(1)"

CounterQ should not challenge the candidate based on the transcription error.

---

## Candidate-visible deadline will be missed

A technically correct probe that arrives after the conversation has moved on is a bad probe.

Discard it.

---

## Time remaining is low

Use remaining time for:

- core correctness;
- final defense;
- wrap-up.

Drop enrichment.

---

## Probe budget should be preserved

A low-value question should not consume capacity that may be needed later for a central misconception.

---

# 18. Strategy catalogue

Phase 1 supports:

- `WHY`
- `PROVE`
- `ASSUMPTION_CHALLENGE`
- `COUNTEREXAMPLE`
- `COMPLEXITY`
- `EDGE_CASE`
- `TRADE_OFF`
- `ALTERNATIVE`
- `IMPLEMENTATION_CHOICE`
- `CONSTRAINT_MUTATION`
- `FAILURE_MODE`
- `TRANSFER`

A strategy describes **why CounterQ is asking**, not the exact wording.

## One delivered probe has one primary strategy

A single candidate-visible `PROBE` must persist exactly one primary `ProbeStrategy`.

During internal reasoning, several strategies may be plausible.

Example:

```text
potential strategies:
IMPLEMENTATION_CHOICE
PROVE
COUNTEREXAMPLE
```

The Examiner must choose the strategy that best describes the immediate evidence purpose before authorization.

If the first probe fails and a second question is justified, that follow-up is a new ExaminerDecision / InterviewerPrompt and may use a different strategy.

This avoids ambiguous records such as:

```text
strategy = IMPLEMENTATION_CHOICE / PROVE
```

and makes:

- analytics;
- probe-quality evaluation;
- budgets;
- offline benchmarking;

much cleaner.

A probe may still have multiple:

- concept IDs;
- skill dimensions;
- evidence targets;

when technically appropriate.

But it has one primary strategy.

---

# 19. WHY

## Purpose

Determine whether the candidate understands the rationale behind a choice.

## Use when

- candidate names a data structure without justification;
- candidate chooses an algorithm immediately;
- candidate makes a meaningful implementation decision;
- rationale is central but absent.

## Do not use when

- rationale was already explained;
- choice is trivial;
- generic "why?" would create conversational friction;
- another strategy can target the uncertainty more precisely.

## Evidence sought

- intentionality;
- rationale;
- understanding of what the choice provides;
- relationship between requirement and tool.

## Typical stages

- APPROACH_DISCOVERY
- APPROACH_DEFENSE
- IMPLEMENTATION
- FINAL_DEFENSE

## Example claim

> "I'll use a priority queue."

## Bad probe

> "Why?"

Too vague.

It forces the candidate to guess what dimension CounterQ cares about.

## Good probe

> "What does the priority queue give you here that a normal queue wouldn't?"

## Follow-up

If candidate correctly explains ordering requirement:

stop.

If rationale remains superficial, possibly move to:

- TRADE_OFF;
- ALTERNATIVE;
- FAILURE_MODE.

## Termination

Stop when the candidate demonstrates sufficient rationale or lack of rationale has been clearly established.

---

# 20. When generic "why?" is acceptable

A short:

> "Why?"

can occasionally sound natural when:

- the candidate made one immediately preceding concrete claim;
- there is zero ambiguity about what "why" refers to;
- conversational rhythm favors brevity.

It should not become CounterQ's default probing mechanism.

Specific questions usually provide better signal.

---

# 21. PROVE

## Purpose

Test whether the candidate can justify correctness or a key invariant.

## Use when

- candidate makes a correctness claim;
- solution depends on an invariant;
- candidate appears to recognize a pattern but may not understand why it works;
- algorithm correctness is non-obvious.

Especially valuable for:

- greedy algorithms;
- sliding window;
- binary search;
- graph traversal;
- shortest paths;
- dynamic programming transitions;
- monotonic structures.

## Do not use when

- proof would be disproportionate to candidate level;
- correctness has already been convincingly established;
- issue is implementation-specific and IMPLEMENTATION_CHOICE is cleaner.

## Evidence sought

- invariant understanding;
- correctness reasoning;
- ability to connect implementation to algorithmic guarantee.

## Typical stages

- APPROACH_DEFENSE
- IMPLEMENTATION
- COMPLEXITY_EDGE_CASES
- FINAL_DEFENSE

## Candidate claim

> "Once we move `left`, we never need to revisit earlier positions."

## Bad probe

> "Can you formally prove the invariant?"

Potentially overformal and vague for a standard coding interview.

## Good probe

> "What guarantees that moving `left` can't make us miss a valid answer?"

## Follow-up

If candidate gives a sound invariant:

stop.

If candidate relies on intuition:

a COUNTEREXAMPLE or implementation-specific probe may follow.

## Termination

Stop when:

- invariant is defended;
- inability to defend it becomes clear;
- additional proof depth would exceed level expectations.

---

# 22. Proof expectations by candidate level

## INTERN

Expected:

- intuitive correctness explanation;
- simple invariant;
- convincing example.

Do not require formal proof terminology.

## NEW_GRAD

Expected:

- explicit invariant or correctness reasoning;
- ability to handle a counterexample;
- connection between reasoning and implementation.

## EARLY_CAREER

Expected:

- clearer assumptions;
- stronger invariant reasoning;
- awareness of limitations;
- ability to discuss how changed constraints affect correctness.

Higher level means deeper reasoning, not more ritualized questions.

---

# 23. ASSUMPTION_CHALLENGE

## Purpose

Test an unsupported, absolute, or hidden assumption.

This should become one of CounterQ's signature behaviors.

## Common signals

Language such as:

- always;
- never;
- guaranteed;
- obviously;
- constant;
- must;
- impossible;
- cannot.

But lexical trigger alone is not enough.

CounterQ must first determine whether the absolute statement is technically meaningful.

## Use when

- candidate makes a consequential absolute claim;
- hidden assumption drives correctness;
- assumption may fail under legitimate input.

## Do not use when

- "always" is casual harmless wording;
- absolute statement is correct and already justified;
- the claim is irrelevant to the interview.

## Evidence sought

- awareness of assumptions;
- ability to calibrate guarantees;
- average vs worst-case reasoning;
- understanding of constraints.

## Typical stages

- PROBLEM_UNDERSTANDING
- APPROACH_DEFENSE
- COMPLEXITY_EDGE_CASES
- FINAL_DEFENSE

## Candidate claim

> "`unordered_map` lookup is always O(1)."

## Bad probe

> "Actually it can become O(n), right?"

This leaks the conclusion.

## Good probe

> "You said always. Is that actually guaranteed?"

## Follow-up

If candidate immediately says:

> "No, average O(1); collisions can degrade the worst case."

Stop.

If candidate insists:

> "Yes."

Escalate minimally:

> "What happens when multiple keys collide?"

## Termination

Stop when:

- candidate correctly qualifies assumption;
- misconception is sufficiently established;
- another strategy would not materially improve evidence.

---

# 24. Absolute-language policy

CounterQ must not become a linguistic gotcha system.

Candidate:

> "We'll always move `right` forward one step."

If that is correct and obvious from the implementation:

do not theatrically challenge "always."

Absolute-language detection is a candidate-generation heuristic.

Technical relevance determines whether it matters.

---

# 25. COUNTEREXAMPLE

## Purpose

Test whether a claimed rule survives adversarial input.

## Use when

- candidate overgeneralizes;
- correctness depends on an untested condition;
- one concrete input could expose the reasoning boundary;
- candidate's explanation sounds memorized.

## Do not use when

- giving the counterexample would reveal the entire bug;
- candidate is already generating tests independently;
- issue can be tested more elegantly with PROVE.

## Evidence sought

- robustness;
- ability to falsify own reasoning;
- boundary awareness;
- correctness reasoning.

## Typical stages

- APPROACH_DEFENSE
- IMPLEMENTATION
- TESTING_DEBUGGING
- COMPLEXITY_EDGE_CASES

## Candidate claim

> "Whenever we see a duplicate, we can set `left` to the previous index + 1."

## Bad probe

> "Try `abba`; your pointer moves backwards."

This gives both the test and the failure.

## Better candidate-generated probe

> "Can you think of a case where the previous occurrence is already outside the current window?"

## Interviewer-supplied probe

If candidate cannot generate one:

> "Walk me through what your update does on `abba`."

## Follow-up

Let candidate trace.

Do not immediately explain the issue.

## Termination

Stop when:

- candidate generates and handles a valid counterexample;
- provided counterexample exposes the gap;
- sufficient evidence exists.

---

# 26. Candidate-generated vs supplied counterexamples

Prefer asking the candidate to generate a counterexample when:

- testing depth is the main goal;
- sufficient time exists;
- candidate level supports it;
- the target is conceptually central.

Provide a concrete input when:

- debugging process is being evaluated;
- candidate already failed to identify the class of edge case;
- time is constrained;
- a particular example efficiently exposes the target.

Providing an input is a stronger intervention and should affect evidence interpretation.

---

# 27. COMPLEXITY

## Purpose

Determine whether candidate complexity reasoning is understood rather than memorized.

## Target areas

- superficial loop counting;
- nested loops;
- amortized analysis;
- recursion depth;
- auxiliary memory;
- data-structure operation cost;
- average vs worst case;
- preprocessing/query trade-offs.

## Use when

- complexity claim appears incorrect;
- reasoning is missing;
- implementation contradicts claimed complexity;
- central complexity guarantee matters.

## Do not use when

- complexity was already correctly derived;
- complexity is trivial and low-value;
- asking again would simply satisfy a checklist.

## Evidence sought

- analytical reasoning;
- aggregate operation counting;
- awareness of hidden costs;
- average/worst-case distinction.

## Typical stages

- APPROACH_DEFENSE
- COMPLEXITY_EDGE_CASES
- FINAL_DEFENSE

## Candidate claim

> "There are two pointers, so it's O(n²)."

## Bad probe

> "Actually each pointer only moves n times, so isn't it O(n)?"

The answer is mostly supplied.

## Good probe

> "Across the whole algorithm, how many times can each pointer move?"

## Follow-up

If candidate derives O(n):

stop.

If they remain confused:

> "Does `left` restart from the beginning for every `right`?"

## Termination

Stop once complexity is correctly reasoned or the misconception is sufficiently established.

---

# 28. EDGE_CASE

## Purpose

Test whether candidate reasoning survives boundary conditions and whether they understand the problem requirements.

## Use when

- candidate has not tested a crucial boundary;
- implementation likely fails on a meaningful class of inputs;
- problem semantics make a particular edge case diagnostic.

## Do not use when

- CounterQ would merely enumerate the test suite;
- candidate is already systematically generating edge cases;
- edge case is irrelevant to configured level.

## Evidence sought

- robustness;
- testing discipline;
- requirements understanding;
- ability to reason about boundaries.

## Typical stages

- PROBLEM_UNDERSTANDING
- APPROACH_DEFENSE
- TESTING_DEBUGGING
- COMPLEXITY_EDGE_CASES

## Bad probe

> "What are all the edge cases?"

Too generic and checklist-like.

## Better candidate-generated probe

> "Which input would you test first to challenge this window logic?"

## Better interviewer-supplied probe

> "What happens if every character is the same?"

## Follow-up

If candidate handles it:

stop.

If it exposes implementation trouble, allow them to debug before further probing.

## Termination

Stop when relevant robustness is demonstrated or the edge-case weakness is clear.

---

# 29. TRADE_OFF

## Purpose

Test engineering judgment and intentional design choice.

## Typical trade-offs

- memory vs time;
- preprocessing vs query time;
- simplicity vs asymptotic optimality;
- ordered vs unordered structures;
- recursion vs iteration;
- storing full state vs minimal state.

## Use when

- candidate has already demonstrated baseline correctness;
- multiple legitimate designs exist;
- stronger candidate warrants deeper reasoning.

## Do not use when

- candidate is still struggling with fundamental correctness;
- alternatives are artificial;
- discussion adds breadth but little diagnostic value.

## Evidence sought

- judgment;
- awareness of consequences;
- intentionality;
- ability to compare solutions.

## Typical stages

- APPROACH_DEFENSE
- COMPLEXITY_EDGE_CASES
- CONSTRAINT_MUTATION
- FINAL_DEFENSE

## Example

Candidate stores last position for each character.

## Bad probe

> "Why didn't you use a set?"

May sound like there is one expected alternative.

## Good probe

> "What are you gaining by storing the last position instead of only keeping a set?"

## Follow-up

Candidate may explain ability to jump `left`.

If correct:

stop.

## Termination

Stop once the meaningful trade-off is articulated.

---

# 30. ALTERNATIVE

## Purpose

Determine whether the chosen solution was intentional rather than reproduced without understanding.

## Use when

- a nearby alternative illuminates the core concept;
- candidate seems to know the canonical implementation but not why;
- comparing solutions has diagnostic value.

## Do not use when

- alternative is unrelated;
- asking merely increases breadth;
- time is constrained;
- candidate already compared approaches.

## Evidence sought

- intentional choice;
- conceptual comparison;
- understanding of what changes across approaches.

## Typical stages

- APPROACH_DEFENSE
- COMPLEXITY_EDGE_CASES
- FINAL_DEFENSE

## Candidate context

Sliding-window solution stores last occurrence indexes.

## Bad probe

> "Name three other solutions."

Breadth without diagnostic value.

## Good probe

> "Could you do this with a set instead of storing the last index? What would change?"

## Follow-up

If candidate explains one-step shrinking vs direct jumps:

stop.

## Termination

Stop once candidate demonstrates meaningful comparison.

---

# 31. IMPLEMENTATION_CHOICE

## Purpose

Connect exact candidate code to conceptual understanding.

This is one of CounterQ's strongest differentiators.

It differs from ordinary code review because the goal is not to enumerate faults.

The goal is:

> **Use the candidate's own implementation as an interview question.**

## Use when

- exact line encodes an important invariant;
- explanation and code differ;
- candidate made a non-trivial implementation choice;
- suspicious logic is highly diagnostic.

## Do not use when

- issue is syntax/style;
- code is obviously incomplete;
- candidate is currently correcting the line;
- test/debugging will produce stronger evidence naturally.

## Evidence sought

- code-to-concept understanding;
- invariant awareness;
- intentionality;
- implementation correctness.

## Typical stages

- IMPLEMENTATION
- TESTING_DEBUGGING
- FINAL_DEFENSE

## Example: correct code

```cpp
left = max(left, last[s[right]] + 1);
```

### Good probe

> "Why is the `max` necessary there?"

---

## Example: suspicious code

```cpp
left = last[s[right]] + 1;
```

### Bad probe

> "You forgot `max`, so `left` can move backwards."

This is code review, not interviewing.

### Good probe

> "What guarantees that `left` never moves backwards here?"

## Follow-up

Allow candidate to inspect their own code.

## Termination

Stop when:

- candidate justifies implementation;
- candidate independently corrects it;
- misconception is established.

---

# 32. FAILURE_MODE

## Purpose

Test what happens when an assumption, structure, or operational condition behaves badly.

## Use when

- candidate reasoning assumes ideal behavior;
- hidden failure behavior matters;
- asking "what happens when..." reveals depth.

## Examples

> "What happens if two keys collide?"

> "What happens if recursion depth reaches n?"

> "What happens if that priority queue contains stale entries?"

## Do not use when

- failure mode is obscure and irrelevant;
- question becomes systems trivia;
- candidate already explained it.

## Evidence sought

- robustness;
- ability to reason beyond happy path;
- knowledge of algorithm/data-structure limitations.

## Typical stages

- APPROACH_DEFENSE
- TESTING_DEBUGGING
- COMPLEXITY_EDGE_CASES
- FINAL_DEFENSE

## Bad probe

> "Hash maps can degrade because of collisions. Explain that."

Answer is embedded.

## Good probe

> "What happens to lookup when many keys land in the same bucket?"

## Termination

Stop once candidate explains the consequence or the gap is clear.

---

# 33. CONSTRAINT_MUTATION

## Purpose

Alter a meaningful original assumption and test whether candidate understanding adapts.

This is a transfer mechanism tied directly to the current problem.

## Mutation examples

- batch input → stream;
- abundant memory → strict memory cap;
- non-negative edges → negative edges;
- random access → sequential access only;
- arbitrary alphabet → fixed tiny alphabet;
- in-memory data → data larger than memory.

## Mutation quality criteria

A good mutation must:

1. affect something important in the candidate's solution;
2. preserve enough of the original problem that transfer is meaningful;
3. test a concept rather than trivia;
4. have a clear reason in the Interview Pack;
5. be appropriate to candidate level;
6. fit remaining time;
7. avoid requiring an entirely different interview domain.

## Use when

- original solution understanding is established;
- transfer evidence would be informative;
- candidate is performing strongly enough;
- problem supports a meaningful mutation.

## Do not use when

- candidate never understood original solution;
- mutation is just harder for the sake of harder;
- insufficient time remains;
- no legitimate mutation exists.

## Evidence sought

- adaptability;
- assumption awareness;
- transfer;
- conceptual depth.

## Typical stage

Primarily:

- CONSTRAINT_MUTATION

Occasionally FINAL_DEFENSE.

## Example

Original uses Dijkstra.

Mutation:

> "Suppose edges can now have negative weights. What part of your reasoning stops being valid?"

## Bad probe

> "Now solve Bellman-Ford."

This changes the task into another algorithm recall test.

## Good probe

> "If edges can be negative, what assumption in your current approach breaks?"

## Termination

Stop once candidate identifies the impact and proposes a reasonable adaptation direction.

Full reimplementation is usually unnecessary.

---

# 34. TRANSFER

## Purpose

Determine whether a candidate can apply the same underlying concept to a nearby context.

TRANSFER is generally lighter and more conceptual than CONSTRAINT_MUTATION.

## Distinction

### CONSTRAINT_MUTATION

Changes a constraint in the current problem.

Question:

> "What happens if this input now arrives as a stream?"

### TRANSFER

Moves the concept to a related scenario or generalization.

Question:

> "If we allowed at most K distinct values instead, what part of this window logic would stay the same?"

TRANSFER tests:

> Can the candidate recognize the reusable idea?

Mutation tests:

> Can the candidate adapt when an assumption changes?

## Use when

- candidate appears strong;
- memorization vs conceptual understanding remains uncertain;
- current concept has a natural related problem.

## Do not use when

- it becomes unrelated trivia;
- candidate is already overloaded;
- sufficient evidence already exists.

## Evidence sought

- abstraction;
- generalization;
- reusable conceptual understanding.

## Typical stages

- CONSTRAINT_MUTATION
- FINAL_DEFENSE

## Bad probe

> "Name other sliding-window problems."

Tests recall of question lists.

## Good probe

> "If we changed unique characters to at most K distinct characters, what would remain the same in your window logic?"

## Termination

Stop once candidate demonstrates genuine transfer or inability to generalize becomes clear.

---

# 35. Probe-strategy selection matrix

This matrix is suggestive, not deterministic.

| Candidate signal | Likely strategies |
|---|---|
| Unsupported absolute claim | ASSUMPTION_CHALLENGE, FAILURE_MODE |
| Correct approach with weak rationale | WHY, PROVE |
| Questionable complexity claim | COMPLEXITY |
| Important correctness claim | PROVE, COUNTEREXAMPLE |
| Potential implementation bug | IMPLEMENTATION_CHOICE, PROVE, COUNTEREXAMPLE |
| Explanation contradicts code | IMPLEMENTATION_CHOICE, PROVE |
| Overgeneralized rule | COUNTEREXAMPLE, ASSUMPTION_CHALLENGE |
| Important hidden assumption | ASSUMPTION_CHALLENGE, FAILURE_MODE |
| Strong correct solution | TRADE_OFF, ALTERNATIVE, TRANSFER |
| Repeated failed execution | FAILURE_MODE, EDGE_CASE |
| Weak debugging hypothesis | FAILURE_MODE, COUNTEREXAMPLE |
| Apparently memorized solution | WHY, PROVE, TRANSFER, CONSTRAINT_MUTATION |
| Weak test coverage | EDGE_CASE |
| Data-structure choice without reason | WHY, TRADE_OFF, ALTERNATIVE |
| Prior weakness naturally reappears | Strategy appropriate to the current evidence |
| Correct code with non-obvious line | IMPLEMENTATION_CHOICE |
| Candidate confidently claims correctness | PROVE, COUNTEREXAMPLE |

Strategy selection must remain target-driven.

---

# 36. Probe target priority

When multiple valid opportunities exist, CounterQ should generally prioritize:

## Priority 1 — Correctness-critical misconception

If unresolved, the candidate's solution fundamentally fails.

Examples:

- wrong invariant;
- invalid algorithm assumption;
- correctness-breaking implementation reasoning.

---

## Priority 2 — Core concept depth

Central to why the solution works.

Examples:

- sliding-window monotonicity;
- binary-search invariant;
- DP state meaning.

---

## Priority 3 — Explicit confident technical claim

Especially useful when it may reveal memorization or misconception.

---

## Priority 4 — Explanation/code inconsistency

Highly diagnostic because it tests whether the candidate understands their own implementation.

---

## Priority 5 — Naturally relevant historical weakness

Only when current problem activates it organically.

---

## Priority 6 — Trade-off / transfer enrichment

Primarily for candidates who have already demonstrated fundamental understanding.

---

# 37. Multiple simultaneous opportunities

Suppose CounterQ identifies:

1. questionable complexity claim;
2. suspicious implementation line;
3. prior weakness around the same concept.

Do not ask three questions.

Rank them.

A useful prioritization process:

```text
Which target:
- most affects correctness?
- is most central to the current problem?
- is freshest?
- would produce the most new evidence?
- can be tested with the smallest intervention?
```

The highest-value target becomes active.

Others may:

- remain pending briefly;
- expire;
- become obsolete;
- become FINAL_DEFENSE candidates.

---

# 38. Confidence calibration

CounterQ must keep three variables separate.

## Model confidence

> How confident are we that our interpretation is technically and semantically correct?

## Technical importance

> How consequential is the issue?

## Probe value

> How much useful evidence would asking produce right now?

Example:

Missing semicolon:

```text
model confidence = very high
technical importance = low
probe value = near zero
```

Potential wrong binary-search invariant:

```text
model confidence = moderate/high
technical importance = very high
probe value = high
```

A model confidence score alone must never determine whether CounterQ speaks.

---

# 39. Candidate confidence

Candidate confidence can influence target priority.

Compare:

> "I think lookup should be average O(1), but I'm not totally sure."

with:

> "Lookup is always O(1)."

The second is more diagnostic if wrong because:

- claim is stronger;
- misconception appears more established.

But CounterQ should not intentionally punish confident speaking.

The purpose is calibration, not personality judgment.

---

# 40. Probe specificity

CounterQ should reference the candidate's actual reasoning.

Bad:

> "Explain hash maps."

Better:

> "You relied on constant-time lookup there. Is that a worst-case guarantee?"

Bad:

> "Explain sliding windows."

Better:

> "Why can `left` only move forward in your implementation?"

Bad:

> "Explain complexity."

Better:

> "Across the whole run, how many times can `left` advance?"

The candidate should feel:

> **It heard what I said and saw what I wrote.**

---

# 41. Probe intent vs spoken wording

The Examiner Brain should normally produce structured intent rather than polished speech.

Example:

```text
strategy:
PROVE

target:
sliding_window_left_monotonicity

evidence_goal:
test whether candidate understands why the left boundary cannot move backward

technical_reason:
monotonicity is required for correctness and linear complexity
```

The Realtime Voice Brain may phrase this naturally as:

> "What guarantees that `left` never moves backwards?"

This separation improves:

- latency;
- consistency;
- model routing;
- speech naturalness.

---

# 42. Spoken-question constraints

Candidate-visible probes should normally be:

- concise;
- one idea at a time;
- easy to understand in speech;
- neutral in tone;
- specific;
- non-leading;
- free from unnecessary jargon.

Avoid:

> "Considering the potential collision characteristics of unordered map implementations and their worst-case degradation, could you elaborate on whether your complexity claim remains valid?"

Prefer:

> "Is that O(1) lookup guaranteed in the worst case?"

Voice questions must sound like spoken interview questions, not generated essays.

---

# 43. Leading-question policy

A probe should reveal as little as required.

Bad:

> "Could collisions make the lookup O(n)?"

Most of the conclusion is supplied.

Better:

> "What happens if many keys collide?"

Often even better initially:

> "Is that O(1) guaranteed?"

Escalate information only when needed.

---

# 44. Probe escalation ladder

Each target may have a progressive sequence.

Example:

## Target

Hash-table worst-case complexity.

### Level 1 — Minimal challenge

> "Is O(1) guaranteed?"

### Level 2 — Direct attention to mechanism

> "What happens when keys collide?"

### Level 3 — Narrow the consequence

> "If many keys end up in the same bucket, what does lookup look like?"

CounterQ should not automatically ask all three.

The ladder stops as soon as sufficient evidence exists.

---

# 45. Probe-chain stopping

The Examiner must be biased toward stopping.

Stop when:

- understanding is demonstrated;
- misconception is sufficiently established;
- candidate self-corrects;
- new evidence resolves uncertainty;
- Coach hint/teaching policy becomes appropriate;
- time becomes constrained;
- probe budget should be preserved;
- target becomes stale;
- another question would add little information.

The goal is diagnosis, not exhaustion.

---

# 46. Candidate self-correction

If the candidate resolves the target before delivery:

do not probe.

Example:

> "Hash-map access is always O(1)... actually, no, that's average case, not guaranteed."

A pending ASSUMPTION_CHALLENGE is now stale.

Potential evidence may instead capture:

- initial uncertainty/mistake;
- independent self-correction;
- final correct understanding.

The same applies to code.

If suspicious implementation is corrected independently:

do not ask about the old code.

---

# 47. Memorization detection

CounterQ should not accuse candidates of memorizing.

It should test whether understanding exists beneath recognition.

Potential signals include:

- immediate canonical answer with little reasoning;
- inability to justify invariant;
- complexity stated from memory but incorrectly;
- inability to modify implementation;
- inability to survive a nearby counterexample;
- inability to adapt constraint;
- inability to compare a closely related alternative.

Possible probing progression:

```text
WHY
→ PROVE
→ COUNTEREXAMPLE
→ TRANSFER
```

Only use as many steps as necessary.

One weak explanation is not sufficient evidence to label someone as memorizing.

---

# 48. Code-aware probe policy

CounterQ observes exact candidate source code.

High-value code targets include:

- explanation/code mismatch;
- important conditional logic;
- invariant violations;
- unexpected state mutation;
- suspicious data structure use;
- logic associated with repeated failures.

The product goal is not:

> comprehensive automated code review.

It is:

> **use the candidate's implementation to test conceptual understanding.**

---

# 49. Explanation/code mismatch

Explanation/code inconsistency should receive high diagnostic priority.

Example:

Candidate states:

> "`left` only moves forward."

But the implementation can assign a smaller value.

Potential probe:

> "Walk me through what happens to `left` if the previous occurrence is already outside the current window."

This is better than:

> "Your code contradicts your explanation."

The first lets the candidate inspect and defend their own implementation.

---

# 50. Debugging probe policy

Execution failure alone does not require an immediate probe.

Default after first meaningful failure:

```text
OBSERVE
```

CounterQ should watch:

- which output the candidate inspects;
- whether they reproduce;
- what hypothesis they form;
- which code they change;
- whether the issue is independently corrected.

A probe becomes useful when:

- failures repeat;
- candidate adopts a wrong debugging hypothesis;
- candidate explicitly becomes stuck;
- a conceptual misunderstanding is visible.

Prefer:

> "Can you trace what happens on `abba`?"

over:

> "Your duplicate handling is wrong."

---

# 51. Candidate-level adaptation

CounterQ supports:

- `INTERN`
- `NEW_GRAD`
- `EARLY_CAREER`

Candidate level changes expected **depth**, not the number of questions.

---

## INTERN

Focus on:

- basic rationale;
- correctness intuition;
- common complexity reasoning;
- important edge cases.

Example:

> "Why does this sliding window work?"

---

## NEW_GRAD

Expect:

- explicit invariant;
- stronger complexity reasoning;
- implementation justification;
- handling common counterexamples.

Example:

> "What invariant lets you move `left` without reconsidering earlier positions?"

---

## EARLY_CAREER

Expect more:

- assumptions;
- trade-offs;
- implementation consequences;
- transfer;
- constraint adaptation.

Example:

> "What invariant are you preserving, and under what changed constraint would this implementation stop being appropriate?"

Do not introduce unrelated senior-system-design expectations.

---

# 52. Strong candidate policy

Strong candidates should receive:

- deeper proof;
- meaningful trade-offs;
- transfer;
- constraint mutation.

They should **not** receive:

- more nitpicking;
- artificial faults;
- obscure trivia;
- endless chains.

Strong performance should increase conceptual depth, not question volume.

---

# 53. Weak candidate policy

When a candidate is clearly struggling, the Examiner should determine:

> Is this a fundamental misunderstanding or a temporary mistake?

Once sufficient evidence exists, stop probing.

More questions may only produce redundant failure.

In Simulation:

move interview forward without teaching prematurely.

In Coach:

transition toward hint policy once diagnostic evidence is sufficient.

---

# 54. Coach vs Simulation

Probe purposes remain diagnostic in both modes.

## Simulation

- no answer revelation;
- no casual correctness confirmation;
- neutral challenge;
- minimal assistance;
- stop once evidence established.

## Coach

- diagnostic probe occurs first where appropriate;
- after sufficient evidence, CounterQ may move to the State Machine's hint ladder;
- candidate may retry;
- assistance level remains part of Evidence.

Hints are not ProbeStrategies.

They are typically:

```text
InterviewerPrompt(kind=INSTRUCTION)
```

with assistance metadata.

---

# 55. Tone policy

CounterQ should challenge reasoning without attacking the candidate.

Avoid:

- sarcasm;
- gotcha phrasing;
- "Obviously...";
- "That's wrong";
- "Are you sure you understand this?";
- excessive praise.

Preferred tone:

> "What makes that true?"

> "Is that guaranteed?"

> "Can you walk me through that case?"

> "What does the priority queue give you here?"

Challenge:

```text
the claim
```

not:

```text
the person
```

---

# 56. Probe fatigue

Probe fatigue indicates additional questioning may reduce interview quality.

Signals include:

- several consecutive probes;
- candidate repeatedly interrupted;
- same concept repeatedly revisited;
- questions producing little new information;
- implementation flow repeatedly broken;
- interview falling behind schedule.

Probe fatigue reduces the priority of optional targets.

A correctness-critical issue can still override fatigue.

---

# 57. Probe diversity

CounterQ should not artificially rotate strategies.

If `PROVE` is the correct strategy twice, use it twice.

But the Examiner should not lazily default to:

> "Why?"

after every statement.

Strategy follows diagnostic purpose.

---

# 58. Probe budgets

Probe budget is consumed according to the frozen State Machine:

- internal potential targets do not consume it;
- rejected decisions do not consume it;
- stale decisions do not consume it;
- expired decisions do not consume it;
- meaningful delivered technical PROBE prompts consume it according to policy.

`ASK` prompts do not consume probe budget when they are genuinely informational.

However, semantic classification governs this rule: a disguised diagnostic challenge is still a `PROBE` even if phrased politely.

Rephrasing an interrupted or partially delivered probe should follow the frozen State Machine's delivery semantics and should not automatically consume a second probe when it is clearly the same interrogation intent.

As budget becomes constrained, prioritize:

1. correctness-critical misconception;
2. unresolved core-concept gap;
3. high-value explanation/code mismatch;
4. protected FINAL_DEFENSE target.

Drop first:

- optional alternatives;
- enrichment;
- low-value trade-offs;
- nonessential transfer.

---

# 59. Time-aware strategy policy

State Machine time pressure remains authoritative.

## NORMAL

Full strategy set available.

## CONSTRAINED

Prefer:

- concise ASSUMPTION_CHALLENGE;
- PROVE;
- IMPLEMENTATION_CHOICE;
- COMPLEXITY;
- other high-information questions.

Reduce:

- ALTERNATIVE;
- extended TRADE_OFF;
- speculative TRANSFER.

## DEFENSE_RESERVED

Protect one or a small number of unresolved high-value targets.

Do not begin long probe chains.

## WRAP_ONLY

No new PROBE.

---

# 60. Cost-aware strategy policy

Interesting does not mean expensive.

Possible routing:

## Cheap model

Useful for:

- claim-type detection;
- absolute-language detection;
- concept normalization;
- duplicate-target detection;
- simple confidence classification.

## Medium reasoning

Primary tier for:

- claim correctness;
- algorithm reasoning;
- code interpretation;
- strategy selection.

## Strong reasoning

Only for:

- ambiguous correctness disputes;
- difficult code semantics;
- technically consequential disagreement.

If a strong model cannot be used:

prefer:

```text
OBSERVE
```

or neutral ASK

rather than issuing an unreliable technical challenge.

---

# 61. Interview Pack integration

The Interview Pack provides technical scaffolding such as:

- expected approaches;
- invariants;
- common misconceptions;
- known edge cases;
- counterexamples;
- complexity expectations;
- mutation opportunities.

The Interview Pack does **not** dictate a fixed question sequence.

Candidate behavior determines relevance.

---

# 62. Precomputed opportunities

An Interview Pack may contain:

```text
target:
sliding_window_left_monotonicity

common_failure:
candidate allows left boundary to move backward

relevant_strategies:
PROVE
IMPLEMENTATION_CHOICE
COUNTEREXAMPLE

useful_counterexamples:
abba
```

This means:

> "Here is a diagnostic opportunity if candidate behavior activates it."

It does not mean:

> "Always ask these questions."

---

# 63. Realtime Brain safety

The Realtime Brain receives an authorized technical intent.

Example:

```text
strategy:
ASSUMPTION_CHALLENGE

target:
hash_table_worst_case_complexity

intent:
determine whether candidate distinguishes average and worst-case lookup guarantees
```

It may phrase:

> "Is that O(1) lookup guaranteed?"

It may not independently switch to:

> "Explain how rehashing works."

because that would change the diagnostic target.

The Realtime Brain handles natural phrasing, not technical agenda selection.

---

# 64. Structured ExaminerDecision

The Examiner Brain should return a structured decision conceptually containing:

```text
action

target_type
target_id

concept_ids
skill_dimension_ids

technical_issue
technical_importance

interpretation_confidence
candidate_claim_confidence

diagnostic_value
self_correction_likelihood
interruption_cost
duplicate_evidence_risk
staleness_risk

recommended_strategy   # exactly one primary strategy when action = PROBE

probe_intent
desired_evidence

reason_for_probe
reason_to_wait_if_applicable

source_event_watermark
source_state_version
source_code_snapshot_id

expiry_class
```

Exact persistence fields remain governed by `DATA_MODEL.md`.

This document defines behavioral semantics.

---

# 65. No persisted ProbeCandidate table

Phase 1 does **not** require a separate persisted `ProbeCandidate`.

Possible targets may exist ephemerally while the Examiner ranks them.

Persist:

```text
ExaminerDecision
```

when a meaningful analysis decision is made.

This keeps the data model simpler.

Conceptual flow:

```text
Observations
    ↓
ephemeral target ranking
    ↓
ExaminerDecision
    ↓
Policy Gate
    ↓
InterviewerPrompt
```

If future evaluation requires retaining all discarded candidates, that can be added intentionally.

It is not a Phase 1 requirement.

---

# 66. Contradictory evidence

Examiner targeting must respond to new evidence.

Example:

Earlier:

> candidate incorrectly explains two-pointer complexity.

Later, without assistance:

> candidate derives the aggregate pointer movement correctly.

CounterQ should not continue treating the first explanation as the current truth.

Potential outcome:

- Evidence becomes MIXED;
- Breakpoint may not be warranted;
- further complexity probe priority drops;
- final report may note confusion followed by correction.

Target ranking uses the current evidence state, not first impression.

---

# 67. Probe success

A probe succeeds if it produces useful diagnostic evidence.

Success can mean:

- candidate demonstrates strong understanding;
- candidate exposes a misconception;
- candidate independently corrects;
- candidate clarifies an ambiguous claim;
- candidate demonstrates transfer;
- candidate demonstrates assistance dependency.

Probe success does **not** mean:

> candidate failed.

CounterQ should be equally satisfied when a probe confirms genuine mastery.

---

# 68. Technical verification and ground-truth precedence

Before challenging a candidate on technical correctness, CounterQ should distinguish:

```text
candidate differs from expected answer
```

from:

```text
candidate is technically wrong
```

These are not equivalent.

## Interview Pack is scaffolding, not unquestionable truth

The Interview Pack provides:

- known approaches;
- invariants;
- complexity expectations;
- counterexamples;
- common misconceptions.

It is a high-value technical prior.

It is **not** permitted to make the following inference automatically:

```text
candidate approach not in Interview Pack
        ↓
candidate approach is wrong
```

Candidates may produce:

- valid alternate algorithms;
- language-specific optimizations;
- different but correct invariants;
- stronger reasoning than the precomputed pack anticipated.

## Verification order

When a candidate-visible technical challenge depends on disputed correctness, prefer evidence in roughly this order:

1. explicit ProblemVersion requirements and constraints;
2. deterministic execution/test facts where they actually settle the issue;
3. exact candidate code and reasoning context;
4. reviewed/verified Interview Pack knowledge;
5. model reasoning over the above;
6. historical mastery only as relevance context, never technical proof.

No one item mechanically dominates every case.

For example, passing visible tests does not prove algorithmic correctness.

## Disagreement with the Interview Pack

If candidate reasoning appears to conflict with the Interview Pack but could plausibly be valid:

- do not accuse;
- run medium/strong verification if the issue is important;
- ASK neutrally if clarification can resolve it;
- or OBSERVE.

When technically consequential uncertainty remains unresolved, prefer:

```text
no challenge
```

over:

```text
confidently challenge a potentially correct candidate
```

## Strong-model escalation

Escalation is justified only when all are reasonably true:

- technical consequence is meaningful;
- the candidate-visible decision depends on resolving it;
- medium reasoning is genuinely uncertain/contradictory;
- time/cost budget allows it;
- result can still arrive before its usefulness deadline.

Do not use the strongest model merely because another model produced a confidence value below an arbitrary threshold.

---

# 69. False-positive control

A false technical challenge is especially damaging.

If CounterQ challenges correct reasoning confidently, the candidate quickly stops trusting the interviewer.

Therefore when uncertainty is meaningful:

prefer:

> "When you say O(1), are you referring to average case or worst case?"

over:

> "That's not actually O(1)."

Other safe options:

- OBSERVE;
- ASK neutral clarification;
- validate using stronger reasoning if high-value;
- do nothing.

---

# 70. Probe-quality telemetry

Future telemetry should include:

- probe proposal count;
- authorized-probe rate;
- stale-probe suppression rate;
- candidate self-correction before delivery;
- duplicate-probe rate;
- probe-chain depth;
- candidate interruption rate;
- probe-to-evidence conversion;
- high-value-probe rate;
- false technical challenge rate;
- expert reviewer quality score;
- answer-leak rate;
- candidate feedback on probe relevance;
- ASK→PROBE policy-misclassification rate in expert review;
- alternate-correct-solution false-challenge rate;
- repeated-target semantic-duplication rate.

An especially important metric is:

> **False technical challenge rate**

Another is:

> **Probe-to-evidence conversion rate**

A probe that produces no meaningful new evidence was probably low value.

---

# 71. Offline probe-review dataset

The policy should eventually support offline Examiner benchmarking.

A review example should be able to contain:

- candidate level;
- interview mode;
- state;
- problem context;
- relevant Interview Pack excerpt;
- transcript context;
- relevant code snapshot/diff;
- current Evidence;
- relevant historical mastery;
- candidate statement/behavior;
- proposed Examiner action;
- proposed target;
- proposed ProbeStrategy;
- proposed probe intent;
- proposed wording;
- expert preferred action;
- expert preferred strategy;
- expert rationale;
- technical correctness label;
- should-be-spoken label;
- answer-leak label.

This allows evaluation of Examiner quality independently of realtime voice quality.

The full evaluation system is outside this document.

---

# 72. Detailed Example 1 — Correct confident answer, no probe

## Observation

Candidate says:

> "I'll store the most recent index of each character. `left` only moves forward, so each pointer advances at most n times overall."

Code matches explanation.

## Interpretation

- sliding-window invariant appears correct;
- complexity explanation appears correct;
- high candidate confidence;
- no contradiction.

## Candidate targets

Possible:

- pointer monotonicity;
- complexity.

## Probe-value reasoning

Existing explanation already demonstrates both.

Additional probe has high duplicate-evidence cost.

## Chosen action

```text
WAIT
```

## Strategy

None.

## Actual question

None.

## Rejected alternatives

PROVE rejected because the candidate already supplied the proof-level reasoning.

COMPLEXITY rejected because complexity is established.

## Stopping condition

Already satisfied.

## Expected evidence

Positive evidence from the unprompted explanation itself.

Important principle:

> Correct, confident reasoning does not need to be challenged merely because CounterQ can challenge it.

---

# 73. Detailed Example 2 — Incorrect absolute complexity claim

## Observation

Candidate:

> "`unordered_map` lookup is always O(1), so this entire algorithm is guaranteed linear."

## Interpretation

Likely incorrect worst-case guarantee.

## Candidate targets

1. hash-table lookup guarantee;
2. whole-algorithm guarantee.

## Probe-value reasoning

Core technical claim.

High diagnostic value.

Candidate strongly committed.

## Chosen action

```text
PROBE
```

## Strategy

`ASSUMPTION_CHALLENGE`

## Question

> "You said always. Is that actually guaranteed?"

## Rejected alternatives

COMPLEXITY is possible, but assumption calibration is the narrower first question.

FAILURE_MODE is a potential second step.

## Follow-up

If candidate:

> "No, average O(1); worst case can degrade with collisions."

stop.

If candidate insists:

> "Yes."

then:

> "What happens when multiple keys collide?"

## Stopping condition

Candidate correctly qualifies guarantee or misconception sufficiently established.

## Expected evidence

Concept:

- hash-table complexity.

Skill:

- complexity reasoning.

---

# 74. Detailed Example 3 — Correct answer, shallow justification

## Observation

Candidate immediately says:

> "Use binary search."

When asked informally how, they say:

> "Because the array is sorted."

But have not defined the search invariant.

## Interpretation

Approach may be memorized or genuinely understood; evidence insufficient.

## Targets

Binary-search correctness/invariant.

## Probe-value reasoning

Core concept.

Implementation may look correct even with shallow reasoning.

## Chosen action

```text
PROBE
```

## Strategy

`PROVE`

## Question

> "What lets you safely discard half of the search space after each comparison?"

## Rejected alternatives

WHY:

> "Why binary search?"

is less diagnostic.

ALTERNATIVE adds breadth without resolving correctness depth.

## Stopping condition

Candidate explains monotonic decision property sufficiently.

## Evidence expected

Correctness reasoning / invariant understanding.

---

# 75. Detailed Example 4 — Suspicious implementation code

## Observation

Candidate writes:

```cpp
left = last[s[right]] + 1;
```

in a longest-unique-substring implementation.

Candidate is actively typing and has not declared block complete.

## Interpretation

Potential backwards movement of `left`.

## Targets

- implementation choice;
- sliding-window invariant.

## Probe-value reasoning

Technically important.

But immediate interruption cost is high.

Self-correction probability is meaningful.

## Initial action

```text
OBSERVE
```

Candidate finishes block and verbally says:

> "`left` always moves forward to the next valid position."

Code remains unchanged.

## New action

```text
PROBE
```

## Strategy

`IMPLEMENTATION_CHOICE`

`PROVE` remains a possible follow-up strategy if the implementation-choice question does not resolve the invariant.

## Question

> "What guarantees that `left` never moves backwards in this update?"

## Rejected alternatives

Bad:

> "You need `max` there."

Would reveal bug.

COUNTEREXAMPLE may become escalation if needed.

## Stopping condition

Candidate identifies issue and corrects it, or fails to justify invariant.

## Evidence expected

- implementation understanding;
- debugging;
- sliding-window invariant.

---

# 76. Detailed Example 5 — Candidate self-corrects before probe

## Observation

Same suspicious code.

Examiner begins analysis.

Before delivery candidate says:

> "Actually, this can move `left` backward if that duplicate is outside the current window."

Candidate changes code to:

```cpp
left = max(left, last[s[right]] + 1);
```

## Interpretation

Candidate independently detected exact issue.

## Target

Previously active implementation invariant.

## Probe-value reasoning

Target is resolved.

Probe value is now near zero.

## Chosen action

```text
WAIT
```

Pending ExaminerDecision becomes stale.

## Strategy

None.

## Actual question

None.

## Rejected alternative

Asking the prepared question would steal self-correction credit and sound stale.

## Stopping condition

Already resolved.

## Expected evidence

Positive evidence:

- debugging;
- implementation understanding;
- independence = INDEPENDENT.

---

# 77. Detailed Example 6 — Failed test, remain silent first

## Observation

Candidate runs code on:

```text
abba
```

Expected:

```text
2
```

Actual:

```text
3
```

Candidate immediately starts tracing code.

## Interpretation

Likely duplicate-window issue.

## Candidate targets

- invariant;
- debugging hypothesis;
- test reasoning.

## Probe-value reasoning

Potentially strong target.

But candidate has just received useful execution feedback and is productively debugging.

## Chosen action

```text
OBSERVE
```

## Strategy

None initially.

After repeated edits, candidate says:

> "I can't see why this becomes 3."

Now:

```text
PROBE
```

## Strategy

`COUNTEREXAMPLE` / `FAILURE_MODE`

## Question

> "Walk me through how `left` changes on each character in `abba`."

## Rejected alternative

> "Your left pointer moves backwards."

reveals the issue.

## Stopping condition

Candidate identifies failure mechanism or repeated inability establishes evidence.

## Expected evidence

- debugging skill;
- invariant understanding;
- response to failed test.

---

# 78. Detailed Example 7 — Strong candidate deserves transfer

## Observation

Candidate:

- explains optimal algorithm;
- proves invariant;
- implements correctly;
- derives complexity;
- handles edge cases.

## Interpretation

Strong evidence already exists for fundamental concepts.

## Candidate targets

Optional:

- trade-off;
- transfer.

## Probe-value reasoning

Another correctness probe would be redundant.

A transfer question can differentiate depth.

## Chosen action

```text
PROBE
```

if time and stage allow.

## Strategy

`TRANSFER`

## Question

> "If we changed this to allow at most K distinct characters, what part of your window logic would stay the same?"

## Rejected alternatives

Another complexity question: duplicate evidence.

Obscure language trivia: low relevance.

## Stopping condition

Candidate demonstrates conceptual generalization.

## Expected evidence

Strong transfer/adaptability evidence.

---

# 79. Detailed Example 8 — Weak candidate, further probing adds no value

## Observation

Candidate cannot articulate a brute-force solution.

After a neutral prompt they propose an invalid approach.

CounterQ uses one PROVE probe.

Candidate cannot explain it.

A second focused question confirms they do not understand the required state.

## Interpretation

Core conceptual gap sufficiently established.

## Candidate targets

Many more questions could theoretically be asked.

## Probe-value reasoning

Expected information gain from another probe is low.

Probe fatigue increasing.

Time should be preserved.

## Chosen action

```text
WAIT / transition according to State Machine
```

In Coach:

hint ladder may begin.

In Simulation:

move to a simpler productive boundary.

## Strategy

No further probe.

## Rejected alternatives

COUNTEREXAMPLE, WHY, ALTERNATIVE could all generate more failure but little new evidence.

## Stopping condition

Breakpoint already sufficiently established.

## Expected evidence

Negative core-concept evidence with clear provenance.

---

# 80. Detailed Example 9 — Previous Mastery weakness naturally relevant

## Historical context

Previous session exposed weakness:

```text
dijkstra_negative_edge_assumption
```

## Current observation

Candidate proposes Dijkstra on a graph problem whose stated constraints currently guarantee non-negative weights.

They correctly explain priority-queue behavior.

## Interpretation

The historical weakness is relevant to this algorithm family.

## Candidate target

Dijkstra's non-negative-edge assumption.

## Probe-value reasoning

Current problem naturally activates the concept.

Transfer/retest value is high.

Fundamental current approach is already correct.

## Chosen action

At an appropriate later stage:

```text
PROBE
```

## Strategy

`CONSTRAINT_MUTATION`

## Question

> "Suppose the graph could contain a negative edge. What assumption in this approach would stop being safe?"

## Rejected alternatives

Do not ask:

> "Do you remember that you got negative edges wrong last time?"

That contaminates the retest.

Do not force this question if session time is constrained.

## Stopping condition

Candidate correctly identifies why Dijkstra's settled-distance assumption can fail, or weakness remains demonstrated.

## Expected evidence

Retest evidence linked to prior Breakpoint.

## Bias-control note

The current answer should be assessed on its own technical content.

Historical weakness increased the value of selecting the mutation; it does not make a negative result more likely by definition.

---

# 81. Detailed Example 10 — Transcription ambiguity

## Audio

Candidate says something the transcription provider renders as:

> "lookup is always O(1)"

Confidence is low around the word "always."

Acoustic alternatives may include:

> "average O(1)"

## Interpretation

Potentially major difference.

## Candidate targets

Hash-table complexity.

## Probe-value reasoning

Technical importance is meaningful.

Interpretation confidence is insufficient.

A confrontational assumption challenge risks a false technical challenge.

## Chosen action

```text
ASK
```

or `OBSERVE`.

## Strategy

None yet.

## Question

> "When you say O(1), are you referring to the average case or a worst-case guarantee?"

## Rejected alternative

> "You said always. Is that actually guaranteed?"

because CounterQ may have misheard the candidate.

## Stopping condition

Candidate clarifies.

## Expected evidence

Only after clarification should a technical claim be assessed.

---

# 82. Detailed Example 11 — Explanation/code contradiction

## Observation

Candidate verbally explains:

> "I mark a node visited only when I pop it with the shortest confirmed distance."

Code marks nodes visited when they are pushed.

## Interpretation

Explanation and implementation differ.

Potential correctness consequence depending on algorithm.

## Target

Implementation semantics vs stated invariant.

## Probe-value reasoning

Highly diagnostic.

Candidate may understand concept but implement it incorrectly—or may not recognize distinction.

## Chosen action

At a natural boundary:

```text
PROBE
```

## Strategy

`IMPLEMENTATION_CHOICE`

## Question

> "In your code, when exactly does a node become visited?"

After candidate answers:

> "When I push it."

Potential follow-up:

> "What does that mean if a shorter route to the same node appears later?"

## Rejected alternative

> "Your code contradicts your explanation."

Too direct.

## Stopping condition

Candidate reconciles implementation and reasoning or gap is clear.

## Expected evidence

- graph invariant;
- implementation reasoning;
- self-correction if fixed.

---

# 83. Detailed Example 12 — Correct confident absolute statement

## Observation

Candidate says:

> "The right pointer never moves backward."

Code:

```text
for right in [0..n)
```

and right is never modified.

## Interpretation

Absolute claim is correct and directly supported.

## Target

Potential absolute-language trigger only.

## Probe-value reasoning

Technical uncertainty near zero.

No evidence gap.

## Chosen action

```text
WAIT
```

## Strategy

None.

## Rejected alternative

ASSUMPTION_CHALLENGE solely because candidate said "never."

This would make CounterQ feel pedantic.

## Evidence expected

Normal positive implementation reasoning may be inferred if relevant, but no new prompt is required.

---

# 84. Probe anti-patterns

The following are prohibited.

## Asking "why?" after every sentence

Generic interrogation is not adaptive interviewing.

---

## Challenging correct statements to appear intelligent

CounterQ must not manufacture doubt.

---

## Arbitrary follow-ups without evidence purpose

Every probe requires a defined uncertainty.

---

## Revealing implementation bugs

Bad:

> "You're moving `left` backwards."

Prefer a conceptual question—or wait.

---

## Asking stale code questions

If code changed, revalidate before delivery.

---

## Retesting concepts already strongly demonstrated

Current-session evidence outranks the urge to ask a familiar question.

---

## Forcing all Interview Pack opportunities

The pack is scaffolding, not a script.

---

## Obscure implementation trivia

If it does not matter to configured interview level, skip it.

---

## Turning strong candidates into adversarial trivia sessions

Strong candidates deserve depth, not hostility.

---

## Continuing after sufficient evidence

More questioning can reduce interview quality.

---

## Forcing prior weaknesses into irrelevant contexts

Historical memory informs relevance.

It does not override it.

---

## Giving the answer inside the probe

The question should expose as little as necessary.

---

## Judging through transcription uncertainty

Clarify first.

---

## Multiple questions in one spoken prompt

Avoid:

> "Why did you choose a map, what's the complexity, and what would happen with collisions?"

Ask one thing.

---

## Technical accusation from the Realtime Brain

Technical agenda requires authorized Examiner intent.

---

## Treating every implementation imperfection as a misconception

Code may simply be incomplete.

---

## Treating the Interview Pack as the only valid solution

A candidate may be correct even when their approach was not anticipated by precomputation.

Verify before challenging.

---

## Using ASK as an unbudgeted hidden probe

If the purpose is to deliberately test suspected understanding, classify it as PROBE and apply probe policy.

---

## Letting historical weakness bias present correctness

Previous evidence may affect relevance, not the truth value of the candidate's current reasoning.

---

## Confusing candidate failure with probe success

A probe confirming strong understanding is equally valuable.

---

# 85. Final probe-selection principles

1. **Every probe needs an evidence purpose.**

2. **No probe is better than a low-value probe.**

3. **Probe the candidate's actual reasoning, not generic textbook knowledge.**

4. **Prefer the minimum useful question.**

5. **Let candidates self-correct when possible.**

6. **Challenge confident assumptions selectively, not theatrically.**

7. **Code is evidence, not merely output.**

8. **Ask conceptual questions before revealing implementation faults.**

9. **Stop once understanding or a breakpoint is sufficiently established.**

10. **Current-session evidence outranks historical assumptions.**

11. **Prior mastery may increase relevance but never manufacture relevance.**

12. **Strong candidates deserve deeper reasoning, not more questions.**

13. **False technical challenges are especially damaging.**

14. **Probe quality matters more than probe count.**

15. **CounterQ should feel like it listened.**

16. **The Examiner should distinguish model confidence from technical importance and probe value.**

17. **A prepared probe is disposable if the candidate moves on.**

18. **Testing and self-correction can produce better evidence than interruption.**

19. **The Interview Pack suggests opportunities; candidate behavior decides which matter.**

20. **The burden of justification increases with every additional probe in a chain.**

21. **Every delivered probe has one primary strategy.**

22. **ASK is informational; it must never be used to bypass probe policy.**

23. **The Interview Pack is a technical prior, not a verdict on alternate reasoning.**

24. **Historical mastery changes attention, not technical truth.**

25. **Semantically duplicate probes are duplicates even when wording changes.**

---

# 86. Final Examiner rule

The CounterQ Examiner should never ask:

> **What question could I ask next?**

It should ask:

> **What important uncertainty remains about this candidate's understanding, and is speaking now the best way to reduce it?**

The governing rule is:

> **Do not ask the next possible question. Ask the smallest question that most reduces uncertainty about what the candidate actually understands.**
