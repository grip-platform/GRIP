---
name: auto
description: Unified autonomous-loop entry point. Subsumes /auto-ship, /auto-rsi, and /rsi-optimal. Args determine sub-shape — bare /auto ships uncommitted changes, /auto deadline=Nh runs a timed loop, /auto wave-mode runs a Fibonacci-wave RSI sprint. Uses bg-dispatch as orchestration primitive (not rewritten).
status: Active
version: 1.1.0
triggers:
  - /auto
  - /auto-ship
  - /auto-rsi
  - /rsi-optimal
  - auto rsi
  - auto ship
  - ship all changes
  - optimal rsi
  - autonomous rsi
  - full rsi sprint
integrates:
  - bg-dispatch/SKILL.md
  - converge/SKILL.md
  - hypothesis-driven-development/SKILL.md
  - scientific-measurement/SKILL.md
  - worktree-isolation/SKILL.md
see_also:
  - sprint
  - bg-dispatch
  - autonomous-rsi-sprint
---

# /auto — Unified Autonomous Loop

One entry point for all autonomous delivery and improvement workflows.
No sub-shape redefines `/bg-dispatch` or `/converge` — imported as-is.

**Plain English**: no args → ships uncommitted changes as a PR. Time limit →
autonomous loop until deadline. `wave-mode` → Fibonacci-wave RSI sprint.

**Glossary**: RSI = recursive self-improvement | Fibonacci wave = sprint with
rest (1,1,2,3,5,8 batches) | bg-dispatch = background-dispatch (Shape A/B/C) |
Broly = multi-model external council | Yolo mode = halting-gate bypass (quality
gates still fire) | Worktree = isolated git tree preventing session races.

---

## Dispatch

| Invocation | Sub-shape | Legacy |
|------------|-----------|--------|
| `/auto` / `/auto "msg"` / `/auto feat/branch` | ship-uncommitted | `/auto-ship` |
| `/auto deadline=3h` / `/auto N=3h "goal"` | autonomous-loop | `/auto-rsi` |
| `/auto wave-mode [Nh] ["goal"] [--no-ship]` | fibonacci-wave | `/rsi-optimal` |

Dispatch logic (Claude evaluates on invocation):
1. Contains `wave-mode` or `until-converge` → **fibonacci-wave**
2. Contains `deadline=`, `N=Nh`, or a bare duration → **autonomous-loop**
3. Anything else (or no args) → **ship-uncommitted**

---

## Shared Loop Pre-flight (PARAMOUNT — Shapes B and C)

`~/.claude` is git-crypt encrypted — native `isolation: "worktree"` fails.
Always run before any loop shape:

```bash
TS=$(date +%s)
WT="/tmp/auto-${TS}"
BRANCH="feat/auto-${TS}"
bash ~/.claude/lib/grip-worktree.sh add "${WT}" "${BRANCH}"
```

Yolo activation (TTL = duration × 3600, capped 28800):

```bash
PYTHONPATH=$HOME/.claude/lib $HOME/.claude/venv/bin/python -c "
from gates.yolo_mode import activate_yolo
activate_yolo('/auto', ttl_seconds=<ttl>, reason='<goal>')
"
```

Ship-uncommitted may skip worktree if on a feature branch with no concurrent
sessions. When in doubt, use a worktree.

---

## Shape A: Ship-Uncommitted

**Stage 1 — Scan**: classify every modified/untracked file. Skip:
`node_modules/`, `dist/`, `build/`, `*.lock`, `.env*`. Abort if secrets
detected. 0 files → "Nothing to ship." and stop.

**Stage 2 — Group**: one commit per coherent unit (max 3 batches): single
message → one batch; files sharing top-level dir → one each; same change type
and < 10 files → merge.

**Stage 3 — Commit**:
```bash
git add <files>
git commit -m "$(cat <<'EOF'
type(scope): description

Primary Author: V>>
EOF
)"
```
No emojis, no AI attribution, subject ≤ 72 chars. Hook failure: fix, retry
once. Still failing: HALT and ask.

**Stage 4 — Push**: `git push -u origin <branch>`. On main: create
`auto/<timestamp>` branch first. Force push always blocked. Goodhart guard:
`lib/goodhart_guard.py` — HALT if returns False.

**Stage 5 — PR**:
```bash
gh pr create --title "<type>(<scope>): <summary>" --body "..." --base main
```
Body: bullet per batch, inferred test plan, `Primary Author: V>>`.
Branch already has PR → skip creation, output existing URL.

