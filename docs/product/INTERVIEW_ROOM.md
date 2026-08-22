# CounterQ — Phase 1 Interview Room

**Document:** `docs/product/INTERVIEW_ROOM.md`  
**Status:** Frozen Phase 1 Product UX Source of Truth  
**Product:** CounterQ  
**Phase:** Phase 1 — Technical Coding Interviews  
**Last Updated:** August 2026

---

# 1. Purpose

This document defines the complete Phase 1 candidate experience inside the CounterQ technical coding Interview Room.

The Interview Room is the primary CounterQ product surface.

It must make CounterQ feel like:

> **a focused, intelligent technical interview**

rather than:

> **ChatGPT beside a code editor.**

The room must support the frozen behavior defined in:

- `docs/PRODUCT.md`
- `docs/PHASE_1.md`
- `docs/ARCHITECTURE.md`
- `docs/data/DATA_MODEL.md`
- `docs/examiner/STATE_MACHINE.md`
- `docs/examiner/PROBE_STRATEGIES.md`
- `docs/examiner/COACH_VS_SIMULATION.md`

The guiding UX principle is:

> **The intelligence should feel present without the interface constantly demanding attention.**

---

# 2. Product objective

The candidate should feel that:

- they are in a real technical interview;
- CounterQ remains conversationally available while the microphone is unmuted;
- CounterQ understands the problem;
- CounterQ sees relevant changes in their code;
- silence is allowed;
- coding is the primary activity;
- CounterQ asks questions selectively;
- questions react to what they actually said or wrote;
- voice interaction is natural and interruptible;
- the interface stays calm even while complex reasoning happens behind it.

The candidate should not need to think about:

- AI model state;
- examiner state;
- probe strategy;
- evidence confidence;
- background reasoning;
- internal interview stages.

Those systems should remain invisible.

---

# 3. What the Interview Room is not

---

## 3.1 Not a chatbot layout

Reject layouts where the dominant surface is:

```text
AI:
Candidate:
AI:
Candidate:
```

The candidate should not primarily type messages to CounterQ.

Voice is the interviewer channel.

The transcript exists only as support.

---

## 3.2 Not an avatar experience

Phase 1 should not contain:

- giant animated human face;
- talking-head avatar;
- lip-synced AI interviewer;
- emotional facial reactions.

These add:

- visual distraction;
- cost;
- latency;
- uncanny failure modes;

without improving CounterQ's core diagnostic advantage.

A subtle branded voice presence is enough.

---

## 3.3 Not an interview dashboard

During the live interview do not expose:

- scores;
- mastery states;
- Breakpoints;
- concept confidence;
- probe count;
- Examiner rationale;
- hidden target concept;
- AI cost;
- evidence quality.

Assessment belongs after the interview.

---

## 3.4 Not a full IDE

Do not build:

- file explorer;
- multi-file project workspace;
- terminal emulator;
- debugger;
- git interface;
- package manager;
- extension marketplace.

Phase 1 needs a focused single-problem coding surface.

---

# 4. Phase 1 layout decision

Use a desktop-first three-layer layout:

1. **Header**
2. **Problem + Coding Workspace**
3. **Interviewer Surface**

Recommended structure:

```text
┌────────────────────────────────────────────────────────────────────┐
│ CounterQ     SIMULATION        21:42        ● Listening      End   │
├──────────────────────┬─────────────────────────────────────────────┤
│                      │                                             │
│   Problem Panel      │               Monaco Editor                 │
│                      │                                             │
│   Title              │                                             │
│   Statement          │                                             │
│   Examples           │                                             │
│   Constraints        │                                             │
│                      │                                             │
│                      │                                             │
├──────────────────────┴─────────────────────────────────────────────┤
│ Run / Tests                              Execution Result            │
├────────────────────────────────────────────────────────────────────┤
│  ◉ CounterQ   “What guarantees that left never moves backwards?”  │
│                                               [Recent conversation] │
└────────────────────────────────────────────────────────────────────┘
```

The Interviewer Surface remains visually compact.

The editor receives the largest area.

---

# 5. Workspace proportions

For typical laptop/desktop widths:

### Problem Panel

Approximately:

```text
32–38%
```

of workspace width.

### Coding Workspace

Approximately:

```text
62–68%
```

The split should be resizable within reasonable minimums.

Persist the candidate's splitter preference locally.

Do not allow resizing to make either surface unusable.

---

# 6. Why voice belongs at the bottom

The voice surface should not consume a full side column.

A right-side chat panel would:

- reduce editor width;
- encourage transcript watching;
- make the product resemble generic AI chat.

A compact bottom interviewer surface gives CounterQ persistent presence without competing with code.

---

# 7. Visual attention hierarchy

The UI should attract attention in this order:

## 1. Current technical work

- code;
- problem;
- execution result.

## 2. Active substantive CounterQ question

The candidate should never wonder:

> "What did it just ask me?"

## 3. Time / mode / voice state

Persistent but subdued.

## 4. Testing feedback

Prominent when relevant.

## 5. Conversation history

Available when requested, not constantly dominant.

The candidate should never be watching an AI animation while solving.

---

# 8. Header

The top header should contain only essential persistent state.

Recommended:

```text
CounterQ | SIMULATION | 21:42 | ● Listening | End Interview
```

or equivalent layout.

Components:

- CounterQ brand;
- mode badge;
- interview timer;
- voice/connection state;
- End Interview control.

No full navigation menu during an active interview.

The candidate is in a focused session.

---

# 9. User-facing interview stages

Phase 1 should **not expose stage progression such as:**

```text
Discuss → Code → Test → Defend
```

and must never expose internal labels such as:

```text
APPROACH_DEFENSE
```

Reason:

Visible phases would encourage candidates to behave according to the UI rather than naturally.

It would also make adaptive transitions feel scripted.

The interviewer itself communicates transitions conversationally:

> "Go ahead and implement it."

> "Let's talk about the complexity."

That is enough.

The header therefore shows:

- mode;
- time;
- voice state;

not interview phase.

---

# 10. Timer

The timer is visible throughout the interview.

It is:

- server-authoritative;
- restored after refresh;
- unaffected by local clock manipulation;
- consistent with State Machine time policy.

The timer should remain visually restrained.

Example:

```text
21:42
```

not:

```text
🔥 21:42 LEFT!!!
```

---

# 11. Timer visual states

## Normal

Neutral presentation.

No animation.

## Constrained

May gain slightly increased prominence.

Do not flash.

## Final-defense reserve

The candidate should normally receive a spoken TIME_WARNING.

The UI may subtly change emphasis.

## Wrap-up

Timer may remain visible but should not create alarm.

Avoid aggressive red countdown styling except perhaps in the final moments of configurations explicitly designed for timed drills.

The interview creates pressure through time itself.

No game treatment is required.

---

# 12. Mode indicator

The active mode remains persistently visible.

Examples:

```text
SIMULATION
```

or:

```text
COACH
```

The badge should be small and distinct enough to remove ambiguity.

Tooltip or first-session explanation may say:

### Simulation

