# CounterQ — Phase 1 Mastery Model

**Document:** `docs/data/MASTERY_MODEL.md`  
**Status:** Frozen Phase 1 Cross-Session Mastery Source of Truth  
**Product:** CounterQ  
**Phase:** Phase 1 — Technical Coding Interviews  
**Last Updated:** August 2026

---

# 1. Purpose

This document defines CounterQ's Phase 1 cross-session Mastery Model.

CounterMap answers:

> **What happened in this interview?**

Mastery answers:

> **Across interviews, what does CounterQ currently have evidence that this candidate can understand, explain, defend, implement, and transfer independently?**

Mastery must be:

- evidence-driven;
- cross-session;
- conservative;
- explainable;
- recalculable;
- sensitive to assistance;
- sensitive to contextual diversity;
- separate from raw problem completion.

The governing principle is:

> **Mastery is a projection over validated evidence—not a model opinion stored as truth.**

---

# 2. Product purpose

The Mastery Model should let CounterQ answer:

- Which concepts has the candidate actually demonstrated?
- Which concepts have only been encountered?
- Which gaps have appeared repeatedly?
- Which concepts were only understood after assistance?
- Which concepts have been demonstrated independently?
- Which weaknesses survived later retesting?
- Which previous weaknesses now appear resolved?
- Which interview skills remain inconsistent across technical areas?
- Which strong areas have enough evidence to trust?
- Which concepts should CounterQ test next?
- Which concepts should be retested in a different context?
- Which apparently strong areas have not been verified recently?

A candidate should eventually see something like:

```text
Sliding-window boundary reasoning

STRONG

Why?

✓ Independently defended in multiple problems
✓ Correctly reflected in implementation
✓ Survived a nearby constraint change
✓ Independently corrected an invariant bug
```

rather than:

```text
Sliding Window
87%
```

---

# 3. Mastery is not problem completion

CounterQ must explicitly reject:

```text
solved problem
→ mastered concept
```

A candidate may solve a problem because they:

- memorized the pattern;
- have seen the exact problem before;
- passed visible tests accidentally;
- copied an implementation structure from memory;
- received a strong Coach hint;
- were taught the missing concept.

Conversely, a candidate may fail to finish code because of:

- syntax;
- time pressure;
- small implementation mistakes;

while still demonstrating strong conceptual understanding.

Therefore:

> **Problem success is context. Validated Evidence is the Mastery input.**

---

# 4. Architectural position

Mastery follows the frozen hierarchy:

```text
Observed Events
        ↓
AI Interpretations
        ↓
Validated Evidence
        ↓
Derived Projections
```

Mastery belongs entirely to:

```text
Derived Projections
```

Canonical truth remains:

```text
Evidence
```

If CounterQ later changes:

- mastery thresholds;
- recency policy;
- concept aggregation;
- assistance interpretation;
- retest policy;

it should:

```text
recompute Mastery
```

not:

```text
rewrite historical Evidence
```

Mastery must always be rebuildable.

---

# 5. Two independent mastery families

CounterQ needs two distinct long-term projections.

---

## 5.1 Technical Concept Mastery

Answers:

> **What technical ideas has the candidate demonstrated understanding of?**

Examples:

- sliding-window boundary invariant;
- hash-table average vs worst-case complexity;
- BFS shortest path in unweighted graphs;
- binary-search monotonic predicate;
- Dijkstra non-negative-edge assumption;
- DP state definition;
- recursion stack-space reasoning.

---

## 5.2 Interview Skill Mastery

Answers:

> **How consistently does the candidate demonstrate important interview behaviors across technical contexts?**

Examples:

- correctness reasoning;
- explanation clarity;
- complexity reasoning;
- debugging;
- test selection;
- edge-case reasoning;
- adaptability.

These projections must not be collapsed.

Example:

```text
Sliding-window boundary invariant
STRONG

Explanation clarity
DEVELOPING
```

is meaningful.

The candidate may understand the concept but explain it inconsistently.

---

# 6. Concept taxonomy

Phase 1 uses a curated technical concept ontology.

Example shape:

```text
Algorithms
├── Arrays
├── Hashing
│   ├── Frequency counting
│   ├── Hash-table collision behavior
│   └── Hash-table lookup complexity
│
├── Two Pointers
│   ├── Pointer monotonicity
│   └── Two-pointer invariant
│
├── Sliding Window
│   ├── Window validity
│   ├── Boundary monotonicity
│   └── Window state maintenance
│
├── Binary Search
│   ├── Monotonic predicate
│   ├── Boundary search
│   └── Search invariant
│
├── Trees
│   ├── Traversal
│   ├── Recursive structure
│   └── Subtree reasoning
│
├── Graphs
│   ├── BFS
│   ├── DFS
│   ├── Visited-state invariant
│   ├── Shortest paths
│   └── Dijkstra assumptions
│
├── Greedy
│   ├── Greedy-choice property
│   └── Exchange/correctness reasoning
│
└── Dynamic Programming
    ├── State definition
    ├── Transition
    ├── Base case
    └── Overlapping subproblems
```

This is illustrative.

The complete Phase 1 ontology should grow incrementally with curated problem coverage.

Do not attempt to encode the entire DSA universe before launch.

---

# 7. Canonical Concept requirements

A Concept should have, consistent with `DATA_MODEL.md`:

- canonical concept ID;
- stable display name;
- category;
- aliases;
- optional parent concept;
- relationship metadata where useful;
- ontology/taxonomy version.

AI may propose concept mappings.

AI may not invent a permanent new canonical identifier during every interview.

Unknown concepts should map to:

- an existing canonical concept;
- or a reviewed provisional concept according to frozen ontology policy.

---

# 8. Concept granularity

Mastery concepts must be neither too broad nor too narrow.

Too broad:

```text
Graphs
```

Too narrow:

```text
Forgot to mark visited on line 17 in interview 934
```

Useful:

```text
BFS visited-state invariant
```

Useful:

```text
Dijkstra non-negative-edge assumption
```

Useful:

```text
Sliding-window boundary monotonicity
```

A mastery concept should generally:

1. recur across multiple problems;
2. have a meaningful technical identity;
3. support independent retesting;
4. represent reasoning rather than one specific code line;
5. be useful for future interview selection.

---

# 9. Concept identity vs evidence detail

Do not create new concepts for every failure mode.

Example:

These should probably normalize to one canonical concept:

```text
hashmap collision complexity
hash map collision worst case
hash-table lookup degradation
unordered_map collision behavior
```

Potential canonical concepts:

```text
hash_table_collision_behavior
hash_table_lookup_complexity
```

The exact failure remains in Evidence.

The ontology remains normalized.

---

# 10. Phase 1 SkillDimension vocabulary

`DATA_MODEL.md` is frozen and remains authoritative for the canonical SkillDimension taxonomy.

Phase 1 therefore uses the seeded dimensions already defined there:

- `correctness`
- `explanation_clarity`
- `complexity_reasoning`
- `edge_case_reasoning`
- `trade_off_reasoning`
- `follow_up_adaptability`
- `debugging`
- `constraint_adaptation`
- `thinking_aloud`
- `communication`

Do **not** silently replace these with a second mastery-only skill taxonomy.

In particular, Phase 1 does not introduce new persistent dimensions such as:

- `IMPLEMENTATION_REASONING`
- `TEST_SELECTION`
- `ADAPTABILITY`
- `COMMUNICATION_PRECISION`

unless `DATA_MODEL.md` is intentionally revised in a future architecture version.

Implementation/test-selection behavior can still be captured in:

- Evidence findings;
- Assessment dimensions;
- source events;
- technical Concepts;
- existing SkillDimensions where legitimately applicable.

The rule is:

> **Mastery consumes the frozen skill ontology; it does not redefine it.**

---

# 11. Candidate-facing skill grouping

The product may use friendlier **display groupings** without changing canonical persistence.

For example:

```text
Adaptability
```

may visually summarize evidence from:

- `follow_up_adaptability`;
- `constraint_adaptation`.

Likewise, candidate-facing communication copy may combine:

- `explanation_clarity`;
- `communication`.

But this is presentation only.

The underlying Mastery projections remain separate and evidence-backed.

Do not merge canonical SkillDimensions merely to simplify the UI.

---

# 12. What skill dimensions must not measure

CounterQ must not create persistent judgments about:

- accent;
- charisma;
- extroversion;
- personality;
- emotional state;
- confidence as a personality trait;
- "leadership presence";
- speech speed by itself.

Technical interview communication should evaluate:

- clarity;
- logical structure;
- precision;
- responsiveness;
- ability to articulate reasoning.

Not personality.

---

# 13. Mastery states

Both technical concepts and skill dimensions use the same categorical states:

```text
UNTESTED
EXPOSED
WEAK
DEVELOPING
STRONG
```

