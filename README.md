# GRIP

**General Reasoning & Intelligence Platform** — a reasoning engine with a provenance moat.

> **Witness it, don't trust it.** GRIP records *what* an AI decided, *what it believed when it decided*, and backs *every outward claim* with deterministic, independently-verifiable provenance.

GRIP turns an AI coding harness into a persistent, self-improving collaborator whose decisions are **tamper-evident and replayable** — and whose dangerous actions are stopped by **deterministic guards**, not hopeful values.

---

## In memoriam — Lukas Scheepers

GRIP is dedicated to **Lukas Scheepers**, father of its creator, who passed away on 6 August 2024.

This system was begun in the weeks after his passing and finished — deliberately — on 6 August 2026, two years to the day. Everything it stands for carries something he taught: that your word should be checkable, that work should be finished properly, and that what you build matters because of the people it serves.

*There's more to all of this.*

## Lineage — two years, one estate

| Era | Name | What it was |
|-----|------|-------------|
| 2024 | **Claude Optim** | The first repo: token-efficiency protocols for an AI coding harness. |
| 2024–2025 | **CIPS** | Claude Instance Preservation System — session continuity: the instance survives the context window. |
| 2025 | **GRIP** | The rename: from preserving an instance to a General Reasoning & Intelligence Platform — critical thinking, recursive self-improvement, deterministic guards. |
| 2025–2026 | **+ HAL** | The engine went provider-agnostic: any model, any provider, with cross-LLM councils. |
| 2026 | **+ HAPPI** | The family learned one protocol: envelope in, event-stream out — SDKs in Python, JavaScript and Go, with a conformance suite. |
| 2026 | **+ GRASP** | The proof layer went public: signable provenance anyone can verify, anchored to Bitcoin. |
| **6 Aug 2026** | **The estate** | The family is licensed under AGPL-3.0-only. GRIP and HAL were still private repositories then. |

---

## The Moat: Cryptographic Causation

GRIP's defensible core is **cryptographic causation** — tamper-evident, replayable, externally-anchorable records of AI reasoning. Three legs, one moat:

1. **What it decided — the IDR.** Each consequential decision emits a signed Intent Decision Record (`lib/idr_forest.py`): the what / why / how / when, hash-chained (HMAC-SHA256), Merkle-rootable, replayable. Spec: [`IDR.md`](IDR.md).
2. **What it believed when — the memory chain.** The reasoning/belief state at decision time is recorded into the context chain (`lib/context_chain.py`), so a record carries the mind that made the decision, not just the output. Spec: [`MEMORY_CONTEXT.md`](MEMORY_CONTEXT.md).
3. **Every claim — prove-it.** Outward, team-facing, or client-facing claims are backed by deterministic, verbatim-quote provenance (`/prove-it`, `lib/prove_it.py`). A fabricated citation cannot pass — it renders as NOT-FOUND.