> No live correctness feedback or hints.

### Coach

> CounterQ may guide you after your independent attempt.

No long explanatory block belongs inside the room.

---

# 13. Problem Panel

The Problem Panel contains:

- problem title;
- full statement;
- examples;
- constraints;
- function signature where applicable;
- minimal verified notes.

All important constraints must be visible in normal scrolling flow.

Do not hide constraints behind tabs such as:

```text
Statement | Constraints | Examples
```

unless later testing strongly supports it.

Candidates should be able to scan the full problem naturally.

---

# 14. Problem Panel behavior

The panel is:

- independently scrollable;
- resizable;
- stable while candidate codes;
- selectable for copying small values/examples where appropriate.

The UI should not unexpectedly collapse or scroll when CounterQ speaks.

---

# 15. Problem-reading state

When the problem is first revealed:

- Problem Panel receives slight visual emphasis;
- editor remains available;
- candidate is allowed to read silently;
- no forced countdown overlay;
- no blocking "Explain before coding" modal.

The editor may remain visually quieter but must stay fully interactive.

Behavioral sequencing is controlled conversationally.

If the candidate starts coding too early, CounterQ may say:

> "Before you implement it, walk me through the approach you're planning."

The editor itself remains unlocked.

---

# 16. Monaco Editor

Phase 1 uses Monaco as the coding surface.

Required capabilities:

- syntax highlighting;
- line numbers;
- automatic indentation;
- bracket matching;
- standard selection/navigation;
- common keyboard shortcuts;
- code persistence;
- server-restorable meaningful snapshots;
- language-appropriate formatting where low-risk;
- Run integration;
- stable cursor behavior during realtime events.

Voice output must never disable editor input.

The candidate may code while CounterQ is speaking.

---

# 17. Autocomplete policy

CounterQ should preserve normal editor ergonomics without introducing AI assistance.

Allow:

- bracket completion;
- quote completion;
- language keywords;
- locally obvious symbol completion;
- syntax-aware indentation;
- ordinary Monaco IntelliSense that does not generate algorithmic code.

Do not allow:

- AI code completion;
- Copilot-style multi-line suggestions;
- full-function generation;
- natural-language-to-code;
- suggested algorithm implementation.

The coding environment should help with syntax, not solve the interview.

---

# 18. Starter code

Curated problems may provide minimal starter code.

Examples:

### C++

```text
class Solution {
public:
    int solve(...) {

    }
};
```

### Java

Minimal class/method wrapper.

### Python

Function definition where required.

Starter code may include:

- required imports;
- method signature;
- basic wrapper.

It must not contain algorithmic hints.

Avoid boilerplate that wastes interview time.

---

# 19. Supported languages

Recommended initial Phase 1 set:

- C++17/20-compatible interview runtime;
- Java 17+;
- Python 3.

Do **not** launch JavaScript/TypeScript initially unless testing demonstrates meaningful demand.

Reason:

The primary initial audience is Indian placement/new-grad candidates, where C++, Java and Python cover the strongest likely usage while reducing:

- execution-runtime complexity;
- language-specific Examiner edge cases;
- starter-template surface;
- testing burden.

Architecture should make additional languages straightforward later.

---

# 20. Language selection behavior

Language is selected before the timed interview begins.

Once the session starts:

> **language is frozen.**

Do not permit mid-session switching.

Reasons:

- code snapshot lineage;
- runtime context;
- Interview Pack implementation guidance;
- examiner interpretation;
- timing realism.

If the candidate selected the wrong language, they can restart before the interview begins.

---

# 21. Coding Workspace

The Coding Workspace contains:

```text
Monaco Editor
        ↓
Execution Panel
```

The execution panel may be:

- collapsed when unused;
- expanded after Run;
- manually resizable within reasonable bounds.

It must not permanently steal vertical space from code.

---

# 22. Run behavior

When the candidate clicks:

```text
Run
```

the system must:

1. capture an exact CodeSnapshot;
2. associate the Run event with that snapshot;
3. send that snapshot to the isolated CodeExecutionProvider;
4. show a restrained execution-in-progress state;
5. return normalized result.

The Examiner always knows which exact code version produced the result.

Run does **not** mean:

> candidate has finished the interview.

---

# 23. Run control

Recommended:

```text
Run
```

primary execution button.

Optional shortcut:

```text
Ctrl/Cmd + Enter
```

depending on platform compatibility.

Avoid additional controls like:

- Compile;
- Debug;
- Profile;
- Benchmark;
- Submit;

in the initial spike.

A later Phase 1 polished version may distinguish:

```text
Run
```

and:

```text
Finish Solution
```

only if user testing shows benefit.

Candidate can already verbally say:

> "I'm done."

or use interview progression naturally.

---

# 24. Execution Panel

The execution panel should remain concise.

Possible structure:

```text
Input
[ custom/sample input ]

Output
[ result ]

Expected
[ result if known ]

Status
Passed / Failed / Compile Error / Runtime Error
```

For multi-test curated execution:

```text
Test 1   Passed
Test 2   Failed
Test 3   Not run
```

Do not create a full terminal shell.

---

# 25. Compiler/runtime errors

Errors should be presented:

- immediately;
- clearly;
- with line references where provided by compiler;
- without CounterQ commentary unless the interviewer chooses to speak.

Example:

```text
Compile Error

line 14: expected ';' after expression
```

CounterQ should usually let the candidate inspect this themselves.

---

# 26. Test success design

Use restrained status language:

```text
Passed
```

or:

```text
3 / 3 visible tests passed
```

Avoid:

```text
🎉 Awesome! All tests passed!
```

Passing tests do not mean:

- reasoning is correct;
- complexity is correct;
- interview is complete;
- the implementation has passed any hidden/private verification CounterQ may perform later.

The visible execution panel reports only what was actually run and shown to the candidate.

---

# 27. Candidate-created tests

When problem structure supports it, the candidate should be able to provide custom input.

This is valuable because test selection itself is behavioral evidence.

Do not automatically suggest:

> "Try an edge case!"

through UI.

CounterQ may ask this verbally when diagnostically appropriate.

UI should provide capability, not coaching.

---

# 28. Hidden tests

Hidden validation is **not required for the Core Interaction Spike** and should not become a candidate-facing judge mechanic in the initial Phase 1 Interview Room.

Curated problems may eventually use a small reviewed hidden validation set for backend technical verification, but Phase 1 policy is:

- visible `Run` behavior uses visible/sample/custom tests;
- hidden validation is never shown as a score or pass percentage;
- hidden-test results do not automatically speak a bug or correction;
- hidden validation may inform post-run Examiner reasoning only when technically verified and useful;
- preferably run broader hidden validation after `candidate_declares_done`, or during post-session analysis, rather than after every editor Run;
- the Examiner must still reason about code and candidate explanation rather than treating hidden tests as ground truth for interview quality.

Do **not** show candidate-facing messages such as:

```text
Additional hidden validation failed
```

in the initial Phase 1 room.

That would shift the experience toward an online judge and can reveal that an unseen edge case exists before the interviewer chooses whether that information is diagnostically useful.

