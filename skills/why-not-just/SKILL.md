---
name: why-not-just
description: Finds the answer an adult plan misses by asking what a bright five-year-old asks - why do we need this, and what can we throw away - then attacks the result. Use when a plan, design or fix has grown, when the simple answer has not been tried, or when invoking /why-not-just.
status: Active
version: 0.1.0
triggers:
  - /why-not-just
integrates:
  - red-teaming-strategic-decisions
  - hypothesis-validation
---

# /why-not-just "<want>"

An adult plan grows because people default to adding (Adams et al. 2021). This skill copies the missing habit, never "child genius": children are weak at inventing tools alone (Beck et al. 2011). So a session with the repo still does the building. The law is in `rules/reasoning-lenses.md`: anything may propose, and only what survives attack may decide.

Write the plain "just do the simple thing" answer first, and keep it. It is what the rest of this run has to beat.

## Steps

1. **Say the want** in a five-year-old's words, and keep what would prove it wrong.
2. **Blind seats.** Outside models get only that want, with no repo, rules or design. Send it through the privacy floor, and cap the calls for the run.

   ```python
   from lib.decision_privacy import may_send
   from lib.sovereign_council import fan_out

   ok, why = may_send(want)              # stop if ok is False
   results, wall = fan_out(want, ["deepseek", "xai", "groq", "gemini"])
   ```

3. **Why ladder.** For each requirement in the adult plan, ask "why do we need this?" until it has evidence or has none. A requirement that ends with no evidence is marked for deletion.
4. **Delete and run.** Remove a block, run the tests, and restore it. Green means unneeded or untested, and both are worth knowing. This step proposes, and it never deletes on its own.

   ```python
   from lib.mutation_proof import mutated, run

   with mutated(path, old=block, new=""):          # restored byte for byte on exit
       result = run(["bin/grip-py", "-m", "pytest", "-q", test_file])
   stayed_green = result.returncode == 0
   ```

5. **Naive wins by default.** The simple answer stands until a test rejects it, and complexity earns itself with a failing test. Weak tests can let a wrong answer win, so an attacker gets one try at writing the test that rejects it first.
6. **Build and retry.** A session with the repo builds what survives. A seat hears once why its idea failed, and tries again.
7. **Attack.** Climb the ladder in `rules/reasoning-lenses.md` and stop at the first kill. The verdicts are KILLED, SURVIVED SO FAR and UNTESTABLE.

## What to report

For each idea, state its fate against the plain answer: what it changed, or that it changed nothing. Name the seats that answered and the ladder step that killed anything. If the method changed nothing, say so.

## Limits

- A blind seat is still a trained model, and only the repo is hidden from it.
- Whether blind seats beat full context is not established. `docs/measurements/2026-11-01-w3-by-hand-trial.md` holds the first trial, and its credit is for looking first and nothing more.
- Delete and run says nothing about text that no test reads, such as a rule.
