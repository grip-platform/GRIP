---
name: activate-grip-tui
description: TUI++ activation wizard with three-act structure (Identity → Capability → First Run). Use when /activate-grip invoked, fresh install detected (no .onboarded.json), or identity reset requested. Replaces skills/authenticating-with-claude with delight-first onboarding while preserving full schema parity.
status: Active
effort: high
version: 1.3.0
triggers:
  - /activate-grip
  - first-run detection (no .onboarded.json)
  - /activate-grip --reset
integrates:
  - asking-users
  - hitl
  - creating-wizards
  - swiss-nihilism-design
  - auto-memory-hook
---

# Activate GRIP — TUI++

Three-act onboarding for new operators. Replaces the legacy procedural wizard
with a paced, character-forward flow that doubles as the operator's first
real GRIP loop.

## Why this exists

The legacy `authenticating-with-claude` skill (now archived under
`skills/_archived/authenticating-with-claude/SKILL.md` per GRIP #3139) was
functionally complete but felt clinical: new operators saw a sequence of bare
AskUserQuestion prompts before they had any sense of what GRIP is. This skill
is the first-touch experience — same setup mechanics, three deliberate acts,
one delight per act.

**Hypothesis**: H329 (formerly H-ACTIVATE-TUI-1). Falsified if the wizard
median exceeds 5 minutes wall-clock, filesystem state diverges from legacy,
test users report the flow as standard CLI, OR any delight crashes the
wizard. Verification 2026-05-18.

## Schema parity (PARAMOUNT)

The legacy wizard's downstream consumers (upgrade detection, settings.local
regeneration, hooks/session-start) depend on `.onboarded.json` v6.0.0
schema. This skill MUST emit the same shape. Drift = breakage.

The shared writer is `lib/activate_grip_state.py`:
```python
from lib.activate_grip_state import write_onboarded, write_identity
write_onboarded(grip_home, operator="V")  # always v6.0.0 with all 9 steps
write_identity(grip_home, name=..., persona=..., signature=..., mode=...)
```

The legacy skill's Step 6 saves the identical payload — the writer is the
parity guarantee, not skill prose.

## Five-Phase Spine (Identity → Capability → Deps → Install → Ready)

