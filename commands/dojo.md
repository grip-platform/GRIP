# /dojo — teach yourself GRIP by using GRIP

A three-lesson, hands-on tour of GRIP's core moves. You don't read about the
commands — you run them on your own work, and a lesson only completes once the
command **demonstrably ran**. No grading of prose, no quizzes that pass on
vibes: the evidence is the command's own output.

The three lessons:

1. **prompt-coach** — turn a vague prompt into a sharp one.
2. **save + recall** — make your work survive a `/clear`.
3. **converge** — iterate any text to a criterion and learn the true stop rule.

## Usage

- `/dojo` — start (or restart) from the top. Lessons are idempotent — re-running
  is always safe.

Invoke the skill: `dojo`.

Backed by `skills/dojo/SKILL.md`. There is no progress file and no per-user
state — the only thing the dojo persists is a graduation sigil in your existing
`state/sigils/passport.jsonl`, and one anonymous finish-rate line in
`logs/dojo-completions.jsonl`.

## What it is NOT

The dojo emits at most **one terse functional line** per thing you unlock
("Green belt unlocked. New: `/converge`."). It never auto-fires a celebration.
Flourishes (`/sigil show`, `/aurora`, `/gg`) exist, but **you** type them — GRIP
never does it for you. Delight is a seasoning, not a meal.
