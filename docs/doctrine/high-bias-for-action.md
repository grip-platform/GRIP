# A High Bias for Action -- You Only Live Once: annex

The narrative behind `rules/high-bias-for-action.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

## Origin

On 2026-11-05 at about 12:03 UTC a session told V>> that a voice change waited for a trial's extended look. V>> answered "do so now, no need to wait an hour". While the session read the plan for that work, V>> added, verbatim: "GRIP Doctrine: It's good to have a high bias for action as YOLO -- you only live once."

## How it relates to what already exists

`.claude/CLAUDE.md` already carries "Execution Bias (PARAMOUNT)", with 133 wrong_approach friction events across 434 sessions as its measured reason. `rules/autonomous-blocker-resolution.md` already says that a known, safe and reversible remedy is applied without asking. This law states the disposition those two rules serve, in the operator's own words, so that a session reads it as a standing preference and not as a rule about one kind of blocker.

It adds no permission. `rules/no-guardrail-evasion.md` still binds, the HALT classes in `rules/canonical-output-style.md` still hold, and the word "YOLO" here names a bias and not the `grip-yolo` switch.

## The choice of channel

The session filed this as a `main` rule without asking, because a question about where to file a doctrine on acting without questions would contradict the doctrine. Re-running `/remember` with another channel moves the rule, so the choice is cheap to undo.

## Falsification

Wrong if, over the next ~20 acts taken under it, more than two need undoing at a cost above the time they saved: then narrow "cheap to undo". Also wrong if it is ever cited to excuse an irreversible act or a safety regression: one such citation is a violation, and the fourth clause needs a mechanical check.

Track the acts undone and what each cost, and the questions the operator bounces back as something the agent could have done. Never track how often the rule is cited.

## Provenance

V>> (the doctrine, verbatim). Claude Opus 5.5 (the clauses and this record).
