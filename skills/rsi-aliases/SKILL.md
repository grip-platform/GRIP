---
name: rsi-aliases
description: Alias commands for RSI session launch and autonomous convergence.
status: Active
effort: high
version: 2.0.0
triggers:
  - "/rsi"
  - "/broly-auto"
integrates:
  - auto_converge.py
  - rsi_delegate_launcher.py
  - converge/SKILL.md
  - broly-meta-agent/SKILL.md
see_also:
  - converge
  - prompt-coach
  - insights-pipeline
---

# RSI Aliases

## Live Context (DCI)

**Git state:** !`cd ~/.claude && git log --oneline -3 2>/dev/null`
**Open PRs:** !`cd ~/.claude && gh pr list --limit 3 --json number,title --jq '.[] | "#\(.number) \(.title)"' 2>/dev/null || echo "none"`
**Open issues:** !`cd ~/.claude && gh issue list --state open --json number --jq 'length' 2>/dev/null || echo "?"` open
**DELIGHT v2:** !`cd ~/.claude && tail -1 learning/delight_v2.jsonl 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'composite={d[\"composite\"]:.3f} vel_shadow={d.get(\"velocity_shadow\",0):.2f}')" 2>/dev/null || echo "no data"`
**Hypotheses:** !`cd ~/.claude && PYTHONPATH=. python3 -c "from lib.hypothesis_engine import status; s=status(); print(f'{s[\"total\"]} total, {s[\"by_status\"].get(\"pending\",0)} pending')" 2>/dev/null || echo "no data"`
**Entropy:** !`cd ~/.claude && PYTHONPATH=. python3 -c "from lib.conversation_entropy import compute_session_entropy; e=compute_session_entropy(120); print(f'combined={e[\"combined_entropy\"]:.3f} iu={e[\"inverted_u_score\"]:.3f}')" 2>/dev/null || echo "no data"`

## /broly-auto

Alias for `/converge broly auto`. Skips baseline capture — use when already in flow.

## /rsi

Full RSI session launcher with delegate mode, Fibonacci scheduling, and genome evolution.

### Delegate Mode (DEFAULT)

`/rsi` uses delegate mode by default. Opus plans each wave; Sonnet executes.
This is non-negotiable for deep convergence work — do not flatten unless explicitly opted out.

The `--delegate` flag (default) auto-launches a Sonnet Loop B process:

1. Spawns `claude --dangerously-skip-permissions` as a background process
2. Captures its session ID from process output
3. Connects via `grip_pair_connect(session_id, steer=true)`
4. Begins Fibonacci wave scheduling with wave-aware delegation
5. Monitors via git polling + delight regression detection
6. Auto-stops on: 3 delight drops, time limit, stall (5min no commits), or RSI stop signal

```
/rsi                    # Delegate mode with auto-launch — DEFAULT
/rsi --delegate         # Explicit delegate mode (same as default)
/rsi --no-delegate      # Single-model mode (opt-out)
/rsi --limit 1h         # Time-budgeted delegate mode
/rsi quick              # 30-min timebox, max_depth=3, auto-criterion
/rsi --fibonacci        # Fibonacci wave scheduling
/rsi --depth 5          # Override max convergence depth
/broly-auto             # Quick alias, skip baseline
```

Delegate launcher: `lib/rsi_delegate_launcher.py`

```bash
python3 lib/rsi_delegate_launcher.py launch              # Spawn Loop B
python3 lib/rsi_delegate_launcher.py launch --limit 1h   # Time-budgeted
python3 lib/rsi_delegate_launcher.py launch --dry-run    # Preview command
python3 lib/rsi_delegate_launcher.py status              # Check delegate
python3 lib/rsi_delegate_launcher.py stop                # Stop delegate
```

Template: `data/prompt-templates/rsi-loop.md`

### Protocol