The semantics of Evidence differ by domain, but shared states make the product understandable.

---

# 14. UNTESTED

Meaning:

> CounterQ does not have enough valid Evidence to evaluate this concept or skill.

Important:

```text
UNTESTED ≠ WEAK
```

A candidate may understand the concept perfectly.

CounterQ simply does not know yet.

UNTESTED must never be treated as negative evidence.

---

# 15. EXPOSED

Meaning:

> CounterQ has encountered the concept and collected some relevant Evidence, but the evidence is insufficient for a reliable directional conclusion.

Examples:

- one brief unchallenged statement;
- one shallow interaction;
- ambiguous Evidence;
- concept surfaced but was not meaningfully tested;
- heavy assistance began before meaningful independent assessment.

EXPOSED prevents premature classification.

Candidate-facing terminology:

> **Limited evidence**

---

# 16. WEAK

Meaning:

> CounterQ has sufficiently strong validated Evidence that a meaningful current gap exists.

Potential examples:

- candidate cannot defend the core invariant;
- candidate repeats a misconception after targeted probing;
- independent retest fails;
- candidate repeatedly misstates a central complexity guarantee;
- core concept can only be reached after strong guidance;
- Breakpoint has strong unresolved support.

WEAK must not result from:

- one syntax error;
- one ambiguous transcript;
- one low-confidence inference;
- one minor transient slip;
- one mistake immediately independently corrected;
- the fact that a candidate asked for a hint;
- the fact that Coach eventually used a strong hint when insufficient independent evidence existed beforehand.

## Phase 1 WEAK policy floor

The first deterministic mastery policy should require at least one of the following patterns before classifying a concept/skill as `WEAK`:

### A. One highly diagnostic negative result

For example:

- `NEGATIVE`;
- `strength = STRONG`;
- valid provenance;
- independence is `INDEPENDENT` or `AFTER_PROBE`;
- central target was meaningfully tested;
- no immediate independent correction resolved the same uncertainty.

A failed independent retest is a particularly strong instance.

### B. Multiple aligned negative results

For example:

- at least two `MODERATE` or stronger negative Evidence items;
- they concern the same canonical concept/skill gap;
- they are not duplicates of the same exact observation;
- no later stronger independent Evidence has already resolved the uncertainty.

### C. Strong unresolved Breakpoint support

Where the Breakpoint itself is backed by qualifying validated Evidence.

Assistance level alone is never enough to classify `WEAK`.

The Mastery Engine must ask:

> **What did the candidate fail to demonstrate before assistance?**

not merely:

> **How much help did Coach eventually provide?**

Candidate-facing terminology:

> **Needs work**

---

# 17. DEVELOPING

Meaning:

> Candidate has demonstrated real understanding or improvement, but CounterQ does not yet have enough consistent independent Evidence to trust the concept across contexts.

Typical cases:

- one strong independent demonstration;
- correct answer after diagnostic probe;
- previous WEAK followed by one independent success;
- mixed positive/negative sessions;
- correct implementation with shallow defense;
- successful transfer after light guidance;
- one successful independent retest following teaching.

DEVELOPING is intentionally broad.

It is the default home for legitimate but incomplete confidence.

---

# 18. STRONG

Meaning:

> CounterQ has repeated, sufficiently independent, sufficiently diverse Evidence that this understanding is reliable.

STRONG should usually reflect some combination of:

- independent demonstration;
- repeated Evidence;
- more than one session or context;
- correct explanation;
- correct application;
- successful defense under probing;
- edge-case/counterexample survival;
- transfer or constraint adaptation where relevant.

No universal checklist applies to every concept.

However:

> **STRONG must be conservative.**

One ordinary correct answer is not enough.

---

# 19. Direct UNTESTED → STRONG

Phase 1 should generally prevent:

```text
UNTESTED
→ one ordinary interaction
→ STRONG
```

A single exceptionally rich session could theoretically contain:

- independent concept discovery;
- correct implementation;
- invariant defense;
- counterexample survival;
- constraint transfer.

Even then, Phase 1 should usually prefer:

```text
DEVELOPING
```

until another context confirms the concept.

This protects the meaning of STRONG.

---

# 20. Why there is no mastery percentage

Candidate-facing Phase 1 Mastery must not show:

```text
82% mastery
74% confidence
91% understanding
```

Reasons:

- Evidence types are heterogeneous;
- assistance affects interpretation;
- contexts differ;
- problem difficulty labels are noisy;
- model confidence is not psychometric confidence;
- CounterQ lacks calibration data for precise percentages.

Internal ranking functions may later use numeric values.

Candidate-facing Mastery remains:

- categorical;
- evidence-backed;
- explainable.

---

# 21. Mastery inputs

Mastery consumes only **valid canonical Evidence**.

Useful Evidence attributes include:

- concept IDs;
- SkillDimension IDs;
- polarity;
- evidence strength;
- independence level;
- source session;
- source problem;
- candidate level;
- source stage;
- assistance metadata;
- evidence timestamp;
- retest linkage;
- source context;
- superseded/invalidated status.

Mastery must not directly consume:

- raw TranscriptSegments;
- raw CandidateClaims;
- raw ExaminerDecisions;
- SessionReport prose;
- CounterMap summaries;
- unvalidated model interpretations.

Those must first become validated Evidence.

---

# 22. Evidence polarity

Evidence may be:

- `POSITIVE`
- `NEGATIVE`
- `MIXED`

---

## POSITIVE

Supports demonstrated understanding.

Examples:

- independently defended invariant;
- correctly derived complexity;
- successfully transferred concept.

---

## NEGATIVE

Supports a meaningful understanding gap.

Examples:

- repeated incorrect assumption;
- failed independent retest;
- inability to defend central concept after targeted challenge.

---

## MIXED

Useful when performance cannot honestly be reduced to positive or negative.

Examples:

- candidate initially misunderstands but independently corrects;
- explanation is right while implementation remains inconsistent;
- concept works in one case but fails under nearby mutation.

Mastery must preserve this nuance.

---

# 23. Do not average polarity

Phase 1 must not implement:

```text
POSITIVE = +1
NEGATIVE = -1
MIXED = 0

average everything
```

Two Evidence items are not necessarily equally diagnostic.

Evidence interpretation depends on:

- strength;
- independence;
- context;
- recency;
- retest semantics;
- contradiction.

---

# 24. Evidence strength

Evidence strength answers:

> **How diagnostically informative is this observation?**

Examples of stronger Evidence:

- independent answer to a targeted technical question;
- independent self-correction;
- successful transfer to a different context;
- successful independent retest;
- robust final defense;
- implementation that correctly embodies an explicitly defended invariant.

Examples of weaker Evidence:

- unchallenged casual statement;
- immediate repetition after teaching;
- success after structural hint;
- ambiguous transcript;
- one passing visible test.

---

# 25. Evidence strength vs model confidence

These must remain separate.

### Model confidence

> How confident was the evaluator that its interpretation was correct?

### Evidence strength

> How strongly does the observed candidate behavior tell us something meaningful about understanding?

Example:

Candidate says:

> "The complexity is O(n)."

Evaluator confidence that transcription is accurate:

high.

Evidence strength for mastery:

low if candidate provides no reasoning.

---

# 26. Independence hierarchy

Use the frozen independence model:

```text
INDEPENDENT
AFTER_PROBE
AFTER_LIGHT_GUIDANCE
AFTER_STRONG_HINT
DIRECTLY_TAUGHT
```

Conceptually:

```text
INDEPENDENT
>
AFTER_PROBE
>
AFTER_LIGHT_GUIDANCE
>
AFTER_STRONG_HINT
>
DIRECTLY_TAUGHT
```

This ordering must influence Mastery qualitatively.

Do not freeze numeric multipliers yet.

---

# 27. Diagnostic probes are not hints

Candidate correctly answers:

> "What guarantees that `left` never moves backwards?"

after CounterQ asks the question.

This is:

```text
AFTER_PROBE
```

It remains meaningful Evidence because the question tested reasoning without giving the correction.

Do not treat:

```text
AFTER_PROBE
```

as equivalent to:

```text
AFTER_STRONG_HINT
```

CounterQ should not over-penalize candidates simply because an interviewer asked a good technical question.

---

# 28. Self-correction

Independent self-correction should have high diagnostic value.

Example:

Candidate:

> "Hash lookup is always O(1)... actually, no. That's the average case, not a guaranteed worst case."

The evidence history may include:

- transient negative/mixed Evidence;
- positive independent self-correction Evidence.

Do not delete the first event.

But self-correction should materially improve interpretation.

If this is an isolated slip, Mastery should likely remain:

```text
DEVELOPING
```

rather than:

```text
WEAK
```

---

# 29. Code self-correction

Example:

Candidate implements a window boundary incorrectly.

