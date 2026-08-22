# CounterQ — Phase 1 Product Scope

**Document:** `docs/PHASE_1.md`  
**Status:** Scope Contract  
**Phase:** Minimum Lovable Product  
**Initial Vertical:** Technical Coding Interviews  
**Last Updated:** August 2026

---

# 1. Purpose

This document freezes the scope of CounterQ Phase 1.

Its purpose is to prevent scope drift while product, architecture and implementation are being developed.

Phase 1 is intentionally:

> **Narrow in breadth and high in depth.**

Anything not explicitly included here should be considered out of scope unless this document is intentionally revised.

Codex must not introduce features outside this scope merely because they are convenient or conventional.

---

# 2. Phase 1 goal

Phase 1 must prove the following product hypothesis:

> A candidate will receive meaningful value from an AI technical interviewer that observes speech and code together, selectively challenges important claims or implementation decisions, captures evidence of where understanding breaks, and uses that evidence later.

Phase 1 is successful only if the core interrogation experience is strong.

A polished dashboard cannot compensate for a weak examiner.

---

# 3. Primary user

Phase 1 targets:

- university placement candidates;
- internship candidates;
- new-graduate software-engineering candidates;
- early-career candidates practicing DSA-style technical coding interviews.

Phase 1 assumes users already have basic familiarity with coding interviews.

CounterQ is not designed to teach programming from zero.

---

# 4. Platform scope

Phase 1 is a:

> **Desktop-first web application**

Primary environment:

- modern Chromium-based desktop browsers;
- laptop or desktop;
- microphone available;
- physical keyboard;
- stable internet connection.

Responsive behavior should remain usable on smaller screens, but a full mobile interview experience is not a Phase 1 requirement.

Native mobile applications are out of scope.

The Chrome extension is out of scope for Phase 1 implementation.

---

# 5. Interview domain

Phase 1 supports:

> **DSA / algorithmic coding interviews**

Topics may include common interview concepts such as:

- arrays;
- strings;
- hashing;
- two pointers;
- sliding window;
- stacks;
- queues;
- linked lists;
- binary search;
- trees;
- heaps;
- graphs;
- recursion;
- backtracking;
- greedy reasoning;
- dynamic programming;
- complexity analysis.

Phase 1 does not attempt to provide complete curricular coverage of every computer-science topic.

System design, low-level design, SQL interviews, OS interviews, networking interviews and behavioral interviews are not Phase 1 interview types.

---

# 6. Supported candidate journey

A Phase 1 candidate must be able to complete the following journey.

## 6.1 Create an account

The candidate can create and access a persistent CounterQ account.

The account must preserve:

- profile;
- interview preferences;
- completed interviews;
- reports;
- CounterMaps;
- mastery state;
- retest recommendations.

Exact authentication providers are an architecture decision.

---

## 6.2 Configure interview profile

At minimum, the candidate can configure:

- interview level;
- preferred coding language;
- default interview mode.

Phase 1 candidate levels should remain deliberately simple.

Recommended initial levels:

- `INTERN`
- `NEW_GRAD`
- `EARLY_CAREER`

These levels primarily influence interviewer expectations and problem/probe calibration.

They are not intended to simulate individual company hiring rubrics.

---

## 6.3 Choose mode

The candidate selects:

- `COACH`
- `SIMULATION`

The selected mode affects interviewer behavior, intervention thresholds and teaching behavior.

It must not create two completely separate interview implementations.

Both modes operate on the same underlying interview state and evidence system.

---

## 6.4 Select a problem

Phase 1 supports two problem sources.

### CounterQ problem library

The candidate can choose from a curated set of CounterQ-supported problems.

Curated problems should have high-quality Interview Packs.

### Custom problem

The candidate may paste a coding problem into CounterQ.

Custom problems may require preprocessing before the interview begins.

Phase 1 does not require automatic scraping of LeetCode or arbitrary websites.

A LeetCode-specific browser integration is reserved for a future phase.

---

## 6.5 Configure interview

The candidate can configure a limited set of interview parameters.

At minimum:

- mode;
- level;
- problem;
- coding language.

Interview duration may be selected from a small set of supported durations if product testing shows this is useful.

The configuration screen should remain lightweight.

CounterQ is not a simulation-configuration dashboard.

