---
name: evolve
description: "Unified evolution dispatcher that routes EvolutionState between GRIP's learning engines. Closes 5 open feedback loops. Thin router, not monolith."
status: Active
effort: high
version: 1.0.0
created: 2026-03-24
triggers:
  - /evolve
  - "evolve the system"
  - "run evolution"
integrates:
  - lib/evolve.py
  - lib/evo.py
  - lib/learning_detector.py
  - lib/converge_metrics.py
  - lib/converge_dna.py
  - lib/delight_v2.py
see_also:
  - evo
  - converge
  - learning-new-skills
  - rsi-aliases
---

# /evolve — Unified Evolution Dispatcher

**Category**: RSI | Infrastructure
**Token Budget**: <500 (phases loaded on demand)

## Live Context (DCI)

**Genome:** !`PYTHONPATH=$HOME/.claude python3 -c "from lib.converge_dna import load_genome; g=load_genome(); print(f'gen {g.generation}, {len(g.genes)} genes, avg={g.avg_fitness:.3f}')" 2>/dev/null || echo "no genome"`
**Last evolve:** !`tail -1 $HOME/.claude/learning/evolve_runs.jsonl 2>/dev/null | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(f'{d[\"run_id\"]} | phases={d[\"phases\"]} | FF={d.get(\"ff_rate\",0):.1%}')" 2>/dev/null || echo "no runs"`
**DELIGHT:** !`tail -1 $HOME/.claude/learning/delight_v2.jsonl 2>/dev/null | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(f'composite={d[\"composite\"]:.3f}')" 2>/dev/null || echo "no data"`

## Usage

```
/evolve              # Full pipeline (all 5 core phases)
/evolve observe      # Phase 1: /evo calibration only
/evolve learn        # Phase 2: pattern detection only
/evolve measure      # Phase 3: DELIGHT + converge metrics only
/evolve genome       # Phase 4: genome fitness update only
/evolve propose      # Phase 5: apply genesis proposals only
/evolve closeout     # RSI closeout sequence (all phases)
```

## Protocol

### Step 1: Run the dispatcher

```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/evolve.py [phase]
```

### Step 2: Review the report

The dispatcher prints a unified report showing all phase outputs. Present
verbatim. Note any new fitness dimensions added to the genome.

### Step 3: No further action

`/evolve` is self-contained. It records its run to `learning/evolve_runs.jsonl`.

## Architecture

```
EvolutionState (shared bus)
  |
  Phase 1: OBSERVE    → evo.py (calibrate friction)
  Phase 2: LEARN      → learning_detector.py (detect patterns)
  Phase 3: MEASURE    → delight_v2 + converge_metrics (capture signals)
  Phase 4: GENOME     → converge_dna.py (unified fitness update)
  Phase 5: PROPOSE    → apply genesis proposals as micro-skills
```

Each phase reads the shared state, does its work, writes output back.
Phases are independent — any subset can run standalone.

## Feedback Loops Closed

| Loop | From → To | Phase |
|------|-----------|-------|
| 1 | Genesis → evo calibration | Phase 1 (via evidence_keywords) |
| 2 | Converge metrics → evo context | Phase 3 → Phase 1 |
| 3 | Calibrated insights → genome | Phase 2 → Phase 4 |
| 4 | DELIGHT → genome fitness | Phase 3 → Phase 4 |
| 5 | Hypothesis → gene correlation | Phase 4 |
