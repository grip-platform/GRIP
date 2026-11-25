---
name: dojo
description: Teach yourself GRIP by USING GRIP. Three hands-on lessons (prompt-coach, save+recall, converge), each gated on a command that demonstrably ran — never on grading prose. Comprehension checks are A/B/C text (survive channels-suppressed AskUserQuestion). Delight rides the existing sigil passport; flourishes are learner-invoked only, the floor is silence. Use when the operator invokes /dojo or wants a guided, runnable intro to GRIP's core moves.
status: Active
effort: low
version: 1.0.0
triggers:
  - "/dojo"
  - "teach me grip"
  - "grip tutorial"
  - "how do I use grip"
integrates:
  - sigil.py
see_also:
  - prompt-coach
  - converge
  - saving-state
  - recall
  - pedagogical-patterns
  - asking-users
  - creating-wizards
  - hitl
  - prompt-routing
---

# /dojo — Teach Yourself GRIP, by Using GRIP

You learn GRIP the only way that sticks: by running it on your own work. Each
lesson has GRIP do the move once (a worked example), then **you** do it on
something real. A lesson completes when the command **demonstrably ran** — its
own output is the proof. You cannot pass by saying the right words; you pass by
doing the thing.

> **DISPLAY NAME** — this skill's learner-facing name is the single constant
> below. A rename (e.g. `/grip-kata`, `/grip-academy`) is ONE edit here; do not
> hardcode "dojo" in learner-facing copy elsewhere.
>
> `DOJO_DISPLAY_NAME = "the dojo"`

---

## The shape of every lesson (ONE shape — DRY)

Five beats, every lesson, no exceptions. (Beat names map to
`skills/pedagogical-patterns/SKILL.md`.)

1. **HOOK** — one plain line: the problem this move solves. *(FASTER: Forget)*
2. **WORKED EXAMPLE** — GRIP performs the move once, fully, BEFORE the learner
   generates anything. *(pedagogical-patterns §3 "Worked Examples", ending in
   "### Your Turn".)*
3. **YOUR TURN** — the learner runs the real, tiny, safe command. **This is the
   completion gate** — see §"The completion gate" below. *(FASTER: Act)*
4. **COMPREHENSION CHECK** — ONE A/B/C **text** question (see §"Channels-safe
   checks"). It teaches understanding; it does NOT gate completion (the
   observable run does). On "C / not sure", a deeper Teach-Back fires
   (pedagogical-patterns §1).
5. **NEXT** — one sentence naming the next capability unlocked. *(Scaffolded.)*

**Review callbacks, NOT "spaced repetition".** A single sitting can't guarantee
the elapsed wall-clock real spacing needs, so do not call anything "spaced
repetition". Instead, on entering Lesson 3, re-surface in one line the L1
prompt-coach reflex and the L2 save reflex (FASTER Enter+Review). Real
wall-clock spacing is a deferred option, never a relabel.

---

## The completion gate — OBSERVABLE, never an LLM grading prose

**This is the most important rule in the skill.** A lesson is NOT completed
because an answer "sounded right". Sycophantic models pass everyone, which would
make every belt worthless. A lesson completes **iff the command demonstrably
ran**, checked mechanically:

| Lesson | Mechanical completion check (observable, no LLM verdict) |
|--------|-----------------------------------------------------------|
| L1 prompt-coach | prompt-coach produced a **non-empty rewrite** of the learner's pasted prompt |
| L2 save + recall | `state/` mtime **advanced** after `/save` AND `/recall` returned **non-empty** |
| L3 converge | a converge run emitted its `Depth N/max_depth \| Score` line on the learner's input |

This is the `rules/exogenous-anchor-law.md` move applied to teaching: the gate's
evidence is **exogenous to the model's own judgement**. The A/B/C check teaches;
the **unlock is the observable run**.

---

## Channels-safe checks (non-negotiable)