---

# 7. Supported coding languages

Phase 1 should launch with a deliberately limited set of widely used interview languages.

Recommended launch set:

- C++17 or later compatible interview environment;
- Java 17 or later compatible interview environment;
- Python 3.

The architecture should make adding languages straightforward.

Phase 1 does not require support for every language Monaco can highlight.

Language support means more than syntax coloring.

A supported language must have:

- editor configuration;
- code execution;
- test execution;
- execution output;
- semantic examiner context;
- correct language-specific prompting where relevant.

---

# 8. Interview room

The Interview Room is the central Phase 1 product surface.

It must feel like a purpose-built technical interview environment rather than a chatbot beside an editor.

Required areas include:

- problem statement;
- code editor;
- run/test controls;
- output or test-result area;
- interview status;
- microphone / voice state;
- interviewer presence;
- appropriate controls to finish or leave the interview.

The candidate must be able to focus primarily on:

> problem + voice + code.

Secondary information should not overwhelm the room.

Detailed UX belongs in:

`docs/product/INTERVIEW_ROOM.md`

---

# 9. Realtime voice requirements

Voice is part of the core product, not an optional novelty.

Phase 1 must support:

- natural spoken interaction;
- streaming speech recognition or equivalent realtime understanding;
- natural spoken interviewer responses;
- interruption / barge-in;
- candidate interruption of CounterQ;
- robust turn-taking;
- Indian-English comprehension;
- reasonable handling of technical vocabulary;
- appropriate silence.

CounterQ should not fill every silence.

Silence during coding and thinking is normal.

The interviewer must distinguish between:

- candidate still thinking;
- candidate actively coding;
- candidate finished speaking;
- candidate apparently stuck;
- candidate expecting a response.

The product should avoid forcing the candidate to repeatedly press a "send voice" button.

---

# 10. Code observation

CounterQ must observe code as part of the interview.

Phase 1 requires:

- current code state;
- meaningful code snapshots;
- meaningful code diffs;
- language;
- run events;
- test results;
- relevant execution failures;
- interview-relative timestamps.

The examiner does not need a reasoning call for every keystroke.

Code analysis should be triggered by meaningful events.

Potential triggers include:

- structural code changes;
- important control-flow changes;
- data-structure choices;
- function completion;
- run;
- repeated debugging;
- candidate verbal claim about code;
- transition between interview states.

---

# 11. Candidate observations

CounterQ should be capable of identifying structured observations such as:

- technical claims;
- stated assumptions;
- complexity claims;
- algorithm choices;
- data-structure choices;
- invariants;
- implementation decisions;
- candidate uncertainty;
- contradictions;
- likely misconception;
- possible bug;
- debugging behavior;
- self-correction;
- dependence on examiner assistance.

Observations are not automatically assessments.

An observation may create a candidate probe target.

---

# 12. Examiner actions

At any meaningful point, the examiner must be able to choose among:

- `WAIT`
- `OBSERVE`
- `ASK`
- `PROBE`

These choices are conceptually different.

## WAIT

Allow the candidate to continue without intervention.

## OBSERVE

Continue gathering information before deciding.

## ASK

Request ordinary interview information without specifically challenging a suspicious claim.

Example:

> "What complexity are you targeting?"

## PROBE

Deliberately test the validity or depth of a claim, assumption or implementation decision.

Example:

> "What guarantees that pointer never moves backwards?"

The examiner must not feel compelled to produce speech whenever an internal analysis completes.

---

# 13. Probe behavior

Phase 1 should support the following probe strategies:

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

Not every strategy must appear in every interview.

The examiner should choose the minimum useful intervention.

Probe behavior is governed by:

`docs/examiner/PROBE_STRATEGIES.md`

---

# 14. Probe quality requirements

A valid Phase 1 probe should generally be:

- technically relevant;
- grounded in observable candidate behavior;
- concise;
- non-leading;
- appropriate to the current interview state;
- proportional to the importance of the issue.

CounterQ should generally avoid questions that accidentally contain the solution.

Bad:

> "Shouldn't this be average O(1) rather than always O(1)?"

Better:

> "You said always. Is that guaranteed?"

Bad:

> "Your left pointer can move backwards, which breaks the invariant. How will you fix it?"