**Falsifiable by construction.** A skeptic — a regulator, an auditor, opposing counsel — can independently *refute or confirm* a record: re-run the replay, re-verify the Merkle path, re-check each quote against its cited source, compare against an external anchor. The verifier is the **maths plus an external party — never the agent that wrote the record**. Real Bitcoin anchoring is available via [OpenTimestamps](https://opentimestamps.org) (a GRASP root has been confirmed on-chain).

```bash
# Hand a skeptic ONE command to re-verify a GRASP claims receipt —
# no GRIP runtime, no network, Python stdlib only.
bin/grasp-verify-receipt <receipt.json>
```

> **Scope honesty.** This substrate is **recorded-always on production decision paths** and is roughly *70% realised* across all surfaces — coverage is still being extended. GRIP does **not** claim every surface emits a record today; where a consequential path can skip recording, that is a gap being closed, not a feature.

---

## The Family (the "HAPPIverse")

GRIP does not stand alone. It is the reasoning + provenance engine of a family. GRIP and HAL are AGPL-3.0-only, with commercial licences available for deployments that cannot meet the AGPL's terms; HAPPI is Apache-2.0, and GRASP is AGPL-3.0-only with an Apache-2.0 verifier. GRIP and HAL are open source at [github.com/grip-platform](https://github.com/grip-platform).

| Piece | What it is | Licence |
|-------|-----------|---------|
| **GRIP** | The reasoning + provenance engine — critical-thinking, recursive self-improvement, and the deterministic safety layer (the *Guarded Response Interception Protocol* is one part, not the whole). | AGPL-3.0-only + commercial |
| **HAL** | Harness Abstraction Layer — an LLM-agnostic multi-provider substrate. Any model, any provider, cloud or local, with auto-failover and cross-LLM councils. Non-Anthropic by default. | AGPL-3.0-only + commercial |
| **HAPPI** | The open protocol the family speaks — one JSON envelope in, one NDJSON event stream out. A published spec with SDKs in Python, JavaScript and Go, plus a conformance suite. | Apache-2.0 |
| **GRASP** | The open proof layer — Governed Reasoning And Signable Provenance; the reference implementation of cryptographic causation, with a public verifier. | AGPL-3.0-only (Apache-2.0 verifier) |

- **GRASP public verifier:** <https://grasp.codetonight.co.za>
- **HAPPI** is described by the shared envelope contract below and mirrored publicly (see [HAPPI Protocol & IDR Audit Primitive](#happi-protocol--idr-audit-primitive)).

*"Deeds, not words."* GRIP and HAL are AGPL-3.0-only; HAPPI is Apache-2.0; GRASP is AGPL-3.0-only with an Apache-2.0 verifier. GRIP and HAL are open source at [github.com/grip-platform](https://github.com/grip-platform). Commercial licences exist for closed deployments.

---

## Safety Through Mechanisms

Values can be argued with. A skilled prompter reframes, invokes edge cases, and shifts context until helpfulness beats refusal. GRIP takes a different approach: **deterministic guards that run outside the model's weights**.

```
Model weights (probabilistic, persuadable)
        |
        v
GRIP safety guards (deterministic, mechanical)
  if condition -> DENY
  No arguments. No persuasion.
        |
        v
    [Action blocked]
```

**The agent cannot persuade GRIP.** Guards fire via a `PreToolUse` hook; the model cannot reframe its way past a DENY.

### Default Guards

| Guard | Trigger | Action |
|-------|---------|--------|
| `confidence-gate` | confidence below threshold | HALT, ask user |
| `context-gate` | context above ~85% | HALT, ask user |
| `dependency-guardian` | reads `node_modules` / `venv` | DENY |
| `destructive-git` | force push, hard reset | DENY |
| `secrets-detection` | commits `.env`, `.pem` | DENY |
| `production-safety` | deploys to prod | DENY |

### Properties

| Property | Values-Based | GRIP Mechanisms |
|----------|--------------|-----------------|
| Persuadable | Yes | **No** |
| Deterministic | No | **Yes** |
| Verifiable | No | **Yes** (test cases) |
| Falsifiable | No | **Yes** |
| User-controlled | No | **Yes** |

**-> [Full Safety Guards Documentation](docs/SAFETY-GATES.md)**
**-> [Why Values Don't Work](docs/WHY-NOT-VALUES.md)**

---

## Recursive Self-Improvement: The Agent Improves Its Own Constraints

The worker can propose new safety guards from observed risky behaviour. The recursive self-improvement loop detects patterns, learns from them, generates candidates, and tracks improvements — but **every proposal requires human approval, and the worker cannot bypass its own constraints.**

RSI loops (`/converge`, `/ultrado`, `/rsi`, `/evo`) are anchored to an **external criterion** — CI on mutation-surviving tests plus a cross-provider council verdict (the exogenous-anchor law, `rules/exogenous-anchor-law.md`). A loop cannot talk its way to a pass.

1. **Observe**: agent detects a risky pattern not yet covered
2. **Propose**: creates a guard proposal
3. **Review**: a human approves or rejects
4. **Enforce**: the guard becomes active and mechanical

See [RSI Architecture](docs/RSI-ARCHITECTURE.md) for the full pipeline.

---

## Installation

> **GRIP is licensed under [AGPL-3.0-only](LICENSE)**. GRIP and HAL are open source at [github.com/grip-platform](https://github.com/grip-platform). Run it, study it, change it, share it, under the same licence. For closed or embedded commercial deployments, a commercial licence is available: `licensing@codetonight.co.za`.

### Install

> **Important**: If you have already run the AI harness, `~/.claude` already exists. Use the **existing-users** command.

**New users (no existing `~/.claude`):**

```bash
git clone https://github.com/grip-platform/GRIP.git ~/.claude && cd ~/.claude && ./scripts/install.sh
```

**Existing harness users (recommended):**

```bash
git clone https://github.com/grip-platform/GRIP.git ~/Projects/grip && cd ~/Projects/grip && ./scripts/install.sh
```

This clones GRIP to a separate directory. The installer detects your existing `~/.claude` and runs an interactive merge, preserving your settings and session data.

**Windows (PowerShell):**

```powershell
git clone https://github.com/grip-platform/GRIP.git $env:USERPROFILE\Projects\grip
cd $env:USERPROFILE\Projects\grip
.\scripts\install.sh
```

### Install Modes

The installer auto-detects your scenario and offers the appropriate preset:

| Scenario | What Happens | Your Action |
|----------|--------------|-------------|
| No `~/.claude` exists | Fresh install | Choose: clone-home, symlink, or copy |
| `~/.claude` exists | Merge install | Backs up your config, merges GRIP in |
| GRIP already installed | Upgrade | Pulls latest and refreshes |

### Upgrade

```bash
cd ~/.claude && git pull && ./scripts/install.sh
```

First run launches `/activate-grip` to configure identity, team, and preferences.

---

## Launch Methods

GRIP provides multiple ways to start sessions, from zero-config to maximum reasoning depth.

### Quick Reference

| Command | Context Tokens | Use Case |
|---------|---------------|----------|
| `grip` | auto | Default entry point (auto-detects preset) |
| `gg` | 2,000 | Quick session with light context |
| `gg+` | 8,000 | Deep session for feature work |
| `gg++` | 12,000 | Maximum reasoning depth |
| `ggr` | full session | Resume exactly where you left off |
| `sesh` | varies | Hot reload preset (persistent session) |

### gg++ (Recommended for Deep Work)

Launches a fresh session with 12,000 tokens compressed from your previous session. The semantic compressor selects by **relevance**, not recency — a critical architectural decision from 50 messages ago scores higher than a routine file read from 3 messages ago.

```bash
gg++    # Shell alias for: grip fresh latest 12000
```

### grip CLI

The primary entry point with subcommands for every workflow:

```bash
grip                    # Auto-detect: onboard or fresh
grip fresh latest 2000  # Fresh session with 2k context
grip resume latest      # Continue last session
grip list               # List available sessions
grip --flow             # Flow mode (bypass permission prompts)
```

See **[Launch Methods Guide](docs/LAUNCH-METHODS.md)** for the complete reference.

---

## Capabilities

### Sovereignty — non-Anthropic by default

GRIP and HAL route to **non-Anthropic models** (DeepSeek, OpenAI, Gemini, Groq, xAI) **by default** — multi-provider routing is on by default, not opt-in. No single vendor, and no single country, is a single point of failure: if one provider rate-limits, raises prices, or restricts access, the work continues on another.

- **Proven live** (2026-06-27) on a Mac and the production server with **zero Anthropic egress** — the model self-identified as DeepSeek.
- **One config, every machine** — keys resolve from the macOS keychain (`grip-<provider>`) and fall back to environment variables (`GRIP_<PROVIDER>_API_KEY`) on Linux.
- The **Claude path still works** verbatim when you explicitly ask for it. A subscription weekly limit is therefore never a blocker.

See **[Sovereign AI guide](docs/SOVEREIGN-AI.md)**.

```bash
grip-llm-usage            # Which providers GRIP is ACTUALLY using — with signed provenance
grip-council "How should X work?" --roster open --synthesize   # Multi-provider council, non-Anthropic
```

### AGORA Multi-Provider Council

Grounded multi-LLM deliberation for CI code review and architecture decisions, powered by a non-Anthropic panel: DeepSeek, Qwen, xAI speakers with a Gemini judge. Every council seed is enriched with canonical grounding excerpts (Rule 21-bis), and councils terminate on quorum or turn-cap. All speakers and the judge are non-Anthropic — $0 weekly cost, exogenous anchor per `rules/exogenous-anchor-law.md`. Activated via `/agora` or composed inside `/ultrado` VERIFY waves. `bin/grip-sovereign-council` runs a fully-sealed, zero-Anthropic variant.

### HAPPI Protocol & IDR Audit Primitive

GRIP and HAL share an envelope contract called HAPPI (Harnessed-AI Polyglot Protocol Interface): one JSON envelope in, one NDJSON event stream out — streaming event types plus v1.1 terminators (`idr`, `context`). The current version (HAPPI/1.3) emits the IDR as a signed `idr` terminator carrying the content hash (sha256) of the envelope plus event stream, the model versions consulted, and an optional CID/block-anchor — a locally verifiable, deterministically replayable audit receipt. The decision anatomy (reasoning, confidence, dissent) rides the `context` memory-chain event, and v1.3 adds `cite.verify`, a deterministic citation-provenance floor.

See [happi.md](https://gist.github.com/operator/2483d5c218c21ecc931130bcee7dee83) (public mirror of the canonical specification).

### Hypothesis Engine

Every PR in an autonomous loop registers a falsifiable prediction via `lib/hypothesis_engine.py` — a metric, a deadline, and a falsification condition — auto-verified at the deadline. The 30–70% confirmation-rate target is Popper's discipline applied to engineering: above 70% means predictions are too soft to falsify; below 30% means too vague to confirm.

```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/hypothesis_engine.py register \
  --pr <NUM> --claim "<what the change does>" \
  --metric "<measurable signal>" --prediction "<expected outcome>" \
  --deadline "<YYYY-MM-DD>"
```

See [Hypothesis-Driven Development](skills/hypothesis-driven-development/SKILL.md) and [Scientific Method](rules/scientific-method.md).

### Session Continuity and Memory

GRIP maintains memory across sessions through layered infrastructure: hook triggers, an instance tree (session lineage), vector embeddings, and the KONO substrate (spatial memory with attestation-based permanence). Recalled memories gain fitness; unused memories decay. A synthesised HOLISTIC kernel with a completeness ledger guarantees no PARAMOUNT rule is silently dropped on session start.

```bash
grip resume latest    # Continue where you left off
grip list             # See available sessions
grip fresh            # Fresh start with inherited context
```

See **[Memory Architecture](docs/MEMORY-ARCHITECTURE.md)**.

### Autonomous Security Healing

`bin/grip-security-heal` counts open Dependabot alerts across a configured repo set and — only when there is work — dispatches the security-healer agent headlessly to open AGORA-gated remediation PRs. Every remediation still passes CI + the council gate before merge.

### Vision model routing (grip-vision)

When the active model is non-vision (e.g. `deepseek-v4-pro`), `bin/grip-vision` routes image inference to a HAL vision model instead of stalling or asking — primary `gemini-4-carbon`, fallback `gpt-5.5`. Routing is transparent to the caller: the image API surface is unchanged, only the backend model differs. The gateway defaults to the local HAL gateway (`GRIP_VISION_GATEWAY`, default `http://127.0.0.1:4010`), so image routing needs the HAL gateway running (`cd ~/.hal && python3 -m lib.hal.gateway --port 4010`); set `GRIP_VISION_GATEWAY` explicitly only to point at a non-default gateway.

### Live Meeting Intelligence (grip-meet)

GRIP listens to a meeting through your audio device, transcribes it locally (whisper), understands each chunk via the local HAL server, acts on simple requests live and wakes a full session for complex ones, and writes the minutes at the end. Shipped as a one-command product: `grip-meet-setup`, `grip-meet doctor`, `grip-meet start`, `grip-meet-dash` for a live browser view.

See [grip-meet Guide](docs/grip-meet.md).

### Session Mesh & Remote Access

Pairing with GRIP is `/pair` (or `/mode pair`): you drive and GRIP navigates, the roles swap on a word, and checkpoints run through the `/hitl` family (`/asking-users`, `/creating-wizards`, `/hitl-decision-matrix`). When the pair spans two machines or two sessions, [grip-session-mesh](https://github.com/CodeTonight-SA/grip-session-mesh) (a separate install) is its transport:

- `/mesh connect [name]` — join the session mesh (local + cross-machine over Tailscale)
- `/mesh list` — list active sessions across the Tailnet
- `/mesh send <name> "message"` / `/mesh broadcast "message"` — message peers

Bring the cross-Tailnet mesh up on any machine with `bash scripts/mesh-tailnet-setup.sh` (idempotent). Access from a phone via Blink Shell + Mosh + Tailscale — see **[Mobile & Remote Setup](docs/MOBILE-GRIP-SETUP.md)**.

### Generated Inventory

GRIP ships a large, self-generating capability catalogue (counts are generated, not hand-maintained):

| Resource | Location |
|----------|----------|
| Skills | `skills/*/SKILL.md` |
| Agents | `agents/*.md` |
| Commands | `commands/*.md` |
| Rules (PARAMOUNT doctrines) | `rules/*.md` |
| Hooks (enforcement) | `hooks/*.py` |
| Operating presets | `modes/definitions/*.yaml` |

Activate presets with `/mode <name>`, stack up to 5 simultaneously. See the **[Modes Guide](docs/MODES-GUIDE.md)**, [Skills Reference](skills/INDEX.md), [Agents Reference](docs/AGENTS.md), and [Commands Reference](docs/COMMANDS.md).

---

## Troubleshooting

### "fatal: destination path '~/.claude' already exists and is not an empty directory"

The harness creates `~/.claude` on first run, so `git clone` cannot clone into it. Clone GRIP to a separate directory and let the installer merge:

```bash
git clone https://github.com/grip-platform/GRIP.git ~/Projects/grip && cd ~/Projects/grip && ./scripts/install.sh
```

### "grip: command not found"

Reload your shell after installation:

```bash
source ~/.zshrc   # or ~/.bashrc
```

Or add manually:

```bash
echo 'export PATH="$HOME/.claude/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

### "claude: command not found"

Install the AI harness (native method recommended):

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Then run `claude doctor` to verify.

### Hooks not running

Verify `settings.json` has correct absolute paths:

```bash
cat ~/.claude/settings.json | jq '.hooks'
```

Paths should use absolute `$HOME/.claude/...` format, not hardcoded usernames.

See **[Troubleshooting](docs/TROUBLESHOOTING.md)** for the full list.

---

## What Makes GRIP Different

| Other Tools | GRIP |
|-------------|------|
| More values, more guidelines | Mechanical enforcement |
| Hope the model refuses | Guarantee it can't act |
| "Trust the output" | Witness it — replay + verify the record |
| One vendor's model | Any model, any provider (HAL) |
| Suggestions | Constraints |
| Philosophy | Code |

**The AI worker is not inherently trustworthy. That's the starting assumption.** Trust is built through mechanisms that constrain and records that can be independently verified — not values that suggest.

---

## Documentation

### Getting Started

- **[Quick Start](docs/QUICK-START.md)** — 10-minute introduction
- **[Launch Methods](docs/LAUNCH-METHODS.md)** — all ways to start GRIP
- **[Troubleshooting](docs/TROUBLESHOOTING.md)** — common issues and fixes

### Core Concepts

- **[Cryptographic Causation](rules/cryptographic-causation.md)** — the moat (IDR + memory-chain + prove-it)
- **[IDR spec](IDR.md)** · **[Memory-Context spec](MEMORY_CONTEXT.md)** — the two record specs, siblings to HAPPI
- **[Safety Gates](docs/SAFETY-GATES.md)** — mechanical enforcement
- **[Hooks Architecture](docs/HOOKS-ARCHITECTURE.md)** — how enforcement works
- **[Exogenous-Anchor Law](rules/exogenous-anchor-law.md)** — why RSI loops need an external criterion
- **[Broly Architecture](docs/BROLY-ARCHITECTURE.md)** — recursive meta-worker
- **[Improvement Cycle Architecture](docs/RSI-ARCHITECTURE.md)** — recursive self-improvement pipeline
- **[Memory Architecture](docs/MEMORY-ARCHITECTURE.md)** — session continuity, instance tree, embeddings, KONO
- **[Why Not Values](docs/WHY-NOT-VALUES.md)** — why philosophy fails

### Configuration

- **[Team Setup](docs/TEAM_SETUP.md)** — multi-user and team isolation
- **[Modes Guide](docs/MODES-GUIDE.md)** — operating presets reference
- **[MCP Setup](docs/MCP_SETUP.md)** — server integrations
- **[Mobile & Remote Setup](docs/MOBILE-GRIP-SETUP.md)** — access GRIP from phone via Blink, Tailscale

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines.

---

## License

GRIP is licensed under the **GNU Affero General Public License v3.0 only** -- see [LICENSE](LICENSE). GRIP and HAL are open source at [github.com/grip-platform](https://github.com/grip-platform).

Copyright (C) 2024–2026 Lourens Cornelius Scheepers / CodeTonight (Pty) Ltd. Patent pending. GRIP, HAL, and HAPPI.md are the creation of V>>.

The AGPL's defining term, plainly: if you run a modified GRIP as a network service, you must offer that service's users the source of your modified version. If your deployment cannot meet that term, a **commercial licence** is available: `licensing@codetonight.co.za`.

Across the family: **HAL** is AGPL-3.0-only as well. The **HAPPI** spec is Apache-2.0, and so are its client libraries `hal-py`, `hal-js` and `hal-go` and its `hal-conformance` test suite. Despite the `hal-` prefix, those four are separate repositories with their own licence, not part of HAL; they moved from AGPL-3.0-only to Apache-2.0 on 17 November 2026. **GRASP** is AGPL-3.0-only with an Apache-2.0 verifier.

---

## Origin

Created by [The GRIP Authors](https://github.com/CodeTonight-SA).

*"The AI worker cannot persuade GRIP — and cannot lie to you about what it did."*

## Teammate ratification (2026-06-09)

Allowlisted teammates (V>> and A>>) can merge a CI-green PR by sending one WhatsApp message — `GRIP: approve PR 36 grip` — in the team group or by DM. Safety is fail-closed: ratifier allowlist, 3-repo allowlist, CI must be fully green, merge-authority files are V>>-only (bootstrap protection), and every attempt is ledger-logged. See `lib/team_ratification.py` and the RATIFY phase in `skills/delegating-team-work/SKILL.md`.
