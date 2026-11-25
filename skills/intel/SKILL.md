---
name: intel
description: Cross-platform intel orchestrator — routes /intel <platform> to the Discord or LinkedIn IntelAdapter with a Phase-1 mode (strat-ops / founder / sneak) shaping the IntelScope. Emits a PRECOG-IDR per scan invocation for replay determinism. Outbound to the operator goes through the relay topology (Mac WA, VPS Slack+Discord) — adapters never speak directly to recipients.
status: Active
effort: medium
version: 1.0.0
created: 2026-05-26
generation: 866
triggers:
  - /intel
  - "intel discord"
  - "intel linkedin"
  - "scan intel"
priority: PARAMOUNT
integrates:
  - intel-linkedin
  - linkedin-feed-scout
  - guild-knowledge-scan
see_also:
  - sneak
  - strat-ops
  - founder
  - canonical-output-style
  - syscall-doctrine
  - llm-augmentation-pattern
  - untrusted-content-capability-lock
  - exogenous-anchor-law
---

# /intel — Cross-Platform Intel Orchestrator

> *"One slash. One adapter. One mode. One IDR per scan."*

The operator surface for the intel primitive shipped across PRs 3242 / 3243 / 3246 / 3247. This skill is the last Phase-1 dispatch: it routes `/intel <platform>` to the right `IntelAdapter`, shapes the `IntelScope` per the operator's mode, and emits a `PrecogIDR` per scan invocation so the run is byte-for-byte replayable (R0 + replay determinism + V>>'s PRECOG mandate).

## §0 What it does (plain English)

You type `/intel discord --mode founder`. The skill:

1. Picks the **DiscordAdapter** (`lib/intel/discord.py`).
2. Builds an **IntelScope** with `mode="founder"` and the `/founder` parameter overlay (rate-limit 30/min, confidence floor 0.5, tool allowlist `Read,Grep,Glob`, consent gate on).
3. Emits a **PrecogIDR** capturing the dispatch decision (effort tier, fingerprint, predecessor chain) BEFORE delegating — so the run is replayable.
4. Calls the adapter's 5-function contract: `discover → scan → enrich → relay → learn`.
5. Outbound observations land on V>>'s configured relay channel (Discord via VPS, WhatsApp via Mac, Slack via VPS) per `rules/operational-context.md` grip-channel topology.
6. Returns a TLDR (≤ 3 lines per `rules/canonical-output-style.md`) with IDR id + observation count + relay receipt count.

Three Phase-1 modes ship today: `/strat-ops`, `/founder`, `/sneak`. The other five modes named in `drafts/intel-primitive-2026-05-25/PLAN.md` §5 (`/business-mode`, `/research-mode`, `/140-85`, `/stealth`, `/absorb-knowledge`) are explicitly deferred to Phase 2 per `AMENDMENTS.md` Q5.

## §1 Usage (slash forms)

```bash
/intel <platform>                       # Default mode, default scope
/intel <platform> --mode <mode>         # Phase-1 mode overlay
/intel <platform> --seed <id>           # Pin a specific seed (channel ID / JID / URL)
/intel <platform> --top N               # Cap observations at N (default: scope.max_observations=50)
/intel <platform> --window Hh           # Time window in hours (default 24)
/intel <platform> --dry                 # discover() + scan() + enrich() but skip relay()
/intel <platform> --replay <idr-id>     # Re-run from a prior PrecogIDR (same scope, same byte tuple)
```

Examples:

```bash
/intel discord                          # Default scope, no mode overlay
/intel discord --mode strat-ops         # Competitive intel focus
/intel linkedin --mode founder          # Peer + opportunity signal
/intel discord --mode sneak --dry       # Drafts-only research, no relay
/intel linkedin --replay precog-1779757926-f601d7ed
```

Platform → adapter dispatch table:

| `<platform>` | Adapter module | Underlying capability |
|--------------|----------------|----------------------|
| `discord`    | `lib/intel/discord.py::DiscordAdapter`     | `lib/guild-scan.sh` + `GuildCrawler` graph JSON |
| `linkedin`   | `lib/intel/linkedin.py::LinkedInAdapter`   | `linkedin-feed-scout` (Brave-CDP) + L1/L2 scoring |