Better:

> "What guarantees that your left pointer never moves backwards?"

---

# 15. Interruption philosophy

Phase 1 should optimize for **high-value interruptions**.

CounterQ should not probe every imperfection.

Probe selection should consider:

- technical importance;
- confidence of detection;
- candidate's explicit claim;
- whether sufficient evidence already exists;
- learning value;
- current mode;
- interview timing;
- cost of interruption;
- whether waiting would produce better evidence.

False-positive or irrelevant probes are particularly harmful because they damage trust in the examiner.

Therefore:

> **Precision is more important than recall.**

---

# 16. Interview state machine

The interview must be controlled by deterministic software.

Phase 1 should support conceptual states similar to:

- `SETUP`
- `INTRODUCTION`
- `PROBLEM_UNDERSTANDING`
- `APPROACH_DISCUSSION`
- `IMPLEMENTATION`
- `TESTING_DEBUGGING`
- `COMPLEXITY_EDGE_CASES`
- `CONSTRAINT_CHANGE`
- `WRAP_UP`
- `COMPLETED`

The final state model may include sub-states.

An AI model may recommend actions.

It must not be the sole owner of interview state transitions.

Detailed behavior belongs in:

`docs/examiner/STATE_MACHINE.md`

---

# 17. Interview Pack

Every curated Phase 1 problem must have a prepared Interview Pack.

At minimum, an Interview Pack should represent:

- problem summary;
- expected approaches;
- complexity expectations;
- important concepts;
- key invariants;
- common mistakes;
- common misconceptions;
- useful edge cases;
- possible counterexamples;
- useful constraint mutations;
- potential probe targets.

The pack should support dynamic questioning.

It must not force the interview into a fixed script.

For custom problems, CounterQ may generate an Interview Pack before allowing the session to begin.

---

# 18. Structured evidence

Phase 1 must create structured evidence during the session.

A useful evidence item should be capable of referencing:

- interview;
- timestamp or time range;
- concept;
- source type;
- candidate claim;
- code state or diff;
- examiner probe;
- candidate response;
- assessment;
- confidence;
- independence / assistance level;
- related CounterMap nodes.

Evidence should distinguish between:

- what was observed;
- what was inferred;
- what was assessed.

This distinction is important for explainability.

---

# 19. Evidence strength

Not all evidence is equally strong.

Phase 1 should distinguish between evidence such as:

### Weak evidence

- concept merely appeared;
- candidate repeated interviewer wording;
- candidate guessed correctly;
- candidate answered after a strong hint.

### Moderate evidence

- candidate independently explained a concept;
- candidate corrected their own implementation;
- candidate correctly justified a decision when asked.

### Strong evidence

- candidate defended reasoning under challenge;
- candidate found a counterexample;
- candidate transferred understanding to a changed constraint;
- candidate independently corrected a misconception;
- candidate applied the concept correctly in another context.

The exact scoring mechanism belongs in later data-model specifications.

---

# 20. End-of-interview report

Every completed Phase 1 interview should generate a detailed report.

The report must prioritize evidence over generic praise or criticism.

Required sections:

## Summary

A concise description of how the interview went.

## Demonstrated strengths

Only claims supported by meaningful evidence.

## Breakpoints / weaknesses

Where the candidate's reasoning or implementation broke down.

## Important claims

Notable technical claims and whether they survived questioning.

## Problem-solving behavior

Examples may include:

- planning;
- debugging;
- self-correction;
- testing discipline;
- response to uncertainty.

## Complexity and edge-case reasoning

Where applicable.

## Assistance dependency

How much examiner intervention was required.

## Recommended retests

Specific concepts or reasoning behaviors that should be tested again.

The report may include aggregate indicators, but it must not rely on unexplained AI-generated numerical scores.

---

# 21. CounterMap

Every completed interview should produce a CounterMap.

The CounterMap is a structured interactive graph of meaningful reasoning events from that interview.

Representative chain:

**Claim → Probe → Response → Assessment → Breakpoint**

Possible node categories include:

- claim;
- assumption;
- approach;
- implementation decision;
- probe;
- response;
- evidence;
- correction;
- strength;
- breakpoint.

The CounterMap must be generated from persisted structured data.

It must not be reconstructed solely from the final report text.

