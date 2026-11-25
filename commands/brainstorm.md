---
name: brainstorm
description: "Runs a whole brainstorm in one command: SEED, DIVERGE and CONVERGE through /ultraideate, CLUSTER, the operator's pick, ATTACK, DECIDE through /hitl-decision-matrix, and DEVELOP, with each result shown through /simple."
argument-hint: "the challenge in your own words; nothing starts with the seed questions"
---

# /brainstorm

Load the runner, and follow it for `$ARGUMENTS`:

```bash
sed -n '1,300p' "${CLAUDE_DIR:-$HOME/.claude}/skills/brainstorm/SKILL.md"
```

The runner walks the phases of `skills/brainstorm-mode/SKILL.md` in order. It composes `/hitl`, `/asking-users`, `/creating-wizards`, `/ultraideate`, `/hitl-decision-matrix` and `/simple`, and it repeats none of them. Load each of those skills when its phase starts.