Direct shell bypass (for scripting, no slash command):

```bash
PYTHONPATH=$HOME/.claude python3 -c "
from lib.intel.discord import DiscordAdapter
from lib.intel.base import IntelScope
adapter = DiscordAdapter()
scope = IntelScope(platform='discord', mode='founder', rate_limit_per_min=30)
for seed in adapter.discover(scope):
    for obs in adapter.scan(seed):
        enriched = adapter.enrich(obs)
        adapter.relay(enriched)
"
```

## §2 Adapter dispatch

```text
operator → /intel <platform> [--mode M]
              │
              ├─► PRECOG-IDR emit (effort tier, fingerprint, predecessor)
              │
              ├─► IntelScope shape ← mode overlay (§3)
              │
              ├─► dispatch by platform ─┬─► DiscordAdapter (PR2)
              │                         └─► LinkedInAdapter (PR3)
              │
              └─► 5-function chain: discover → scan → enrich → relay → learn
```

The dispatch is a flat dict lookup (KISS — no plugin registry). Adding a Phase-2 adapter is one line:

```python
ADAPTERS = {
    "discord":  DiscordAdapter,
    "linkedin": LinkedInAdapter,
}
```

Unknown platform → loud HITL, exit non-zero. Never silent fail.

## §3 Phase-1 modes (3 of 8)

Phase 1 ships exactly three modes. Each mode is a deterministic `IntelScope` mutation — the mode name is captured in `scope.mode` for downstream debugging / IDR replay, and the listed fields are overlaid on the default scope.

### `/strat-ops` — Competitive intel focus

| `IntelScope` field | Value | Why |
|--------------------|-------|-----|
| `rate_limit_per_min` | **10** | Slow + careful; high-stakes targets, easy to trip platform rate limits |
| `confidence_floor` | **0.7** | High bar — only surface what L1+L2 agree is genuinely competitive signal |
| `capability_lock` | `("Read", "Grep")` | No Glob — narrow file surface for any spawned helper |
| `consent_gate` | `True` | Strict ToS / opt-in respect |
| `mode` | `"strat-ops"` | IDR-replayable label |

Use when: competitor product launches, market shifts, regulatory moves, customer-discovery signal on a named competitor.

### `/founder` — Peer + opportunity signal

| `IntelScope` field | Value | Why |
|--------------------|-------|-----|
| `rate_limit_per_min` | **30** | Default — broader peer surface, more observations per scan |
| `confidence_floor` | **0.5** | Default — surface weaker signal too; operator triages |
| `capability_lock` | `("Read", "Grep", "Glob")` | Default — peer signal often spread across many files |
| `consent_gate` | `True` | Default |
| `mode` | `"founder"` | IDR-replayable label |

Use when: peer founder posts, opportunity radar (jobs / RFPs / partnerships), ecosystem health pulse.

### `/sneak` — Drafts-only research

| `IntelScope` field | Value | Why |
|--------------------|-------|-----|
| `rate_limit_per_min` | **15** | Conservative — sneak runs are AFK / overnight |
| `confidence_floor` | **0.6** | Higher than founder, lower than strat-ops |
| `capability_lock` | `("Read",)` | Tightest — reads only, no Grep / Glob in spawned helpers |
| `consent_gate` | `True` | Strict |
| `power` | `("sneak",)` | Triggers `relay()` short-circuit → drafts-only, no outbound |
| `mode` | `"sneak"` | IDR-replayable label |

Use when: AFK research, overnight runs, anything where outbound delivery is forbidden by `/sneak` posture (drafts land in `~/.claude/drafts/<topic>/` per the sneak skill contract; no Slack / Discord / WhatsApp delivery).

### Mode parameter resolution (pseudocode)

