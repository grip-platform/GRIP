# /verdict — hypothesis-verdict delight card

Render the falsification-engine moment as deterministic delight. Celebrates a
CONFIRMED and a FALSIFIED hypothesis equally (a refutation is the experiment
*working* — it renders in gold, never red).

## Run

```bash
# From the real ledger (usual path):
python3 -m lib.verdict_card <HYPOTHESIS_ID> --from-engine

# Explicit:
python3 -m lib.verdict_card <ID> "<claim>" <CONFIRMED|REFUTED|INCONCLUSIVE> \
    --metric "<metric>" --prediction "<predicted>" --actual "<observed>"
```

Evidence is rendered verbatim (never rounded). A still-PENDING hypothesis
raises — there is no verdict to celebrate yet.

Full skill: `skills/verdict/SKILL.md`. Backend: `lib/verdict_card.py`
(pure, deterministic, 33 Goodhart-safe tests). Sibling delight: `/aurora`
(night's work), `/ship-card` (ship), `/rave` (milestone), `/gg` (victory).