The legacy three acts (Identity, Capability, First-Run) are preserved; a
**Deps** detect phase and an **Install** heal phase are inserted before the save
so no operator ever finishes "activated" with dead MCP servers (issue #3896).

### Act I — Identity

**Pre-context panel** (render before first prompt):

```
═══════════════════════════════════════
GRIP — Guarded Response Interception Protocol.
A self-optimising recursive self-improvement engine.
First, who are you.
═══════════════════════════════════════
```

**Step 1: Auth check** (silent if already authenticated):

```bash
claude auth status 2>/dev/null || echo "not-authenticated"
```

If not authenticated:
```text
AskUserQuestion
  Question: "GRIP uses Claude for AI capabilities. Sign in to continue?"
  Header: "Auth"
  Options:
    - "Sign in now"        → claude login
    - "Continue limited"   → some features will be unavailable
```

**Step 2: Name**:

```text
AskUserQuestion
  Question: "What should I call you?"
  Header: "Name"
  Options:
    - "Use '{whoami}'"   → name = $(whoami)
    - "Stay anonymous"   → name = "User"
```

**Step 3: Persona**:

```text
AskUserQuestion
  Question: "What best describes how you'll use GRIP?"
  Header: "Persona"
  Options:
    - "Developer"  → /create-pr, /feature-complete, /generate-e2e-tests, /setup-ci
    - "Designer"   → /figma, /ui-complete, /audit-mobile-responsive, /image-optim
    - "Writer"     → /generate-pdf, /write-medium-article, /update-docs, /markdown-lint
    - "Founder"    → /founder, /contract-formal, /gitops, /backup-grip
```

**Step 4: Signature picker**:

```text
AskUserQuestion
  Question: "GRIP supports quick command prefixes (e.g. J>>). Create one?"
  Header: "Signature"
  Options:
    - "Yes, create one"  → follow-up: prompt for letter (single uppercase A-Z)
    - "Skip"             → signature = ""
```

**Delight 1 — Signature reveal** (the moment):

After the operator picks a letter, render the signature block before
proceeding to Act II. Use `lib/activate_grip_signature.py`:

```python
from lib.activate_grip_signature import render_signature, render_flavour
print(render_signature(letter))   # large block + instruction-mode explainer
print(render_flavour(letter))     # one-line "Welcome, X>>" line
```

The renderer uses Swiss-Nihilism monospace glyphs (no ANSI escapes — keeps
chunk-boundary streaming safe per `lib/learned-insights.md`
skill-20260331004751). Falls back to plain text if the helper raises.

### Act II — Capability

**Pre-context panel**:

```
═══════════════════════════════════════
What you'll have access to:
  · 285 skills, 38 agents, 88 commands
  · Hypothesis-driven loops with falsifiable predictions
  · Closed-loop memory across sessions
  · Multi-provider LLM mesh (HAL)
Two more questions, then we run a real GRIP skill together.
═══════════════════════════════════════
```

**Step 5: Mode**:

```text
AskUserQuestion
  Question: "How will you use GRIP?"
  Header: "Mode"
  Options:
    - "Solo"        → single-operator default
    - "Join team"   → enter team password
    - "Create team" → invoke team-creation sub-wizard
```

**Step 5.5: Extended thinking**:

```text
AskUserQuestion
  Question: "Do you have Claude MAX subscription with extended thinking?"
  Header: "MAX"
  Options:
    - "Yes, I have MAX"  → touch ~/.claude/.extended-thinking
    - "No/Unsure"        → defer (skills load on-demand)
```

**Step 5.6: Budget**:

```text
AskUserQuestion
  Question: "Enable budget mode? Reduces token usage via caching + shorter outputs."
  Header: "Budget"
  Options:
    - "Yes, budget mode" → settings.local.json env.GRIP_BUDGET_MODE="true"
    - "No, full mode"    → settings.local.json env.GRIP_BUDGET_MODE="false"
```

**Step 5.65: Plan tier** (the context-window envelope — PARAMOUNT for Pro
users; source trace: `docs/grip-tier-1m-context.md`):

Why this step exists: Claude Code's 1M-context window is **credit-gated on
every subscription plan** (Pro: 1M needs usage credits; Max: Opus 1M
included but Sonnet 1M still needs credits; only API/pay-as-you-go gets 1M
free). A Pro user with no credits is at **200K** and hits the wall fast.
GRIP works inside 200K — but only if it knows to compact early + lean on
memory. This step captures the plan so GRIP applies the right envelope.
This supersedes the old binary "Do you have MAX?" framing (Step 5.5),
which missed the Pro/credits reality entirely.

```text
AskUserQuestion
  Question: "Which Claude plan are you on? (Tunes GRIP's context budget — Pro is 200K unless you add usage credits.)"
  Header: "Plan"
  Options:
    - "Pro"                 → grip-tier = "pro"  (200K — GRIP compacts early + uses memory so sessions survive)
    - "Max/Team/Enterprise" → grip-tier = "max" (Opus 1M included)
    - "API / pay-as-you-go" → grip-tier = "api" (full 1M, no gating)
    - "Not sure"            → grip-tier = "auto" (auto-detect: API-key present → api, else pro safe-floor)
```

After pick, render teaching moment:

```text
Picked: {choice}
GRIP tier applied. {envelope blurb — e.g. for Pro:}
  "Pro plan — 200K window. GRIP compacts early + uses memory so sessions
   survive. Want 1M? Add usage credits at claude.ai/settings/usage."
Switch any time:
  grip tier <pro|max|api|auto>
  grip tier status     ← shows current + what it means
```

Persistence: during Act III save phase, call
`lib.grip_tier.set_tier(choice, set_by="wizard", apply_launch_defaults=True)` — writes
`state/grip-tier-config.json` and, on `pro`, applies the Pro launch defaults
(`lib/pro_launch_defaults.py`): the launch pin `claude-sonnet-5-5` through
`lib/model_pin.py` (the plain vendor id, never the `[1m]` spelling) and
`medium` effort in `settings.json` (`env.CLAUDE_CODE_EFFORT_LEVEL` and
`effortLevel`). `max` and `api` write nothing beyond the tier file: the
reference profile's `max` effort and the operator's own pin stand. Say so in
the Pro teaching moment: "GRIP will launch Sonnet 5.5 at medium effort; change
either with `grip-model-default` or `/effort`." The **"Not sure"** path persists `"auto"`
(resolver detects each session start; never asserts an entitlement the
principal may not have). The resolved envelope's `auto_compact_window`
(180K for pro) is the value GRIP surfaces for `CLAUDE_CODE_AUTO_COMPACT_WINDOW`
so Pro compacts *before* the 200K wall (and since Claude Code bypasses
`CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE` when auto-compact is on, early
auto-compact is exactly the right lever — per the source trace).

**Step 5.66: Frontier model** (opt-in — #394; source trace: `.claude/CLAUDE.md`
"Frontier-generation onboarding opt-in"):

GRIP runs on Claude Code's model. `default` is the routine default: it clears
any override and reverts to the account's runtime default (Opus 5.5 at 1M on
Max), matching bin/grip's launch pin. Opus 4.8 and the Fable generation are the
frontier tier (Claude 5, Mythos-class — the latest SOTA). Opt-in — the default
keeps `default`.