```python
PHASE1_MODES = {
    "strat-ops": dict(rate_limit_per_min=10, confidence_floor=0.7,
                       capability_lock=("Read", "Grep"), consent_gate=True),
    "founder":   dict(rate_limit_per_min=30, confidence_floor=0.5,
                       capability_lock=("Read", "Grep", "Glob"), consent_gate=True),
    "sneak":     dict(rate_limit_per_min=15, confidence_floor=0.6,
                       capability_lock=("Read",), consent_gate=True,
                       power=("sneak",)),
}

def build_scope(platform: str, mode: str, **overrides) -> IntelScope:
    base = PHASE1_MODES.get(mode, {})
    return IntelScope(platform=platform, mode=mode, **{**base, **overrides})
```

Phase 2 modes are NOT validated here — passing `--mode business-mode` today raises a clear "Phase 2 — not yet shipped" error (loud HITL, exit non-zero).

## §4 Relay integration

Outbound deliveries follow the canonical grip-channel topology (PARAMOUNT — `rules/operational-context.md` W10 cutover):

| Platform | Where the daemon runs | Why |
|----------|----------------------|-----|
| **Slack** (CodeTonight `#nexus-chats` C0EXAMPLE01, `#grip-updates` C0EXAMPLE01) | VPS pm2 `grip-channel` :3101 | Laptop-independent relay |
| **Discord** (GRIP guild) | VPS pm2 `grip-channel` :3101 | Laptop-independent relay |
| **WhatsApp** | Mac pm2 `grip-channel` :3101 | Hetzner IP blocked by WhatsApp — Mac only |

Adapters never speak directly to recipients. They call `RelayReceipt = adapter.relay(enriched)`, which goes through the existing `lib/slack_transport.py` / equivalents — same syscall-doctrine surface as the rest of GRIP.

`sneak` power short-circuits relay: `RelayReceipt(delivered=False, reason="sneak-mode")`. Drafts land in `~/.claude/drafts/intel-<platform>-<ts>/` per the `/sneak` skill contract.