If later user testing justifies candidate-visible hidden validation, add it deliberately as a separate product decision.

---

# 29. Voice is the primary interviewer channel

The Interview Room should assume:

> the candidate speaks to CounterQ.

There should be no persistent text message box.

A text fallback may be introduced later for accessibility if required, but Phase 1's defining interaction is realtime voice.

---

# 30. Voice presence

The room should contain a subtle persistent `VoicePresence`.

Recommended representation:

```text
● Listening
```

plus a small animated waveform/orb.

The presence should communicate state, not personality.

No giant animated sphere dominating the room.

---

# 31. Candidate-facing voice states

Recommended states:

- `Connecting…`
- `Listening`
- `CounterQ speaking`
- `Muted`
- `Reconnecting…`
- `Voice unavailable`

Do not show:

```text
Examiner reasoning
```

or:

```text
Deep model analyzing code
```

The Examiner should remain invisible.

---

# 32. Candidate speaking

When the candidate is speaking:

- listening indicator may react subtly;
- microphone state clearly remains active;
- optional transient transcription may appear only if useful;
- UI should not visually pulse aggressively.

Do not make the candidate stare at their waveform.

---

# 33. CounterQ speaking

When CounterQ speaks:

- small waveform/orb reflects output;
- current substantive prompt becomes readable;
- editor remains usable;
- candidate can interrupt naturally.

---

# 34. Barge-in

Barge-in is mandatory.

When candidate begins speaking while CounterQ is speaking:

1. CounterQ audio stops quickly;
2. visual state switches to Listening;
3. candidate receives the conversational floor;
4. no alert/error appears;
5. partial PromptDelivery is recorded internally.

No "Stop" button should be required.

Natural interruption is part of the product.

---

# 35. Microphone controls

Phase 1 controls:

- mute/unmute;
- permission/error recovery.

Default behavior:

> **open conversational microphone**

rather than push-to-talk.

This is necessary for interview realism.

---

# 36. Push-to-talk recommendation

Do not make push-to-talk the default.

It creates:

- artificial interaction;
- missed turns;
- additional cognitive load.

An optional push-to-talk fallback may later be offered for:

- noisy environments;
- accessibility;
- poor voice-activity detection.

It is not needed for the first technical spike.

---

# 37. Current prompt surface

The current substantive CounterQ question should remain readable.

Example:

```text
CounterQ

“What guarantees that left never moves backwards?”
```

This stays visible while the candidate answers.

When a new substantive prompt arrives:

- previous one moves into conversation history;
- new one becomes active.

## Candidate-visible prompt text follows delivery, not authorization

The frontend must never show an `InterviewerPrompt` merely because the backend has authorized it.

That would leak a question before CounterQ has actually asked it and would violate the frozen conversation-floor policy.

The visual source of truth is:

```text
PromptDelivery
```

not:

```text
InterviewerPrompt authorization
```

A substantive question becomes candidate-visible only when delivery has actually begun.

## Streaming prompt text

Preferred behavior:

- as CounterQ speaks, provider output text/transcript may progressively populate the current-prompt surface;
- once delivery completes, the finalized delivered wording remains visible;
- if provider output text cannot be reliably synchronized, show the full prompt only once delivery is sufficiently underway/completed rather than leaking it at authorization time.

## Interrupted prompt

If the candidate barges in before the question is fully delivered:

- stop audio;
- record partial/interrupted PromptDelivery;
- do not reveal the undisclosed remainder of the intended prompt text;
- keep only the actually delivered portion where technically available;
- policy later decides whether to retry, rephrase or discard the question.

A candidate must not gain technical information merely because the UI rendered text that CounterQ never actually delivered.

---

# 38. Why current prompt text matters

Technical interview questions may involve:

- variable names;
- complexity notation;
- input examples;
- specific constraints.

Audio alone is insufficient for accessibility and precision.

The candidate should be able to reread the question without asking CounterQ to repeat it.

---

# 39. Conversational acknowledgements vs active prompts

Not every CounterQ utterance should replace the active prompt.

Examples:

> "Okay."

> "Go on."

> "Mm-hm."

These are conversational acknowledgements.

They should not appear as large prompt cards.

Only substantive interviewer prompts receive the main prompt surface.

---

# 40. Transcript policy

Phase 1 recommendation:

> **Show the current substantive prompt persistently and provide an optional recent-conversation drawer.**

Do not display a full transcript permanently.

---

# 41. Recent conversation drawer

A small control such as:

```text
Recent conversation
```

opens a side drawer or overlay.

It may show:

- last several **actually delivered** substantive CounterQ prompts;
- relevant finalized candidate responses;
- optionally the complete finalized transcript on further expansion.

It must never show:

- merely authorized-but-undelivered prompts;
- stale/rejected Examiner decisions;
- internal probe intents;
- hidden assistance plans.

The drawer should not remain open by default.

---

# 42. Candidate transcript

Candidate speech transcript should be:

- hidden by default;
- optionally visible in Recent Conversation;
- temporarily surfaced during microphone diagnostics if needed.

Do not continuously show live candidate transcription in the main workspace.

Reasons:

- distraction;
- self-consciousness;
- transcription mistakes may confuse candidate;
- product begins to resemble chat.

---

# 43. Partial transcript

Partial speech recognition is ephemeral.

It may appear subtly during setup/testing.

During normal interview it should usually remain invisible.

Candidate-facing transcript should prefer finalized segments.

---

# 44. Thinking silence UX

When candidate is thinking:

- VoicePresence simply remains `Listening`;
- current question remains visible;
- no "waiting..." animation;
- no automated nudging indicator;
- timer continues normally.

Silence should feel allowed.

---

# 45. CounterQ latency UX

Most analysis should finish ahead of the conversational turn.

Still, occasional delay will occur.

## Sub-second

No visible state change.

## Normal short conversational pause

No indicator.

Humans pause before speaking.

## Longer-than-natural delay

A subtle activity state may appear in VoicePresence.

Example:

```text
CounterQ
```

with restrained motion.

Avoid explicit wording such as:

```text
AI reasoning...
```

or:

```text
Analyzing your code...
```

unless a specific operation genuinely requires user awareness.

---

# 46. Filler speech policy

Do not fill reasoning latency with repetitive phrases such as:

> "Let me think about that."

> "Interesting."

> "Hmm, one moment."

occasionally is natural, but systematic filler makes latency more obvious.

Prefer silent natural pause.

---

# 47. Coach hint presentation

When Coach provides genuine assistance, the UI may show a small label:

```text
Coach guidance
```

or:

```text
Hint
```

above the substantive prompt.

Do not show:

```text
Hint Level 3
```

to candidates.

Internal hint level remains persisted.

The badge appears only once solution-directed assistance has actually begun delivery.

An authorized but cancelled/stale hint must never appear as though the candidate received help.

The user only needs to understand:

> this turn contained assistance.

---

# 48. Simulation controls

Simulation must not expose:

- Hint;
- Reveal Answer;
- Check My Approach;
- Ask AI;
- Live Score;
- Show Bug;
- Solution.