```text
AskUserQuestion
  Question: "Which Claude model should GRIP run on? `default` reverts to your account's runtime default (Opus 5.5, 1M on Max); Opus 4.8 / Fable are the frontier tier (heavier — best for surgery + hard reasoning)."
  Header: "Model"
  Options:
    - "Default (recommended)" → "default"   (default — account runtime default; Opus 5.5 at 1M on Max)
    - "Sonnet 5"              → "sonnet-5"  (sonnet[1m] — fast, routine; 1M needs usage credits on Max)
    - "Sonnet 5.5"            → "sonnet-5-5" (claude-sonnet-5-5[1m] — the Pro tier's launch default at Sonnet 5's price; `grip-tier pro` pins the plain id)
    - "Opus 5.5"              → "opus-5-5"  (claude-opus-5-5 — natively 1M, cheaper than Opus 5)
    - "Opus 4.8"              → "opus-4-8"  (claude-opus-4-8[1m] — high-effort / VERIFY frontier)
    - "Fable 5.1"             → "fable-5-1" (claude-fable-5-1 — current Fable; long-horizon agentic)
    - "Fable 5"               → "fable-5"   (claude-fable-5 — previous Fable, vendor-listed legacy)
```

Every key here must exist in `lib/grip_model.FRONTIER_MODELS`; an option the map
does not carry raises on save, and a map entry no option offers is a choice no
operator can reach. Keep the two in step whenever either changes.

Tier-awareness (teaching moment, non-blocking): Opus 4.8 1M is included on
Max/Team/Enterprise; on Pro the 1M variant needs usage credits (ties to the plan
tier from Step 5.65). Say so, never block.

Persistence: during Act III save phase, call `lib.grip_model.set_model(choice)`
— writes ONLY `settings.json` `.model` (a user field install.sh preserves across
`--update`), every other key untouched; an unknown choice raises rather than
writing a broken id. Switch any time with the native `/model`.