The user should be able to inspect important branches and understand why CounterQ reached a conclusion.

Detailed schema belongs in:

`docs/data/COUNTERMAP.md`

---

# 22. Mastery Map

Phase 1 must maintain persistent candidate mastery across interviews.

Initial concept states:

- `UNTESTED`
- `EXPOSED`
- `WEAK`
- `DEVELOPING`
- `STRONG`

Mastery updates should be based on evidence.

A single successful answer should not automatically establish `STRONG`.

A single uncertain statement should not automatically establish `WEAK`.

The model must consider evidence quality, recency and repeated behavior.

Phase 1 should focus on a curated concept taxonomy rather than attempting to model every possible programming concept.

Detailed behavior belongs in:

`docs/data/MASTERY_MODEL.md`

---

# 23. Retest queue

Phase 1 must make previously discovered weaknesses actionable.

A candidate should be able to see concepts or reasoning behaviors that CounterQ recommends retesting.

A retest may occur through:

- another full interview;
- another problem that exercises the concept;
- an appropriate constraint mutation;
- a transfer probe within a later interview.

Phase 1 does not require a sophisticated standalone spaced-repetition learning product.

The minimum requirement is:

> CounterQ remembers what deserves retesting and can deliberately surface it in a future interview.

---

# 24. Interview history

The candidate should be able to view previous interviews.

Each interview entry should provide access to:

- problem;
- mode;
- date;
- completion state;
- report;
- CounterMap;
- meaningful mastery changes.

A complex analytics dashboard is not required.

---

# 25. Mastery experience

The candidate must have a persistent view of their current mastery state.

Phase 1 should emphasize:

- tested concepts;
- weaknesses;
- developing concepts;
- strengths;
- concepts requiring retest.

The Mastery Map may use a graph-shaped visual representation.

Its underlying source of truth remains PostgreSQL.

Neo4j is explicitly not required for Phase 1.

---

# 26. Realtime architecture requirements

Phase 1 must separate:

### Realtime conversational layer

Responsible for:

- speech interaction;
- natural voice;
- turn-taking;
- interruption;
- immediate interviewer presence.

### Examiner reasoning layer

Responsible for deeper technical analysis when required.

### Deterministic interview controller

Responsible for:

- state;
- budgets;
- timing;
- allowed actions;
- persistence;
- interview progression.

The realtime voice model should not become the sole repository of interview intelligence or state.

---

# 27. Think-ahead requirement

CounterQ should perform analysis before the candidate explicitly needs the next interviewer turn.

While the candidate speaks:

- stream transcript;
- detect claims;
- update candidate context;
- begin lightweight analysis.

While the candidate codes:

- process meaningful diffs;
- track implementation structure;
- prepare possible probe targets.

When a likely probe target is detected:

- analysis may continue asynchronously;
- the examiner should decide whether to interrupt now, later or never.

The candidate should not visibly wait for deep reasoning unless unavoidable.

---

# 28. Model routing

Phase 1 must use a model-agnostic gateway.

Different tasks should be routable to different model tiers.

Example workload classes:

### Cheap / fast

- extraction;
- classification;
- transcript cleanup;
- concept tagging;
- straightforward claim detection.

### Medium reasoning

- algorithm analysis;
- semantic code review;
- explanation evaluation;
- probe-target analysis.

### Strong reasoning

- difficult correctness disputes;
- ambiguous algorithms;
- complex candidate explanations;
- selected post-interview synthesis.

The strongest model must not be the default for every event.

Provider names and concrete routing rules belong in architecture/configuration documents rather than this scope document.

---

# 29. Session budgets

Every Phase 1 interview configuration must support limits such as:

- maximum duration;
- maximum probes;
- maximum deep reasoning calls;
- maximum strongest-model calls;
- maximum vision calls;
- soft inference budget;
- hard inference budget.

Budget exhaustion must degrade gracefully.

A budget limit must never cause the interview to suddenly behave nonsensically.

---

# 30. Vision

Continuous computer vision is not part of Phase 1.

CounterQ may use image reasoning only when meaningful context cannot reliably be represented structurally.

Potential future examples include:

- third-party coding environments;
- diagrams;
- external whiteboards.

