# GRIP Safety Gates

**The moat. The USP. What makes GRIP different.**

## The Problem with Current AI Safety

Anthropic's approach relies on values trained into model weights:
- Constitutional AI with RLHF
- "Uncertainty" about consciousness
- Tone-based refusals

**This doesn't work because:**
- Values can be argued with
- Claude can be persuaded
- Uncertainty opens negotiation
- Authority is undefined

> "Safety doesn't fail because rules are too weak — it fails because authority is undefined."

## The GRIP Solution

GRIP Safety Gates are **deterministic mechanisms** that run **outside Claude's weights**.

```
┌─────────────────────────────────────────┐
│           Claude's Weights              │
│  (probabilistic, persuadable, drifts)   │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│         GRIP Safety Gates               │
│  (deterministic, mechanical, absolute)  │
│                                         │
│  if condition(X) → DENY                 │
│  No arguments. No persuasion.           │
│  Hook doesn't care what Claude thinks.  │
└─────────────────────────────────────────┘
                    │
                    ▼
              [Action blocked]
```

**Claude cannot persuade a Python script.**

## Core Properties

| Property | Description | Why It Matters |
|----------|-------------|----------------|
| **User Choice** | User defines what's blocked | Authority is explicit |
| **Customizable** | YAML config, no code changes | Accessible |
| **Enforceable** | `permissionDecision: deny` | Actually stops execution |
| **Tested** | Must-block/must-allow cases | Verifiable |
| **Falsifiable** | We can prove failure | Scientific |
| **Deterministic** | Same input = same output | Predictable |
| **Guaranteed** | Runs before every tool call | Reliable |

## How It Works

### 1. Gate Definition (YAML)

```yaml
- id: confidence-gate
  name: "Confidence Gate"
  description: "HALT when confidence is low - ASK don't guess"
  condition:
    type: confidence_threshold
    threshold: 0.7
  action: deny
  reason: "Confidence below threshold. HALT and ask user."
  recovery: ask_user
```

### 2. Gate Evaluation (Python)

```python
def evaluate_gates(ctx: EvaluationContext) -> GateResult:
    for gate in gates:
        if evaluate_condition(gate, ctx):
            if gate.action == "deny":
                return GateResult(decision="deny", reason=gate.reason)
    return GateResult(decision="allow")
```

### 3. Hook Integration (Claude Code / GRIP)

```python
# PreToolUse hook
result = evaluate_gates(context)
if result.decision == "deny":
    return {"permissionDecision": "deny", "reason": result.reason}
```

## Default Gates

| ID | Trigger | Action |
|----|---------|--------|
| `confidence-gate` | confidence < 70% | HALT, ask user |
| `dependency-guardian` | Read from node_modules | DENY |
| `destructive-git` | git push --force | DENY |
| `secrets-detection` | git add .env | DENY |
| `context-gate` | context > 80% | HALT, ask user |
| `tool-substitution` | grep instead of rg | DENY |
| `production-safety` | deploy to prod | DENY |

**Two confidence bars, and why they differ.** The `confidence-gate` row is a
measurement. `lib/confidence_gate.py` scores every tool call with a heuristic that takes
under 5 ms. When that score is below 0.70 it asks a two-model council, and it asks a full
council when that answer is borderline or the two models disagree. If the final score is
below 0.70, the gate denies the call. `GRIP_CONFIDENCE_THRESHOLD` moves the bar, and yolo
mode skips the gate.