`AskUserQuestion` is globally suppressed whenever channels are loaded
(`rules/operational-context.md` "All HITL prompts in skills will fail silently
while channels are loaded"). Therefore **every comprehension check is authored
text-first** as A/B/C — mirroring `skills/prompt-routing/SKILL.md` "Path B —
Inline text fallback":

```
Teach-back — reply A, B, or C:
  A) <option>     B) <option>     C) Not sure — show me again
```

`AskUserQuestion` is a **progressive-enhancement wrapper IF it is available** —
never the sole path. Read the learner's next message for the first character
matching `A|B|C` (or the word `skip`). The learner *experiences* a well-formed
`/asking-users`-shaped choice (≤12-char framing, 2–4 options, a real "not sure"
that re-teaches, no junk "Other") — that experience IS the dogfooding of HITL.
Do **not** ask the learner to inspect a rendered picker's internals — you can't
see them; the learner experiences good HITL by being asked, not by auditing it.

---

## Delight — subtle, escalating, learner-invoked flourishes (the kill-line)

Currency is the **existing** `state/sigils/passport.jsonl`. No new counter, no
`state/dojo/`.

The functional-vs-flourish split (load-bearing):

- GRIP auto-emits **at most ONE terse FUNCTIONAL line** per earned event —
  e.g. `Green belt unlocked. New: /converge.` This is a *state-change
  notification* (same class as a build-result line), NOT a celebration.
- A **flourish** (`/sigil show`, `/aurora`, `/rave`, `/gg`) is
  **LEARNER-INVOKED ONLY** — GRIP NEVER auto-fires one. (Mirrors
  `commands/sigil.md`: mint is milestone-triggered, *render* is invoked.)
- **Floor = silence.** Repeated / non-first work in a tier the learner already
  cleared → plain task output, zero notification.
- **Failure = plain encouragement + the scaffold** (pedagogical-patterns
  "Scaffolded") — e.g. `Not quite — criterion wants ≤3 sentences, you have 5.
  Try --depth 2.` Never decorated, never cold silence.

Escalation curve:

| Tier | Functional line GRIP emits (terse, ≤1 line) | Flourish (learner must TYPE) |
|------|----------------------------------------------|------------------------------|
| L1 first success | `Saved nothing yet — but /recall "last session" is how work comes back.` | none |
| repeat (any) | *(silence)* | none |
| L2 first | `Recall works. Your work now survives a /clear.` | none |
| L3 first converge | `Converge ran. Max depth or criterion — both count.` + quiet sigil mint to passport | `/sigil show` (optional) |
| graduation (all 3) | `Three for three. You've got the core of GRIP.` + earned sigil | `/aurora` · `/gg` · `/sigil show` |

**Modal learner** (never finishes): mint a quiet sigil at each **first-of-tier**
verified event (using `commands/sigil.md`'s `verify=` contract), so delight is
not graduation-gated-dead for people who only ever do Lesson 1.

### THE HARD KILL-LINE

> **DELIGHT IS A SEASONING, NOT A MEAL.**
> A **flourish** (`/sigil show`, `/aurora`, `/rave`, `/gg`, any rendered
> celebration) is **LEARNER-INVOKED ONLY** — GRIP NEVER auto-fires one. A
> **functional notification** (one terse line: what you unlocked, which command
> is now available) is permitted at an earned event, max ONE line, NEVER on
> repeated / non-first work in a known tier (silence is correct), NEVER
> decorated.
> NEVER, anywhere before graduation: emoji-confetti, an XP number, a streak
> counter, a progress-bar animation, a mascot / character voice, or
> congratulatory tone ("nice work!", "you did it!", "Routed it yourself").
> State the fact, name the unlock, stop.
> Failure gets PLAIN ENCOURAGEMENT + the scaffold (teach the gap), NEVER a
> flourish and NEVER cold silence.
> **THE TEST (R0 self-falsification):** would a senior engineer (David, V>>)
> doing this at 2am read this as a respectful status line — or close the
> terminal because the tool *winked* at them? Any doubt → cut to the bare
> functional fact. A lesson with zero flourishes is correct; one that auto-fires
> even one is broken.
> **A denylist cannot catch cringe of TONE — read the actual rendered line and
> judge it. The token-count test is a necessary floor catching ~20%; the
> 2am-test is the real gate.**

---

## Opener order

Recommend **L1 (prompt-coach) first** — highest immediate "aha". BUT if the
learner has no prompt in flight to sharpen, fall back to **L2 (save+recall)
first** so they have something concrete to run. (This is operator-confirmable —
see the HITL gate at the end.)

---

## Lesson 1 — prompt-coach: vague in, sharp out

**HOOK.** A vague prompt makes GRIP guess. A sharp prompt makes GRIP act. The
fastest single win in GRIP is letting it rewrite your prompt before you run it.

**WORKED EXAMPLE.** GRIP runs `/prompt-coach` on a deliberately vague prompt and
shows the rewrite-first output:

```
Input (vague):  "fix the tests"
prompt-coach →  "Run the failing tests in tests/, identify the root cause of
                 each failure, fix it at the source (not the test), and show
                 the before/after pass count. Don't modify unrelated tests."
```

Notice: it added scope, a done-condition, and a guardrail — the three things a
vague prompt is missing.

**### Your Turn.** Paste a real, vague prompt YOU have right now (something you
were about to ask). Then run:

```
/prompt-coach
```

…on it, and read the rewrite. **Completion gate:** prompt-coach produced a
non-empty rewrite of *your* pasted prompt. (If you have nothing vague in flight,
say so — we'll start with Lesson 2 instead, then return here.)

**COMPREHENSION CHECK:**

```
Teach-back — reply A, B, or C:
  A) prompt-coach guesses what I meant and just does it
  B) prompt-coach rewrites my prompt to add scope + a done-condition + guardrails, for me to run
  C) Not sure — show me again
```

(B is the move. On C, re-run the worked example with a second vague prompt.)

**NEXT.** You can sharpen any prompt now. Next: make your *work* survive a
`/clear` — save and recall.

---

## Lesson 2 — save + recall: your work survives a /clear

**HOOK.** Close the terminal or `/clear`, and the conversation is gone — unless
you saved it. `/save` persists session state; `/recall` brings it back.

**WORKED EXAMPLE.** GRIP runs the round-trip on THIS tutorial session:

```
/save                       → persists this session's state to ~/.claude/state/
/recall "what we just did"  → returns the saved context about THIS dojo session
```

The point isn't magic — it's that what you just did is now recoverable.

**### Your Turn.** Run, in order:

```
/save
/recall "what we just did"
```

**Completion gate:** `state/` mtime advanced after `/save`, AND `/recall`
returned non-empty. **Scope the recall to THIS session's content** — ask
"what we just did", not a cold topic query. (A brand-new GRIP has an empty
knowledge base; a cold `/recall "kubernetes"` on a fresh install legitimately
returns nothing and is NOT a failure of *you* — it's just empty. This is why we
recall *this session*, per `feedback_kono_recall_false_first_session`.)

**COMPREHENSION CHECK:**

```
Teach-back — reply A, B, or C:
  A) /save uploads my code somewhere
  B) /save persists this session's state locally; /recall reads it back so work survives a /clear
  C) Not sure — show me again
```

(B. On C, re-run the round-trip and point at the mtime change.)

**NEXT.** Your work is durable now. Last move, the one that makes GRIP a loop and
not a one-shot: `/converge`.

---

## Lesson 3 — converge: iterate to a criterion, and the TRUE stop rule

> **Review callback (one line each):** before this lesson — remember L1, you can
> `/prompt-coach` any vague prompt; and L2, `/save` makes this recoverable.

**HOOK.** One pass is a guess. `/converge` runs your task again and again,
scoring each pass against a criterion you set, keeping the best — until it's good
enough OR it hits the depth cap.

**WORKED EXAMPLE.** GRIP converges an over-long paragraph down to a tight one.
Use this bundled input (no prior state needed):

```
Input paragraph (over-long):
  "The system, which was originally designed in a way that was honestly quite
   ad-hoc and grew organically over many months without much in the way of an
   overarching plan, has a number of components that interact in ways that are
   sometimes surprising and occasionally undocumented, and as a result new
   contributors often find it difficult to know where to begin."

Command:
  /converge "<that paragraph>" --criterion "≤3 sentences, plain English" --depth 1 --show-work
```

GRIP narrates the TRUE stop reason. **The honest rule (verified against
`skills/converge/SKILL.md`):** converge stops at **score ≥ 93 OR max_depth
reached — whichever comes first** (`converge/SKILL.md:175` `if score >= 93`;
`converge/SKILL.md:252` "Max depth reached"). With `--depth 1`, depth 1 IS the
cap, so the run stops at **"Max depth reached"** — NOT because "84 was good
enough". Two
important caveats it teaches:

- `--show-work` is **default-OFF** (`converge/SKILL.md:257`). You must pass it to
  see every depth, otherwise you only see the final converged output.
- "Converged" in the header can mean *criterion met* OR *max depth reached* —
  read which one (`converge/SKILL.md:252`).

**### Your Turn.** Run converge on YOUR OWN over-long paragraph (paste one), with
`--depth 1 --show-work`, and add "explain only, no files":

```
/converge "<your paragraph>" --criterion "≤3 sentences" --depth 1 --show-work
```

**Completion gate:** a converge run emitted its `Depth N/max_depth | Score` line
on your input.

**COMPREHENSION CHECK:**

```
Teach-back — reply A, B, or C:
  A) converge stops as soon as the output is "good enough" at any depth
  B) converge stops at score ≥ 93 OR when it hits max_depth — whichever comes first
  C) Not sure — show me again
```

(B is the verified rule. On C, re-show the worked example and point at the
`Depth 1/1 | ... | Max depth reached` line.)

**NEXT.** That's the core of GRIP: sharpen the prompt, make work durable, iterate
to a criterion. Bigger work (multi-PR sprints, full mesh councils) exists *when
you need it* — `skills/execution-router/SKILL.md` routes a task scored 5–7 to
`/converge`, 8–10 to converge + tests, 11–13 and 14–15 to `/rsi-optimal`. You do
NOT need those yet; reach for them only when a task genuinely is that big (YAGNI).

---

## Graduation

When all three completion gates have fired this session, emit ONE functional
line and mint the earned sigil (rarity is picked by the work, per
`commands/sigil.md` — never rerolled):

```
Three for three. You've got the core of GRIP.
```

Then — and ONLY if the learner types it — a flourish is available: `/sigil show`
to see the earned sigil in the passport, `/aurora`, or `/gg`. GRIP does not type
these for them.

Append one anonymous line to `logs/dojo-completions.jsonl` (see §"Progress").

---

## Progress / resume (KISS)

- **NO state file. NO `lib/dojo_progress.py`. NO `state/dojo/`.** Three lessons
  run in one sitting. "Where am I" = what you remember + re-run `/dojo` from the
  top. Lessons are idempotent (re-running `/save` is harmless).
- Not having a state file is *deliberate*: it kills the shared-`default`-user
  clobber (GRIP is single-operator-per-`~/.claude`) AND the no-TTL append-grow
  lifecycle violation (`rules/lifecycle-closed-or-expired.md`). Both vanish by
  not having the file.
- **The one persistent artefact** is the graduation sigil in the existing
  `state/sigils/passport.jsonl` (earned, de-duped by `(kind, ref)`, SHA-256).
  That IS the progress record.
- **Finish-rate probe (the ONE measurement that licenses Lesson 4):** append one
  line per completion to `logs/dojo-completions.jsonl`. This is an append-only
  **log** — explicitly the by-design row in `rules/lifecycle-closed-or-expired.md`
  (a finish-rate sample, NOT a "current work" queue; nothing counts its raw line
  count as live work). **Gate to build Lesson 4+:** ≥50% of ≥10 starters finish
  all 3. Note: passport sigils and the finish-rate log intentionally measure
  different populations — passport tracks per-tier engagement (modal learners who
  only complete L1 still earn a sigil); the log tracks full-session graduation
  rate. Both metrics are useful; they are not expected to match.
- **Cross-session resume + `lib/dojo_progress.py` + per-user state + belts-in-
  state are DEFERRED to N=2** (`rules/knowledge-maturation-functor.md`) — built
  only when 2 distinct lessons are proven completed AND resume is
  operator-requested, with per-user isolation + TTL designed in then.

---

## Falsification (R0)

This skill is wrong if:

- **(a)** any operator calls the copy patronising / naggy / "winks" even once →
  strip to bare `<thing> unlocked.`;
- **(b)** the sigil passport shows learners earning an L1 sigil then ZERO further
  across ≥5 sessions → first-of-tier minting isn't firing for non-graduators →
  fix the per-tier trigger;
- **(c)** repeated failed exercises with no retry → the scaffold copy isn't
  teaching → fix the encouragement line (NOT add a flourish);
- **(d)** `logs/dojo-completions.jsonl` shows <50% of ≥10 starters finishing all
  3 → lessons too slow / cringe → cut beats / strip ornament.

All four read from artefacts that EXIST and fire at the volume most learners live
(L1/L3). Track via the passport + the completions log.

---

## Operator decisions (HITL — do NOT decide these autonomously; Rule 7)

These are channels-safe A/B/C text questions for V>> before/after build:

- **D1 — Name:** `/dojo` (recommended) vs `/grip-kata` vs `/grip-academy`.
- **D2 — Belt metaphor vs plain "Lesson N of 3":** plain is the DEFAULT (belts
  risk the exact XP-mechanic/cringe smell `flow-mode` flags). Ask only whether
  V>> wants a belt skin as an opt-in flourish later. Do NOT weld karate belts to
  tier names by default.
- **D3 — Opener:** L1 prompt-coach first (recommended) vs L2 save+recall first.
