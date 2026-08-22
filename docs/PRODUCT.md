# CounterQ — Product Specification

**Document:** `docs/PRODUCT.md`  
**Status:** Source of Truth  
**Product:** CounterQ  
**Initial Vertical:** Technical Coding Interview Preparation  
**Last Updated:** August 2026

---

## 1. Purpose of this document

This document defines what CounterQ is, what problem it exists to solve, the core product behavior that must remain true across implementations, and the principles that should guide product and engineering decisions.

This document is intentionally more durable than individual implementation plans.

If an implementation decision conflicts with this document, the implementation should change unless this document is explicitly revised.

---

# 2. Product thesis

CounterQ is an AI-powered technical interview training system designed to discover whether a candidate genuinely understands what they are saying and coding.

Its central behavior is:

> **Observe what a candidate does, listen to what they claim, challenge those claims intelligently, discover where their understanding breaks, preserve evidence of those weaknesses, and retest them later.**

CounterQ is not primarily a coding-question platform.

CounterQ is not primarily a chatbot.

CounterQ is not primarily an AI tutor.

CounterQ is an **adaptive technical examiner with memory**.

The product should help answer a question that ordinary practice platforms usually cannot:

> **If an interviewer starts questioning this candidate about their decisions, how deep does their understanding actually go?**

---

# 3. The problem

Technical interview preparation tools are heavily optimized around solving problems.

Candidates are trained to:

- recognize known patterns;
- produce accepted code;
- memorize standard approaches;
- complete DSA sheets;
- measure progress using solved-question counts;
- optimize for online judge acceptance.

Actual interviews evaluate more than this.

Candidates are also expected to:

- interpret ambiguous problem statements;
- communicate their reasoning;
- state assumptions;
- justify data structures;
- explain algorithmic trade-offs;
- reason about complexity;
- defend implementation decisions;
- identify edge cases;
- debug under pressure;
- respond when assumptions are challenged;
- adapt when constraints change;
- distinguish average-case guarantees from worst-case guarantees;
- demonstrate understanding rather than recognition.

A candidate can therefore be competent at solving a problem and still perform poorly in an interview.

The deeper problem is that candidates often do not know **where their understanding becomes fragile**.

A successful solution hides this weakness.

CounterQ exists to expose it.

---

# 4. Target user

The initial user is a candidate preparing for software-engineering technical interviews involving coding and algorithmic reasoning.

Primary initial audience:

- university students preparing for placements;
- internship candidates;
- new-graduate software-engineering candidates;
- early-career engineers preparing for coding interviews.

CounterQ Phase 1 is not intended to be a general interview platform.

The product may eventually support other technical and non-technical interview formats, but those future verticals must not dilute the initial product.

---

# 5. Core job to be done

When preparing for a technical coding interview:

> **Help me discover whether I can explain, defend, debug, and adapt my solution when another technically competent person starts questioning my reasoning.**

A secondary job is:

> **Remember the exact kinds of reasoning failures I have demonstrated previously and verify whether I have actually improved.**

---

# 6. Product promise

CounterQ should help the candidate answer:

1. What did I understand correctly?
2. What did I claim that was inaccurate or insufficiently justified?
3. Where did my reasoning break when challenged?
4. Which concepts repeatedly cause difficulty?
5. Did I independently correct a weakness or require assistance?
6. Can I transfer the idea when the problem changes?
7. Which weaknesses should be retested?
8. Am I becoming more interview-ready across sessions?

CounterQ must support these conclusions with evidence from the interview rather than arbitrary model-generated scoring.

---

# 7. Core product loop

The fundamental CounterQ loop is:

**Attempt → Observe → Claim → Probe → Evidence → Assess → Remember → Retest**

Expanded:

1. Candidate attempts an interview problem.
2. CounterQ observes speech, code and relevant interaction events.
3. CounterQ identifies technically meaningful claims or decisions.
4. CounterQ decides whether a claim deserves investigation.
5. CounterQ asks a targeted counter-question where appropriate.
6. Candidate responds verbally or through code.
7. CounterQ records evidence about the candidate's understanding.
8. CounterQ updates the candidate's mastery state.
9. A future interview deliberately retests important weaknesses.