Before CounterQ speaks, they notice the invariant violation and repair it.

This should support:

- relevant technical concept;
- `DEBUGGING`;
- possibly `IMPLEMENTATION_REASONING`.

Because correction is:

```text
INDEPENDENT
```

it is particularly useful Evidence.

---

# 30. Assistance and Mastery

Teaching creates learning Evidence.

Teaching does not establish durable mastery.

Example:

```text
WEAK
    ↓
Coach explains concept
    ↓
candidate immediately repeats explanation correctly
```

Possible result:

```text
WEAK + RETEST_DUE
```

or:

```text
DEVELOPING + RETEST_DUE
```

depending on previous Evidence.

Never:

```text
STRONG
```

from immediate repetition.

---

# 31. Mastery state is computed, not incremented

Mastery is not a workflow like:

```text
positive answer
→ advance one state

negative answer
→ move down one state
```

Do not implement:

```text
mastery = next_state(mastery)
```

Instead:

```text
Evidence set changes
        ↓
Mastery Engine recomputes
        ↓
state emerges from current valid evidence
```

This is necessary for:

- deletion;
- evidence invalidation;
- policy changes;
- contradictory evidence;
- taxonomy changes.

---

# 32. Conceptual state transitions

Possible recomputed transitions include:

```text
UNTESTED → EXPOSED
EXPOSED → WEAK
EXPOSED → DEVELOPING
WEAK → DEVELOPING
DEVELOPING → STRONG
STRONG → DEVELOPING
DEVELOPING → WEAK
WEAK → STRONG
```

The last transition should be possible only when the current Evidence set genuinely supports it, typically after substantial later independent evidence.

States are projections.

They are not manually pushed through a state-machine lifecycle.

---

# 33. Contradictory Evidence

Candidates are inconsistent.

CounterQ must represent that honestly.

Example:

```text
Session A
NEGATIVE

Session B
POSITIVE

Session C
NEGATIVE
```

Do not hide older Evidence because a newer answer differs.

Mastery might become:

```text
DEVELOPING
```

with explanation:

> **Performance has been inconsistent across contexts.**

Contradiction itself is useful diagnostic information.

---

# 34. Recency

Recent independent Evidence should generally matter more when interpreting current ability.

But Phase 1 should **not** implement arbitrary continuous decay.

Reject:

```text
mastery_score -= 5% every 30 days
```

Historical Evidence remains historically true.

What changes is:

> how recently the understanding has been verified.

---

# 35. Separate Mastery from verification freshness

Phase 1 should implement a separate **derived freshness semantic**.

Recommended values:

```text
CURRENT
AGING
RETEST_DUE
```

This is an important behavioral decision, but it does **not** require adding a new column to frozen `DATA_MODEL.md`.

For Phase 1, freshness can be deterministically derived from existing projection/provenance inputs such as:

- `concept_mastery.last_evidence_at` / skill equivalent;
- admitted mastery Evidence;
- RetestRecommendation state;
- Breakpoint state;
- mastery policy version.

If later performance warrants materializing freshness as a column, that belongs in a future Data Model revision.

The frozen schema remains authoritative.

Example:

```text
Mastery state:
STRONG

Verification freshness:
RETEST_DUE
```

means:

> The concept was strongly demonstrated, but CounterQ has not verified it recently.

This is better than silently changing:

```text
STRONG → WEAK
```

because time passed.

---

# 36. CURRENT

Meaning:

Relevant mastery Evidence is sufficiently recent for current product policy.

This is not an absolute calendar claim.

Freshness thresholds may depend on:

- concept importance;
- usage frequency;
- candidate goals;
- retest policy.

---

# 37. AGING

Meaning:

Evidence is still credible but beginning to become less useful as proof of current readiness.

Candidate-facing wording may simply be:

> **Not tested recently**

rather than exposing internal enum.

---

# 38. RETEST_DUE

Meaning:

The concept should be verified again.

Possible reasons:

- long time since independent evidence;
- prior teaching but no later independent retest;
- contradictory recent evidence;
- important Breakpoint awaiting resolution.

RETEST_DUE does not imply WEAK.

---

# 39. Evidence sufficiency

Mastery should separately derive how much trustworthy evidence supports the projection.

Recommended internal classification:

```text
LOW
MEDIUM
HIGH
```

This is not the same as Mastery state.

Examples:

```text
DEVELOPING
evidence_sufficiency = LOW
```

versus:

```text
DEVELOPING
evidence_sufficiency = HIGH
```

The second may mean consistently mixed performance.

Phase 1 does not require a new `evidence_sufficiency` database column.

It can be recomputed from:

- `concept_mastery_evidence` / `skill_mastery_evidence`;
- `supporting_evidence_count`;
- `context_diversity`;
- Evidence strength/independence;
- contradiction.

Candidate-facing state explanations may carry this in ordinary derived API/view payloads.

---

# 40. Candidate-facing evidence sufficiency

Do not show:

```text
Evidence confidence: 0.63
```

Translate it into language.

Examples:

```text
Limited evidence
Some evidence
Well supported
```

Show this mainly where it improves interpretation.

Example:

> **Developing — limited evidence**

is useful.

For obvious well-supported STRONG concepts, the extra label may be unnecessary.

---

# 41. Evidence sufficiency factors

Sufficiency may consider:

- number of meaningful Evidence items;
- number of independent demonstrations;
- number of sessions;
- contextual diversity;
- evidence strength;
- evidence consistency;
- independence;
- recency;
- relevance to target candidate level.

No universal fixed formula is frozen yet.

---

# 42. Retest readiness

Mastery Engine should identify concepts that are useful to test again.

Typical causes:

- unresolved Breakpoint;
- teaching occurred without independent verification;
- DEVELOPING Evidence needs another context;
- previous WEAK concept now has learning evidence;
- STRONG concept is stale;
- contradictory Evidence exists;
- important concept has low sufficiency.

Candidate-facing state:

> **Retest ready**

or:

> **Retest due**

depending on context.

---

# 43. Retest priority

Internally, `RetestRecommendation` may rank opportunities based on:

- unresolved Breakpoint severity;
- concept relevance to candidate level;
- concept centrality;
- evidence uncertainty;
- freshness;
- prior teaching;
- contradiction;
- candidate goal.

A numeric internal priority is acceptable.

Do not display:

```text
Retest priority = 83.7
```

to candidates.

Once a `retest_recommendation` is exposed/scheduled, its workflow status is persistent application state even though its ranking/rationale are derived.

Supported frozen statuses include:

- `PENDING`
- `SCHEDULED`
- `ATTEMPTED`
- `SATISFIED`
- `DISMISSED`
- `SUPERSEDED`

A mastery recalculation may supersede a recommendation; it must not silently erase an already exposed/scheduled workflow row.

---

# 44. Retest eligibility

A mastery retest should not merely test immediate recall after teaching.

Prefer:

- different problem;
- different surface wording;
- different implementation context;
- no explicit reminder of previous mistake;
- enough separation from the original intervention.

Do not freeze universal timing intervals in Phase 1.

The important principle is:

> **Retest retrieval and transfer, not repetition.**

---

# 45. Retest success

Example:

Previous state:

```text
WEAK
```

Candidate receives Coach teaching.

Later:

- different problem;
- no help;
- concept appears naturally;
- candidate identifies and defends it correctly.

Likely result:

```text
DEVELOPING
```

not immediately STRONG.

Repeated independent success may later produce:

```text
STRONG
```

---

# 46. Retest failure

A failed independent retest is highly diagnostic.

Example:

Previous Coach session taught:

> hash-table lookup is not a strict worst-case O(1) guarantee.

Later Simulation:

candidate again says:

> "It's always O(1)."

This is strong negative Evidence because the weakness persisted after instruction.

Possible results:

- Breakpoint reinforced;
- Mastery remains or becomes WEAK;
- Retest remains required.

---

# 47. Contextual diversity

Repeated Evidence becomes more trustworthy when it comes from varied contexts.

Potential diversity factors:

- different problem;
- different session;
- different surface formulation;
- different code structure;
- different ProbeStrategy;
- transfer/constraint mutation;
- explanation vs implementation Evidence.

Do not require every factor.

Diversity is evidence quality, not a checklist.

---

# 48. Memorization inflation protection

Repeatedly answering the exact same question correctly should have diminishing diagnostic value.

Example:

Question repeated:

> "Is `unordered_map` O(1) in the worst case?"

Candidate learns the stock answer:

> "Average O(1), worst O(n)."

This alone should not create STRONG mastery.

A stronger retest might ask:

> "What complexity guarantee does your algorithm actually have if its hash-table operations degrade?"

or use a different data-structure context.

---

# 49. Concept Evidence vs Skill Evidence

One candidate interaction may contribute to both projections.

Example:

Candidate cannot justify why a sliding window remains valid.