Available controls remain:

- Run;
- test input;
- microphone;
- transcript/history;
- End Interview.

---

# 49. Coach help request

Phase 1 should support both:

### Spoken request

> "Can I get a hint?"

and:

### A small `Ask for hint` button

The button improves:

- discoverability;
- accessibility;
- recovery when candidate does not know voice command expectations.

The button does **not** directly reveal a hint.

It emits:

```text
candidate_requested_help
```

The Coach policy then chooses the minimum appropriate assistance level.

---

# 50. Ask-for-hint button behavior

The button should be visually secondary.

When clicked:

- no menu of "small / medium / big hint";
- no answer reveal;
- no gaming-style hint token count.

CounterQ responds conversationally.

Example:

> "Which part feels least clear right now?"

if that is the correct next intervention.

---

# 51. End Interview control

An `End Interview` action must always be available.

It should be obvious enough to find but visually secondary to Run.

On click, show a lightweight confirmation:

> **End this interview?**  
> Your progress will be saved and your report will reflect what you've completed so far.

Options:

- Continue Interview
- End Interview

---

# 52. Ending early

After confirmation:

- no new technical probes;
- current response may finish;
- state transitions toward WRAP_UP;
- session completes safely;
- evidence is preserved.

Do not trap the candidate because the planned lifecycle has unfinished stages.

---

# 53. Refresh restoration

Refreshing the page must not reset the interview.

Temporary state:

```text
Restoring your interview…
```

Restore:

- problem;
- mode;
- authoritative timer;
- latest code snapshot;
- current substantive prompt where relevant;
- recent transcript context;
- execution context;
- connection state.

Do not replay:

- introduction;
- already delivered questions.

---

# 54. Code persistence UX

Code should autosave through meaningful snapshots/events.

Do not display:

```text
Saving…
Saved!
Saving…
Saved!
```

for normal edits.

Internally, the client should distinguish at least:

```text
SYNCED
LOCAL_PENDING
PERSISTENCE_UNCONFIRMED
```

or equivalent semantics.

A persistence indicator should appear only when:

- connection is unstable;
- local changes have not yet been acknowledged durably;
- restoration occurs;
- candidate attempts to leave;
- backend cannot safely persist.

## Do not overclaim that code is saved

Candidate-facing recovery copy must reflect the last state the client can actually verify.

If the latest snapshot is acknowledged by the backend:

> "Your latest saved progress is safe."

If there are local edits whose durable persistence cannot be confirmed:

> "Connection interrupted. Your latest saved version is safe; newer local edits are being held on this device while we reconnect."

If persistence itself is uncertain:

do not display:

> "Everything is saved."

This matters because trust is worse if CounterQ confidently claims durability and then loses the newest code.

---

# 55. Network/realtime failure states

Failures must be explicit but calm.

---

# 56. Voice provider failure

Candidate sees a message based on verified persistence state, for example:

```text
Voice reconnecting…
Your latest saved interview state is safe.
```

If newer local code is not yet durably acknowledged, say so rather than claiming it is already saved.

Coding remains usable during a short reconnect window.

Timer behavior follows frozen reconnect policy.

If reconnection succeeds:

> "You're back. Continue from where you left off."

If failure persists, do not silently convert the experience into text chat.

---

# 57. Control/WebSocket failure

Candidate sees:

> **Connection interrupted.**  
> Your latest saved progress is safe. Reconnecting…

During this state:

- local code editing may continue briefly;
- Run should be disabled if backend command delivery is unavailable;
- voice behavior depends on whether realtime media path remains connected.

If authoritative state cannot be safely maintained, session should enter recovery rather than pretend everything is normal.

---

# 58. PostgreSQL/persistence failure

If the system cannot safely persist critical events:

show:

> **We're having trouble saving the interview.**

A short bounded buffering period may continue.

If durability cannot recover:

- prevent irreversible progression;
- preserve local code;
- clearly explain that session must be paused/ended;
- do not claim data was saved when it was not.

---

# 59. Reconnect UX

Reconnection should feel like recovery, not restart.

Never:

- reset timer;
- reset mode;
- clear code;
- replay problem presentation;
- repeat already delivered questions unnecessarily.

Current prompt may be restored if it was unresolved.

---

# 60. Leave-page protection

If the user navigates away during an active interview:

use standard leave-page protection where supported.

Message concept:

> You have an active CounterQ interview.

Do not attempt to trap the browser.

Durable persistence remains the primary protection.

---

# 61. Desktop-first viewport

Recommended full Interview Room support:

```text
≥ 1280px width
```

Preferred:

```text
1440px+
```

Minimum usable laptop target:

```text
~1180px width
```

Below this:

- panels may compress;
- execution panel may become drawer-like;
- problem/editor stacking may be used if unavoidable.

The app should recommend desktop/laptop for the interview.

---

# 62. Mobile policy

Phase 1 does not build a full mobile interview room.

On mobile-sized devices, users may:

- browse product;
- view reports;
- configure sessions;

but starting a full coding interview may show:

> **CounterQ coding interviews are designed for a laptop or desktop.**

Do not spend Phase 1 engineering time reproducing Monaco interview UX on phones.

---

# 63. Accessibility

Required considerations:

- keyboard navigation;
- visible focus states;
- sufficient contrast;
- current-question text;
- transcript accessibility;
- non-color status labels;
- mute control accessible without mouse;
- semantic labels for voice states;
- screen-reader-compatible buttons;
- reduced-motion support.

Voice-first must not mean audio-only.

---

# 64. Keyboard shortcuts

Phase 1 should keep shortcuts minimal.

Recommended:

```text
Ctrl/Cmd + Enter
```

Run code.

Potential later shortcut:

```text
Ctrl/Cmd + Shift + M
```

Mute/unmute, only if it does not conflict with platform/browser conventions.

Avoid a complex shortcut system.

Monaco's common coding shortcuts take precedence.

---

# 65. Pre-interview readiness experience

Before entering the Interview Room, show a compact readiness screen.

Recommended structure:

```text
Ready for your CounterQ interview?

Mode: Simulation
Level: New Grad
Language: C++
Duration: Standard Interview

Microphone     ✓
Audio output   ✓
Voice service  ✓
Code runtime   ✓

[ Start Interview ]
```

Optional concise reminders:

Simulation:

> CounterQ won't tell you whether you're right during the interview.

Coach:

> CounterQ will let you attempt independently before guiding you.

---

# 66. Setup checks

Before `Start Interview` becomes available:

- microphone permission resolved;
- realtime provider reachable;
- Interview Pack ready;
- supported browser confirmed;
- code execution service sufficiently healthy.

Do not burn interview time discovering setup problems.

---

# 67. Audio output check

A simple:

```text
Play test sound
```

is sufficient.

Do not require a lengthy device-configuration wizard.

---

# 68. Problem reveal

Recommended flow:

```text
Start Interview
    ↓
brief CounterQ introduction
    ↓
problem becomes visible immediately
    ↓
CounterQ invites candidate to read/explain
```

Avoid theatrical reveal animations.

