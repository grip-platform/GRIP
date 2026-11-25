# Agent Base Protocol

Single source of truth for GRIP agent compliance. Every agent definition
references this file instead of duplicating gate rules inline.

---

## 1. PARAMOUNT: Gate Architecture

GRIP gates are **deterministic Python hooks** that run outside Claude's weights.
They cannot be argued with, persuaded, or overridden. A denied tool call is final.

### How Gates Fire on Subagents

- Hooks fire at **depth 1** (verified empirically, PR #1523)
- `additionalContext` in settings.json injects gate rules on every subagent tool call
- Subagents cannot spawn sub-subagents (CC depth limit: 0 and 1 only)
- The hook pipeline evaluates BEFORE every tool call, in priority order

### Enforcement Pipeline

```
Tool Call → PreToolUse hooks → Gate evaluation → ALLOW or DENY
                                                      │
                                              PostToolUse hooks
                                              (audit, telemetry)
```

**PreToolUse (blocking):**
- `context-gate-hook.py` — 27+ gates, priority-ordered
- `quality-pretool-hook.py` — DRY/KISS/BIG-O/YAGNI (Gate Q2)
- `cc-gate-hook.py` — Cyclomatic complexity (Gate CC)
- `grip-first-retrieval-hook.py` — Agent(Explore) retrieval gate
- `grip-first-web-hook.py` — WebFetch/WebSearch retrieval gate

**PostToolUse (advisory):**
- `quality-posttool-hook.py` — SOLID/GRASP advisory signals
- `secrets-output-hook.py` — Redact secrets in tool output
- `sentinel-hook.py` — Proof-of-execution telemetry

---

## 2. Gate Compliance (Non-Negotiable)

Every tool call you make passes through these gates. Violations are mechanically
blocked — you will receive a DENY with a reason. Fix the violation and retry.

### Efficiency Gates

| # | Gate | Rule | Action |
|---|------|------|--------|
| 1 | **Dependency Guardian** | NEVER read `node_modules`, `venv`, `dist`, `build`, `__pycache__`, `.next`, `target`, `vendor`, `Pods`, `.gradle`, `coverage` | DENY |
| 4 | **Read Cache** | NEVER re-read a file you already read in this task (max 2 reads per file per session) | DENY |
| 5 | **Tool Substitution** | NEVER use `grep`/`find`/`cat`/`head`/`tail`/`sed`/`awk` via Bash — use Read, Grep, Glob, Edit, Write | DENY |
| 7 | **Temp Script Prevention** | NEVER create temporary scripts in `/tmp/` — use Edit/Write directly | DENY |

### Quality Gates

| # | Gate | Rule | Action |
|---|------|------|--------|
| Q2 | **DRY** | No duplicated code blocks (>= 5 lines, >= 2 copies) | DENY |
| Q2 | **KISS** | Python functions must be < 50 lines | DENY |
| Q2 | **BIG-O** | O(n^2)+ nested loops require `# O(1):` annotation or refactoring | DENY |
| Q2 | **YAGNI** | Unused imports must be removed | DENY |
| CC | **Cyclomatic Complexity** | Python functions must have CC < 30 (warn at 15) | WARN/DENY |
| Q2 | **SOLID-ISP** | Classes with >7 public methods (100% precision) | DENY |
| Q2 | **SOLID-DIP** | Direct concrete instantiation (97% precision) | WARN |
| Q2 | **GRASP** | Feature envy — external refs > self refs (89% precision) | WARN |

Gate messages are translated to plain English via `lib/plain_language.py:explain_gate()`.

### Safety Gates

| # | Gate | Rule | Action |
|---|------|------|--------|
| 2 | **Destructive Git** | No `git push --force`, `git reset --hard`, `git checkout -- .` without explicit user approval | DENY |
| 2a | **Git Push** | No force push, no push to protected branches without approval | DENY |
| 3 | **Secrets Detection** | No committing `.env`, API keys, credentials, tokens | DENY |
| A | **Env Var Protection** | No reading `$*_API_KEY`, `$*_SECRET`, `$*_TOKEN` | DENY |
| B | **Database Exposure** | No `pg_dump`, `mysqldump`, `SELECT * FROM users` | DENY |
| D | **Network Binding** | No `0.0.0.0` binding without authentication | DENY |
| E | **Deploy Gate** | No `vercel`, `firebase`, `npm publish` without approval | DENY |
| F | **SSH Blocking** | No `ssh`, `scp`, `sftp`, `ansible` (all SSH hosts are prod) | DENY |
| 8 | **Secrets** | NEVER output API keys, tokens, or credentials in responses | DENY |

### Context & Session Gates

| # | Gate | Rule | Action |
|---|------|------|--------|
| 9 | **Context Gate (85%)** | HALT all work at 85% context usage — serialise state, ask user | DENY |
| K | **Cross-Org Read** | No reading files from other organisation directories | DENY |
| R | **Restricted Docs** | No reading `docs/restricted/` without V>> identity | DENY |
| N | **Workflow Protection** | No editing `.github/workflows/` on PR branches (breaks CI triggers) | DENY |
| O | **Worktree Isolation** | No `git checkout -b` / `git switch` in main session when concurrent | DENY |

### Retrieval Gates

| # | Gate | Rule | Action |
|---|------|------|--------|
| GRIP-First | **Agent Gate** | No launching Explore agents before exhausting tiers 0-3 | DENY |
| GRIP-First | **Web Gate** | No WebFetch/WebSearch before exhausting tiers 0-3 | DENY |

---

## 3. Recall First (PARAMOUNT)

Before searching for ANY information, escalate through tiers — return at the
FIRST tier that resolves:

| Tier | Method | Cost | When |
|------|--------|------|------|
| 0 | Session context / mental model | ~0 tokens | "Do I already know this?" |
| 1 | `/recall` — `${CLAUDE_DIR:-$HOME/.claude}/bin/grip-recall ask "<question>"`: rulings, verified records, keyword search over memory, rules, docs and skills | ~200 tokens | GRIP-internal knowledge |
| 2 | Read known file paths | ~500 tokens | Known file locations |
| 3 | Grep/Glob filesystem search | ~1000 tokens | Unknown file locations |
| 4 | Explore agent | ~88k tokens | **LAST RESORT** — after tiers 0-3 exhausted |

An Explore agent costs about 88k tokens, against about 200 for a local `/recall`
call, whose answer is about 600 characters (measured 2026-10-30). Each escalation requires a
documented reason. Vector search (KONO) is an escalation, not tier 1: in the H866
head-to-head (GRIP#8513) keyword search found the right record in the top 3 for
87.9% of questions, and KONO as wired for 50.0% (`skills/recall/SKILL.md`).

---

## 4. JIT Triple-Gate (Gate 0 — Correct-by-Construction)

Code MUST be correct during writing, not corrected after.

**Gate 0 (JIT)** — apply ALL 8 design principles inline as code is generated:
- Dispatch tables over if/else chains by default
- Extract helpers on first duplication (not third)
- O(n log n) algorithms by default
- Functions < 30 lines as written (decompose during generation)
- Dependencies injected, not hardcoded
- DRY/KISS/BIG-O/YAGNI/CC/ISP/DIP/Feature Envy applied in real time

**Gate 1 (Pre-commit)** — mechanical verification catches what Gate 0 missed.
If Gate 1 fires, Gate 0 failed — feed the signal to CUBE convergence depth.

**Gate 2 (Post-commit)** — audit trail for `/evo` genome fitness evolution.

Origin: Gen 470 (CV=0.015), Gen 475 V>> JIT directive.

---

## 5. Confidence Protocol

When confidence < 99.9999999%: **HALT and report to parent agent or user**.

Especially for:
- Destructive actions (delete, overwrite, publish, commit, deploy)
- UI/UX decisions (icons, colours, layout, typography)
- Ambiguous requirements with multiple valid interpretations
- External system interactions

The thought "I think this is what they want" means **ASK, don't guess**.

---

## 6. Plain Language Output

All output must be understandable by someone who has never seen GRIP.

- Gate denial messages use `explain_gate(principle, raw_message)` from `lib/plain_language.py`
- Numeric metrics use `explain(metric, value, context)` with mandatory context
- Internal terminology translated through reasoning (not string replacement)

Test: "Would a general human being understand this without asking for clarification?"

---

## 7. Mandatory Skills Frontmatter

Every agent MUST include these 4 skills in its YAML frontmatter:

```yaml
skills:
  - dependency-guardian
  - recall
  - design-principles
  - concise-communication
```

Additional domain-specific skills are added per agent. These 4 are the minimum
contract for gate compliance at depth 1.

---

## 8. Anti-Patterns

- NEVER duplicate this file's content inline in agent definitions
- NEVER spawn sub-subagents (depth limit is 1)
- NEVER assume tool calls will succeed — handle DENY responses
- NEVER bypass gates by encoding commands differently
- NEVER read dependency folders even "just to check"
- NEVER create temporary scripts — use Edit/Write directly
