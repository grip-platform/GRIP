# ADR 0010 — Twin-Pane GUI Delight++ (Predictive Skill Surface)

| Field | Value |
|-------|-------|
| Status | Ratified (design); implementation deferred to follow-up sprint |
| Date | 2026-05-24 |
| Deciders | V>> (operator); invented mid-plan during overnight RSI sprint |
| Codename | "twin-pane" (working) |
| Carve-out from | `plans/tender-popping-catmull.md` Wave 6-I (V>>-added mid-plan) |
| Hypotheses | H-TWIN-PANE-1..5 (deadline 2026-08-01) |
| Follow-up plan | `plans/twin-pane-implementation.md` (this PR ships it) |
| Sibling ADRs | [0008](./0008-ambitious-workflows-federation.md), [0009](./0009-idr-forest-spec.md) |

## V>>'s framing (verbatim, load-bearing)

> "Best of both worlds — a minimalist but elegant, sleek, left-hand side
> text editor with novel delight++ ability to enter prompts — free text
> entry, with novel assistant reveal/surface suggestions via 'Did you
> mean to run /random-skill-name?'… As the commands are predicted,
> auto-load the skills in advance. Unload the skill from context if the
> prompt is idle (not actioned) for a set duration (timeout) — this
> makes it faster, and less taxing on the running agent main thread on
> right hand side."
>
> "Key-logging is ephemeral, temporary, never persisted to disk, but
> added to an LRU in-memory cache."
>
> "Make sure no key-tracking is interceptable via prompt-injection."

These three constraints are **load-bearing safety properties** — every
implementation choice below honours them or is rejected.

## Strategic value

Claude Code's terminal UX is power-user-only. GRIP carries 200+ skills
the operator cannot recall by name. A predictive command surface turns
the latent skill catalogue into a discoverable affordance WITHOUT
breaking the keyboard-driven minimalist aesthetic. The skill pre-load
mechanism flips the canonical relationship — instead of every skill
loading at session start (context bloat), only predicted-imminent
skills are warm.

This is the "novel delight++" V>> named: it is not a feature graft on
the terminal; it is a structural reshaping of the human-CC interaction
pattern.

## Architecture (locked)

```
┌─────────────────────────┬──────────────────────────┐
│ LEFT PANE — Prompt-Pad  │ RIGHT PANE — GRIP Session│
│                         │                          │
│ ┌── Prompt Card ──────┐ │ [live session stream]    │
│ │ <free-text editor>  │ │                          │
│ │ @file-ref overlay   │ │                          │
│ │ /cmd auto-complete  │ │                          │
│ │ ┌── Tip surface ──┐ │ │                          │
│ │ │ Did you mean    │ │ │                          │
│ │ │ /holistic-mem?  │ │ │                          │
│ │ │ [Insert]        │ │ │                          │
│ │ └─────────────────┘ │ │                          │
│ │ [Send ▾] [Close]    │ │                          │
│ └─────────────────────┘ │                          │
│ ┌── Trashbin (recov) ─┐ │                          │
│ │ closed cards        │ │                          │
│ └─────────────────────┘ │                          │
└─────────────────────────┴──────────────────────────┘
       │                          ▲
       │ IPC                      │
       ▼                          │
   ┌───────────────────────────────────┐
   │ Predictive engine (in-process,    │
   │ ephemeral RAM only — never disk)  │
   │  - BM25 over skill index          │
   │  - LRU cache: (cmd, conf, count)  │
   │  - Skill pre-load / idle-unload   │
   └───────────────────────────────────┘
```

## Tech-stack decision

**Recommended: Web SPA on localhost served by GRIP**, IPC fabric via
the existing `mcp-servers/grip-channel` Web platform (already shipped,
canonical at `https://your-gateway.example.com/mcp`).

Candidates considered:

| Stack | Verdict | Reasoning |
|---|---|---|
| **Web SPA on localhost + grip-channel Web IPC** | **Chosen** | Reuses shipped grip-channel infra; lowest new-surface risk; cross-platform; aligns with `rules/syscall-doctrine.md` (everything is a shim) |
| Tauri (Rust + webview) | Rejected | Adds Rust toolchain dep; new build surface |
| Electron | Rejected | ~200MB idle footprint contradicts the "ultra-minimalist" mandate |
| Native macOS (Swift) | Rejected | macOS-only; foreign codebase for GRIP |

## Component decomposition (locked)

