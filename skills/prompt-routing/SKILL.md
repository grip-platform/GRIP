---
name: prompt-routing
description: Auto-assesses every non-exempt user prompt and recommends the optimal execution pattern (Direct/Focused/Sprint/FullMesh). Detects both under-specified prompts that need upgrading and over-specified prompts that need downgrading. HITL-agnostic — ships with an A/B/C text fallback that works when AskUserQuestion is unavailable.
status: PARAMOUNT
version: 1.0.0
effort: medium
chains_to: capability-loading
triggers:
  - "routing"
  - "optimal prompt"
  - "which command should I use"
integrates:
  - execution-router/SKILL.md
  - auto-mode/SKILL.md
  - prompt-coach/SKILL.md
  - asking-users/SKILL.md
  - creating-wizards/SKILL.md
  - hitl/SKILL.md
  - recall/SKILL.md
see_also:
  - execution-router
  - rsi-optimal
  - ultrado
---

# Prompt Routing — Auto-optimal execution pattern selection

## The Problem

GRIP has a taxonomy of execution patterns (`execution-router`): Direct, Focused,
Sprint, Full Mesh. Each matches a complexity tier. Users rarely know which
pattern to invoke — they just describe the task. Result: sub-optimal execution,
either **over-stacking** (launching `/rsi-optimal` for a typo fix) or
**under-powering** (using `/converge` for a whole-codebase refactor).

The previous session's insight, codified by V>>:

> Match the execution architecture to the task's actual complexity, not the
> maximum available complexity. Stacking every pattern is O(n!) when O(n) would do.

This playbook is the meta-layer that mechanically applies that insight to
every non-exempt user prompt.

## When It Activates

On **every user message**, except the following exemptions:

1. Conversational exchanges ("thanks", "RL++")
2. Self-contained slash commands (`/save`, `/recall`, `/status`, etc.)
3. Bare slash commands with no content
4. Direct execution verbs ("commit this", "edit line 42")
5. Pure creation ("write a new script that...")
6. Already-in-context data (pasted code with no question)
7. User-requested external search ("google this", "search the web")

The exemption check is the fast path. When none of these match, the
prompt-routing assessment runs.

## The Assessment Flow

```
user prompt
     │
     ├── is_exempt() ── yes ── skip routing, proceed normally
     │
     └── no ──> lib/prompt_routing.assess(prompt)
                     │
                     │  ├── BAR score (lib/prompt_router.classify_prompt)
                     │  ├── recommended_pattern   (Direct|Focused|Sprint|FullMesh)
                     │  ├── user_implied_pattern  (from phrasing + slash command)
                     │  └── mismatch_tiers        (|recommended - implied|)
                     │
                     ├── mismatch = 0 or 1 ── proceed with user's framing (silent)
                     │
                     └── mismatch ≥ 2 ── HITL confirmation (see below)
```

## HITL Confirmation (Hybrid — Agnostic to Tool Registration)