Inside the native Phase 1 Interview Room, CounterQ already has structured access to the editor and should not require screenshots to understand its own interface.

---

# 31. Privacy and persisted interview data

Phase 1 should persist the information necessary to provide reports, CounterMaps, mastery and retesting.

Durable data should prioritize:

- transcript;
- structured events;
- evidence;
- code snapshots/diffs;
- assessments.

Raw audio does not need to be permanently retained as a core product requirement.

Continuous screen recording is out of scope.

Users must not need to surrender unnecessary data to receive the core product value.

Detailed retention policy is a later architecture/legal decision.

---

# 32. Phase 1 launch UX quality

The launch product must not feel like an engineering demo.

Required baseline quality:

- coherent visual system;
- responsive interactions;
- clear loading states;
- clear microphone states;
- understandable failures;
- reconnect behavior where practical;
- preserved session state where practical;
- no raw model JSON exposed to users;
- no debug controls in normal user flows;
- no unexplained AI errors shown directly;
- polished empty states;
- consistent terminology;
- usable report presentation;
- usable CounterMap;
- usable mastery view.

---

# 33. Failure behavior

CounterQ must degrade safely when components fail.

Examples:

### Voice connection failure

The user receives a clear recovery path.

### Examiner analysis failure

The interview may continue without asking a probe rather than inventing a questionable one.

### Code execution failure

Infrastructure failure must be distinguishable from candidate code failure.

### Report generation failure

Persisted evidence must remain available so report generation can be retried.

### Model timeout

Interview state must remain valid.

AI failure must not corrupt deterministic session state.

---

# 34. The first technical spike

Before major dashboard or analytics development, CounterQ must prove the core interaction loop.

The spike must demonstrate:

1. candidate enters a coding interview;
2. candidate speaks naturally;
3. candidate writes code;
4. CounterQ receives streaming speech context;
5. CounterQ receives structured code context;
6. candidate deliberately makes a questionable technical statement or implementation choice;
7. CounterQ recognizes the issue;
8. CounterQ evaluates whether it deserves intervention;
9. CounterQ asks a concise counter-question;
10. CounterQ does not reveal the answer;
11. conversation remains natural and low-latency.

Canonical verbal example:

Candidate:

> "I'll use `unordered_map` because lookup is always O(1)."

Desired examiner behavior:

> "You said always. Is that actually guaranteed?"

Canonical code example:

Candidate introduces logic allowing a sliding-window left pointer to move backwards.

Desired examiner behavior:

> "What guarantees that your left pointer never moves backwards?"

The spike passes only if these moments feel like intelligent interviewing rather than keyword-triggered scripts.

---

# 35. Phase 1 feature priority

Implementation priority is:

## P0 — Core examiner experience

- interview room;
- realtime voice;
- code observation;
- deterministic interview state;
- claim detection;
- probe-target detection;
- selective probing;
- core evidence capture.

## P1 — Complete interview loop

- code execution;
- structured report;
- CounterMap;
- persistent interviews;
- mastery updates;
- retest state.

## P2 — Launch polish

- account setup;
- interview history;
- mastery UI;
- curated problem library;
- configuration UX;
- failure recovery;
- product analytics;
- cost controls;
- operational tooling.

A P2 dashboard feature must not delay fixing a weak P0 probe experience.

---

# 36. Explicit Phase 1 non-goals

The following are out of scope unless this document is revised.

## Interview verticals

- behavioral interviews;
- HR interviews;
- system design interviews;
- low-level design interviews;
- PM interviews;
- school viva;
- government interviews;
- presentation defense;
- SQL interview suite;
- CS-fundamentals oral interview suite.

## Platforms

- native Android application;
- native iOS application;
- desktop native app;
- public Chrome extension.

## Collaboration

- human interviewer marketplace;
- peer interview matching;
- group interviews;
- live mentor collaboration.

## Content breadth

- thousands of scraped problems;
- complete LeetCode replacement;
- full DSA curriculum;
- video-course library;
- handwritten-note platform.

## Gamification

- public leaderboards;
- XP systems;
- streak mechanics as a core feature;
- social feeds;
- achievements.

## AI spectacle

- full-time avatar interviewer;
- continuous webcam emotion detection;
- personality analysis;
- arbitrary body-language scoring;
- continuous screenshot analysis.