Evidence may reference:

```text
Concept:
sliding_window_window_validity

SkillDimension:
CORRECTNESS_REASONING
```

The same Evidence can contribute to:

- concept Mastery;
- skill Mastery.

But each projection interprets it in its own context.

---

# 50. Skill mastery uses the same states

Skill dimensions use:

```text
UNTESTED
EXPOSED
WEAK
DEVELOPING
STRONG
```

Example:

```text
DEBUGGING
STRONG
```

should require repeated evidence such as:

- identifying failures;
- forming useful hypotheses;
- using test feedback;
- independent correction;

across more than one context.

One impressive self-correction is positive Evidence.

It does not automatically create STRONG debugging mastery.

---

# 51. Thinking aloud

Thinking aloud should not become a simplistic requirement like:

```text
more words = better interview skill
```

CounterQ should respect different reasoning styles.

Evaluate whether the candidate can:

- articulate important reasoning when needed;
- answer technical follow-ups;
- explain decisions;
- communicate assumptions;
- make their reasoning understandable.

Long silent periods while coding do not inherently reduce Mastery.

---

# 52. Communication Precision

`COMMUNICATION_PRECISION` should focus on technical precision.

Examples:

Positive:

> "Average O(1), but not a strict worst-case guarantee."

Negative:

> repeatedly using absolute technical terms inaccurately.

It must not score:

- accent;
- grammar perfection;
- native-speaker similarity;
- charisma.

---

# 53. Concept relationships

The frozen concept ontology may support relationships such as:

- `IS_A`
- `RELATED_TO`
- `PREREQUISITE_OF`
- `USES`
- `VARIANT_OF`

Mastery should use these conservatively.

Phase 1 does not need a sophisticated reasoning graph.

A simple curated relationship table is sufficient.

---

# 54. Prerequisite use

Prerequisite relationships may help:

- choose a retest;
- explain a weakness;
- choose teaching order.

Example:

```text
window validity
PREREQUISITE_OF
sliding-window boundary invariant
```

But do not automatically infer:

```text
weak child → weak prerequisite
```

or:

```text
strong prerequisite → strong child
```

Mastery requires direct Evidence.

Ontology relationships guide decisions.

They do not create Evidence.

---

# 55. Parent concept aggregation

Parent concepts are useful for navigation, but Phase 1 should avoid turning child Mastery projections into new pseudo-Evidence.

Example:

```text
Sliding Window
├── Window validity        WEAK
├── Boundary monotonicity  STRONG
└── State maintenance      DEVELOPING
```

Candidate-facing summary may display:

```text
Sliding Window
DEVELOPING
```

because a meaningful child weakness remains.

---

# 56. Parent aggregation principles

For initial Phase 1:

> **Treat parent state primarily as a presentation/query-time aggregate.**

Do not create new Evidence merely because a child is WEAK/STRONG.

Do not let:

```text
child mastery
→ synthetic evidence
→ parent mastery
```

reverse the source-of-truth hierarchy.

A parent display summary should:

- summarize child projections conservatively;
- never erase meaningful child weaknesses;
- avoid claiming strength from one strong child;
- expose coverage/limited evidence where relevant.

If the parent Concept itself has direct validated Evidence, its normal `concept_mastery` projection may exist independently.

Persisting child-derived parent mastery as its own cached projection should be deferred until there is a clear implementation need and an explicit policy for the underlying admitted Evidence.

---

# 57. Parent concept is a summary, not truth replacement

Candidate UX may show:

```text
Sliding Window
DEVELOPING

Window validity
Needs work

Boundary monotonicity
Strong

State maintenance
Developing
```

The child detail remains authoritative for understanding the gap.

Do not collapse everything into:

> Sliding Window 71%.

And do not treat the displayed parent aggregate as canonical Evidence.

---

# 58. Candidate-facing hierarchy depth

Phase 1 should show at most:

- category;
- useful mastery concept;

with one optional child level where it materially helps.

Do not expose a 7-level ontology tree.

Most candidates care about:

> what to improve next

not ontology navigation.

---

# 59. Breakpoints and Mastery

Breakpoint is a persistent evidence-backed weakness object.

Mastery is the broader state derived from all valid Evidence.

Conceptually:

```text
Breakpoint
        ↓
supporting negative Evidence
        ↓
Mastery Engine
        ↓
WEAK / DEVELOPING / etc.
```

A Breakpoint can strongly influence Mastery.

But it is not the Mastery state itself.

---

# 60. Breakpoint improvement

Mastery may improve before a Breakpoint is considered fully resolved.

Example:

```text
Breakpoint:
hash_table_worst_case_complexity
status = RETEST_PENDING

ConceptMastery:
DEVELOPING
```

This can happen after the candidate demonstrates partial later improvement but has not yet satisfied the independent resolution policy.

Align Breakpoint lifecycle with frozen `DATA_MODEL.md`.

Do not introduce a second competing Breakpoint state machine here.

---

# 61. Breakpoint resolution principles

A Breakpoint should generally require:

- independent retest success;
- or sufficiently strong later independent Evidence demonstrating the original gap no longer holds.

These do not resolve it:

- being taught once;
- repeating the explanation immediately;
- success after strong hint.

The exact status transition remains governed by the frozen Breakpoint model.

---

# 62. Candidate level

Mastery must know the interview level under which Evidence was produced.

Do **not** add an `evaluated_at_level` column to `evidence` in Phase 1.

The frozen model already provides the provenance path:

```text
Evidence
→ InterviewSession
→ InterviewConfiguration
→ interview_level
```

Supported levels remain:

- `INTERN`
- `NEW_GRAD`
- `EARLY_CAREER`

Mastery projection should interpret Evidence relative to the candidate's configured target level using that canonical session/configuration provenance.

---

# 63. Level-relative interpretation

A candidate may demonstrate enough depth for:

```text
INTERN
```

but not yet for:

```text
EARLY_CAREER
```

Do not solve this by maintaining three completely separate mastery profiles.

Instead:

- preserve Evidence level/context;
- compute Mastery relative to the candidate's current target level;
- retain historical Evidence so recalculation can occur if target level changes.

Candidate-facing concept may say:

> **Strong for your current New Grad target**

only if such qualification materially improves clarity.

Do not clutter every card with level labels.

## Target-level changes must not rewrite historical mastery truth

If a candidate changes target level, for example:

```text
INTERN → NEW_GRAD
```

CounterQ recomputes the **current projection relative to the new target** from the same historical Evidence.

It must not rewrite old Evidence as if it had been collected under the new level.

Where useful internally, mastery explanation should distinguish:

- evidence originally collected at lower/higher interview levels;
- whether that evidence is still sufficiently diagnostic for the new target.

If evidence is insufficient for the new target, prefer:

```text
EXPOSED / DEVELOPING
```

over automatically declaring:

```text
WEAK
```

solely because expectations increased.

---

# 64. Problem difficulty

Do not make Mastery depend heavily on:

```text
Easy / Medium / Hard
```

or external labels such as:

```text
LeetCode Medium
```

These labels are noisy.

Problem context may still contribute through:

- conceptual depth;
- constraints;
- probe depth;
- implementation complexity;
- transfer requirements.

But Evidence quality should dominate.

---

# 65. STRONG promotion policy

Phase 1 should make `STRONG` deliberately difficult to earn.

## Technical Concept STRONG policy floor

A leaf-level technical Concept should normally require all of the following:

1. **At least two qualifying positive demonstrations.**
2. **At least two distinct contexts**, normally different sessions/problems or a later independent Quick Drill.
3. **At least one fully `INDEPENDENT` demonstration.**
4. The second qualifying demonstration is at least:
   - `INDEPENDENT`, or
   - `AFTER_PROBE` where the probe was diagnostic/non-leading.
5. Evidence includes actual reasoning/application, not only:
   - passing tests;
   - reciting a definition;
   - immediate post-teaching repetition.
6. No unresolved recent correctness-critical `STRONG` negative Evidence for the same concept.
7. Evidence is not merely the same exact question repeated.

A single excellent interview therefore normally produces:

```text
DEVELOPING + HIGH sufficiency
```

not `STRONG`.

## Interview Skill STRONG policy floor

Because skills are intended to generalize across technical content, `STRONG` should usually require:

- repeated qualifying positive Evidence;
- multiple sessions;
- more than one technical concept/problem family where applicable;
- no recent strong contradictory pattern.

For example, `debugging = STRONG` should not come from one impressive bug fix.

These are policy-v1 floors, not database constraints.

Future calibration may adjust them through `mastery_policy_version`, but Phase 1 should begin conservatively.

---

# 66. Exceptional rich session

A single session may contribute multiple independent forms of Evidence:

- candidate discovers the approach;
- defends invariant;
- implements it correctly;
- handles counterexample;
- adapts mutation.