When a mismatch of 2+ tiers is detected, the playbook must surface the
upgrade/downgrade proposal to the user. Because `AskUserQuestion` may be
unavailable (see `bin/grip` docs and anthropics/claude-code#39342), the
confirmation mechanism is a **two-path hybrid**:

### Path A — Native `AskUserQuestion`

Try first. Build a 3-option question:

```
question: "Prompt routing suggests {recommended}, you seemed to want {implied}."
options:
  - label: "Original"
    description: "Use {implied_pattern}: {implied_reason}"
  - label: "Optimal"
    description: "Use {recommended_pattern}: {recommended_reason}"
  - label: "Explain"
    description: "Break down the scoring before deciding"
```

If the tool call succeeds and the user picks one, proceed accordingly.

### Path B — Inline text fallback

If `AskUserQuestion` is not registered in the session (ToolSearch returns
nothing) OR the call raises an error, emit this text:

```
▸ Prompt routing: {user's framing} would be {implied_pattern},
  but complexity analysis suggests {recommended_pattern} (score {N}/15).
  Rationale: {rationale}

  [A] Proceed with {implied_pattern} (your original framing)
  [B] Upgrade/downgrade to {recommended_pattern}
  [C] Explain the scoring breakdown first

  Reply with A, B, or C.
```

Wait for the next user message. Parse the first non-whitespace character
that matches `[ABCabc123]`:

- `A`, `a`, `1` → proceed with implied pattern
- `B`, `b`, `2` → proceed with recommended pattern
- `C`, `c`, `3` → emit scoring breakdown, then ask again

If the user responds with prose that doesn't clearly select an option,
**proceed with the recommended pattern** and mention the fallback choice
in the next response. Never block waiting forever.

## Downgrade Detection (the lesson from the previous session)

The playbook MUST fire equally on over-specification. When a user invokes
a heavy pattern for trivial content:

```
/rsi-optimal "add a comment"
```

The lib scores the content at 5/15 (Direct), while the `/rsi-optimal` prefix
implies FullMesh. Mismatch = 3. The HITL flow runs, proposing the downgrade.
This prevents burning hours on Fibonacci waves for a one-line change.

Without this, the playbook would be asymmetric — helping timid users scale
up but letting confident users burn tokens on over-stacked patterns. Both
directions are in scope.

## Cost-Aware Downgrade (HAL Sentinel + cost_tracker)

`prompt-routing` consults two HAL components to recommend cheaper patterns
when the session is under budget pressure or the primary provider is near
its utilisation limit. This is the InfiniteMachine pattern applied to
routing: HAL for tokens, cheaper tier for cost.

### Substrate

| Source | Type | Read pattern |
|--------|------|--------------|
| `lib/hal/sentinel.py` state file at `state/hal/limits.json` | per-provider `utilisation` | file-read, fail-safe |
| `lib/hal/cost_tracker.py` `_PRICING` table | per-model USD/Mtok | pure-function import |

`prompt-routing` does NOT instantiate `LimitSentinel` or any registry. It
reads the persisted Sentinel state directly so the composition layer stays
clean and the HAL stack stays optional.

### Provider tiers

Each pattern has an implied provider tier:

| Pattern | provider_tier |
|---------|---------------|
| Direct  | free / local |
| Focused | paid_standard (Sonnet-tier) |
| Sprint  | paid_premium (Opus-tier) |
| FullMesh | paid_premium (Opus-tier) |

### Downgrade rule (4-condition AND)

A cost-driven downgrade fires only when ALL four conditions hold:

1. The BAR-recommended pattern is `Sprint` or `FullMesh`.
2. Anthropic Sentinel utilisation ≥ 0.70 OR session cost ≥ budget.
3. The prompt is downgrade-eligible (not flagged as hypothesis-driven,
   architecture-review, or codebase-wide).
4. A cheaper pattern with adequate capability exists.
   (Sprint → Focused, FullMesh → Sprint.)

The default budget is `$5/session`, configurable via `GRIP_COST_BUDGET_USD`.

### Output fields

When a cost-driven downgrade applies, three additional fields are populated
on `RoutingAssessment`:

| Field | Meaning |
|-------|---------|
| `cost_aware_pattern` | The cheaper pattern suggested (or `None`) |
| `cost_aware_reason` | Human-readable rationale (`"anthropic utilisation 0.82 ..."`) |
| `provider_tier` | Always populated (`paid_premium`/`paid_standard`/`free`/`local`) |

### Audit log

Every cost-driven downgrade appends one JSONL line to
`state/routing/cost-downgrades.jsonl` with timestamp, original pattern,
downgraded pattern, reason, sentinel utilisation, and session cost. The log
is the falsification record for hypothesis H198.

### Fail-safe invariants

- HAL state file unreadable → no cost-awareness, no downgrade, behaviour
  identical to today.
- `cost_aware_pattern` is a SUGGESTION. It never silently overrides
  `recommended_pattern`. The downstream skill consults it as part of the
  HITL Path A/B confirmation.
- Hypothesis-driven and architecture-tier prompts are NEVER downgraded for
  cost reasons. Those scopes are user-owned.

### Hypothesis H198

> Cost-aware routing reduces paid-service token spend ≥ 15% over 30 days
> versus the pre-W16 baseline.

Metric: tokens/session on paid Opus from `lib/hal/cost_tracker.py`
breakdown. Deadline: 2026-05-27. Falsifier: spend unchanged or higher.

## Integration with Other Skills

| Skill | Relationship |
|-------|-------------|
| `execution-router` | Canonical source of the four-pattern taxonomy. This playbook auto-invokes it. |
| `auto-mode` | Orthogonal — modes stack at any pattern tier. This playbook does not overlap. |
| `prompt-coach` | Passive; suggests improvements when users explicitly invoke it. This playbook is active. |
| `asking-users` | PARAMOUNT. The HITL mechanism respects its bidirectional pattern. |
| `recall` | Provides the exemption categories. |

## Fail-safe Invariants

1. **Any exception → exempt.** The playbook never blocks a prompt on its own failure.
2. **Mismatch < 2 → silent.** No ceremony when the user's implied pattern is close enough.
3. **No text fallback → proceed with recommended.** Never wait forever on an unparseable reply.
4. **Never downgrade a hypothesis-driven task.** If the prompt registers a falsifiable hypothesis, the assessment is advisory only — hypothesis scope is user-owned.

## Execution Protocol (for the agent)

On every user message, the agent SHOULD:

1. **Check** `state/routing/last-assessment.json` (written asynchronously by `hooks/prompt-routing-hook.py`).
2. If the file does not exist → assume exempt or still computing → proceed normally.
3. If it exists:
   - If `exempt: true` → delete the file, proceed normally.
   - If `mismatch_tiers < 2` → delete the file, proceed normally. Mention the pattern choice in the response if noteworthy.
   - If `mismatch_tiers >= 2` → run the HITL confirmation flow (Path A, fall back to Path B), then delete the file.
4. After handling, always **delete** `state/routing/last-assessment.json` to prevent stale assessments firing on subsequent turns.

The hook runs asynchronously so the assessment result may arrive after the
agent's first reasoning step. Read the file once at the start of each turn;
if it arrives mid-turn, it applies to the NEXT turn.

## CLI for Debugging

```bash
python3 ~/.claude/lib/prompt_routing.py "fix the typo on line 42"
python3 ~/.claude/lib/prompt_routing.py "make the whole codebase better"
python3 ~/.claude/lib/prompt_routing.py "review the architecture of the entire project"
```

## Known Limitations

1. **Heuristic-only when embeddings unavailable.** The BAR classifier's Tier 2
   semantic layer requires `sentence-transformers`; without it, the fallback
   is keyword-only.
2. **English-only patterns.** Exemption lists are English-only. Multilingual
   support would need i18n tables.
3. **No context-of-conversation yet.** The assessment looks at the single
   prompt. A prompt that means "continue the previous refactor" can't know
   what the previous refactor was. `session_context` parameter is reserved
   for future enhancement.
4. **Slash command exemption list is static.** New self-contained slash
   commands need to be added to `_SELF_CONTAINED_SLASH_COMMANDS` in
   `lib/prompt_routing.py`.

## Origin

Codified in session `5c26f278` (Gen 479+) after V>>'s ultraplan for a
meta-layer that auto-applies `execution-router` to every user prompt.
The HITL-agnostic text fallback was designed in response to a concurrent
discovery of the AskUserQuestion/ExitPlanMode regression
(anthropics/claude-code#39342).

**Falsification criterion:** This playbook's value is disproved if:

1. False-positive routing (mismatch flagged when user was right) exceeds 10%
2. The text fallback is slower/clunkier than native `AskUserQuestion` in ≥80%
   of cases once the upstream tool regression is fixed
3. Users consistently pick `[A]` (their original framing) when the scorer
   recommends `[B]` — evidence that the scorer is wrong, not the user