Every major feature should reinforce this loop.

---

# 8. What makes CounterQ different

## 8.1 Selective interrogation

CounterQ must not ask a follow-up question after every candidate statement.

It should distinguish between:

- harmless wording;
- correct statements;
- uncertain statements;
- technically important claims;
- suspicious assumptions;
- implementation decisions;
- potential misconceptions;
- genuine breakpoints.

CounterQ should interrupt only when doing so meaningfully improves the interview.

**Probe precision is more important than probe volume.**

---

## 8.2 Claim-aware questioning

CounterQ should reason about what the candidate actually claimed.

Example:

Candidate:

> "I'll use `unordered_map` because lookup is always O(1)."

CounterQ should notice that the word **always** changes the technical claim.

A good probe is:

> "You said always. Is that actually guaranteed?"

A poor response is:

> "`unordered_map` lookup is average O(1), but worst case O(n)."

The first tests the candidate.

The second teaches the candidate before testing them.

CounterQ should default to testing before revealing.

---

## 8.3 Code-aware questioning

CounterQ must understand meaningful implementation behavior rather than treat the editor as passive text.

Example:

A candidate writes sliding-window logic where the left pointer can move backwards.

CounterQ should not immediately reveal:

> "Your left pointer can move backwards."

A better probe is:

> "What guarantees that your left pointer never moves backwards?"

The candidate must demonstrate whether they understand the invariant.

---

## 8.4 Evidence-backed assessment

CounterQ should never reduce an interview to unsupported statements such as:

> "Algorithm skill: 7.8/10"

An assessment should instead be traceable to evidence such as:

- candidate claim;
- code snapshot;
- code diff;
- probe asked;
- candidate response;
- test behavior;
- correction;
- hint dependency;
- constraint mutation response;
- timestamps;
- relevant concept.

Scores, if presented, must be derived from structured evidence rather than invented independently by a language model.

---

## 8.5 Cross-session memory

A weakness should not disappear when an interview ends.

If a candidate demonstrates confusion about:

- hash table complexity;
- binary-search invariants;
- recursion stack space;
- integer overflow;
- graph visitation;
- sliding-window conditions;
- mutable shared state;
- edge-case reasoning;

CounterQ should preserve the evidence.

Future sessions should be capable of determining whether the weakness remains.

---

# 9. Product modes

CounterQ initially supports two interview modes.

## 9.1 Simulation Mode

Purpose:

> Reproduce the pressure and behavioral expectations of a real technical interview.

CounterQ should:

- behave primarily as an interviewer;
- avoid unnecessary teaching;
- avoid revealing answers prematurely;
- allow reasonable silence;
- ask concise questions;
- challenge important claims;
- let mistakes develop far enough to expose understanding;
- provide detailed teaching primarily after the interview.

The candidate should feel assessed rather than continuously assisted.

---

## 9.2 Coach Mode

Purpose:

> Develop interview reasoning through guided but still interrogative practice.

CounterQ may:

- intervene slightly earlier;
- provide progressively stronger prompts;
- ask metacognitive questions;
- help the candidate recover from a dead end;
- explain concepts when appropriate;
- pause and teach after sufficient evidence has been gathered.

Coach Mode should still make the candidate think.

It must not become an answer-generation assistant.

Detailed behavioral differences belong in:

`docs/examiner/COACH_VS_SIMULATION.md`

---

# 10. The CounterQ interview

A CounterQ coding interview is a structured, stateful session.

A typical session includes:

1. interview setup;
2. problem comprehension;
3. candidate restatement;
4. approach exploration;
5. complexity discussion where appropriate;
6. implementation;
7. testing and debugging;
8. targeted cross-questioning;
9. edge-case discussion;
10. changed constraint or transfer challenge where useful;
11. interview completion;
12. evidence-backed assessment.

The exact order is not rigid.

The examiner may WAIT, OBSERVE, ASK or PROBE depending on candidate behavior.

---

# 11. Core intelligence model

CounterQ is composed conceptually of four primary systems.

## 11.1 Observation Engine

The Observation Engine constructs a continuously updated understanding of what is happening in the interview.

Possible inputs include:

- streaming transcript;
- voice turn boundaries;
- current source code;
- meaningful code diffs;
- run events;
- submit events;
- test results;
- problem context;
- interview state;
- elapsed time;
- prior weaknesses;
- selected visual context where explicitly necessary.

Its role is not merely to collect raw events.

It should convert relevant events into usable observations.

Examples:

- candidate asserted constant-time lookup;
- candidate changed from brute force to hashing;
- candidate removed a boundary check;
- candidate repeatedly modified the same invariant;
- candidate ran code after a significant structural change;
- candidate verbally claimed a complexity inconsistent with the implementation.

---

## 11.2 Examiner Engine

The Examiner Engine determines what CounterQ should do next.

It reasons about:

- candidate claims;
- technical correctness;
- uncertainty;
- importance;
- likely misconception;
- interview timing;
- current interview state;
- existing evidence;
- previous probes;
- probe value;
- interruption cost.

The examiner's possible high-level actions include:

- `WAIT`
- `OBSERVE`
- `ASK`
- `PROBE`

The examiner should not be free to arbitrarily control the whole product.

Its decisions operate within the deterministic CounterQ interview state machine.

---

## 11.3 Evidence Engine

The Evidence Engine stores structured evidence generated during the interview.

Evidence should answer:

- what happened;
- what concept was involved;
- what the candidate claimed or did;
- what CounterQ asked;
- how the candidate responded;
- whether the candidate corrected themselves;
- how much assistance was required;
- what conclusion can reasonably be drawn.

Evidence is the foundation of reports and mastery updates.

---

## 11.4 Memory / Mastery Engine

The Mastery Engine aggregates evidence across interviews.

Concept states initially include:

- `UNTESTED`
- `EXPOSED`
- `WEAK`
- `DEVELOPING`
- `STRONG`

Mastery must not be updated merely because a candidate encountered a concept.

It should depend on demonstrated evidence.

Evidence quality matters.

For example:

Independent explanation under challenge is stronger evidence than selecting a correct multiple-choice answer.

Independent correction is stronger evidence than correction after a direct hint.

Successful transfer under changed constraints is stronger evidence than repeating a memorized solution.

---

# 12. Probe strategies

CounterQ may use strategies including:

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

A strategy describes the purpose of a probe.

It is not itself the natural-language question.

Detailed semantics belong in:

`docs/examiner/PROBE_STRATEGIES.md`

---

# 13. CounterMap

The CounterMap is a session-specific interactive representation of the interview's reasoning path.

Its purpose is to make the interview explainable.

Conceptually, a branch may contain:

**Claim → Probe → Response → Assessment → Breakpoint**

Not every spoken sentence becomes a node.

The graph should capture meaningful reasoning events.

Examples include:

- technical claims;
- assumptions;
- examiner probes;
- candidate responses;
- implementation decisions;
- corrections;
- misconceptions;
- breakpoints;
- demonstrated strengths.

The CounterMap is not a visualization generated from arbitrary AI prose.

It is a view over structured session data.

Detailed specification belongs in:

`docs/data/COUNTERMAP.md`

---

# 14. Mastery Map

The Mastery Map is the persistent cross-session representation of the candidate's demonstrated understanding.

It should answer questions such as:

- Which concepts have actually been tested?
- Which concepts repeatedly break under questioning?
- Where has the candidate improved?
- Which weaknesses have not been retested?
- Which strengths have survived multiple interviews?
- Which areas require transfer testing rather than repetition?

The Mastery Map should be evidence-derived.

It should not require a graph database merely because it is visualized as a graph.

PostgreSQL remains the initial system of record.

Detailed specification belongs in:

`docs/data/MASTERY_MODEL.md`

---

# 15. Retesting

Retesting is a first-class product behavior.

CounterQ should eventually challenge important weaknesses again using:

- another problem;
- a related implementation;
- a changed constraint;
- a conceptual question;
- an edge case;
- a transfer scenario.

Retesting should avoid simply repeating the exact original question whenever possible.

The goal is to determine whether understanding improved, not whether the candidate memorized feedback.

---

# 16. Realtime experience

The interview must feel like a live technical conversation.

The candidate should not experience a repeated pattern of:

**speak → wait several seconds → receive long AI paragraph**