Confidence floor applies at relay time: an `EnrichedObservation` with `confidence < scope.confidence_floor` is suppressed (still IDR'd — R2 invariant — but `delivered=False, reason="below-confidence-floor"`).

## §5 PRECOG-IDR usage (V>>'s PARAMOUNT mandate)

Every `/intel` invocation emits a **dispatch IDR** BEFORE delegating to the adapter. The IDR captures:

- `decision`: effort tier, chosen adapter, mode, scope shape (rate-limit, confidence floor, capability lock)
- `fingerprint`: sha256 of `(platform, mode, seed_hints, time_window, scope)` — same input → same fingerprint → IDR replay
- `predecessor_idr`: chains to the prior intel-dispatch or precog-IDR on this machine
- `inputs`: redacted invocation args

```python
from lib.precog.idr import build_idr, append_idr

idr = build_idr(
    prompt=f"/intel {platform} --mode {mode}",
    fingerprint=fingerprint,
    decision={
        "kind": "intel-dispatch",
        "adapter": platform,
        "mode": mode,
        "rate_limit_per_min": scope.rate_limit_per_min,
        "confidence_floor": scope.confidence_floor,
        "capability_lock": list(scope.capability_lock),
        "effort": effort_tier,
    },
    predecessor_idr=last_idr_id,
    depth=depth,
    kind="precog-intel-dispatch",
    inputs={"platform": platform, "mode": mode, "seed_hints": list(scope.seed_hints)},
)
append_idr(idr)
```

The chain lives at `state/precog/idr.jsonl` (default) and is replayable via `--replay <idr-id>` (re-runs the exact same scope deterministically — same observations, same enriched verdicts modulo platform freshness).

Per-observation IDRs are emitted by the adapters themselves at the `enrich()` boundary (R2: L1-fail outcomes are IDR'd, not silently dropped). This skill's IDR is the **dispatch-level** parent — the per-observation IDRs reference it as predecessor.

## §6 Anti-patterns

- **NEVER auto-relay below the confidence floor.** Adapters MUST honour `scope.confidence_floor` at relay time. A scope says `0.7` because the operator wanted high-bar surfacing — emitting `0.5` confidence observations breaks the operator's intent.
- **NEVER bypass `lib.intel.spawn.spawn_with_lock`** for content-driven subprocesses. Capability-lock the spawn surface per `rules/untrusted-content-capability-lock.md`.
- **NEVER drop a Phase-2 mode silently.** Unknown mode → loud HITL ("Phase 2 — not yet shipped"), exit non-zero. Silent default-to-founder is a Goodhart failure (operator thinks `/intel x --mode business-mode` ran with strict ToS gating; it didn't).
- **NEVER speak directly to recipients.** Always go through the relay (`RelayReceipt`-emitting code paths). Direct Slack / Discord / WhatsApp SDK calls bypass per-recipient consent gates.
- **NEVER skip the PRECOG-IDR emit.** A scan run with no IDR is not replayable, defeats V>>'s PRECOG mandate, and breaks the exogenous-anchor law (`rules/exogenous-anchor-law.md` — IDR is the prediction the deadline-verifier checks against).

## §7 Falsification

This skill's value claim: **"V>> invokes `/intel <platform> --mode <mode>` more frequently than the underlying `bin/linkedin-feed-scout` / `lib/guild-scan.sh` direct invocations over 30 days, indicating the unified orchestrator removes real friction."**

Falsified by any of:

- V>> never invokes `/intel` in 30 days post-merge and keeps running direct shell invocations → the orchestration was not actually friction; the skill is overhead. Delete and revert.
- The three Phase-1 modes prove indistinguishable in practice (operator can't tell `/strat-ops` from `/founder` outputs) → mode parameterisation is not load-bearing; collapse to one default scope.
- PRECOG-IDR replay (`--replay <idr-id>`) is never used in 30 days → IDR-per-dispatch is dead telemetry. Drop the IDR emit, keep the adapters.
- Phase-2 mode requests exceed Phase-1 usage 3:1 in the first 30 days → V>> needed the full 8-mode matrix on day one; the Q5 trim was the wrong call. Re-open `AMENDMENTS.md` Q5 and ship Phase 2 immediately.
- Capability-lock per mode causes ≥3 separate "I needed Glob but only had Read" friction reports → the lock granularity is too tight; widen `/sneak` to include Grep.

Tracked via **H-INTEL-SKILL-1** (30-day deadline, registered at PR-create time per `rules/scientific-method.md`).

## Cross-references

- Adapters: `lib/intel/{discord,linkedin}.py` (PRs 3243 / 3246)
- Base contract: `lib/intel/base.py` (PR 3242)
- Spawn capability lock: `lib/intel/spawn.py` (PR 3242)
- IDR construction: `lib/precog/idr.py`
- Per-platform skills: `skills/intel-linkedin/SKILL.md`, `skills/guild-knowledge-scan/SKILL.md`
- Relay topology: `rules/operational-context.md` W10 cutover section
- Mode skills: `skills/strat-ops/SKILL.md`, `skills/founder/SKILL.md`, `skills/sneak/SKILL.md`
- Plan grounding: `drafts/intel-primitive-2026-05-25/PLAN.md` §5 (modes) + §14 (PR5 scope)
- Phase-1 trim rationale: `drafts/intel-primitive-2026-05-25/AMENDMENTS.md` Q5
- Council verdict: `drafts/intel-primitive-2026-05-25/COUNCIL.md` (PROCEED_WITH_REVISIONS)

## Origin

Created 2026-05-26 (Gen 866) per V>>'s overnight intel-primitive sprint PRECOG-IDR mandate. Last of 5 Phase-1 PRs:

- PR 3242 — `lib/intel/base.py` + `lib/intel/spawn.py` (MERGED)
- PR 3243 — `DiscordAdapter` (MERGED)
- PR 3246 — `LinkedInAdapter` (MERGED)
- PR 3247 — `WhatsAppAdapter` (MERGED; removed 2026-10-10 with the group walker it wrapped, after GRIP#2808 was closed as not planned)
- PR <this> — operator surface + 3 Phase-1 modes (THIS PR)

Watermark `V>>--<<V`. Anchored by PRECOG-IDR `precog-1779757926-f601d7ed` (effort HIGH, predecessor `precog-1779757258-9301ac02` REPLAY ship-relay).

— V>>--<<V