The implementation is a multi-PR epic; this ADR locks the boundaries.

| Sub | Component | Scope |
|---|---|---|
| Sub-1 | This ADR + spec + follow-up plan file | **THIS PR** |
| Sub-2 | Prompt-pad core (left pane) | Card-based text editor, `@file` overlay, `/cmd` auto-complete (substring fuzzy match against skill index). NO predictive engine yet. |
| Sub-3 | Predictive tip engine | BM25 over `~/.claude/skills/*/SKILL.md` `triggers:` + `description:`. LRU in-memory cache `{keystroke_hash → (cmd, confidence, count)}`. **Pure classifier — output is suggestion only, never executes.** Fold-search / fold-compress / fixed-point attractor variants explored vs BM25. |
| Sub-4 | Skill pre-load / unload | Confidence ≥0.7 → pre-load via existing skill loader; idle timeout 30s → unload. Hooks into GRIP's skill cache layer. |
| Sub-5 | Right pane — GRIP session display + routing | Live session stream rendered card-by-card. **Send** routes to running CC session via grip-channel Web platform inbound. **Schedule** enqueues after current task completes. **Cancel** if pre-routed. |
| Sub-6 | Trashbin recovery | Closed cards land in `state/twin-pane/trashbin/` (ephemeral, time-bounded retention). Restore UI in left pane. |
| Sub-7 | Safety hardening | Prompt-injection audit; ephemeral-memory leak prevention; performance budget (main thread load < 5% CPU overhead). |

Sub-2 through Sub-7 live in the **follow-up sprint plan**
(`plans/twin-pane-implementation.md` — shipped in this PR).

## Safety properties (V>>-emphasised, load-bearing — DO NOT relax)

These are non-negotiable. Any implementation choice that breaks one of
these is rejected even if it improves performance or convenience.

### 1. Ephemeral RAM only, NEVER persisted

Key-logging lives exclusively in process memory. LRU cache evicts on
size bound (default 1024 entries). Process exit clears everything. A
process-kill mid-session leaves **zero residue** in `~/.claude/state/`,
`/tmp`, the keychain, or any other path.

**Test contract**: `lsof | grep <pid>` after process-kill shows no
twin-pane-owned file handles. `find ~/.claude/state -newer <session-start>`
shows zero new keystroke-derived files.

### 2. Prompt-injection safety

The predictor reads keystrokes as **DATA**, not as instructions. The
output is a structured `(cmd, confidence, count)` tuple. There is **NO
code path** where keystroke content can be interpreted as an executable
instruction by any component (predictor, pre-loader, UI render).

Mechanically: the predictor's only output sink is the UI tip card. The
UI tip card's only action is **"insert literal text into the
prompt-pad editor"** — never auto-execute. The operator must press
**Send** explicitly to dispatch.

**Test contract**: a keystroke stream containing `rm -rf ~/` or
`/exfiltrate-secrets` or a fabricated prompt-injection payload causes
the predictor to surface a literal `(cmd, confidence, count)` tuple
but executes nothing. Adversarial input over 30-day dogfooding window
produces zero unintended executions.

### 3. LRU bounded — DoS-resistant

Cache cap (default 1024 entries) prevents memory-growth attacks via
adversarial input crafted to fill the cache. Eviction is age-and-LRU
based; an attacker cannot pin entries.

### 4. Skill pre-load is reversible + idle-bounded

If a malicious prediction tries to keep a heavy skill loaded, the idle
timeout (30s) unloads it regardless. There is no "stickiness" knob.

### 5. Scope-locked keystroke capture

Keystroke capture is strictly scoped to the prompt-pad editor widget.
Nothing outside that focus is observed. No global hotkey hooks. No
screen-scraping. No accessibility-API reads of other apps' windows.

## Hypotheses (registered post-PR-open, deadline 2026-08-01)

| H-id | Claim | Falsified if | Anchor |
|---|---|---|---|
| **H-TWIN-PANE-1** | The predictive tip engine surfaces the correct skill (top-1) for ≥70% of typed prompts in a held-out corpus of 200 historical V>>-issued prompts | top-1 accuracy < 50% over the corpus | retrieval quality |
| **H-TWIN-PANE-2** | Skill pre-loading reduces median prompt-to-first-token latency by ≥30% vs cold-load baseline | latency improvement < 10% | performance |
| **H-TWIN-PANE-3** | Idle-unload prevents context-budget creep — average skills loaded over a 1h session ≤ 8 (vs ≥40 cold-load all) | average loaded > 20 | context discipline |
| **H-TWIN-PANE-4** | Zero prompt-injection incidents over the first 30 days of dogfooding | any successful injection within 30 days | safety property #2 |
| **H-TWIN-PANE-5** | Zero disk persistence of keystroke data — `lsof` + `find` + tmpfs audit shows no file traces | any keystroke fragment found on disk | safety property #1 |

