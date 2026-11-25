---
name: ultraideate
description: Many ideas from outside models, ranked by Jev, picked by the operator -- the ultra* family's ideation limb.
---

# /ultraideate

Invokes the `ultraideate` skill -- DIVERGE (zero-Anthropic council) + CONVERGE
(Jev's five-criteria Score) + COMBINE (code, operator weights), then the
operator picks from the ranked shortlist before DEVELOP continues. The full
protocol, the honest limits, and the measure live in
`skills/ultraideate/SKILL.md`; this stub deliberately does not duplicate them.

Kernel: `~/.grip/venv/bin/python -m lib.ultraideate rank "<topic>" --top N
[--ideas-file FILE | --seeds-file FILE] [--json] [--seal]`. `--seeds-file`
scores the operator's own ideas together with DIVERGE's and always shows them.
`~/.grip/venv/bin/python -m lib.ultraideate picks <run_id> <ids> --topic "<topic>"`
records the operator's pick against that run's full ranked order. The `report` subcommand
(`~/.grip/venv/bin/python -m lib.ultraideate report`) reads the picks log,
`logs/ultraideate-picks.jsonl` by default, and prints precision@k against a
random baseline.
