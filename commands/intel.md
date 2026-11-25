---
description: Cross-platform intel orchestrator — /intel <platform> [--mode strat-ops|founder|sneak] routes to the Discord or LinkedIn IntelAdapter, emits PRECOG-IDR per dispatch.
---

# /intel

Invokes the `intel` skill. See `~/.claude/skills/intel/SKILL.md` for the full
protocol (adapter dispatch, Phase-1 modes, PRECOG-IDR usage, relay integration,
falsification).

| Flag | Behaviour |
|------|-----------|
| `<platform>` | One of `discord`, `linkedin` — routes to the matching `IntelAdapter` |
| `--mode <mode>` | Phase-1 mode overlay: `strat-ops` (RL 10/min, conf 0.7, RO+Grep), `founder` (RL 30/min, conf 0.5, RO+Grep+Glob), `sneak` (RL 15/min, conf 0.6, RO, drafts-only) |
| `--seed <id>` | Pin a specific channel ID / JID / URL |
| `--top N` | Cap observations at N (default 50) |
| `--window Hh` | Time window in hours (default 24) |
| `--dry` | discover + scan + enrich, skip relay |
| `--replay <idr-id>` | Re-run a prior PRECOG-IDR for byte-for-byte replay |

Phase-2 modes (`business-mode`, `research-mode`, `140-85`, `stealth`,
`absorb-knowledge`) are deferred per `drafts/intel-primitive-2026-05-25/
AMENDMENTS.md` Q5 — they raise a loud HITL today.