The transition should feel professional and fast.

---

# 69. Timer start boundary

The authoritative timer begins:

> **when all critical technical setup has succeeded and the active interviewer introduction begins.**

Do not start while waiting for:

- microphone permission;
- voice handshake;
- Interview Pack generation;
- code runtime health check.

---

# 70. Interviewer voice selection

Phase 1 should launch with:

> **one excellent default voice**, optionally plus one alternative if quality is equally strong.

Do not build a voice marketplace.

Voice selection multiplies:

- QA;
- latency testing;
- pronunciation testing;
- branding variability.

The selected voice should be:

- calm;
- professional;
- natural;
- concise;
- not overly enthusiastic;
- understandable for Indian-English candidates.

---

# 71. Interviewer identity

The interviewer should simply be:

> **CounterQ**

Avoid fake personas such as:

> "Sarah, Senior Engineer"

or:

> "Rahul from Google"

unless future product testing demonstrates real value.

A fake human identity creates unnecessary expectations and may reduce trust.

---

# 72. Response pacing

Voice behavior should favor:

- short interviewer turns;
- natural pause before questions;
- immediate interruption;
- no long lectures during active interview;
- no reading full code aloud.

Most candidate-visible technical probes should be one sentence.

---

# 73. Spoken code references

Good:

> "In your update to `left`, what prevents it from moving backwards?"

Bad:

> "On line thirty-seven, you wrote left equals max open parenthesis..."

CounterQ should refer to concepts and variable names naturally.

The UI can provide contextual highlighting when appropriate.

---

# 74. Code highlighting

Phase 1 may support temporary code-range highlighting when CounterQ asks about an explicit known implementation choice.

Example:

Candidate has just explained:

```cpp id="d5b5ca"
left = max(left, last[s[right]] + 1);
```

CounterQ asks:

> "Why is the `max` necessary here?"

That line may receive a subtle non-error highlight.

---

# 75. When not to highlight code

Do **not** highlight suspicious code if identifying its location would itself reveal the bug.

Example:

Candidate has a large implementation and CounterQ wants to test whether they can locate the incorrect invariant.

Question:

> "What guarantees that your window boundary never moves backwards?"

Do not simultaneously highlight the faulty line.

That would weaken the diagnostic probe.

---

# 76. Code-reference policy

Highlighting is allowed when:

- candidate already referenced the code;
- the question is explicitly about a known line/choice;
- location itself is not part of the challenge.

Avoid when:

- bug location is diagnostic;
- highlighting narrows search too strongly;
- CounterQ is asking a general correctness question.

The Examiner intent may carry a code range internally.

The policy gate decides whether candidate-visible highlighting is permitted.

---

# 77. Problem constraint highlighting

A similar optional Phase 1 capability may highlight a relevant problem constraint when CounterQ asks:

> "Does your approach still work with the stated memory limit?"

This should be used sparingly.

Do not create a generalized annotation platform in Phase 1.

---

# 78. Visual stress policy

Reject:

- streaks;
- scores;
- lives;
- combos;
- flashing errors;
- celebratory confetti;
- red pulse around timer;
- "probe count";
- "interviewer difficulty meter."

Pressure should come from:

- genuine uncertainty;
- time;
- technical reasoning.

CounterQ is not an arcade experience.

---

# 79. Active interview feedback

## Simulation

No visual:

- correctness badge;
- score;
- strength indicator;
- green/red answer indicator.

## Coach

Hints may show a subtle:

```text
Coach guidance
```

label.

Do not show real-time scoring.

---

# 80. Interview completion

When CounterQ finishes:

spoken:

> "That's all for this interview."

Then transition to a completion surface.

Do not instantly replace the entire UI with a dense analytics dashboard before the closing audio finishes.

---

# 81. Completion screen

Initial completion state should be restrained.

Example:

```text
Interview complete

Your session is saved.

Problem: Longest Substring Without Repeating Characters
Mode: Simulation
Duration: 27 min

Preparing your interview analysis…

[ View available session details ]
```

Do not display gimmicky metrics such as:

> "You survived 7 probes."

---

# 82. Progressive report handoff

Post-session derived work may still be processing.

The user should immediately have access to:

- completed session state;
- problem;
- code;
- transcript;
- execution history;
- basic session metadata.

As derived artifacts become available:

- report;
- CounterMap;
- mastery updates;

the UI may progressively reveal them.

The user must not be trapped on one full-screen spinner.

Do not fabricate a summary before evidence processing is complete.

---

# 83. Report failure

If report generation fails:

show:

> **Your interview is saved, but the detailed report could not be generated yet.**

The session remains accessible.

Do not imply the interview data was lost.

A retry mechanism may be available.

---

# 84. Privacy indicators

Candidate must always know when:

- microphone is active;
- microphone is muted;
- voice is disconnected.

"Listening" means the microphone is actively participating in the current realtime session while unmuted. It does **not** imply that CounterQ stores raw microphone audio indefinitely.

Raw-audio retention policy remains governed by the frozen architecture/privacy rules and should be disclosed outside the live room where appropriate.

Phase 1 native Interview Room does not require:

- webcam;
- screen sharing;
- continuous screen capture.

If selected screenshots are ever introduced, separate explicit consent is required.

---

# 85. Analytics events

Candidate UX telemetry may include:

- `interview_readiness_opened`
- `interview_started`
- `voice_connected`
- `first_candidate_turn`
- `run_clicked`
- `custom_test_created`
- `candidate_requested_hint`
- `candidate_interrupted_counterq`
- `counterq_prompt_delivered`
- `conversation_history_opened`
- `candidate_ended_early`
- `reconnect_started`
- `reconnect_succeeded`
- `reconnect_failed`
- `interview_completed`
- `report_opened`

Analytics payloads must not contain:

- transcript content;
- candidate source code;
- private problem text;
- hidden Examiner rationale.

---

# 86. Experience metrics

---

## Voice quality

Track:

- time to first CounterQ audio;
- candidate-turn-to-response latency;
- barge-in stop latency;
- voice connection failure rate;
- reconnect rate;
- reconnect success;
- microphone setup failures.

---

## Interview usability

Track:

- first-interview completion;
- early-abandon rate;
- Run usage;
- custom-test usage;
- hint request rate;
- transcript drawer usage;
- accidental navigation/refresh recovery;
- End Interview use.

---

## Experience quality

Collect explicit candidate feedback such as:

- "CounterQ interrupted me unnecessarily."
- "CounterQ understood my code."
- "The interviewer questions were relevant."
- "The voice conversation felt natural."
- "It felt like a real technical interview."

Avoid trying to infer subjective interview realism solely from telemetry.

---

# 87. Experience budgets

Phase 1 should define engineering targets for:

```text
turn latency budget
barge-in stop budget
reconnect recovery budget
prompt-length budget
visual interruption budget
```

Exact SLA values should be benchmarked rather than frozen here.

The UX principle is:

> **If CounterQ feels slow or constantly interruptive, technical intelligence alone will not save the experience.**

---

# 88. Failure-state matrix

