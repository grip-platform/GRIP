# An AGORA-Mergeable PR Is Merged by the Agent, Never Handed Over as a Command: annex

The narrative behind `rules/an-agora-mergeable-pr-is-merged-not-handed-over.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

## Origin

2026-10-12 13:41 UTC, V>> through `/remember`, on reading this closer at the end of a session report:

```
RECOMMENDED NEXT STEP (yours)
gh pr merge 7513 -R grip-platform/GRIP --squash
```

His words: "IF the recommended next step is a simple merge of a PR, which AGORA deemed mergeable, then don't stop and ask me to type in the `gh pr merge`.... Just do it yourself!"

## The instance

GRIP#7513 (ASD-STE100 as the house writing style) had its merge conflicts with main resolved at 846b507fe, its branch updated twice, and a fourth AGORA sitting on head 3ed6be8b3 reporting DEFER CHANGES with 0 substantiated concerns, three of three seats, every check green, no Codex review and no open review thread. The session still handed the merge to the operator because the pull request's own body, written by an earlier session on 2026-10-05, said only he should merge it. He merged nothing; he ruled instead. The session merged it at 13:44:47 UTC as 5b0ab1e13.

The owner test (`rules/canonical-output-style.md`, V>> 2026-10-10) already made this `(mine)`: "a merge the AGORA-green or stuck-loop rule already authorises" is listed as never his on its own. What the session got wrong was treating a session-written body hold as an Authority carve-out. This rule names that mistake so it is not repeated, and binds it mechanically.

## What is mergeable on those terms

The same terms the merge sweep (`bin/grip-pr-monitor`) applies and `CLAUDE.md` "AGORA-Green = Merge" states: a genuine AGORA verdict for the pull request's current head of MERGE or DEFER CHANGES (`lib/agora_verdict_router.genuine_verdict_for_sha`), at least two seats sitting, no REQUEST_CHANGES, the required checks `leak-check` and `Forward Anchors` green, no unresolved review thread, and either a Codex review of the head or a head older than the Codex wait (20 minutes). A pull request that touches a path in the sweep's `MERGE_MACHINERY_RE` is never forced by the hook: the by-effect judgment stays with the session.

## Measurement and falsification

Track, from `logs/next-step-tokens.jsonl`: `(yours)` closers whose step text is a `gh pr merge` command (target 0 after this rule), and the hook's `merge_yourself` records (each block, and each probe that declined with its reason). Wrong if the hook ever forces a merge the carve-outs reserve (a bootstrap or machinery pull request, a danger class) -- then the probe's checks are too narrow and must widen, never the rule dropped; wrong too if a block fires on a pull request that was not mergeable (a probe defect, fixed in `lib/merge_yourself.py`); and wrong if the operator reverses a merge made under it more than once in ~20 merges, which would mean his instruction was narrower than this rule reads it.