**Stage 6 — Merge**: when CI green (or no CI) and no conflicts:
```bash
gh pr merge <PR_NUMBER> --admin --squash --subject "<PR title>"
```
Post-merge: `git checkout main && git pull --rebase`, sprint completion
notification via `lib/auto_ship_hooks.py notify-completion`, anti-drift output.

---

## Shape B: Autonomous-Loop

**Phase 0**: run shared loop pre-flight above.

**Phase 1 — Plan**: `/ultraplan "<goal>"` — falsifiable plan, hypothesis per
iteration (`rules/scientific-method.md`).

**Phase 2 — Loop until deadline**:
1. Pick bg-dispatch Shape A/B/C per `skills/bg-dispatch/SKILL.md`
2. `/converge broly auto "<task>" criterion: "<hypothesis>" depth: 2`
3. Run Shape A to deliver; verify hypothesis (CONFIRMED / FALSIFIED / INCONCLUSIVE)
4. Anti-drift output + context gate check (85% → HALT)

**Phase 3 — Close**:
```bash
PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python -c "
from gates.yolo_mode import deactivate_yolo; deactivate_yolo('/auto')
"
/save && /closeout
```

---

## Shape C: Fibonacci-Wave

Worktree-isolated Fibonacci-wave RSI sprint. Subsumes all `/rsi-optimal`
behaviour. `skills/rsi-optimal/SKILL.md` is a redirect stub pointing here.

**Phase 0**: run shared loop pre-flight, then:
`/caffeinate && /mode rsi code && /auto-mode <goal>`

**Phase 1 — Plan**: `/ultraplan "<goal>"` — wave decomposition (1,1,2,3,5,8),
hypothesis per wave, dispatch Shape per wave.

**Phase 2 — Wave Dispatch**: converge depth scales with wave index: W1–W2 d=1,
W3 d=2, W4 d=3, W5 d=5, W6 d=8. Each task:
`/converge broly auto "<task>" criterion: "<hypothesis>" depth: {d}`.
Use `direct-implementation` subagent for execution; `broly` only for council.

**Phase 3 — Per-Wave Ship** (unless `--no-ship`): run Shape A after each wave.
`--no-ship` defers all shipping to Phase 5.

**Phase 4 — Measure + Breathe**: hypothesis verification → Broly council
scoring (never self-score) → `lib/delight_v2.py snapshot` →
`lib/conversation_entropy.py --window 120` → genome evolution → anti-drift
output → context gate check (85% → HALT and serialise).

**Phase 5 — Close**: push branch, `/create-pr` if `--no-ship`, then
`/save && /closeout && deactivate_yolo('/auto')`.

**Parameters**: `duration` (default 3h, range 1h–8h), `goal` (inferred),
`--no-ship`, `--no-fibonacci`, `--no-hypothesis`, `--depth N`.

---

## Guardrails (All Shapes — PARAMOUNT)

| Rule | Shapes |
|------|--------|
| Secrets scan MANDATORY — never ship secrets | A, B, C |
| Never force push | A, B, C |
| Worktree isolation MANDATORY for loop shapes (git-crypt repo) | B, C |
| Goodhart guard blocks score-chasing infra-only PRs | A, B, C |
| Context gate 85% — HALT, serialise, never auto-compact mid-wave | B, C |
| Hypothesis falsifiability — every loop iteration is an experiment | B, C |
| External council scoring — never self-score | C |
| 3-attempt revert per PR before escalating | A, B, C |
| Quality gates (DRY/KISS/BIG-O/CC) always fire regardless of yolo state | A, B, C |

## Abort Conditions

| Condition | Action |
|-----------|--------|
| Secrets detected | HALT immediately, do not stage |
| Pre-commit hook fails twice | HALT, AskUserQuestion |
| Goodhart guard returns False | HALT, output reason |
| Merge conflict | HALT, output conflict files |
| 0 changes after scan | Output "Nothing to ship." and stop |
| 85% context | HALT, serialise, offer compact/fresh |

## Integration Points

| System | Shape(s) | When Used |
|--------|----------|-----------|
| `lib/goodhart_guard.py` | A, B, C | Pre-push gate |
| `lib/auto_ship_hooks.py` | A, B, C | Sprint completion notification |
| `grip-worktree.sh` | B, C | Isolation (mandatory) |
| `lib/hypothesis_engine.py` | B, C | Hypothesis registration |
| `lib/delight_v2.py` | C | Wave measurement |
| `lib/conversation_entropy.py` | C | Entropy measurement |
| `skills/bg-dispatch/SKILL.md` | B, C | Wave orchestration (imported only) |
| `gh pr merge --admin` | A, B, C | GRIP repo merge |