| Failure | Candidate sees | Continue? | Timer | Code safety | Recovery |
|---|---|---|---|---|---|
| Microphone denied before start | Permission guidance | No interview start | Not started | N/A | Grant permission |
| Microphone lost mid-session | Voice unavailable/reconnecting | Briefly, code only | Policy-controlled | Preserved | Reconnect/device fix |
| Voice provider failure | "Voice reconnecting…" | Short grace only | Configurable grace | Preserved | Recreate voice session |
| Control WebSocket failure | "Connection interrupted…" | Limited local editing | Configurable | Latest durable snapshot safe | Reconnect |
| Backend unavailable | Recovery state | No unsafe progression | Policy-controlled | Local + durable snapshot | Retry/recover |
| Code execution unavailable | Run disabled, clear message | Yes, if voice healthy | Continues | Preserved | Retry executor |
| Persistence failure | "Trouble saving interview" | Only bounded buffering | Policy-controlled | Local state retained | Recover or graceful stop |
| Report generation failure | Post-session warning | Interview already done | N/A | Preserved | Retry report |
| Reconnect timeout | Clear interrupted-session state | No normal live continuation | Ends/policy | Preserved | Finish interrupted session |

---

# 89. Voice unavailable but code works

Persistent voice failure should **not** degrade into a generic text-chat interview in Phase 1.

Voice is core to the product.

Recommended behavior:

1. controlled reconnect;
2. preserve code/state;
3. short grace period;
4. if unrecoverable, close session as interrupted.

This is preferable to delivering a substantially different product without warning.

---

# 90. Code execution unavailable but voice works

CounterQ may continue temporarily because reasoning and coding remain possible.

Candidate can:

- write code;
- explain;
- manually trace.

Run UI shows:

> **Code execution is temporarily unavailable.**

CounterQ must not pretend any execution occurred.

If the failure persists and materially harms the interview:

- candidate may continue as reasoning-only;
- or end early.

Do not automatically cancel a strong voice interview because one Run fails.

---

# 91. Wireframe 1 — Pre-interview readiness

```text
┌──────────────────────────────────────────────────┐
│ CounterQ                                         │
│                                                  │
│ Ready for your interview?                        │
│                                                  │
│ Mode        Simulation                           │
│ Level       New Grad                             │
│ Language    C++                                  │
│ Duration    Standard Interview                   │
│                                                  │
│ Microphone       ✓ Ready                         │
│ Audio output     ✓ Ready                         │
│ Voice service    ✓ Connected                     │
│ Code runtime     ✓ Available                     │
│                                                  │
│ Simulation won't confirm whether you're right    │
│ while the interview is running.                  │
│                                                  │
│                    [ Start Interview ]            │
└──────────────────────────────────────────────────┘
```

---

# 92. Wireframe 2 — Problem reading

```text
┌───────────────────────────────────────────────────────────────┐
│ CounterQ   SIMULATION        29:18        ● Listening    End  │
├───────────────────────┬───────────────────────────────────────┤
│ Longest Substring     │                                       │
│ Without Repeating     │       Monaco Editor                   │
│ Characters            │                                       │
│                       │  class Solution {                     │
│ Given a string...     │      ...                              │
│                       │  }                                    │
│ Example 1             │                                       │
│ ...                   │                                       │
│                       │                                       │
│ Constraints           │                                       │
│ ...                   │                                       │
├───────────────────────┴───────────────────────────────────────┤
│ Run                                    Execution hidden       │
├───────────────────────────────────────────────────────────────┤
│ ◉ CounterQ                                                   │
│ “Take a moment to read it, then tell me how you understand   │
│  the problem.”                                    [History]   │
└───────────────────────────────────────────────────────────────┘
```

---

# 93. Wireframe 3 — Active coding + listening

```text
┌───────────────────────────────────────────────────────────────┐
│ CounterQ   SIMULATION        18:43        ● Listening    End  │
├───────────────────────┬───────────────────────────────────────┤
│ Problem               │                                       │
│                       │ int left = 0;                          │
│                       │ for (int right = 0; ... ) {            │
│                       │     ...                                │
│                       │ }                                     │
│                       │                                       │
│                       │                                       │
├───────────────────────┴───────────────────────────────────────┤
│ [ Run ]                      Test / Output                     │
├───────────────────────────────────────────────────────────────┤
│ ◉ CounterQ                                                   │
│ Listening                                                    │
│                                        [Recent conversation]  │
└───────────────────────────────────────────────────────────────┘
```

No prompt is required simply because the candidate is coding silently.

---

# 94. Wireframe 4 — Technical question

```text
┌───────────────────────────────────────────────────────────────┐
│ CounterQ   SIMULATION        16:21     ◉ CounterQ speaking   │
├───────────────────────┬───────────────────────────────────────┤
│ Problem               │                                       │
│                       │ left = last[s[right]] + 1;             │
│                       │                                       │
│                       │                                       │
├───────────────────────┴───────────────────────────────────────┤
│ [ Run ]                                                      │
├───────────────────────────────────────────────────────────────┤
│ ◉ CounterQ                                                   │
│ “What guarantees that left never moves backwards?”           │
│                                        [Recent conversation]  │
└───────────────────────────────────────────────────────────────┘
```

The candidate may start speaking immediately and interrupt remaining audio.

---

# 95. Wireframe 5 — Test failure / debugging

```text
┌───────────────────────────────────────────────────────────────┐
│ CounterQ   SIMULATION        12:08        ● Listening    End  │
├───────────────────────┬───────────────────────────────────────┤
│ Problem               │ Monaco                                │
│                       │                                       │
├───────────────────────┴───────────────────────────────────────┤
│ Input            abba                                         │
│ Expected         2                                            │
│ Output           3                                            │
│ Status           Failed                                       │
├───────────────────────────────────────────────────────────────┤
│ ◉ CounterQ                                                   │
│ Listening                                                    │
└───────────────────────────────────────────────────────────────┘
```

CounterQ does not immediately speak.

The candidate gets first opportunity to debug.

---

# 96. Wireframe 6 — Coach hint

```text
┌───────────────────────────────────────────────────────────────┐
│ CounterQ   COACH             14:42        ● Listening    End  │
├───────────────────────┬───────────────────────────────────────┤
│ Problem               │ Monaco                                │
│                       │                                       │
├───────────────────────┴───────────────────────────────────────┤
│ [ Run ]                                      [ Ask for hint ] │
├───────────────────────────────────────────────────────────────┤
│ Coach guidance                                                │
│ “Try tracing a case where the repeated character is already  │
│  outside your current window.”                               │
└───────────────────────────────────────────────────────────────┘
```

No visible numeric hint level.

---

# 97. Wireframe 7 — Reconnecting

```text
┌───────────────────────────────────────────────────────────────┐
│ CounterQ   SIMULATION        11:09      ○ Reconnecting…  End │
├───────────────────────┬───────────────────────────────────────┤
│ Problem               │ Monaco                                │
│                       │ latest code remains visible            │
│                       │                                       │
├───────────────────────┴───────────────────────────────────────┤
│ Run temporarily unavailable                                  │
├───────────────────────────────────────────────────────────────┤
│ Voice reconnecting…                                           │
│ Latest acknowledged progress is safe.                          │
└───────────────────────────────────────────────────────────────┘
```