This is substantially stronger than one correct sentence.

Phase 1 should treat this as:

```text
DEVELOPING with HIGH evidence sufficiency
```

and prioritize a lighter later verification.

Under mastery policy v1, a single session does not create `STRONG` for a technical Concept, regardless of how many correlated observations occurred inside that session.

Reason:

multiple observations inside one interview are not the same as evidence that understanding survives retrieval in a later context.

---

# 67. WEAK classification policy

WEAK should also be conservative.

It may be justified by:

### One highly diagnostic failure

Example:

Candidate repeatedly defends a false core invariant after focused probing.

### Multiple aligned negative Evidence items

Example:

Incorrect explanation + incorrect code + failure under relevant counterexample.

### Failed independent retest

Especially after previous teaching.

### Strong unresolved Breakpoint

Where evidence quality is high.

CounterQ should not require five failed sessions before admitting a genuine weakness exists.

---

# 68. DEVELOPING as the safe intermediate state

DEVELOPING should absorb cases including:

- one strong success;
- mixed history;
- recent improvement;
- correct response after probe;
- one successful retest;
- strong concept but weak transfer;
- correct implementation with incomplete defense.

This keeps classification honest.

---

# 69. Mastery Map candidate experience

The Phase 1 candidate-facing experience should be a structured list/grid, not a giant skill tree.

Example:

```text
Mastery

TECHNICAL CONCEPTS

Strong
────────────────────────────
Sliding-window boundary reasoning
BFS traversal

Developing
────────────────────────────
Hash-table complexity
Binary-search invariants

Needs work
────────────────────────────
Dijkstra assumptions


RETEST READY
────────────────────────────
Hash-table worst-case complexity
[ CounterQ me again ]


INTERVIEW SKILLS

Strong
────────────────────────────
Debugging

Developing
────────────────────────────
Complexity reasoning
Explanation clarity

Needs work
────────────────────────────
Adaptability
```

The product should emphasize:

- evidence;
- next action;

not gamified completion.

---

# 70. Do not show the entire UNTESTED ontology

A new candidate may technically have hundreds of:

```text
UNTESTED
```

concepts.

Do not display them all.

Default Mastery UX should show:

- tested concepts;
- relevant parents;
- retest-ready concepts;
- perhaps a few important adjacent concepts.

Full ontology browsing may come later.

---

# 71. Cold start

For a new candidate:

Mastery should be sparse.

Good UX:

> **CounterQ is still learning where your interview strengths are. Complete a few interviews to build an evidence-backed view.**

Do not initialize:

```text
Arrays: 50%
Graphs: 50%
DP: 50%
```

There is no evidence for that.

---

# 72. Sparse evidence

After one short interview:

show only what was meaningfully tested.

Do not create the illusion of a comprehensive learner model.

The Mastery experience should become richer as actual Evidence accumulates.

---

# 73. Concept detail

Example:

```text
Hash-table worst-case complexity

DEVELOPING
Limited evidence

Why this state?

You initially treated lookup as a guaranteed O(1)
operation. In a later interview, you independently
explained how collision behavior can degrade lookup.

Evidence

Aug 21
Simulation
Needs work
Independent

Aug 28
Coach
Correct after probe

Sep 03
Simulation
Independent retest
Strong demonstration

Freshness
Current

Next
Verify this once more in a different context.

[ CounterQ me again ]
```

No raw database fields should be necessary to understand the state.

---

# 74. Skill detail

Example:

```text
Complexity reasoning

DEVELOPING

What CounterQ has seen

✓ Independently derived two-pointer amortized complexity
✗ Treated hash-table average lookup as a guarantee
✓ Correctly included recursion stack space

Why Developing?

Your reasoning is strong in several algorithmic contexts,
but complexity guarantees are not yet consistently precise.

Next

Practice stating both the complexity and the assumptions
behind it.
```

This cross-concept view is a key benefit of separating skills from technical concepts.

---

# 75. Evidence timeline

Concept/skill detail should provide a compact history.

Example:

```text
Aug 21 · Simulation
Needs work
Hashing problem
Independent

Aug 28 · Coach
Improved
After probe

Sep 03 · Simulation
Strong demonstration
Independent retest
```

The candidate should be able to inspect source CounterMap/session if desired.

---

# 76. Mastery explanation

Every displayed state should answer:

> **Why does CounterQ think this?**

Good:

> **Why Developing?**  
> You have one recent independent success, but earlier contradictory evidence has not yet been verified in another context.

Bad:

> **Developing — model confidence 72%.**

Mastery explanation is grounded in:

- supporting Evidence;
- contradicting Evidence;
- freshness;
- diversity;
- assistance.

Not private model reasoning.

---

# 77. No circular grading

CounterQ must avoid:

```text
Mastery says candidate is weak
        ↓
Examiner assumes candidate is wrong
        ↓
Examiner probes aggressively
        ↓
more negative opportunities
        ↓
Mastery becomes weaker
```

Historical Mastery may influence:

- problem selection;
- retest priority;
- target relevance.

It must **not** influence whether a current answer is technically correct.

Frozen Probe Strategy rule remains:

> Historical weakness may increase relevance. It may not bias correctness judgment.

---

# 78. Retest generation

Mastery may help select future opportunities.

Example:

Breakpoint:

```text
sliding_window_boundary_monotonicity
```

Possible retest contexts:

- longest substring with at most K distinct values;
- frequency-constrained window;
- minimum valid window variant.

The concept remains the same.

The surface changes.

---

# 79. Retest diversity policy

When practical, alter at least one of:

- problem;
- wording;
- implementation structure;
- constraint;
- counterexample;
- ProbeStrategy.

Do not simply replay the original interview question.

---

# 80. Retest mode

Phase 1 mastery verification should use:

> **Quick Drill using Simulation policy**

rather than introducing a third full interview mode.

A retest remains explicitly linked through the frozen:

```text
retest_recommendations
→ retest_attempts
→ retest_attempt_evidence
```

workflow.

The later interview itself still uses ordinary `InterviewSession` + Simulation mode semantics.

This provides:

- short focused retest;
- no active solution guidance;
- independent verification;
- reuse of existing State Machine / Examiner policy.

If the user wants Coach assistance after failing:

that should be a separate Coach session/drill under existing mode rules.

Do not create a new `RETEST` behavioral engine.

---

# 81. Mastery update timing

Mastery does not belong on the latency-sensitive live Examiner path.

During interview:

- validated Evidence may be created;
- Breakpoints may be established.

Normally after session:

```text
Evidence committed
        ↓
Mastery recalculation job
```

A retest completion may also trigger recalculation.

The candidate should never wait for Mastery processing before the live interview can continue.

---

# 82. Transactional outbox integration

Conceptually:

```text
Evidence committed
and/or
Interview completed
        ↓
same PostgreSQL transaction writes OutboxEvent
        ↓
background publisher
        ↓
RECALCULATE_MASTERY
        ↓
idempotent worker
        ↓
new Mastery projection
```

If Redis publication fails:

the durable outbox retains the job.

Mastery recalculation must be idempotent.

---

# 83. Persistence alignment

Use the **physical structures already frozen in `DATA_MODEL.md`**:

Current projections:

- `concept_mastery`
- `skill_mastery`

Evidence admitted into mastery:

- `concept_mastery_evidence`
- `skill_mastery_evidence`

Audit/history:

- `mastery_transitions`
- `mastery_transition_evidence`

Retesting:

- `retest_recommendations`
- `retest_attempts`
- `retest_attempt_evidence`

Do not introduce a generic physical `MasteryEvidence` table.

`MasteryEvidence` is only a conceptual umbrella term in the Data Model; referential integrity is intentionally implemented through the separate concept/skill association tables.

This document defines semantics, not migrations.

---

# 84. Mastery evidence association role

`concept_mastery_evidence` and `skill_mastery_evidence` link a current Mastery target to validated Evidence admitted by the active mastery policy.

They should make it possible to answer:

> Why is this concept DEVELOPING?

without reparsing transcripts.

Their contribution classification/context should distinguish roles such as:

- supporting;
- contradicting;
- retest;
- resolution-related;

to the extent supported by the frozen schema/policy.

---

# 85. MasteryTransition role

Because Mastery is recomputed, `mastery_transitions` is an audit/history record.

Example:

```text
WEAK
→ DEVELOPING

reason:
successful independent retest

policy_version:
mastery_v3

supporting Evidence:
E102, E131
```

Those sources are linked through:

```text
mastery_transition_evidence
```

The transition does not cause the new state.

The recomputed Evidence projection causes it.

The transition records what changed.

---

# 86. Mastery rebuildability

Recompute when:

- Evidence is invalidated;
- Evidence is corrected;
- an interview is deleted;
- taxonomy mapping changes;
- Mastery policy changes;
- candidate target level changes materially;
- Breakpoint resolution affects retest policy.