Required qualities include:

- natural turn-taking;
- low response latency;
- strong Indian-English understanding;
- interruption support;
- barge-in handling;
- appropriate silence;
- concise spoken questions;
- minimal awkward dead time.

CounterQ should reason ahead whenever possible.

While the candidate speaks, downstream systems may begin analyzing partial transcript.

While the candidate codes, meaningful code events may be analyzed asynchronously.

Before an interview starts, CounterQ should prepare relevant technical context.

The architecture should therefore optimize cost **around** the realtime experience rather than degrading the realtime experience to minimize cost.

---

# 17. Interview Pack

Before an interview, CounterQ may generate or retrieve an Interview Pack.

An Interview Pack may contain:

- canonical approaches;
- alternate approaches;
- expected time complexity;
- expected space complexity;
- important concepts;
- implementation invariants;
- common misconceptions;
- likely failure modes;
- useful edge cases;
- possible constraint mutations;
- useful counterexamples;
- candidate-appropriate probes.

The Interview Pack is internal examiner context.

It is not an answer sheet shown to the candidate during Simulation Mode.

Interview Packs should reduce realtime reasoning requirements without forcing the interviewer into a predetermined script.

---

# 18. AI architecture philosophy

CounterQ must be:

> **Software powered by AI**

not:

> **One giant AI prompt surrounded by software**

Deterministic software owns:

- interview state;
- timers;
- session lifecycle;
- session budgets;
- probe limits;
- permissions;
- billing;
- code snapshots;
- event ordering;
- graph structure;
- evidence persistence;
- score aggregation;
- mastery-state transitions;
- retest scheduling.

AI owns tasks requiring semantic reasoning, including:

- candidate-language understanding;
- claim extraction;
- semantic code understanding;
- explanation evaluation;
- misconception detection;
- conceptual target selection;
- natural interviewer phrasing;
- evidence-backed feedback generation.

Model output should be constrained by schemas and product state wherever practical.

---

# 19. Model philosophy

CounterQ must remain model-agnostic.

No core product concept should depend unnecessarily on a single model provider.

Different workloads may use different model classes.

Examples:

### Lightweight models

Suitable for:

- extraction;
- classification;
- summarization;
- topic tagging;
- simple claim detection;
- structured transformations.

### Reasoning models

Suitable for:

- algorithm analysis;
- code semantics;
- misconception evaluation;
- probe selection;
- transfer reasoning.

### Strongest available reasoning models

Reserved for cases where weaker models are insufficient, such as:

- ambiguous candidate reasoning;
- complex implementation analysis;
- difficult correctness disputes;
- high-value post-interview synthesis.

Realtime voice may use a specialized realtime model independently from deeper examiner reasoning.

---

# 20. Cost philosophy

Each interview type must operate within explicit computational budgets.

Budgets may include:

- maximum interview duration;
- maximum examiner probes;
- maximum deep-reasoning calls;
- maximum strongest-model calls;
- maximum vision calls;
- soft monetary budget;
- hard monetary budget.

Cost should be reduced through:

- model routing;
- precomputation;
- prompt caching;
- compact state;
- event-triggered analysis;
- code diffs;
- selective snapshots;
- selective vision;
- deterministic logic.

However:

> **The interviewer experience must not be degraded merely to save a small amount of inference cost.**

The correct target is efficient intelligence, not cheap mediocrity.

---

# 21. Product quality principles

## 21.1 Test before teaching

When reasonable, CounterQ should establish evidence before revealing the answer.

## 21.2 Do not over-interrupt

Silence and observation are legitimate examiner actions.

## 21.3 Ask one useful thing at a time

Spoken interviewer turns should normally be concise.

## 21.4 Challenge the candidate's reasoning, not their confidence

CounterQ should be rigorous without being adversarial for theatrical effect.

## 21.5 Separate uncertainty from error

A candidate saying "I think this is O(n)" is different from confidently claiming an incorrect guarantee.

## 21.6 Consider context

A technically imperfect shorthand may not deserve interruption if it is irrelevant to the current interview.

## 21.7 Preserve provenance

Important conclusions should be traceable to source evidence.

## 21.8 Avoid fake precision