1. **Baseline**: `python3 ~/.claude/lib/auto_converge.py check` — capture test_pass_rate, reviews (AGORA's first-sitting pass rate, GRIP#9668), composite_score
2. **Report baseline**: Show test_pass_rate, reviews and composite_score; meta_work_pct is shown but not graded until something records it
3. **Launch**: `/converge broly auto` with RSI criterion wired
4. **Delight**: After each cycle, call `delight_snapshot()` from `auto_converge.py`

### Quick Mode

```
/rsi                    # Full unified wave protocol (Fibonacci + delegation + breathing) — DEFAULT
/rsi quick              # 30-min timebox, truncated wave (W1-W3 only)
/rsi --no-fibonacci     # Flat execution, no wave scheduling
/rsi --no-delegate      # Single-model, no Opus/Sonnet split
/rsi --depth 5          # Override max convergence depth
/broly-auto             # Quick launch, skip baseline capture
```

`/rsi quick` runs a focused 30-minute session:
- `max_depth=3`, auto-criterion derived from baseline
- Genome evolution still runs at every pause — non-negotiable
- Stops at timebox, writes session summary to MEMORY

### Unified Fibonacci Wave Protocol

The default `/rsi` runs a unified wave protocol where batch size, delegation mode,
breathing duration, and convergence depth move together. This is not three separate
systems — it is three projections of the same underlying rhythm.

Wave state is managed by `lib/wave_state.py` via `.wave-position` file.

**GENOME EVOLUTION RUNS AT EVERY BREATHING PAUSE WITHOUT EXCEPTION.**

The wave ascends in intensity, peaks at W8, then descends with creativity increasing.
Breathing pauses are longer on the descent — recovery exceeds effort.

**Delegation follows convergence depth.** You can delegate what you've CODIFIED.
You cannot delegate what's LATENT. A DETECTED pattern needs Opus to investigate;
an UNDERSTOOD pattern can have Sonnet execute under Opus supervision; a CODIFIED
pattern runs on Sonnet with minimal oversight.

#### Ascent-Descent Wave Table

| Wave | Batch | Cube W | Delegation | Breathing | Activity | Eligible Modes |
|------|-------|--------|------------|-----------|----------|----------------|
| **ASCENT** | | | | | | |
| W1 | 1 | DETECTED | Opus solo | 5 min | Delight snapshot, genome evolve | research, brainstorm |
| W2 | 2 | UNDERSTOOD | Opus + 1 Sonnet | 10 min | Backlog reprioritization | analysis, synthesis, decision |
| W3 | 3 | CODIFIED | Opus + 2-3 Sonnet | 10 min | Convergence assessment, insight review | architect, code, security |
| W5 | 5 | EMBODYING | Opus + 3-5 Sonnet | 15 min | Full delight + trend analysis | code+testing, code+security |
| W8 | 8 | EMBODIED | Max delegation (5-8 Sonnet) | 20 min | Continue/stop decision | testing, review |
| **DESCENT** | | | | | | |
| W5 | 5 | CODIFIED+ | Opus creates, Sonnet supports | 15 min | Write documentation | writing, marketing |
| W3 | 3 | UNDERSTOOD+ | Opus writes, 1 Sonnet edits | 20 min | Write article + post to GitHub | teaching |
| W2 | 2 | DETECTED+ | Opus reflects solo | 25 min | Retrospective + delight | learning |
| W1 | 1 | LATENT seeds | Opus solo | Rest | Session summary, seed next cycle | *(bare reflection)* |

Domain-context modes (strategy, business, legal, planning) stack at any position.
Orthogonal modes (incognito, flow, pair, demo, git-ops, create) stack freely.

The `+` suffix means revisiting this convergence level with richer understanding
gained from having reached EMBODIED. W:3 on descent is qualitatively different
from W:3 on ascent — you are now TEACHING what you codified, not just codifying it.

**Descent activities require Opus**: Documentation, articles, and retrospectives
are synthesis tasks. They need the model that did the learning, not a delegated
worker. Breathing duration inverts on descent — consolidation takes longer than
execution.

#### Depth-Phase Table

| Wave | Issues | Depth | Phase | Breathing | Activity |
|------|--------|-------|-------|-----------|----------|
| W1 | 1 | 3 | Ascent | 5 min | Genome evolve |
| W2 | 2 | 3-4 | Ascent | 10 min | Genome + delight snapshot |
| W3 | 3 | 3-4 | Ascent | 10 min | Genome + insight review |
| W5 | 5 | 3-5 | Ascent | 15 min | Genome + discovery scan |
| W8 | 8 | 1-3 | Peak | 20 min | Genome + docs + articles (full breathing) |
| W5 | 5 | 3-5 | Descent | 15 min | Genome + write documentation |
| W3 | 3 | 3-4 | Descent | 20 min | Genome + write article + post to GitHub |
| W2 | 2 | 3-4 | Descent | 25 min | Genome + retrospective + delight |
| W1 | 1 | 3 | Descent | Rest | Genome + session summary |

Design rationale:
- Intensity peaks at W8 (ascent)
- Creativity peaks on descent (documentation, articles, posts)
- Longer descent pauses allow synthesis of what was built during ascent
- Genome fitness is continuously tracked across all pauses

**YAGNI endpoint**: At W:4 EMBODIED, the skill itself becomes unnecessary. If the
wave protocol is truly embodied, you will not need these tables — the system naturally
batches, delegates, breathes, and consolidates at the right rhythm. The tables are
scaffolding (W:3 CODIFIED). Full convergence means the scaffolding dissolves.

### Wave State Management

1. **Create** `.wave-position` at W1 ascent start
2. **Update** at each wave transition via `advance_wave()`
3. **Checkpoint** to KONO at breathing pauses via `checkpoint_to_kono()`
4. **Suggest** modes at each wave based on Eligible Modes column
5. **Clear** `.wave-position` at session end

### Mode Sequencing Guidance

Wave-aligned modes activate at their natural W-level. The wave enforcer
(`lib/wave_enforcer.py`) emits advisory warnings if a mode is activated
outside its natural position. Non-wave modes (orthogonal, domain, meta)
never trigger warnings.

Between waves, the agent runs:
- Genome evolution (`converge_dna.py`) with wave-phase expression context
- Delight snapshot (`auto_converge.py`)
- KONO wave checkpoint (`wave_state.py`)
- Insight review (`learning/insights.jsonl`)
- Dynamic discovery scan (YSH 3x pattern check)

### Goodhart Health Signal

At breathing pauses (between waves), call `pre_flight_check()` from
`lib/goodhart_guard.py` and feed into RSI state:

- `(True, "")`: Silent. Healthy.
- `(True, "")` with warnings: Log to KONO. Continue wave.
- `(False, reason)`: Do NOT halt the RSI session. Log the critical signal.
  The next sprint-loop iteration self-heals via the Goodhart Self-Healing Gate.

This turns Goodhart violations into observable health signals within the
Fibonacci wave rhythm rather than hard stops.

### Delight Metric

Captured at breathing pauses via `delight_snapshot()`:

**User delight signals** (observable):
- Flow state: consecutive operations without gate blocks
- Velocity: time from issue-pick to PR-merge
- Zero-interruption streaks
- Completion ratio: issues completed / issues attempted

**AI delight signals** (system-measurable):
- Genome fitness trend
- Converge velocity (positive = productive)
- Novel pattern discovery count
- KONO embedding diversity