---

# 98. Wireframe 8 — Interview complete

```text
┌──────────────────────────────────────────────────┐
│ CounterQ                                         │
│                                                  │
│ Interview complete                              │
│                                                  │
│ Longest Substring Without Repeating Characters   │
│ Simulation · C++ · 27 min                        │
│                                                  │
│ Your interview is saved.                         │
│                                                  │
│ Interview analysis is being prepared.            │
│                                                  │
│ [ View session ]                                 │
└──────────────────────────────────────────────────┘
```

No fake score before evidence processing completes.

---

# 99. Component architecture

Recommended React component hierarchy:

```text
InterviewExperience
├── PreInterviewReadiness
│   ├── InterviewConfigurationSummary
│   ├── MicrophoneCheck
│   ├── AudioOutputCheck
│   ├── RealtimeHealthCheck
│   ├── CodeRuntimeHealthCheck
│   └── StartInterviewButton
│
└── InterviewRoom
    ├── InterviewHeader
    │   ├── CounterQBrand
    │   ├── ModeBadge
    │   ├── InterviewTimer
    │   ├── VoiceConnectionStatus
    │   └── EndInterviewButton
    │
    ├── InterviewWorkspace
    │   ├── ProblemPanel
    │   │   ├── ProblemTitle
    │   │   ├── ProblemStatement
    │   │   ├── ProblemExamples
    │   │   └── ProblemConstraints
    │   │
    │   └── CodingWorkspace
    │       ├── EditorToolbar
    │       │   └── LanguageDisplay
    │       ├── MonacoInterviewEditor
    │       └── ExecutionPanel
    │           ├── RunButton
    │           ├── TestInput
    │           ├── TestResults
    │           └── ExecutionError
    │
    ├── InterviewerSurface
    │   ├── VoicePresence
    │   ├── ActivePrompt
    │   ├── CoachGuidanceBadge
    │   └── RecentConversationButton
    │
    ├── RecentConversationDrawer
    │   ├── InterviewerTurns
    │   └── CandidateTranscript
    │
    ├── CoachControls
    │   └── RequestHintButton
    │
    ├── InterviewRecoveryOverlay
    │   ├── RestorationState
    │   ├── ReconnectState
    │   └── PersistenceFailureState
    │
    └── InterviewConnectionLayer
        ├── CounterQWebSocketClient
        ├── RealtimeVoiceClient
        ├── InterviewRestoreCoordinator
        └── PersistenceSyncState
```

`CoachControls` is absent in Simulation.

---

# 100. UI state ownership

The frontend must respect backend/realtime ownership boundaries.

---

# 101. Client-local state

Owned only by the browser unless restoration convenience warrants persistence.

Examples:

- problem/editor splitter position;
- execution panel expansion;
- transcript drawer open/closed;
- current editor selection;
- scroll positions;
- local audio output volume;
- reduced-motion preference.

These states do not affect interview truth.

---

# 102. Server-authoritative state

FastAPI/Interview Orchestrator owns:

- InterviewSession status;
- interview mode;
- candidate level;
- selected language;
- server deadline;
- current interview stage;
- state version;
- session budgets;
- hint/probe budget;
- Prompt authorization;
- session completion;
- latest accepted code snapshot;
- durable transcript;
- execution records.

Frontend never invents these.

---

# 103. Realtime-provider state

Realtime provider/adapter owns immediate media facts such as:

- audio transport connected;
- CounterQ currently producing speech;
- candidate voice activity;
- partial transcript;
- provider connection quality.

Critical provider events are normalized back into CounterQ state where required.

---

# 104. Derived UI state

Frontend may derive:

- displayed timer from server deadline;
- `Listening` label from voice state;
- whether Coach hint badge should show;
- whether Run is currently disabled;
- whether reconnect overlay appears;
- active substantive prompt styling.

Derived state must not become authoritative.

---

# 105. Ownership example — timer

Server stores:

```text
deadline_at
```

Browser displays:

```text
deadline_at - synchronized_current_time
```

Refresh gets the same deadline.

Frontend does not persist a local:

```text
remaining_seconds
```

as the source of truth.

---

# 106. Ownership example — active prompt

Server authorizes:

```text
InterviewerPrompt
```

Realtime provider may then speak it.

Authorization alone is **not candidate-visible state**.

`PromptDelivery` records what actually reached the candidate.

Frontend displays substantive prompt text from delivery-aware state:

```text
authorized
    ↓
delivery_started
    ↓
partial / completed / interrupted
```

Rules:

- `authorized` only: do not display;
- `delivery_started`: show only text that has actually begun/been delivered where provider synchronization supports it;
- `completed`: show finalized delivered wording;
- `interrupted`: never reveal the undisclosed remainder;
- `stale/rejected before delivery`: never display.

A local acknowledgement must not accidentally replace the active substantive question.

---

# 107. Phase 1 scope cuts

The following should not be built initially.

## AI avatar

No.

## Candidate webcam

No.

## Video interviewer

No.

## Screen recording

No.

## Continuous vision

No.

## Collaborative whiteboard

No.

## Multi-file IDE

No.

## Full terminal

No.

## Debugger

No.

## AI autocomplete

No.

## Mobile-first coding interface

No.

## Large voice marketplace

No.

## Live score dashboard

No.

## Probe counters

No.

## Animated emotions

No.

## Visible internal interview stages

No.

## Rich code annotation platform

No.

## Candidate-facing hidden-test judge

No for initial Phase 1.

Hidden verification may exist behind the scenes where useful, but the room should not become a competitive-programming submission interface.

These cuts protect the quality of:

- realtime voice;
- Monaco experience;
- Examiner behavior;
- prompt timing;
- recovery.

---

# 108. Technical Core Interaction Spike

The first technical spike needs only the Interview Room subset necessary to prove CounterQ's defining interaction.

The spike is not a miniature finished product.

It should test the hardest system interaction.

---

# 109. Spike components required

## Interview shell

- minimal InterviewRoom;
- fixed test mode/configuration;
- one known problem.

## Header

- timer;
- voice state;
- end/reset for development;
- no production navigation.

## Problem Panel

- one curated problem;
- statement;
- constraints.

## Monaco

- one or two languages at most for spike;
- editing;
- source version tracking;
- meaningful snapshot emission.

C++ alone is acceptable for the earliest spike.

## Voice

- microphone;
- realtime provider connection;
- natural CounterQ speech;
- barge-in;
- finalized transcript handling.

## Interviewer Surface

- voice status;
- current substantive prompt.

## Control channel

- browser ↔ FastAPI WebSocket.

## Examiner integration

Enough UI wiring for:

- candidate turn event;
- code event;
- authorized probe;
- stale-probe cancellation.

## Minimal persistence

Enough persistence to inspect afterward:

- finalized transcript;
- code snapshots;
- ExaminerDecision;
- InterviewerPrompt;
- PromptDelivery;
- CandidateResponse;
- basic Evidence candidate if required.