The canonical Evidence remains unchanged unless the Evidence itself is invalidated.

---

# 87. Taxonomy versioning

The concept ontology will evolve.

`DATA_MODEL.md` freezes versioning as an architectural requirement but does not require a separate `concept_taxonomy_version` column/table in Phase 1.

Therefore Phase 1 should version the curated ontology as a code/configuration artifact referenced by the mastery/policy release.

Concept rows retain stable canonical IDs/keys.

When a taxonomy migration occurs, the migration itself must be versioned and auditable.

If concepts later merge:

```text
hash_map_collision_cost
+
hash_table_collision_behavior
```

historical Evidence must remain traceable to its original mapping.

The new Mastery projection may map both to a new canonical concept.

Do not mutate historical provenance into pretending the new ontology existed originally.

---

# 88. Concept split/merge policy

Phase 1 only needs the architectural requirement:

```text
old canonical mapping
        ↓
ontology migration mapping
        ↓
new projection
```

Do not build a sophisticated automated ontology migration framework initially.

Curated migrations are enough.

---

# 89. Evidence invalidation

If Evidence becomes invalid:

1. exclude it from Mastery input;
2. recalculate affected ConceptMastery/skill projection;
3. update MasteryEvidence links;
4. record transition if state changes;
5. reconsider associated Breakpoint/retest policy.

A cached STRONG state must not survive because its source Evidence was removed.

---

# 90. Interview deletion

When user deletes a session:

- its Evidence is removed/invalidated according to frozen deletion policy;
- Mastery recalculates from remaining valid Evidence;
- ConceptMastery may change;
- Skill mastery may change;
- RetestRecommendation may change.

There must be no "ghost interview" still affecting the candidate.

---

# 91. Mastery projection algorithm

Recommended deterministic flow:

```text
1. Load all valid candidate Evidence relevant to the
   requested mastery scope.

2. Resolve Evidence onto:
   - canonical Concept IDs;
   - SkillDimension IDs.

3. Remove:
   - invalidated Evidence;
   - superseded Evidence where policy says it no longer counts.

4. Group Evidence by:
   - Concept;
   - SkillDimension.

5. For each group classify Evidence by:
   - polarity;
   - strength;
   - independence;
   - source session;
   - source problem;
   - evaluated candidate level;
   - assistance;
   - recency;
   - retest status;
   - contextual diversity.

6. Identify:
   - supporting Evidence;
   - contradicting Evidence;
   - independent retest Evidence;
   - teaching-only Evidence.

7. Determine evidence sufficiency:
   LOW / MEDIUM / HIGH.

8. Determine whether evidence supports:
   UNTESTED / EXPOSED / WEAK /
   DEVELOPING / STRONG.

9. Determine verification freshness:
   CURRENT / AGING / RETEST_DUE.

10. Evaluate unresolved Breakpoints.

11. Determine retest eligibility and reason.

12. Apply conservative parent aggregation.

13. Persist:
   - mastery projection;
   - supporting Evidence links;
   - policy version;
   - freshness;
   - explanation inputs.

14. Compare with previous projection.

15. If state/freshness materially changed:
   persist MasteryTransition.

16. Emit downstream retest recommendation updates
   when appropriate.
```

No generative model decides the final state.

---

# 92. Deterministic responsibilities

Deterministic Mastery policy owns:

- valid Evidence selection;
- state criteria;
- assistance interpretation;
- independence hierarchy;
- contradiction handling;
- contextual diversity;
- freshness;
- parent aggregation;
- retest eligibility;
- supporting source IDs;
- policy version;
- state persistence.

---

# 93. AI responsibilities

AI may assist with:

- candidate-facing explanation wording;
- concise Evidence summaries;
- retest-context suggestions;
- mapping provisional language to curated concept candidates;
- grouping related Evidence summaries.

AI may not arbitrarily output:

```text
mastery = STRONG
```

as authoritative state.

If AI-generated explanation conflicts with deterministic Mastery inputs:

discard the explanation.

---

# 94. Mastery policy versioning

Every projection should be attributable to:

```text
mastery_policy_version
```

CounterQ will almost certainly recalibrate this policy after launch.

Example:

```text
mastery_policy_v1
mastery_policy_v2
```

If v2 changes STRONG requirements:

recompute projections.

Do not rewrite Evidence.

---

# 95. Mastery explanation inputs

Candidate-facing explanations should be grounded in structured inputs such as:

```text
state
freshness
evidence_sufficiency

supporting_evidence_ids[]
contradicting_evidence_ids[]

independent_demonstration_count
distinct_context_count
recent_retest_result

unresolved_breakpoint_ids[]

retest_reason
```

Exact persisted fields follow `DATA_MODEL.md`.

Do not store unrestricted LLM prose as the sole reason a state exists.

---

# 96. Mastery policy v1 decision floor

Phase 1 needs deterministic behavior even before statistical calibration exists.

The following is a **policy floor**, not a numeric scoring formula.

| State | Minimum interpretation |
|---|---|
| `UNTESTED` | No admitted valid Evidence for the target |
| `EXPOSED` | Evidence exists, but there is not yet enough trustworthy directional Evidence for WEAK or DEVELOPING |
| `WEAK` | Qualifying strong negative pattern exists under the WEAK policy floor and is not already resolved by stronger current Evidence |
| `DEVELOPING` | Meaningful understanding is demonstrated, but consistency/context/independence is insufficient for STRONG, or the history is materially mixed |
| `STRONG` | Meets the conservative STRONG policy floor across distinct contexts with no unresolved recent correctness-critical negative pattern |

When several rules appear applicable, prefer the state that best expresses **current uncertainty** rather than the most flattering or most punitive state.

Examples:

```text
one strong positive independent demonstration
→ DEVELOPING
```

```text
one ambiguous negative observation
→ EXPOSED
```

```text
failed independent retest with strong negative Evidence
→ WEAK
```

```text
two qualifying positive demonstrations across distinct contexts,
including at least one independent,
with no unresolved strong contradiction
→ eligible for STRONG
```

The policy must remain versioned through `mastery_policy_version`.

---

# 97. Example A — One correct independent answer

Candidate encounters sliding-window boundary reasoning in one Simulation.

Evidence:

- positive;
- independent;
- strong;
- one session;
- one problem;
- no contradiction.

Expected:

```text
Mastery:
DEVELOPING

Evidence sufficiency:
LOW or MEDIUM

Freshness:
CURRENT
```

Why not STRONG?

Because CounterQ has evidence of real understanding but does not yet know whether it survives another context.

---

# 98. Example B — Repeated independent success

Session 1:

- candidate correctly defends sliding-window invariant.

Session 2:

- different problem;
- candidate implements invariant correctly;
- handles counterexample.

Session 3:

- transfer question;
- adapts concept successfully.

No important recent negative Evidence.

Expected:

```text
STRONG
```

with:

```text
evidence_sufficiency = HIGH
freshness = CURRENT
```

This is what STRONG should mean.

---

# 99. Example C — Coach teaching

Candidate cannot explain hash-table worst-case behavior.

Evidence:

```text
NEGATIVE
INDEPENDENT
```

CounterQ teaches concept.

Candidate immediately repeats it correctly.

New Evidence:

```text
POSITIVE
DIRECTLY_TAUGHT
```

Expected:

```text
WEAK
or
DEVELOPING
```

depending on the **pre-assistance validated Evidence** and prior history.

The `DIRECTLY_TAUGHT` success itself cannot justify `WEAK` or `STRONG`; it mainly creates learning/retest context.

Freshness/retest:

```text
RETEST_DUE
```

Definitely not STRONG.

---

# 100. Example D — Independent self-correction

Candidate says:

> "Two pointers means O(n²)."

Then, before CounterQ intervenes:

> "Actually, no. Each pointer only moves forward, so total movement is O(n)."

Evidence:

- initial negative/mixed signal;
- strong positive independent self-correction;
- correct final reasoning.

If no repeated misconception exists:

likely:

```text
DEVELOPING
```

rather than WEAK.

The initial error remains visible in Evidence history.

---

# 101. Example E — Strong but stale

Six months ago candidate repeatedly demonstrated strong hashing reasoning.

No recent relevant Evidence.

Expected:

```text
Mastery:
STRONG

Freshness:
RETEST_DUE
```

Not:

```text
WEAK
```

CounterQ is saying:

> "You demonstrated this strongly, but I have not verified it recently."

That is epistemically cleaner.

---

# 102. Example F — Contradictory Evidence

Session A:

```text
POSITIVE
INDEPENDENT
```

Session B:

```text
NEGATIVE
INDEPENDENT
```

Session C:

```text
POSITIVE
AFTER_PROBE
```

Expected:

```text
DEVELOPING
```

Likely explanation:

> Your complexity reasoning has been inconsistent across contexts. You have recent positive evidence, but it is not yet stable enough to classify as Strong.

---

# 103. Example G — Parent aggregation

Children:

```text
Sliding Window

Window validity
WEAK

Boundary monotonicity
STRONG

State maintenance
DEVELOPING
```

Parent:

```text
DEVELOPING
```

Why?

Because the candidate demonstrates meaningful sliding-window competence but still has a central weakness in validity reasoning.

Parent STRONG would hide an important gap.

---

# 104. Example H — Skill across concepts

Candidate shows:

```text
Two pointers:
strong amortized complexity reasoning

Hashing:
weak average/worst-case reasoning

Recursion:
strong stack-space reasoning
```

Skill projection:

```text
COMPLEXITY_REASONING
DEVELOPING
```

Explanation:

> You reason correctly about complexity in several contexts, but guarantees around data-structure operations remain inconsistent.

This is more useful than one topic-specific score.

---

# 105. Example I — Failed independent retest

Previous:

- Dijkstra negative-edge misconception;
- Coach teaching;
- Breakpoint remains retest pending.

Later Simulation Quick Drill:

Candidate again claims Dijkstra works with negative edges.

Evidence:

```text
NEGATIVE
INDEPENDENT
RETEST
```

Expected:

```text
Mastery:
WEAK

Breakpoint:
reinforced / unresolved
```

This is strong evidence that the teaching did not persist.

---

# 106. Example J — Memorized exact answer

Candidate previously learned:

> "`unordered_map` is average O(1), worst case O(n)."

Later CounterQ asks the exact same question.

Candidate recites it correctly.

But on a different problem they still claim:

> "The algorithm is guaranteed O(n) because the map is constant-time."

Expected:

not STRONG.

Likely:

```text
DEVELOPING
```

or potentially WEAK depending on Evidence strength.

The candidate retained the sentence.

Transfer of understanding remains uncertain.

---

# 107. Mastery Map wireframe

```text
┌──────────────────────────────────────────────────────────┐
│ Mastery                                                  │
│ What CounterQ has evidence you can defend independently. │
├──────────────────────────────────────────────────────────┤
│                                                          │
│ TECHNICAL CONCEPTS                                       │
│                                                          │
│ Strong                                                   │
│ ──────────────────────────────────────────────────────── │
│ Sliding-window boundary reasoning                        │
│ BFS traversal                                            │
│                                                          │
│ Developing                                               │
│ ──────────────────────────────────────────────────────── │
│ Hash-table complexity            Limited evidence        │
│ Binary-search invariants                                 │
│                                                          │
│ Needs work                                               │
│ ──────────────────────────────────────────────────────── │
│ Dijkstra assumptions                                     │
│                                                          │
│ Retest ready                                             │
│ ──────────────────────────────────────────────────────── │
│ Hash-table worst-case complexity                         │
│                                      [ CounterQ me again ]│
│                                                          │
├──────────────────────────────────────────────────────────┤
│ INTERVIEW SKILLS                                         │
│                                                          │
│ Strong                                                   │
│ Debugging                                                │
│                                                          │
│ Developing                                               │
│ Complexity reasoning                                     │
│ Explanation clarity                                      │
│                                                          │
│ Needs work                                               │
│ Adaptability                                             │
└──────────────────────────────────────────────────────────┘
```

No overall percentage is required.

---

# 108. Concept detail wireframe

```text
┌───────────────────────────────────────────────┐
│ Hash-table worst-case complexity              │
│                                               │
│ DEVELOPING                                    │
│ Limited evidence                              │
│                                               │
│ Why this state?                               │
│                                               │
│ You initially treated lookup as a guaranteed │
│ O(1) operation. In a later interview, you    │
│ independently explained how collisions can   │
│ degrade worst-case lookup.                   │
│                                               │
│ Evidence                                      │
│                                               │
│ Aug 21 · Simulation                           │
│ Needs work · Independent                      │
│ [ View interview ]                            │
│                                               │
│ Aug 28 · Coach                                │
│ Correct after probe                           │
│ [ View interview ]                            │
│                                               │
│ Sep 03 · Simulation                           │
│ Strong demonstration · Independent retest     │
│ [ View interview ]                            │
│                                               │
│ Freshness                                     │
│ Current                                       │
│                                               │
│ Next                                          │
│ Verify this once more in another context.     │
│                                               │
│ [ CounterQ me again ]                         │
│                                               │
│ This assessment seems wrong                   │
└───────────────────────────────────────────────┘
```

---

# 109. Component hierarchy

Conceptual React structure:

```text
MasteryPage
├── MasteryHeader
│   ├── MasteryIntro
│   └── EvidenceCoverageSummary
│
├── TechnicalConceptSection
│   ├── MasteryStateGroup
│   │   └── MasteryConceptCard
│   └── RetestReadyGroup
│       └── RetestConceptCard
│
├── SkillDimensionSection
│   ├── MasteryStateGroup
│   │   └── SkillMasteryCard
│
└── MasteryDetailDrawer
    ├── MasteryStateHeader
    ├── EvidenceSufficiency
    ├── WhyThisState
    ├── FreshnessSection
    ├── EvidenceTimeline
    │   └── EvidenceHistoryItem
    ├── BreakpointSection
    ├── RetestRecommendation
    └── MasteryActions
        ├── CounterQAgainButton
        └── DisputeAssessmentButton
```

Do not build a complex game-style tree for Phase 1.

---

# 110. Candidate terminology

Internal state:

```text
UNTESTED
EXPOSED
WEAK
DEVELOPING
STRONG
```

Recommended candidate-facing wording:

| Internal | Candidate-facing |
|---|---|
| UNTESTED | Not tested |
| EXPOSED | Limited evidence |
| WEAK | Needs work |
| DEVELOPING | Developing |
| STRONG | Strong |

Freshness:

| Internal | Candidate-facing |
|---|---|
| CURRENT | Current |
| AGING | Not tested recently |
| RETEST_DUE | Retest due |

Use plain language.

Do not require candidates to learn CounterQ's database vocabulary.

---

# 111. Mastery does not require readiness score

Mastery must remain useful without:

```text
Interview Readiness = 76
```

A future readiness projection may consume:

- concept mastery;
- skill mastery;
- freshness;
- target role.

But Mastery should remain independently explainable.

Do not distort the mastery architecture to optimize for one future score.

---

# 112. Personalized problem selection

Mastery may later help CounterQ:

- choose relevant interview problems;
- increase probability of natural retesting;
- select transfer contexts;
- avoid repeatedly testing already well-established areas.

But selection and grading remain separate.

Rule:

> **Personalize what gets tested, not what counts as correct.**

---

# 113. Candidate disagreement

Concept/skill detail may expose:

> **This assessment seems wrong**

The action should reference:

- current Mastery projection;
- supporting Evidence IDs;
- candidate optional feedback.

Candidate cannot manually change:

```text
Needs work
→ Strong
```

Mastery remains evidence-derived.

---

# 114. Internal reviewer view

Internal tooling should eventually allow inspection of:

- all contributing Evidence;
- contradicting Evidence;
- invalidated Evidence;
- independence;
- assistance;
- source sessions;
- contextual diversity;
- freshness;
- Breakpoints;
- Mastery policy version;
- state-recompute explanation;
- retest history.

Candidate UX remains simple.

---

# 115. Telemetry

Useful events include:

- `mastery_opened`;
- `mastery_concept_opened`;
- `mastery_skill_opened`;
- `mastery_evidence_history_opened`;
- `counterq_me_again_clicked`;
- `mastery_assessment_disputed`;
- `retest_started`;
- `retest_completed`.

Do not include:

- transcript text;
- source code;
- private candidate content;

in general analytics payloads.

Use canonical IDs.

---

# 116. Product-quality metrics

Useful Mastery quality metrics include:

- percentage of active users opening Mastery;
- concept-detail open rate;
- retest conversion;
- retest completion;
- Breakpoint resolution rate;
- candidate agreement with state explanation;
- percentage of STRONG concepts backed by multiple contexts;
- percentage of STRONG concepts later contradicted;
- false-WEAK dispute rate;
- Coach → retest → independent-success rate.

Two especially important metrics:

> **False STRONG rate**

and:

> **False WEAK rate**

---

# 117. False STRONG rate

If CounterQ labels concepts STRONG too easily, later retests will expose gaps.

That damages trust.

Phase 1 should optimize against premature strength claims.

It is better to say:

```text
DEVELOPING
```

for too long than:

```text
STRONG
```

without enough evidence.

---

# 118. False WEAK rate

The opposite matters too.

CounterQ should not classify someone WEAK because of:

- one odd sentence;
- transcription noise;
- one syntax error;
- one self-corrected slip.

When evidence is limited:

prefer:

```text
EXPOSED
```

When evidence is genuinely mixed:

prefer:

```text
DEVELOPING
```

---

# 119. Mastery calibration principle

Phase 1 should intentionally bias toward epistemic humility.

Prefer:

```text
DEVELOPING
```

over unjustified:

```text
STRONG
```

Prefer:

```text
EXPOSED
```

over unjustified:

```text
WEAK
```

As Evidence accumulates, classification can become stronger.

---

# 120. Offline evaluation

Future Mastery benchmarking should support examples containing:

- canonical Evidence set;
- concept/skill;
- candidate level;
- assistance history;
- timestamps;
- contextual diversity;
- previous state;
- expert-expected state;
- system-computed state;
- system explanation;
- retest recommendation;
- future retest result.

This will allow CounterQ to evaluate:

> Did the Mastery projection actually predict later independent performance?

The full evaluation framework is outside this document.

---

# 121. Phase 1 implementation order

Recommended implementation sequence:

## A. Curated ontology

Establish the canonical Concepts needed by the initial curated problems.

Seed/use the SkillDimension vocabulary exactly as frozen in `DATA_MODEL.md`.

Do not model all DSA upfront and do not create a second mastery-specific skill taxonomy.

---

## B. Evidence mapping

Ensure every validated Evidence produced by the vertical slice references:

- canonical Concept IDs;
- SkillDimension IDs;
- polarity;
- independence;
- source provenance.

---

## C. Deterministic Mastery Engine

Implement:

- grouping;
- sufficiency;
- conservative states;
- freshness;
- Evidence links;
- recomputation.

---

## D. Simple Mastery UX

Build list/group/detail surfaces.

Do not begin with a graph.

---

## E. RetestRecommendation

Connect:

> **CounterQ me again**

to mastery gaps/Breakpoints.

---

## F. Personalized selection

Only after Mastery has demonstrated real value should it meaningfully influence broad interview problem selection.

---

# 122. Technical Core Interaction Spike requirement

Mastery UI is **not required** for the first Core Interaction Spike.

However, the spike must already persist Evidence with:

- canonical Concept IDs through `evidence_concepts`;
- canonical SkillDimension IDs through `evidence_skills`;
- polarity;
- strength;
- independence;
- source provenance;
- validation policy/evaluator provenance.

Interview level/mode remain reconstructable through the Evidence → InterviewSession → InterviewConfiguration path.

Otherwise later Mastery would require unreliable transcript re-analysis.

This is a core architecture acceptance requirement.

---

# 123. Phase 1 scope cuts

Explicitly defer:

- numeric mastery percentages;
- psychometric claims;
- Elo;
- Bayesian learner models;
- complex decay formulas;
- giant knowledge graphs;
- fully automated DSA curriculum;
- hundreds of tiny mastery concepts;
- automatically generated canonical ontology;
- spaced-repetition sophistication;
- leaderboards;
- percentile rankings;
- peer comparison;
- recruiter-facing mastery profiles;
- personality scoring;
- emotion scoring;
- confidence/personality analysis;
- university dashboards;
- cross-role mastery beyond Phase 1 coding interviews.

Keep Mastery explainable and evidence-first.

---

# 124. Acceptance criteria

Phase 1 Mastery is acceptable only when all of the following hold.

---

## Evidence integrity

- Every Mastery state references valid Evidence.
- Invalidated Evidence cannot continue affecting Mastery.
- Deleted-session Evidence disappears through recomputation.
- Assisted and independent Evidence remain distinguishable.
- Diagnostic probe success remains distinguishable from hint-assisted success.
- Self-correction remains visible in supporting history.

---

## Conservative classification

- One ordinary correct answer does not automatically create STRONG.
- Under policy v1, a single session does not create technical-concept STRONG.
- Technical-concept STRONG requires qualifying positive Evidence across at least two distinct contexts, including at least one fully independent demonstration.
- Skill STRONG requires repeated evidence across multiple sessions/technical contexts.
- One ordinary error does not automatically create WEAK.
- Asking for/receiving a strong hint does not itself create WEAK; pre-assistance negative Evidence must support the gap.
- One qualifying highly diagnostic negative pattern or multiple aligned moderate negative Evidence items can create WEAK.
- Immediate post-teaching repetition cannot create STRONG.
- Repeated independent Evidence across contexts can create STRONG.
- A failed independent retest has substantial negative influence.
- EXPOSED is used when CounterQ genuinely lacks enough information.
- DEVELOPING is used when Evidence is meaningful but not yet stable.

---

## Freshness

- Time alone never changes STRONG directly into WEAK.
- Freshness is represented separately.
- Stale strong concepts can become RETEST_DUE.
- Retesting can refresh verification without deleting old history.

---

## Rebuildability

- Mastery can be recomputed entirely from valid Evidence.
- Physical persistence uses `concept_mastery`, `skill_mastery`, their separate evidence association tables, `mastery_transitions`, and `mastery_transition_evidence` exactly as frozen.
- Freshness/evidence sufficiency do not require unapproved Phase 1 schema columns.
- Mastery policy changes do not rewrite historical Evidence.
- Taxonomy remapping does not destroy original provenance.
- Mastery transitions are auditable.

---

## Explainability

- Candidate can understand why each visible state exists.
- Supporting and contradicting Evidence can be inspected.
- Candidate-facing explanations do not require chain-of-thought.
- No numeric percentage is necessary.
- Candidate can see when Evidence is limited.

---

## Concepts and skills

- Concept Mastery and Interview Skill Mastery remain separate.
- One Evidence item may contribute explicitly to both.
- Skill dimensions exactly align with the frozen `skill_dimensions` vocabulary.
- Parent summaries do not create synthetic Evidence.
- Child weaknesses remain visible.
- Candidate-facing grouping may simplify labels without merging canonical SkillDimensions.

---

## Assistance

- Strong hints cannot create independent Evidence.
- Direct teaching cannot create immediate STRONG mastery.
- A taught weakness remains retest-eligible.
- Successful independent retest can materially improve Mastery.

---

## Personalization safety

- Historical weakness may prioritize a relevant retest.
- Historical weakness cannot influence technical correctness judgment.
- Current strong Evidence can contradict and eventually override older weakness.
- Previously STRONG concepts are not probed solely to manufacture activity.

---

## Retesting

- Retests prefer different contexts where practical.
- Exact question repetition does not strongly inflate mastery.
- `CounterQ me again` produces a legitimate RetestRecommendation.
- Failed independent retests reinforce unresolved weaknesses.
- Successful independent retests can support Breakpoint resolution.

---

## Product quality

- Sparse Evidence produces sparse Mastery.
- Mastery works without an overall readiness score.
- Candidate-facing states remain understandable.
- STRONG remains conservative enough to mean something.
- Mastery Engine is not on the live latency-sensitive Examiner path.

---

# 125. Final Mastery principles

1. **Mastery is evidence, not intuition.**

2. **Solving is not mastering.**

3. **UNTESTED is not WEAK.**

4. **EXPOSED means CounterQ does not yet know enough.**

5. **One answer rarely proves STRONG.**

6. **One mistake rarely proves WEAK.**

7. **Independent Evidence matters most.**

8. **AFTER_PROBE Evidence can still be strong.**

9. **A Probe is not a hint.**

10. **Teaching creates learning Evidence, not instant mastery.**

11. **Retest what was taught.**

12. **Prefer different contexts when retesting.**

13. **Independent self-correction is meaningful Evidence.**

14. **Contradictory Evidence should remain visible.**

15. **Recency affects verification freshness, not historical truth.**

16. **Technical concepts and interview skills are different projections.**

17. **Parent concepts should aggregate conservatively.**

18. **Ontology relationships guide testing; they do not create Evidence.**

19. **Historical weakness can influence selection, never grading.**

20. **No fake precision.**

21. **STRONG should be difficult to earn and meaningful when earned.**

22. **WEAK should require genuine diagnostic Evidence, not model suspicion.**

23. **Mastery must remain rebuildable.**

24. **Personalization must never create circular grading.**

25. **The candidate should always be able to answer: "Why does CounterQ think this?"**

26. **Mastery policy must align with the frozen Concept/Skill taxonomy rather than inventing a parallel one.**

27. **Strong hints do not prove weakness; validated pre-assistance evidence does.**

28. **Under policy v1, technical-concept STRONG requires verification beyond one session.**

29. **Freshness and evidence sufficiency are derived semantics unless a future Data Model revision materializes them.**

30. **Parent summaries may aggregate projections for UX, but they must never create synthetic Evidence.**

The governing product rule is:

> **CounterQ should never say "you mastered this" because the candidate saw it once.**

And the standard for durable strength is:

> **Mastery means the candidate has repeatedly demonstrated that the understanding survives questioning, implementation, and new contexts with little or no assistance.**