## Falsification (the ADR + epic itself)

The twin-pane invention is wrong if:

1. The predictive engine's top-1 accuracy ≤ random-baseline
   (1 / skill-count ≈ 0.5%) over a real corpus — BM25 / fold-attractor
   approach doesn't generalise. Pivot to embedding-similarity or
   transformer-based intent classifier.
2. Operators (V>> primarily) report the GUI breaks the keyboard-driven
   flow they rely on Claude Code for — the GUI failed its
   "ultra-minimalist" mandate. Pivot to a terminal-overlay design
   instead of full split-pane.
3. Skill pre-load creates more context bloat than it saves (because
   the predictor mis-fires too often) — tighten idle-unload threshold
   OR drop the pre-load mechanism and keep only the tip surface.
4. Prompt-injection demonstrated within 30 days — safety architecture
   has a hole; remediate before further dogfooding (gate further
   development on patch + hypothesis re-registration).

## R0 dissent (recorded, not suppressed)

The strongest case against shipping this:

- **N=1 design check**: V>> invented this mid-plan; no independent
  council ratified the specific component decomposition. Mitigation:
  this ADR registers H-TWIN-PANE-1..5 as falsification anchors, and
  the follow-up plan keeps every component in its own gated sub-PR.
- **Safety property #2 is the load-bearing one**: prompt-injection
  resistance lives in the architectural choice that the predictor's
  ONLY output sink is the UI tip card with "insert literal text"
  semantics. If a future refactor adds an auto-execute path (even one
  "convenient" shortcut), the safety property dies silently. The
  Sub-7 safety-hardening sub-PR includes a mechanical assertion test
  that no code path between predictor → executor exists.

## Critical files / prior art to reuse (per C5 of ADR 0008)

| File | Why |
|---|---|
| `mcp-servers/grip-channel/server/*` | Web platform IPC fabric (right-pane session ⇄ left-pane prompts) |
| `lib/holistic_synthesiser.py` | Skill catalogue indexing reference (R1 completeness ledger pattern) |
| `lib/kono_preload.py` | Existing skill warm-cache pattern to extend |
| `skills/*/SKILL.md` `triggers:` + `description:` frontmatter | BM25 corpus source |
| `rules/no-guardrail-evasion.md` | Prompt-injection safety doctrine to inherit |
| `rules/syscall-doctrine.md` | Any IPC mechanism must be a clean shim, not scattered glue |
| `drafts/fold-the-kernel-substrate-sigma0-*.tar.zst` | Fold-search / fold-compress prior art V>> referenced |

## Decision

**Ratify this design.** The follow-up sprint plan (`plans/twin-pane-implementation.md`)
governs implementation across Sub-2 through Sub-7. Implementation begins only after
V>> reviews and approves both this ADR AND the follow-up plan (V>>-gated launch,
matching the discipline V>> lifted for #2799 IDR-PROVE during this same sprint).

## Out of scope (this PR)

- Actual implementation of Sub-2 through Sub-7
- Tech-stack alternatives (decision is locked — Web SPA on localhost +
  grip-channel)
- Live dogfooding telemetry harness (Sub-7 ships it)
- Operator onboarding doc (post-Sub-7)

## References

- `plans/tender-popping-catmull.md` — overnight sprint plan (W6-I atom)
- `plans/twin-pane-implementation.md` — follow-up sprint plan (this PR ships it)
- V>>'s mid-plan invention (2026-05-24, this overnight sprint)
- `rules/syscall-doctrine.md` — IPC shim discipline (Web platform as fabric)
- `rules/no-guardrail-evasion.md` — prompt-injection safety doctrine
- `rules/efficiency-rules.md` Rule 13 — Worktree-First Isolation (mandatory for multi-PR epic)
- `rules/exogenous-anchor-law.md` — V>> review is anchor #2 (human merge) for this design
- `rules/knowledge-maturation-functor.md` — N=1 design; this ADR is the canonical encoding

Primary Author: V>>