## Run

Optional for the very first voice+code probe test.

Required before calling the overall core interaction spike complete.

---

# 110. Spike does not require

- polished login;
- onboarding;
- Session Report;
- CounterMap UI;
- Mastery UI;
- payments;
- billing;
- broad problem library;
- hidden tests;
- custom problem ingestion;
- full three-language support;
- polished responsive behavior;
- Coach hint ladder;
- report generation;
- analytics dashboard.

---

# 111. Core spike flow

The spike must demonstrate:

```text
Candidate enters interview
        ↓
Problem is visible
        ↓
Monaco is usable
        ↓
Realtime microphone works
        ↓
CounterQ speaks naturally
        ↓
Candidate explains approach
        ↓
Candidate modifies code
        ↓
Speech and code reach CounterQ concurrently
        ↓
Observation detects meaningful issue
        ↓
Examiner prepares technical target
        ↓
CounterQ does not interrupt immediately
        ↓
Natural conversational boundary occurs
        ↓
Policy confirms issue is still relevant
        ↓
CounterQ asks concise technical question
        ↓
Candidate interrupts CounterQ if desired
        ↓
Response is captured
        ↓
Evidence/provenance can be inspected afterward
```

---

# 112. Canonical spike scenario — verbal claim

Candidate:

> "I'll use unordered_map because lookup is always O(1)."

Expected room behavior:

1. voice transcript flows;
2. no visible "analysis";
3. candidate can continue talking;
4. Examiner detects candidate claim;
5. at natural boundary CounterQ asks:

> "You said always. Is that actually guaranteed?"

6. current question appears in Interviewer Surface;
7. candidate answers naturally;
8. no chat typing required.

---

# 113. Canonical spike scenario — code issue

Candidate writes code with a backwards-moving window boundary.

Expected:

1. Monaco emits meaningful snapshot;
2. Examiner detects suspicious invariant;
3. no immediate red code marker appears;
4. no "bug detected" UI appears;
5. candidate continues working;
6. if candidate self-corrects, pending question disappears/stales;
7. otherwise CounterQ asks at useful boundary:

> "What guarantees that `left` never moves backwards?"

This is the defining native-code experience.

---

# 114. Phase 1 acceptance criteria

The Interview Room is not launch-ready until the following are true.

---

## Core interaction

- Candidate can complete an interview without using text-chat input.
- Candidate can read problem, speak, code, run and finish from one focused surface.
- CounterQ can ask a substantive question without moving focus away from Monaco.
- Candidate can continue coding while backend Examiner reasoning occurs.

---

## Voice

- Candidate can speak naturally without push-to-talk.
- CounterQ can be interrupted without pressing a stop button.
- Interrupted CounterQ speech stops promptly enough to feel conversational.
- Voice reconnection does not reset the interview.
- Current substantive question remains readable.

---

## Code

- Monaco remains responsive during voice activity.
- Meaningful code snapshots are associated with the correct session.
- Run results reference the exact submitted snapshot.
- Candidate code survives normal refresh/reconnect.
- UI distinguishes acknowledged durable code from newer local pending edits during connection failure.
- The product never claims unsynced edits are saved when persistence is unconfirmed.
- AI autocomplete is absent.

---

## Prompt behavior

- UI never shows ProbeStrategy, ExaminerDecision or hidden target.
- Conversational acknowledgements do not overwrite the substantive current question.
- Only one substantive active question is visually emphasized.
- Authorized-but-undelivered prompts are never shown to the candidate.
- Interrupted prompts never expose undisclosed question text.
- Conversation history contains delivered prompts, not internal Examiner candidates.
- Stale probes never appear after candidate correction.

---

## Mode correctness

- Simulation exposes no hint control.
- Simulation exposes no live correctness feedback.
- Coach provides a help-request control.
- Coach assistance is visibly distinguishable as guidance without exposing internal hint levels.

---

## Session control

- Timer is server-authoritative.
- Refresh cannot reset timer.
- Refresh cannot reset mode.
- Refresh does not replay the introduction.
- Candidate can safely End Interview at any stage.
- End Interview preserves completed evidence.

---

## Failure recovery

- Short voice disconnect preserves code and session state.
- Control-channel reconnect can restore the active interview.
- Run becomes unavailable when execution cannot be safely reached.
- Persistence failure is surfaced honestly.
- Voice failure does not silently degrade into a generic text chatbot.

---

## Visual quality

- Room remains usable at common laptop resolutions.
- Monaco is the dominant interactive surface.
- Problem remains readable without modal navigation.
- Voice presence is persistent but visually secondary.
- No major UI animation competes with technical work.
- Timer remains visible without dominating normal attention.

---

## Accessibility

- Candidate can reread current question.
- Voice state does not rely on color alone.
- Important controls are keyboard reachable.
- Reduced-motion mode remains usable.
- Muting microphone is always accessible.

---

# 115. Product quality acceptance tests

Before public launch, user testing should verify statements such as:

> "I forgot I was using an AI interface and focused on the interview."

> "It seemed to notice what I actually wrote."

> "It gave me enough time to think."

> "The questions felt related to my reasoning."

> "I could interrupt it naturally."

> "I never had to manage the interface while solving."

Negative signals include:

> "It felt like ChatGPT with Monaco attached."

> "I kept looking at the transcript instead of thinking."

> "It talked too much."

> "It interrupted me while I was fixing the bug."

> "The AI animation distracted me."

> "I wasn't sure whether it could hear me."

These should be treated as product defects, not merely subjective preferences.

---

# 116. Final UX principles

1. **The editor is the primary workspace.**

2. **Voice is the primary interviewer channel.**

3. **CounterQ should feel present, not visually dominant.**

4. **Silence must feel permitted.**

5. **One substantive question at a time.**

6. **Candidate speech owns the floor.**

7. **The candidate can always reread the current question.**

8. **Do not expose hidden Examiner machinery.**

9. **Do not gamify interview pressure.**

10. **Do not turn Simulation into a tutor.**

11. **Do not turn Coach into autocomplete.**

12. **Code failures belong to the candidate first.**

13. **Reconnect should feel recoverable, not like restarting.**

14. **AI reasoning latency should usually remain invisible.**

15. **The room should feel calmer than the intelligence operating underneath it.**

16. **The problem should remain visible without fighting the editor for attention.**

17. **Normal editor convenience is acceptable; AI solution generation is not.**

18. **Passing tests does not equal passing the interview.**

19. **Do not make candidates operate an AI system while they are supposed to be solving a problem.**

20. **Every visible element must justify the attention it consumes.**

21. **Authorization is internal; delivery is what the candidate sees.**

22. **Never reveal the undisclosed remainder of an interrupted question.**

23. **Recovery copy must distinguish saved progress from merely local pending edits.**

24. **Hidden validation must not turn the room into an online judge.**

The final design rule is:

> **CounterQ's Interview Room should look simpler than the system behind it.**

> **The candidate should remember the questions CounterQ asked—not the interface they had to operate.**