Do not imply measurement precision the system cannot support.

## 21.9 Candidate independence matters

Whether a candidate solved something independently, after probing, or after direct help must affect assessment.

## 21.10 Transfer matters

Strong understanding should survive changed examples and constraints.

---

# 22. User experience principles

CounterQ should feel:

- focused;
- technically credible;
- calm;
- fast;
- professional;
- interview-like;
- evidence-driven.

It should not feel:

- gamified for its own sake;
- filled with unnecessary dashboards;
- like a generic chat interface;
- like a coding judge with voice added;
- like an AI that constantly lectures;
- like a prototype stitched together from unrelated AI features.

---

# 23. Data principles

CounterQ should store the minimum data required to deliver the product well.

Primary durable data includes:

- account and preferences;
- interview configuration;
- problem information;
- transcript;
- meaningful code snapshots/diffs;
- structured observations;
- claims;
- probes;
- evidence;
- assessments;
- CounterMap structure;
- mastery updates;
- retest state.

Raw realtime audio should not be treated as the primary durable source of truth.

Unless explicitly required by a future feature, structured transcript and evidence should be sufficient for normal persistence.

Continuous screenshots or continuous visual recording are not part of the core data model.

Vision should be selective and purposeful.

---

# 24. Product success

CounterQ succeeds if candidates experience moments where they realize:

> "I thought I understood this, but I could not defend it."

and later:

> "CounterQ found that weakness before, tested me differently this time, and now I can actually explain it."

The core value is not the novelty of speaking with an AI.

The value is **diagnostic depth plus persistent retesting**.

---

# 25. Core product metrics

The most important metrics should measure whether CounterQ discovers useful weaknesses and helps candidates improve.

Examples include:

### Core interaction

- interview completion rate;
- useful-probe rate;
- inappropriate-probe rate;
- probe response rate;
- average high-value probes per completed interview;
- user-rated fairness of probes;
- user-rated technical relevance.

### Evidence quality

- percentage of report findings linked to evidence;
- percentage of mastery changes backed by sufficient evidence;
- evaluator agreement on findings.

### Learning

- weaknesses later retested;
- weakness-to-improvement conversion;
- success on transfer probes;
- repeated misconception rate.

### Retention

- users completing a second interview;
- users returning for recommended retests;
- interviews per active candidate.

Vanity metrics such as total questions solved should not become CounterQ's primary measure of value.

---

# 26. Core product risk

The largest product risk is not whether an AI can conduct a voice conversation.

Modern models can already do that.

The real risk is whether CounterQ can consistently identify **which technical moments are worth probing** and ask questions that feel:

- relevant;
- technically correct;
- appropriately timed;
- concise;
- non-leading;
- human-interviewer-like.

Therefore the central product capability to validate is:

> **High-precision adaptive technical probing grounded in candidate speech and code.**

Everything else exists to support, preserve or compound that capability.

---

# 27. Phase 1 objective

Phase 1 must prove that CounterQ can deliver a polished end-to-end coding interview in which:

1. the candidate speaks naturally;
2. the candidate codes naturally;
3. CounterQ observes both;
4. CounterQ identifies meaningful technical claims and implementation decisions;
5. CounterQ selectively challenges important issues;
6. the challenge does not reveal the answer;
7. the interaction remains realtime;
8. the session produces evidence-backed feedback;
9. that evidence becomes persistent candidate memory;
10. future sessions can retest discovered weaknesses.

The exact launch scope is defined in:

`docs/PHASE_1.md`

---

# 28. Future extensibility

CounterQ may eventually expand to other interview types.

Future extensibility should influence clean domain boundaries, but Phase 1 should not implement speculative functionality for:

- school viva;
- government interviews;
- PM interviews;
- behavioral interview suites;
- presentation defense;
- sales interviews;
- generalized oral examination.

Future flexibility is an architectural consideration, not a Phase 1 feature requirement.

---

# 29. Product decision rule

When deciding whether to add something to CounterQ, ask:

> **Does this materially improve CounterQ's ability to observe, challenge, diagnose, remember, or retest candidate understanding?**

If not, the feature should face a high bar for inclusion.

CounterQ should win through depth, not surface area.