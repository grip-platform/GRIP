---
name: knife-fight
description: Puts the Occam habit and Why Not Just against each other on one real problem. Each proposes, each attacks the other's answer under blind labels, and the result goes on a scoreboard by kind of problem. Use when two approaches compete, when a plain answer needs a real challenger, or when invoking /knife-fight.
status: Active
version: 0.1.1
triggers:
  - /knife-fight
integrates:
  - why-not-just
---

# /knife-fight "<problem>"

Two ways of thinking go in, and a scoreboard learns which one wins on which kind of problem. It costs outside-model calls only, with no Claude fan-out.

## Steps

1. **Write the plain answer first,** and keep it. "Neither" is a real result when it wins.
2. **Name the kind of problem** in one word, such as `bug`, `design`, `cost` or `process`, so that the scoreboard means something.
3. **Each proposes.** Follow the Occam paragraph in `rules/reasoning-lenses.md` for one answer. Run `skills/why-not-just/SKILL.md` for the other, and stop before its attack step.
4. **Each attacks the other.** Give both answers and the plain one to outside seats under random labels, with the measured facts, and let an outside judge seal the sitting. Use `lib.sovereign_council.run_council`, after `lib.decision_privacy.may_send`.
5. **Record it,** with the winner read from the sitting and never from your own view of it.

   ```bash
   bin/grip-popper-board --record-fight <kind> <occam|why-not-just|neither> <seal>
   ```

6. **Read the board.** `bin/grip-popper-board` shows the scoreboard beside the towers.

## Limits

One fight proves nothing about a method. The scoreboard means something only after many fights on real problems, and only if the labels stayed blind. The same author writes both proposals, so the judge's verdict is a reading and never a measurement of the two methods.

The `/occam` skill was deleted on 2026-11-02 after it missed its bar (`docs/measurements/2026-11-02-reasoning-bars.md`). The Occam side of a fight is the habit in the rule, which stays on trial under H905.