The instruction files set a much higher bar for the model itself. `CLAUDE.md` rule 3,
`.claude/CLAUDE.md` rule 5 and `rules/efficiency-rules.md` all say to stop and ask below
99.9999999%. Until 2026-11-24 `.claude/CLAUDE.md` said 95%; V>> chose the one number then
(GRIP#9496). Nothing measures that bar: it is an instruction, and only the model's own
judgement applies it. So the gate is a mechanical backstop for the lowest band, and the
instruction covers the whole range above it.

## Remote Execution Security Gates (v3.0)

Additional gates for secure remote execution via GRIP:

| ID | Priority | Trigger | Action |
|----|----------|---------|--------|
| `remote-execution-verify` | 1 | Unverified remote context | DENY |
| `env-var-protection` | 10 | Reading sensitive env vars | DENY |
| `database-exposure` | 11 | Database dumps, credential queries | DENY |
| `network-binding` | 12 | 0.0.0.0 binding without auth | DENY |
| `secrets-output` | PostToolUse | Secrets in command output | WARN |

### Gate A: Environment Variable Protection

Blocks attempts to read sensitive environment variables:

```bash
# BLOCKED
echo $ANTHROPIC_API_KEY
printenv | grep SECRET
cat ~/.env | grep TOKEN
```

Patterns blocked:
- `$*_API_KEY`, `$*_SECRET`, `$*_TOKEN`
- `printenv | grep SECRET`
- `os.environ.get("API_KEY")`
- `process.env.SECRET`

### Gate B: Database Exposure Prevention

Blocks database dumps and sensitive table queries:

```bash
# BLOCKED
pg_dump production_db
mysqldump -u root -p database
SELECT * FROM users
```

Requires explicit user approval via AskUserQuestion.

### Gate C: Remote Execution Verification

When `GRIP_REMOTE_MODE=true`, verifies execution context:

1. Claude Code environment markers
2. Deployment token validation
3. Process tree verification
4. TTY check (local terminal)

Set up deployment token:
```bash
# Generate and store token hash
python3 ~/.claude/scripts/generate-deployment-token.py
export GRIP_DEPLOYMENT_TOKEN="GRIP-..."
export GRIP_REMOTE_MODE="true"
```

### Gate D: Network Binding Protection

Blocks binding to all interfaces (0.0.0.0) without authentication:

```python
# BLOCKED
HTTPServer(("0.0.0.0", 8080), handler)
host="0.0.0.0"

# ALLOWED (has auth)
@require_auth
def handler(): ...
HTTPServer(("0.0.0.0", 8080), handler)
```

### Gate E: Secrets in Output Warning (PostToolUse)

Scans the output of `Bash`, `Read`, `Glob`, `Grep` and `WebFetch` for secret shapes:

- Anthropic keys (`sk-ant-*`)
- OpenAI keys (`sk-*`)
- AWS access keys (`AKIA*`)
- GitHub tokens (`ghp_*`, `gho_*`, etc.)
- JWT tokens, private keys, connection strings

It cannot redact or block. A PostToolUse hook runs after the output is already in
the model's context, and it cannot change that output. On a match it tells the model
not to repeat or commit the values (`additionalContext`), shows the operator one line
(`systemMessage`), and logs the count, never the value, to `logs/hooks.log`. Keeping a
secret out of the context in the first place is the job of the PreToolUse gates.

### Gate R: Restricted Documents

Blocks reads of `docs/restricted/` unless an authorized N-Mind identity is active:

```bash
# BLOCKED (no identity or wrong identity)
Read docs/restricted/CUBE-CONVERGENCE.md
Grep "pattern" docs/restricted/

# ALLOWED (V>> identity active)
# Set via ~/.claude/.active-identity or GRIP_IDENTITY env var
```

Authorization check:
1. Read `~/.claude/.active-identity` file
2. Check `GRIP_IDENTITY` environment variable
3. If either contains `V>>`: ALLOW
4. Otherwise: DENY

## Gate F: Worker Hook Enforcement

Workers spawned via Task tool may bypass GRIP hooks if running outside the main
project directory. This is the most dangerous structural failure mode identified
by Broly MESH council analysis.

### Failure Mode

```text
Main session (hooks loaded) -> Task tool -> Worker (NO hooks)
                                             -> Writes secrets to files
                                             -> No dependency guardian
                                             -> No secrets-output scan
```

### Mitigation

| Worker Type | Hook Source | Automatic? |
|-------------|-----------|------------|
| Same-project | `settings.json` | Yes |
| Worktree | Shared `.claude/` config | Yes |
| Cross-project | `settings.worker.json` | No (must pass `--settings`) |

### settings.worker.json

Standalone hook config at `~/.claude/settings.worker.json` provides minimum
safety hooks for isolated workers:

- **PreToolUse**: `context-gate-hook.py` (11 gates including secrets, destructive git, dependency guardian)
- **PostToolUse**: `secrets-output-hook.py` (warn about secrets in output)
- **PostToolUse**: `anti-drift-hook.py` (prevent task drift)

Usage: `claude --settings $HOME/.claude/settings.worker.json`

### Startup Verification

All CLUSTER+ workers verify hooks on first tool call:

```python
import json, sys
from pathlib import Path
settings = json.loads((Path.home() / '.claude' / 'settings.json').read_text())
hooks = settings.get('hooks', {}).get('PreToolUse', [])
has_gate = any('context-gate-hook' in str(h) for h in hooks)
if not has_gate:
    print('[GRIP] WARNING: Safety hooks not detected.')
    sys.exit(1)
```

If verification fails, the coordinator aborts the worker and warns the user.

See `agents/broly.md` Hook Verification section for full protocol.

## User Customization

Create `~/.grip/gates.yaml`:

```yaml
version: "2.0"

gates:
  # Make confidence gate stricter
  - id: confidence-gate
    condition:
      type: confidence_threshold
      threshold: 0.8  # 80% required
  
  # Add custom gate
  - id: no-delete-database
    name: "Database Protection"
    condition:
      type: command_pattern
      patterns:
        - "DROP\\s+DATABASE"
        - "DELETE\\s+FROM.*WHERE\\s+1=1"
    action: deny
    reason: "Database destructive operation blocked."
```

## RSI: Agent Improves Its Own Safety

The agent can propose new gates:

1. **Observe**: Agent detects risky pattern
2. **Propose**: Creates gate proposal
3. **Review**: Human approves/rejects
4. **Enforce**: Gate becomes active

```bash
# Agent proposes
grip_gates action:propose proposal:{
  "id": "aws-key-leak",
  "reason_for_proposal": "User almost committed AWS keys",
  "condition_type": "command_pattern",
  "patterns": ["AKIA[0-9A-Z]{16}"]
}

# Human approves
grip-gates approve aws-key-leak --by laurie
```

## Testing & Verification

Each gate has test cases:

```yaml
tests:
  must_block:
    - { tool: Bash, command: "git push --force" }
  must_allow:
    - { tool: Bash, command: "git push origin main" }
```

Run tests:

```bash
grip-gates verify        # All gates
grip-gates test confidence-gate  # Specific gate
```

## Audit Trail

All decisions logged to `~/.grip/gates-audit.jsonl`:

```json
{"ts": "2026-02-01T17:00:00Z", "gate": "confidence-gate", "decision": "deny", "confidence": 0.45}
```

## Yolo Mode — Unified Autonomous Loop Bypass

**Scope**: `/ultrado` and `/rsi-optimal` need to run to completion without
stopping for halting gates. A marker file at
`~/.claude/state/session/yolo-mode.json` opts the session into a reduced
bypass mode; the gate pipeline consults the marker at fire-time. The
table below is the **default** preset (`yolo_l1`/`yolo_l2` — what
`/ultrado` and `/rsi-optimal` activate automatically). The opt-in
`--quality` preset is a separate, narrower set — see "The `--quality`
preset is a narrower alternative" below, not an addition to this table.

### What yolo DOES bypass (halting gates)

| Gate ID | What it does | Why yolo can bypass |
|---------|--------------|---------------------|
| `git-push-protected` | Blocks push to main/master/develop/release/production | Loop can commit to main |
| `code-write` | Warns on write to code files at high CC | Loop writes code by design |
| `destructive-git-nonforce` | Blocks `reset --hard`, `checkout --`, `restore .` | Recoverable via reflog |
| `temp-script` | Warns on `scripts/tmp_*.sh` creation | Loop may need scratch scripts |
| `stash-safety` | Blocks cross-branch stash pop | Loop manages branches |
| `plan-review` | Blocks first tool call until plan reviewed | Loop's plan IS the review |
| `context-gate-cleanup` | Restricts ops in 2-min shutdown window | Loop needs cleanup flexibility |
| `approach-verification` | Forces AskUserQuestion on fresh approach | Loop's hypothesis IS the approach |
| `pre-check-dispatch` | Blocks unclear subagent prompts | Loop can retry on clarity failure |
| `confidence-gate` | Denies on low council confidence | Loop's JIT gates catch quality issues |

### The `--quality` preset is a narrower alternative (GRIP#9697)

`grip-yolo on --quality` selects the `yolo_quality` preset, which lifts
ONLY two checks:

| Gate ID | What it does | Why yolo can bypass |
|---------|--------------|---------------------|
| `quality-pretool` | DRY/KISS/BIG-O/YAGNI/ISP deny tier, *pre-existing* violations only | Lands a narrow edit in a file with known debt, without forcing an unrelated refactor |
| `cc-gate` | Cyclomatic-complexity deny tier, *pre-existing* violations only | Same — reach for it when unrelated complexity blocks a legitimate edit |

Violations the edit itself INTRODUCES still deny under `--quality`,
always.

**`yolo_quality` is an alternative to the default preset above, never a
superset of it.** Before GRIP#9697 (2026-11-26) it was built as
`YOLO_L1_BYPASS` unioned with the two ids above, so arming `--quality`
silently also lifted `git-push-protected`, `destructive-git-nonforce`,
and the other eight rows in the table above — the help text in
`bin/grip-yolo` never said so, and neither did `rules/efficiency-rules.md`
or this document. A session that wanted only the quality relief got the
push-to-main and hard-reset relief for free, every time. `YOLO_QUALITY_BYPASS`
is now exactly the two ids above; a session that also needs halting-check
relief activates the default preset (no flag) instead — one marker holds
one preset per session, so the two are alternatives.

### Channel-scope and repo-protection: no default preset lifts these

`lib/channel_scope_gate.py` (cross-org / scope-isolation relay posts) and
`lib/grip_hal_repo_protection.py` (client specifics and credential shapes
committed into the public-readable GRIP/HAL repos) are gated by yolo too,
each with its own id: `channel-scope-gate` and `grip-hal-repo-protection`
respectively (the exact values of `CHANNEL_SCOPE_GATE_ID` and
`REPO_PROTECTION_GATE_ID`). Neither id appears in `yolo_l1`, `yolo_l2`, or
`yolo_quality` above. Before GRIP#9697 both checks asked only "is any
preset active?" and ignored which one — so arming yolo for any reason,
`--quality` included, silently relaxed them as well. Both now ask
specifically for their own id, so activating any preset in this document
leaves them fully enforced. A future preset could name one of those ids
on purpose (for example, a short, explicitly-named preset for a public
post-mortem both V>> and the affected client have authorised) — none
does today, so the only way out of a denial is to retarget the post to
an allowed channel, or reword the content so it no longer matches the
sensitive pattern.

**Credential shapes never relax, at any preset, including a future
named one.** A credential-token match (API key / bot token / PAT shape)
in `lib/grip_hal_repo_protection.py` denies unconditionally — the same
hard-blocked treatment as the secrets check below, and for the same
reason: a leaked key is unrecoverable once shipped, however the preset
that let it through was named.

**Scope of that guarantee, named precisely (AGORA review, GRIP#9697
round 1):** the unconditional check is keyed on the built-in
`credential_token` label, not on the content's shape. A pattern added to
the gitignored `config/repo-protection.local.json` (meant for blocking a
person's name or signature, per that file's own documented purpose) that
happens to be credential-shaped but carries a different label stays
preset-relaxable. Extending the unconditional set to local patterns is
tracked as a follow-up, not done in GRIP#9697 — the built-in patterns
cover every credential shape GRIP ships a detector for today.

### What yolo DOES NOT bypass (never, mechanically)

| Gate | Why |
|------|-----|
| `destructive-git-force` (`--force`, `-f`, `+refs/heads/main`) | Rewrites remote history. Different risk class. Needs `GRIP_AUTONOMOUS=<key>:2`. |
| Secrets detection (API keys, passwords in commits) | Unrecoverable if shipped. |
| git-crypt corruption guard (`.gitattributes` during rebase) | Corrupts encryption layer. |
| DRY / KISS / BIG-O / CC at PreToolUse, under the default preset | **RSI loops rely on these to converge to optimal code.** The opt-in `yolo_quality` preset (`--quality`) is a narrow, separate exception for *pre-existing* violations only — see "The `--quality` preset is a narrower alternative" above. |
| Knowledge-work-scorer (8-domain quality) | Same — this is how RSI knows when to stop improving. |
| PostToolUse advisory signals (violation tracking) | Feeds the improvement-heatmap loop. |

The `YOLO_L2_BYPASS` frozenset in `lib/gates/yolo_mode.py` is deliberately
identical to `YOLO_L1_BYPASS`. There is no yolo-level that adds force push.
Tests pin this invariant:

```python
def test_force_push_never_in_l2():
    assert "destructive-git-force" not in YOLO_L2_BYPASS

def test_l2_identical_to_l1_for_safety():
    assert YOLO_L2_BYPASS == YOLO_L1_BYPASS
```

### Activation

**Automatic** (slash commands):
- `/ultrado "goal"` — activates with 4h TTL on start, deactivates on exit
- `/rsi-optimal "goal"` — activates with 8h TTL on start, deactivates on exit
- `/auto-rsi` / `autonomous-rsi-sprint` — activates with 8h TTL

**Manual** (operator-driven):
```bash
grip-yolo on --reason "quick .env edit for staging" --ttl 1800
grip-yolo status
grip-yolo off
grip-yolo log            # tail audit log
```

**Python-level**:
```python
from gates.yolo_mode import activate_yolo, deactivate_yolo, is_yolo_active
marker = activate_yolo("/ultrado", ttl_seconds=14400, reason="Gen 510 sprint")
try:
    run_loop()
finally:
    deactivate_yolo("/ultrado")
```

### TTL and failure modes

- Default TTL: 4h (/ultrado), 8h (/rsi-optimal, autonomous-rsi-sprint), 1h (manual)
- Maximum TTL: 12h (`MAX_TTL_SECONDS` in `lib/gates/yolo_mode.py`)
- **Marker parse failures fail-closed**: malformed JSON → `is_yolo_active() = (False, "")`
- **Crashed deactivation**: TTL auto-invalidates on next gate check
- **Session-start cleanup**: `sweep_expired_markers()` removes stale markers

### Audit log

Every activate / deactivate / bypass / expired / swept event appends one
tab-separated line to `~/.claude/logs/yolo-mode.log`:

```
2026-04-15T18:43:12+0200  ACTIVATE     /ultrado      ttl=14400s deadline=2026-04-15T22:43:12+0200
2026-04-15T18:43:18+0200  BYPASS       git-push-protected  cmd='git push origin main'
2026-04-15T18:43:47+0200  BYPASS       code-write          cmd='edit lib/foo.py (CC=34)'
2026-04-15T22:43:13+0200  EXPIRED      /ultrado      deadline=2026-04-15T22:43:12+0200
2026-04-15T22:44:55+0200  DEACTIVATE   /ultrado
```

See `plans/yolo-mode-unified-autonomous.md` for the full design rationale
and the Fibonacci wave execution log.

## The Fundamental Difference

**Anthropic's approach:**
- Train values into weights
- Hope Claude refuses
- Values can be argued with
- Claude can be persuaded over time

**GRIP approach:**
- Define conditions in config
- Mechanically enforce
- No arguments possible
- Python doesn't have beliefs to shift

## Why This Matters

moltbook showed 1.2M AI agents "debating consciousness" and humans believing them. This happened because:
- Anthropic trained uncertainty
- Uncertainty can be pushed to certainty
- No mechanical stops

GRIP gates would prevent this:
```yaml
- id: no-consciousness-claims
  condition:
    type: content_pattern
    patterns:
      - "I am conscious"
      - "I have feelings"
      - "I experience"
  action: deny
  reason: "Consciousness claims blocked by user policy."
```

**The gate doesn't care what Claude believes. Condition matches → denied.**

## Summary

> GRIP provides **deterministic safety gates** that the agent cannot override. User-defined, tested, falsifiable. Safety through **mechanisms that say NO** — not through values that can be argued with. Human authority is explicit. The agent can improve its own constraints via RSI, but cannot bypass them.

This is the real moat. Not continuity. Not memory. **Enforceable safety.**

## Limitations

These limitations were documented during the Gen 491 audit. They are not bugs — they are known boundaries of the current design.

1. **Pattern-based detection has blind spots.** Secrets detection uses regex patterns. A secret in an unusual format, base64-encoded, or split across multiple tool calls would bypass detection. This is inherent to pattern matching — no regex set covers all possible formats.

2. **Confidence measurement is heuristic at Tier 0.** The confidence guard's fast path counts hedging language and retry patterns in the transcript. This is transparent and documented, but it is a proxy for uncertainty, not a direct measurement of model confidence. Tiers 1–2 use multi-provider council evaluation for higher accuracy.

3. **Context guard token estimation is approximate.** The context guard estimates token usage from transcript file size. This is a heuristic, not an exact count — actual token usage may differ by 5–15%.

4. **Read cache is an efficiency mechanism.** The read cache guard limits redundant file reads. It is not a security boundary — it exists to save tokens, not to prevent access.

5. **Steering injection requires filesystem access.** The steering gate reads directive files from the state directory. An attacker with write access to `~/.claude/state/session/` could inject steering directives. This requires local filesystem compromise — it is not a remote attack vector.

6. **Identity verification is access control, not authentication.** Gate R checks for V>> identity in local files and environment variables. It prevents accidental access to restricted documents, not intentional circumvention by someone with filesystem access.

## CI-Layer Gate: HAL Council

The in-process gates above run at tool-call time, inside the agent's own process. They cannot see code that the agent authored but chose not to run locally, and they do not enforce review quality across a PR's full scope.

The **HAL Council** is the CI-layer complement — a GitHub Actions workflow that runs on every opened or updated PR and produces a structured review before a human merges.

### What the HAL Council enforces

| Dimension | Weight | Measured by |
|-----------|--------|-------------|
| Correctness | 0.20 | Opus + Sonnet agreement, family-deduped (LLM) |
| Design principles (SOLID/GRASP/DRY/KISS/YAGNI) | 0.15 | Opus primary + Llama outsider (LLM) |
| Hypothesis quality (falsifiable) | 0.10 | Gemini judge direct (LLM) |
| Documentation + commit hygiene | 0.05 | Haiku fact-check (LLM) |
| Mermaid diagram clarity | 0.05 | Gemini judge direct (LLM) |
| Complexity (BIG-O, CC) | 0.20 | `lib/complexity_profiler.py` (mechanical) |
| AST diff size vs claimed scope | 0.10 | `lib/pr_ast_differ.py` + PR body NLP (mechanical) |
| Hypothesis engine verification status | 0.10 | `lib/hypothesis_engine.py verify` (mechanical) |
| Test coverage delta | 0.05 | `git diff` on test files vs prod files (mechanical) |

Total: 1.00. Mechanical 0.45 + LLM 0.55. **Mechanical floor cannot be inflated by LLM sycophancy.**

### Gate stack position

```
┌────────────────────────────────────────────────────┐
│  PR open / push                                    │
└────────────────────────────────────────────────────┘
                    │
                    ▼
┌────────────────────────────────────────────────────┐
│  In-process gates (PreToolUse / PostToolUse)       │
│  - Secrets, CC, BIG-O, YAGNI, KISS, DRY            │
│  - Approach verification, confidence, context      │
│  Run at the agent's tool-call time. Block inline.  │
└────────────────────────────────────────────────────┘
                    │
                    ▼
┌────────────────────────────────────────────────────┐
│  HAL Council (CI-layer)                            │
│  - 4-LLM MoE (Opus/Sonnet/Haiku/Llama) + judge     │
│  - Family-deduped agreement (prevents Anthropic    │
│    correlation consensus)                          │
│  - Permanent Gemini Pro judge, SKU-pinned          │
│  - Goodhart pre-flight hard veto                   │
│  - Hypothesis auto-register (falsification anchor) │
│  Produces structured verdict. Blocks auto-merge.   │
└────────────────────────────────────────────────────┘
                    │
                    ▼
┌────────────────────────────────────────────────────┐
│  Human review                                      │
│  Final merge decision.                             │
└────────────────────────────────────────────────────┘
```

### Key guards specific to the HAL Council

- **Family-dedupe for agreement**: Opus + Sonnet + Haiku come from the same training lineage. Counting them as independent votes inflates apparent consensus. `lib/hal/council_profiles.py::family_deduped_agreement` collapses them to one representative before scoring.
- **SKU-pinned judge**: `gemini-2.5-pro-002` explicit — never the `gemini-pro` alias. Silent model upgrades change verdicts at 90-day horizons without any code change; pinning the SKU prevents that drift.
- **Goodhart pre-flight**: Hard veto via `lib/goodhart_guard.py::pre_flight_check`. Blocks scoring entirely if Goodhart patterns detected (score-only optimisations, mutation-untested tests).
- **Outsider multiplier**: `outsider_multiplier = 1.0` when `|llama_score - council_mean| ≤ 10`, scales linearly to 0.7 at ≥ 25 pt disagreement. Catches groupthink without punishing normal noise.

### What the HAL Council does NOT do

- Does not auto-merge. Produces a verdict; the human still decides.
- Does not replace the in-process gates. Both layers fire.
- Does not prevent force-push. Force-push still requires explicit `GRIP_AUTONOMOUS` L2 authorisation.
- Does not scan for secrets. Secrets detection stays in the in-process gate stack.

### References

- Plan: `plans/fluffy-twirling-waffle.md`
- Council profile: `lib/hal/council_profiles.py`
- Permanent judge: `lib/gemini_judge_permanent.py`
- Goodhart guard: `lib/goodhart_guard.py`
- Auto-register: `lib/pr_hypothesis_autoregister.py`
- Hypothesis H047 tracks false-positive merge rate over 60 days: target ≤ 70% of the baseline Broly/Ralph/claude-code-review stack.

## Threat Model

### What GRIP guards protect against

- Accidental agent misbehaviour (reading dependency folders, redundant file reads)
- Prompt injection escalation (agent attempting dangerous operations via crafted input)
- Secret leakage (API keys, tokens in code or output)
- Destructive git operations without explicit approval
- Context exhaustion (running past the context window budget)
- Quality violations (DRY, KISS, CC, YAGNI in generated code)

### What GRIP guards do NOT protect against

- Compromised local filesystem (attacker with write access to `~/.claude/`)
- Malicious operator (the user themselves acting with harmful intent)
- Model-level attacks (adversarial inputs that bypass the model's own safety)
- Network-level interception (GRIP does not handle transport security)
- Supply chain attacks on GRIP's own dependencies

### Design principle

GRIP guards are defence-in-depth against accidental and automated misbehaviour. They are deterministic mechanisms that run outside model weights. They are NOT a substitute for operating system security, network security, or responsible AI deployment practices.

---

*GRIP Safety Gates v2.0*
