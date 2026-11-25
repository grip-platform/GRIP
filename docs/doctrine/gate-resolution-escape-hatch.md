# Gate Resolution Escape-Hatch + Gate-as-RSI-Target: annex

The narrative behind `rules/gate-resolution-escape-hatch.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

Origin: 2026-06-08. A live session deadlocked at the context gate: at 85% it
denied EVERY tool call — including the `Edit`/serialize that would have resolved
the condition AND the `AskUserQuestion` fallback the deny message itself
demanded. Only an EXTERNAL human (V>>, ungated) could edit the gate file to break
it.

## RSI-on-GATES mandate

`gate-failure`'s repair hatch matched only
  paths under `CLAUDE_DIR`, so from any worktree a crashed gate blocked the very
  edit that would fix it: Invariant 1 violated, by the same shape as the
  2026-06-08 incident this rule was written after. Fixed by resolving the target
  against the checkout that CONTAINS it rather than one absolute prefix, with
  `lib/idr_forest.py → deny` retained from every tree as the control that the
  hatch stayed narrow.
