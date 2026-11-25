# Delegate Mode: Opus Plans, Sonnet Executes

GRIP's Dual-Model RSI Architecture

## The Idea

Delegate Mode splits AI reasoning from AI execution across two concurrent Claude Code sessions:

```
Opus 4.6 (Loop A)                    Sonnet 4.6 (Loop B)
  Reasoning + Planning                 Execution + Implementation
  200K context window                  200K-1M context window
  Discovers improvements               Implements improvements
  Prioritizes backlog                  Commits, tests, closes issues
  Steers via context injection         Follows directives autonomously
```

Opus thinks. Sonnet builds. Both run simultaneously.

## How It Works

### 1. Opus Discovers

Loop A (Opus 4.6) continuously analyzes the codebase across five discovery sources:

| Source | Method | Output |
|--------|--------|--------|
| Codebase patterns | Anti-patterns, silent exceptions, stale TODOs | Infrastructure fixes |
| Mode/skill coverage | Cross-reference mode definitions against skill registry | Feature gaps |
| Delight decomposition | Weakest sub-signal in composite metric | Targeted improvements |
| Cross-project transfer | KONO memory patterns from other projects | Transferable features |
| Creative ideation | Novel capabilities brainstorm | New features |

### 2. Opus Prioritizes

Each discovery becomes a GitHub issue, scored across 7 dimensions by the backlog prioritizer. Opus maintains a target ratio of ~75% features to ~25% infrastructure work.

### 3. Opus Steers

Steering uses file-based context injection:

```
/tmp/grip-injected-{session_id}.txt
```

Loop B's context-gate hook reads this file on the next tool call, injecting the directive into Sonnet's context. The directive includes:

- Task title and issue number
- Priority and type (feature/infra)
- Specific files to modify
- Acceptance criteria
- Completion instructions (commit, close issue, run tests)

### 4. Sonnet Executes

Loop B (Sonnet 4.6) receives the steering directive and executes autonomously using `/converge broly auto {task}`.

Sonnet's execution includes:
- Code implementation
- Test creation and validation
- Git commit with conventional commit messages
- Issue closure on completion
- PR creation for larger changes

### 5. Opus Monitors

Loop A watches for completion signals:
- `git log --since="2 minutes ago"` for new commits
- `gh pr list` for new pull requests
- `gh issue list --state closed` for closed issues
- Delight snapshot measurements between waves

## Fibonacci Wave Scheduling

Discoveries are batched in waves following the Fibonacci sequence:

| Wave | Issues | Focus | Breathing Pause |
|------|--------|-------|-----------------|
| W1 | 1 | Single creative feature | Delight snapshot |
| W2 | 2 | 1 feature + 1 infra | Backlog reprioritization |
| W3 | 3 | Mixed from 3 sources | Convergence assessment |
| W5 | 5 | All 5 discovery sources | Full delight + trend analysis |
| W8 | 8 | Comprehensive + weak signals | Continue/stop decision |

Each breathing pause measures whether delight is improving. If delight decreases three times consecutively, the loop stops.

## Wiring Status

### What exists (built)

| Component | Location | Status |
|-----------|----------|--------|
| Context injection hook | `hooks/context-gate-hook.py` | Reads `/tmp/grip-injected-{session_id}.txt` |
| Steering lock | `lib/session_control_server.py` | WebSocket-based lock acquisition |
| Pair-mode steering | `lib/session_monitor.py` | `--steer` flag for directive injection |
| Delight snapshots | `lib/delight_engine.py` | Composite scoring (user + system health) |
| Backlog prioritizer | `lib/backlog_prioritizer.py` | 7-dimension scoring |
| Auto-converge | `lib/auto_converge.py` | Baseline capture + convergence |
| RSI orchestrator | `lib/rsi_orchestrator.py` | Phase management |

### What needs wiring (remaining)

| Component | Gap | Priority |
|-----------|-----|----------|
| Auto-launch Loop B | Manual terminal setup required | P1 |
| Fibonacci wave scheduler | Wave batching is manual | P1 |
| Delight regression detector | 3-drop stop logic not automated | P2 |
| Cross-loop git monitoring | Opus polling not continuous | P2 |
| Session ID discovery | Requires `grip_pair_list` MCP call | P3 |

### One-command launch (target)

```bash
# Target UX (not yet implemented)
/rsi --delegate

# Current UX (manual)
# Terminal 1: claude --dangerously-skip-permissions  (Sonnet executor)
# Terminal 2: claude → /rsi --fibonacci              (Opus planner)
```

## Safety Architecture

Delegate Mode inherits GRIP's safety gates:

| Gate | Purpose |
|------|---------|
| Context gate (85%) | Halt before context exhaustion |
| Operation limit | Max operations per session |
| Circuit breakers | Stop on repeated failures |
| Failure threshold | 3 consecutive failures = pause |
| Delight regression | 3 consecutive drops = stop loop |
| Meta-work ratio | Keep infrastructure < 25% of total |

The steering mechanism is pull-based — Loop B must make a tool call to receive a directive. If Loop B is stalled, Opus detects it and escalates to the user.

## ECC Feedback Loops

Delegate Mode benefits from Error-Correcting Code patterns:

- **Parity check**: Each steering directive includes expected outcome hash. If Loop B's result diverges, Opus re-steers.
- **Hamming distance**: Track edit distance between planned and actual changes. Large divergence triggers review.
- **Redundancy**: Critical tasks dispatched to both loops independently. Results compared for consistency.

See `docs/ECC-RSI-ARCHITECTURE.md` for the full ECC-RSI mapping.

## Measured Results (Feb 23-24, 2026)

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Test failures | 27 | 0 | -27 |
| Mode validity | 10/27 | 27/27 | +17 modes |
| Missing skills | 19 | 0 | -19 |
| Delight composite | 0.605 | 0.746 | +23% |
| Genome fitness trend | -0.000 | +0.005 | Positive |

GRIP v6.0.0 | grip-platform/GRIP