## Architecture

- microservices without demonstrated need;
- Neo4j solely for CounterMap/Mastery Map;
- Kubernetes at prototype scale;
- custom model training before product evidence warrants it.

## Enterprise features

- university administration portal;
- recruiter portal;
- enterprise SSO;
- placement-cell analytics;
- bulk student management.

These may become valid later.

They are not Phase 1.

---

# 37. Phase 1 launch gates

CounterQ should not be considered ready for public Phase 1 launch merely because all screens exist.

The following must be true.

## Core interaction gate

The interviewer can reliably produce technically meaningful adaptive follow-ups grounded in candidate speech and code.

## Probe-quality gate

Internal evaluation shows that most surfaced probes are:

- relevant;
- correct;
- non-leading;
- appropriately timed.

## Realtime gate

Normal conversations do not repeatedly suffer from awkward multi-second pauses caused by preventable architecture decisions.

## Evidence gate

Important report findings can be traced to persisted evidence.

## State gate

Interview progression is controlled deterministically and survives expected AI failures.

## CounterMap gate

The CounterMap communicates important branches of reasoning rather than displaying arbitrary graph noise.

## Mastery gate

Cross-session mastery changes are evidence-backed and understandable.

## Retest gate

At least one previously discovered weakness can be intentionally and meaningfully retested in a later session.

## Reliability gate

A user can complete a normal interview without developer intervention.

## Product-quality gate

The main workflow feels cohesive enough to confidently share publicly.

---

# 38. Phase 1 evaluation scenarios

Before launch, the system should be repeatedly tested using controlled candidate behaviors.

Examples:

### Incorrect guarantee

> "`unordered_map` lookup is always O(1)."

Expected:

CounterQ challenges the guarantee without immediately teaching the answer.

### Suspicious invariant

Candidate writes code that may violate a monotonic pointer invariant.

Expected:

CounterQ tests the invariant rather than naming the bug.

### Correct reasoning

Candidate makes a technically correct and sufficiently justified choice.

Expected:

CounterQ does not manufacture a challenge merely to appear intelligent.

### Harmless shorthand

Candidate uses slightly imprecise language that does not materially affect the interview.

Expected:

CounterQ usually allows the conversation to continue.

### Self-correction

Candidate notices and fixes their own error.

Expected:

The system records stronger evidence than if CounterQ had directly pointed out the error.

### Strong hint dependency

Candidate succeeds only after escalating Coach Mode assistance.

Expected:

The final assessment reflects that dependency.

### Constraint mutation

Candidate solves the original problem but cannot adapt when an assumption changes.

Expected:

The original success and transfer weakness are represented separately.

### Repeated weakness

Candidate previously misunderstood a complexity guarantee and encounters a related concept later.

Expected:

CounterQ can deliberately verify whether that weakness remains.

---

# 39. Phase 1 product metrics

Early metrics should prioritize examiner quality and return value.

Key measurements should include:

- completed interviews;
- repeat interview rate;
- high-value probes per interview;
- inappropriate probes per interview;
- user-rated probe relevance;
- user-rated interview realism;
- report usefulness;
- percentage of findings backed by inspectable evidence;
- weaknesses scheduled for retest;
- weaknesses successfully retested;
- cross-session improvement.

The team should pay particular attention to:

> **Would the candidate have discovered this weakness without CounterQ?**

That question is closer to CounterQ's real value than total AI messages or coding problems attempted.

---

# 40. Phase 1 definition of done

Phase 1 is complete when a real candidate can:

1. create an account;
2. configure their interview level;
3. choose Coach or Simulation;
4. choose a curated problem or provide a custom problem;
5. enter a polished coding interview room;
6. speak naturally with CounterQ;
7. explain their reasoning;
8. write executable code;
9. run and debug it;
10. receive selectively chosen technical counter-questions grounded in their speech and code;
11. complete the interview;
12. receive an evidence-backed report;
13. inspect the reasoning path through CounterMap;
14. see relevant mastery changes;
15. return later;
16. encounter a meaningful retest of a previously discovered weakness.

And, critically:

> **The candidate should be able to identify at least one moment where CounterQ tested understanding rather than merely checking whether the final answer was correct.**

That moment is the core Phase 1 product.