**Step 5.7: Output style** (#2868 ultraenhance — V>> 2026-05-21):

```text
AskUserQuestion
  Question: "How should GRIP format its replies?"
  Header: "Style"
  Options:
    - "Medium"        → output-tier = "medium" (default — concise + plain + load-bearing facts only)
    - "Super-simple"  → output-tier = "super-simple" (ELI5 register, broader audiences)
    - "Detailed"      → output-tier = "detailed" (audit-trail mode, RSI/traceability work)
```

After pick, render teaching moment before Step 6 confirm:

```text
Picked: {choice}
You can switch any time:
  /output-tier <super-simple|medium|detailed>
  /output-tier status     ← shows current
```

Persistence: during Act III save phase, call `lib.output_tier._set_tier(choice)` — writes `state/output-tier-config.json` with `set_by="wizard"` + `set_at` timestamp.

**Step 5.75: Semantic memory** (opt-in always-warm embeddings — #4025):

A quick, optional offer. Declining is instant and never blocks activation.

```text
AskUserQuestion
  Question: "Enable always-warm semantic memory? A small local model (ollama +
             nomic-embed-text, running on your machine) turns your notes and
             history into searchable meaning, kept warm for instant recall.
             Optional; nothing leaves your machine."
  Header: "Memory"
  Options:
    - "Yes, enable"  → run: grip embeddings-warm --accept enabled
    - "Not now"      → run: grip embeddings-warm --accept disabled
```

If ollama is not installed, the setup no-ops gracefully with a plain-English note
— it never blocks activation. The `embeddings_warm` onboarding step is recorded
either way (the choice was offered). Revisit any time with `grip embeddings-warm`;
prove it works with `/verify-embeddings`.

**Step 5.8: Harness × model** (surface the harness-agnostic capability — #3648/#3649 `grip-harness`):

GRIP's substrate (HAL multi-provider routing + `grip-run` code-exec) can power
OTHER AI CLIs through its HAL gateway (`:4010`), not just Claude Code. This step
surfaces that capability. Opt-in — `Just Claude Code` is the common default and
writes no state.

```text
AskUserQuestion
  Question: "GRIP can also power other AI CLIs (codex, aider, opencode, cursor) through its HAL gateway — your GRIP substrate in a foreign harness, one command. Wire one now?"
  Header: "Harness"
  Options:
    - "Just Claude Code"  → skip (default); `grip-harness list` shows the registry any time
    - "codex"             → wire via `grip-harness codex`
    - "opencode"          → wire via `grip-harness opencode`
    - "Other / later"     → run `grip-harness list`, defer wiring
```

If a harness is chosen, offer the model (default `grip_harness.HAL_DEFAULT_MODEL`
= `gpt-5.5`; any `claude-*` id is also valid), then wire:

```bash
grip-harness <harness> [--model <id>]     # idempotent; rewrites ONLY the GRIP-managed fence
```

Persistence: the model preference resolves `GRIP_HARNESS_MODEL` →
`state/harness-model-config.json` `models[<harness>]` → `HAL_DEFAULT_MODEL`
(`lib/grip_harness_model.py`). Wiring writes only the harness's own config-file
GRIP-managed fence — never `.onboarded.json`, so schema parity holds. Skip →
no state written; GRIP stays on Claude Code.

**Step 5.85: Where will you run GRIP?** (the door, not the harness; asked once,
the gift-kit review of 2026-10-16):

GRIP is a layer on Claude Code and opens from several doors. Only `gg++` goes
through `bin/grip`, so only it applies the plan tier's launch defaults from
Step 5.65 (the model pin, the effort level, the first-run banner). Ask once, so
the welcome memory records which door this operator uses and a later session
can say "start with gg++" when a setting seems not to apply.

```text
AskUserQuestion
  Question: "Where will you run GRIP most? Only the Terminal door (gg++) applies your plan tier's model and effort; the other doors take your account's defaults."
  Header: "Door"
  Options:
    - "Terminal (gg++)"          → door = "terminal"  (recommended; the full GRIP launch)
    - "VS Code (gg++-vscode)"     → door = "vscode"    (the extension's Claude Process Wrapper; GRIP warms context, then the account's default model applies)
    - "Desktop app or claude.ai"  → door = "app"       (works; skips GRIP's launcher, account defaults)
    - "Codex or OpenCode"         → door = "harness"   (Step 5.8 wires it)
```

Persistence: the answer goes into the welcome memory seed,
`lib.activate_grip_welcome.welcome_entry(operator, first_skill, door=door)`,
never into `.onboarded.json` (schema parity). No launcher code reads it: it is a
remembered preference that recall surfaces, not a switch.

**Step 5.9: HAL gateway key** (needed only when a foreign harness is wired):

A wired harness authenticates to the HAL gateway with the `HAL_GATEWAY_KEY` env
var, sourced from the keychain entry `grip-hal-gateway-token`. With multiprovider
on (the default) this Bearer is a client PLACEHOLDER — the gateway injects the
real per-model provider key (`grip-openai` / `grip-deepseek` / …) internally,
never the Anthropic key (sovereign, non-Anthropic-by-default).

Only prompt if Step 5.8 wired a harness AND the key is absent:

```bash
security find-generic-password -s grip-hal-gateway-token -w >/dev/null 2>&1   # present?
```

If absent:

```text
AskUserQuestion
  Question: "Set the HAL gateway key (grip-hal-gateway-token)? With multiprovider on it's a client placeholder — the gateway supplies real provider keys itself."
  Header: "Gateway key"
  Options:
    - "Auto (placeholder)" → security add-generic-password -s grip-hal-gateway-token -w grip-local -U
    - "Enter my own"       → prompt (never echo), then security add-generic-password -s grip-hal-gateway-token -w <value> -U
    - "Skip"               → `grip-harness env` prints the export + set-up line for later
```

Verify with `grip-harness doctor` (probes `:4010` + the keychain entry). Skip →
no keychain write; the wired harness prints the one-line set-up on first use.

**Honest-failure path** (mirrors Phase 4): if `grip-harness doctor` still reports
the gateway or keychain entry RED after the set, NEVER report success — show the
doctor's reason line and offer *retry* (re-run the set) or *skip for now*
(`grip-harness env` prints the manual set-up). The wizard still completes; the
wired harness is simply flagged "gateway key unverified — run `grip-harness
doctor` after setup". Correctness is not opportunistic (verify-canonical).

**Step 5.95: HAL default model** (what HAL serves first, for Codex, OpenCode and
every harness that routes through HAL):

A wired or native harness inherits HAL's routing. HAL's shipped strategy is
`routing.default: fallback`, which serves the HEAD of `routing.fallback_chain`.
On that path HAL never reads `routing.default_model` (`lib/hal/router.py`
`_route_fallback`). So this step writes BOTH keys, and the choice made here is
the one Codex gets. That closes the "I chose Grok but Codex still used Groq"
surprise (GRIP#7791).

Ask only if Step 5.8 wired a harness, or the operator asks for it. Otherwise
set `hal_default_choice = "keep existing default_model"` and write nothing to HAL.

```text
AskUserQuestion
  Question: "Which model should HAL serve first? Codex, OpenCode and any harness wired through HAL inherit it. 'Keep existing' leaves HAL's own default in place."
  Header: "HAL default"
  Options:
    - "Keep existing (recommended)"     → hal_default_choice = "keep existing default_model"
    - "xAI Grok 4.6 (frontier, paid)"   → hal_default_choice = "xai/grok-4.6"
    - "Groq gpt-oss-120b (fast, free)"  → hal_default_choice = "groq/openai/gpt-oss-120b"
```

The automatic *Other* entry takes any `provider/model` address from HAL's
catalogue, for example `gemini/gemini-2.5-flash`, `deepseek/deepseek-v4-flash`
or `ollama/gemma4:26b`. Every address names a provider that HAL's
`config/providers.yaml` declares. An address with an unknown provider raises
on save. So the wizard never writes a route HAL cannot serve, the same posture
as Step 5.66's model allowlist. Read the live list with `/llm list`. The ids
above were current on 2026-10-12.

After the pick, a teaching moment:

```text
Picked: {hal_default_choice}
HAL now serves it first. Switch for one session with /llm set <provider/model>.
Re-run this step to change the persistent default.
```

Persistence: during Act III save phase, call
`lib.hal_default_model.set_hal_default(hal_default_choice)`. It writes
`state/hal-default-model.json` (the wizard's record: the choice,
`set_by="wizard"`, `set_at`). For a real choice it also edits two lines of
HAL's `config/providers.yaml`, resolved from `HAL_HOME`, else `~/.hal`. Those
lines are `routing.default_model` and the head of `routing.fallback_chain`. It
drops any later copy of the same address. It re-parses the result and checks
both lines before the atomic write, and every comment and other key survives
byte for byte.

`keep existing default_model` touches neither line. The call returns a small
dict. When it reports `providers_yaml_present: False`,
HAL's config is not on this machine. Say that in the summary instead of
reporting the choice as applied.

**Step 6: Confirm summary** (Gate 4: Destructive):

```text
Configuration:
─────────────
Name:               {name}
Persona:            {persona}
Signature:          {signature or "(none)"}
Mode:               {mode}
Extended Thinking:  {extended_thinking}
Budget Mode:        {budget_mode}
Plan Tier:          {grip_tier}  ({envelope blurb})
Model:              {model or "Sonnet 5 (default)"}
Output Style:       {output_tier}
Harness:            {harness or "Claude Code (default)"}
Gateway Key:        {gateway_key_status}
HAL default:        {hal_default_choice}

AskUserQuestion
  Question: "Save this configuration?"
  Header: "Confirm"
  Options:
    - "Save and start"  → write all files via lib helpers
    - "Go back"         → re-prompt from Step 2
```

### Phase 3 — Foundations (dependency check)

Before saving, GRIP verifies that its own tools actually work — the step the
legacy wizard skipped (issue #3896: a machine could finish "activated" with dead
MCP servers). Run a **dry preview** (verify only, no install):

```bash
python3 "$CLAUDE_DIR/lib/grip_activation_health.py" --check
```

This is a LIVE per-server handshake (`claude mcp list`), NOT a filesystem check —
the #3896 bug is precisely "files present, server won't boot". Show the operator
which servers are offline and the honest reason for each. If all are already
green, say so and fast-track to Phase 5.

### Phase 4 — Install (GRIP heals itself)

The delight moment — GRIP repairs its own broken tools live and signs the proof:

```bash
python3 "$CLAUDE_DIR/lib/grip_activation_health.py"
```

The engine runs `detect → heal (idempotent grip-deps-install.sh) → verify (live
handshake)` and streams the "GRIP is healing itself" narrative: each server flips
`OFFLINE → ✔ ONLINE (healed)` as its dependencies land. On all-green it stamps the
operator's signature seal, plays the completion chime, and writes a tamper-evident
signed receipt (HMAC-SHA256, the moat's signing key) to `state/activation/` for
day-two reconciliation. The SAME engine backs the standalone re-entry
`grip-doctor --fix`.

**Honest partial-failure** (PARAMOUNT): if a server stays red, GRIP never reports
success — it shows the reason and `grip-doctor --fix` to retry. Correctness is not
opportunistic; the wizard still completes so the operator is never trapped
(escape-hatch-safe). Deps/verify state lives in the signed receipt + the live
SessionStart probe — never a stale `.onboarded.json` flag (verify-canonical, so
the schema is intentionally unchanged).

### Phase 5 — Ready (First Run)

**Save phase** (atomic — one helper call, no partial state):

```python
from pathlib import Path
from lib.activate_grip_state import write_onboarded, write_identity
from lib.output_tier import set_tier
from lib.grip_tier import set_tier as set_plan_tier
from lib.grip_model import set_model
from lib.hal_default_model import set_hal_default

grip_home = Path.home() / ".claude"
write_identity(grip_home, name=name, persona=persona, signature=signature,
               mode=mode, extended_thinking=extended_thinking)
write_onboarded(grip_home, operator=signature.replace(">>", "") or "U",
                extra_steps=["welcome_memory_seeded"])
set_plan_tier(plan_tier_choice, set_by="wizard", apply_launch_defaults=True)  # Step 5.65 -- on pro also pins claude-sonnet-5-5 + medium effort
set_tier(output_tier_choice, set_by="wizard")  # #2868 Step 5.7 — persist tier choice
set_model(model_choice)  # #394 Step 5.66 — persist frontier model to settings.json
hal_result = set_hal_default(hal_default_choice)  # GRIP#7791 Step 5.95 -- HAL default_model + fallback-chain head
# settings.local.json generation: jq the reference template per legacy Step 6
```

**Persona skill auto-installation** (per legacy Step 7 mapping):

| Persona | Auto-installed skills |
|---------|------------------------|
| Developer  | pr-automation, feature-complete, e2e-test-generation, github-actions-setup, gitops |
| Designer   | figma-to-code, ui-complete, mobile-responsive-ui, image-optim, wcag-image-accessibility |
| Writer     | auto-update-documentation, markdown-expert, professional-pdf, medium-article-writer |
| Founder    | leading-organisation, legal-ops, backing-up-grip-infrastructure, gitops |

Display: `Installing {persona} toolkit: {N} skills...`

**Delight 2 — Welcome memory seed**:

Use `lib/activate_grip_welcome.py` to write the first memory entry, passing
the Step 5.85 answer as `door=` so the seed names the door too. The
`auto-memory-hook.py` Stop hook will absorb it on session end — but seeding
it now means day-two recall already knows who the operator is.

```python
from lib.activate_grip_welcome import welcome_entry, welcome_index_line

operator_letter = signature.replace(">>", "")  # "V>>" → "V"
memory_dir = Path.home() / ".claude/projects" / project_path / "memory"
memory_dir.mkdir(parents=True, exist_ok=True)
(memory_dir / "welcome_grip_first_session.md").write_text(
    welcome_entry(operator_letter, first_skill="/coffee")
)

memory_index = memory_dir / "MEMORY.md"
existing = memory_index.read_text() if memory_index.exists() else ""
memory_index.write_text(existing + "\n" + welcome_index_line(operator_letter) + "\n")
```

**Delight 3 — First-skill demo** (`/coffee`):

The wizard ends by actually invoking one skill so the operator sees a real
GRIP loop run before the wizard exits. `/coffee` is the canonical choice —
it's harmless, it shows DELIGHT v2 telemetry, it's an easter-egg, and it
returns in <30 seconds.

```text
"Before I let you go — here's GRIP doing GRIP. Running /coffee..."
[invoke Skill tool: /coffee]
"...that was a real GRIP skill, with measurement and telemetry. Type
 /save when you finish a session to persist memory."
```

If the skill invocation fails (any error), the wizard still completes.
Delight is opportunistic, not load-bearing.

**Closeout**:

```text
[RL++] Welcome, {SIGNATURE}. GRIP active.

Next:  /save when you finish your first real session
       /recall to load memory at the start of any session
```

## Helpers (testable surface)

| Helper | File | Purpose |
|--------|------|---------|
| `render_signature(letter)` | `lib/activate_grip_signature.py` | Swiss-Nihilism signature block |
| `render_flavour(letter)` | `lib/activate_grip_signature.py` | One-line post-reveal flavour text |
| `welcome_entry(op, first_skill)` | `lib/activate_grip_welcome.py` | Memory file content |
| `welcome_index_line(op)` | `lib/activate_grip_welcome.py` | MEMORY.md index line (<200 chars) |
| `build_onboarded_payload(op)` | `lib/activate_grip_state.py` | Pure payload builder (no I/O) |
| `write_onboarded(home, op)` | `lib/activate_grip_state.py` | Schema-compliant `.onboarded.json` writer |
| `build_identity_md(...)` | `lib/activate_grip_state.py` | identity.md content (no I/O) |
| `write_identity(home, ...)` | `lib/activate_grip_state.py` | identity.md writer |

37 Goodhart-proof tests in `tests/test_activate_grip_tui.py` cover every
helper, plus an integration test verifying full wizard exit state parity.

## Falsifiable invariants

| Invariant | Test | Falsifier |
|-----------|------|-----------|
| Signature renders for any A-Z letter | `test_signature_renders_for_each_letter` | mismatched letter in output |
| Signature rejects invalid input | `test_signature_rejects_invalid` | accepts lowercase / multi-char |
| No ANSI escapes in rendered output | `test_signature_no_ansi_escape_sequences` | `\x1b` present |
| Welcome entry includes operator | `test_welcome_entry_includes_operator` | absent or wrong letter |
| Welcome index line ≤ 200 chars | `test_welcome_index_line_under_200_chars` | violates Rule 20 hygiene |
| Schema version is 6.0.0 | `test_onboarded_schema_version_is_canonical` | drift from legacy |
| All 9 expected steps present | `test_onboarded_expected_steps_match_legacy` | upgrade detection breaks |
| Cross-artefact operator parity | `test_full_wizard_exit_state_parity` | identity ≠ onboarded ≠ welcome |

## Error handling

| Error | Response |
|-------|----------|
| Claude auth fails | Retry or "Continue limited" |
| Team password invalid | Retry without limit (PARAMOUNT) |
| File write fails | Display error, suggest manual fix, abort wizard |
| Wizard interrupted mid-Act-II | No `.onboarded.json` written → re-running starts fresh |
| Signature reveal helper raises | Catch + fall back to plain "Got it. {letter}>>" line |
| First-skill demo fails | Print "Couldn't run demo this time — try /coffee later" |

## Token budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~1100 |
| Per question (incl. pre-context) | ~250 |
| Signature reveal | ~80 |
| Harness × model + gateway key (Steps 5.8–5.9, opt-in) | ~250 (skip) – ~600 (wire) |
| First-skill demo (`/coffee`) | ~400 |
| Total wizard | ~2800-3600 |

Slightly heavier than legacy (~1100) — the extra ~1500 buys three deliberate
delights and a real GRIP loop, which is the entire point of the rewrite.

## Related skills

- `asking-users` — PARAMOUNT source of truth for AskUserQuestion
- `creating-wizards` — anti-patterns and bidirectional pattern
- `_archived/authenticating-with-claude` — legacy wizard, archived 2026-05-21 (V>> override of 90-day defer, GRIP #3139)
- `swiss-nihilism-design` — aesthetic reference for the rendered panels
- `auto-memory-hook` — absorbs the welcome memory entry at session end

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-05-04 | Initial — three-act structure, three delights, full schema parity with legacy v3.0.0 |
| 1.1.0 | 2026-07-13 | Act II Steps 5.8 (harness × model via `grip-harness`) + 5.9 (HAL gateway key) — surfaces the harness-agnostic capability (#3648/#3649) in onboarding. Persists to `state/harness-model-config.json` + keychain `grip-hal-gateway-token`, never `.onboarded.json` (v6.0.0 schema parity preserved). #23 part 2. |
| 1.2.0 | 2026-07-13 | Act II Step 5.66 — frontier-model opt-in (Sonnet 5 default / Opus 4.8 / Fable 5). Writes `settings.json` `.model` via `lib/grip_model.py::set_model` (validated allowlist — an unknown choice raises, never a broken id; every other settings key preserved). #394. |
| 1.3.0 | 2026-10-12 | Act II Step 5.95 -- HAL default model. Writes `routing.default_model` AND the head of `routing.fallback_chain` in HAL's `config/providers.yaml` (resolved from `HAL_HOME`, else `~/.hal`) via `lib/hal_default_model.py::set_hal_default`, re-parsed and checked before the atomic write. An unknown provider raises. Closes the "chose Grok, Codex used Groq" surprise. GRIP#7791. |
