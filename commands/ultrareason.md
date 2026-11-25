---
description: Moat-grounded, cost-tiered reasoning — ground the question in GRIP's verified moat + RED chain, run a cheap Pool-B pass, escalate to the sovereign council only if the answer is unconfident. $0, off the weekly Anthropic limit.
disable-model-invocation: false
harness: universal
---

# /ultrareason — moat-grounded, cost-tiered reasoning

Answer the user's question via the `ultrareason` skill
(`skills/ultrareason/SKILL.md`): ground it in the moat, reason cheap first on
Pool-B, escalate to the sovereign council only if a confidence criterion is unmet.

## Steps

1. **Route check first.** If the question is external research (a tool, paper,
   framework) → use `/research-council` instead. If it is a code/PR review →
   `/ultrajudge`. Otherwise proceed.
2. **Run the ladder** on the operator's question (`$ARGUMENTS`):

   ```bash
   $HOME/.grip/venv/bin/python -c "import json,sys; from lib.ultrareason import ultrareason; print(json.dumps(ultrareason(sys.argv[1]), indent=2))" "$ARGUMENTS"
   ```

   (Passing the question as a single `argv[1]` — never interpolated into the
   code string — closes the shell→python injection surface.)
3. **Report** the answer plus its provenance in plain language: the answer, which
   tier produced it (`cheap` vs `council`), whether it escalated, and the moat
   facts that grounded it (`grounded_on`). If it escalated, the sovereign council
   sealed a record — mention that the verdict is exogenously judged.

## Notes

- Both tiers run on **Pool-B** (HAL `grip_infer` + `bin/grip-sovereign-council`) —
  non-Anthropic, $0, off the weekly limit (`rules/pool-a-offload-routing.md`).
- Fail-open: if a tier is unreachable, the best answer in hand is returned — never
  a hard error.
- Falsifier + N=1 maturity caveat: see `skills/ultrareason/SKILL.md`.
