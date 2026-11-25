---
name: server-mode
description: Always-on server posture for the second Mac as the WhatsApp/Slack relay exit-node. Gives each relay-spawned session warm per-channel context (channel history + warm-start kernel + KONO recall + cross-machine mesh) instead of a cold start, with periodic checkpointing, grip-presence mesh registration, and a HAL-routed bias to conserve the Claude weekly limit. Use when a machine runs unattended as a relay/server (e.g. weekend, operator travelling).
---

# /server-mode — always-on relay server posture

**Plain English.** When a Mac runs unattended as the relay exit-node, every
inbound WhatsApp/Slack message spawns a fresh `claude -p`. By default that
session starts **cold** — it has no idea what was said in that channel before,
what GRIP already knows, or what other sessions/machines are doing. `/server-mode`
makes each spawn open **warm**, cheaply, without re-reading the world.

## What it composes (per channel, read-only, bounded)

`lib/server_mode_context.py::compose_channel_context(channel_id, topic="", budget_chars=4000)`
assembles a budgeted preamble from GRIP's existing substrates — every section
degrades to an **honest `(unavailable …)` note**, never fabricated content
(`rules/no-stubs.md`):

| Section | Source | "Warm" value |
|---------|--------|--------------|
| Channel history | `state/relay/sessions/<channel>.txt` + this channel's rows in `logs/relay-dispatcher.jsonl` | who said what here recently — **per-channel isolated** |
| GRIP kernel | `rules/session-context.md` (HOLISTIC + Next Step + recall) | what GRIP is + the standing next step |
| Relevant memory | `lib/kono_cli.py query <topic>` | memory keyed on the message's topic |
| Cross-machine mesh | `mesh_discovery.load_peers()` + `concurrent_session_detector` | what GRIP is doing across the second Mac / blackbook / VPS |

## Posture (the mode)

- **Context injection** — call the composer per inbound message; inject as a
  preamble to the relay spawn prompt.
- **Periodic checkpoint** — `/save` (saving-state) / `/ultraclear` on a cadence
  so the long-running server never loses state to compaction.
- **Mesh presence** — register the session via the grip-presence MCP
  (`session_register` + `session_heartbeat`) so concurrent sessions/machines see
  each other (`feedback_grip_presence_mesh_always_on`).
- **Capability lock preserved** — the relay spawn stays tool-restricted
  (`rules/untrusted-content-capability-lock.md`); context injection is billing/
  context only, never a capability widening.
- **HAL-routed bias** — prefer HAL non-Anthropic providers for heavy reasoning
  to conserve the Claude weekly limit (`docs/auth-bucket-failover.md`).

## How to use

```bash
# Smoke the composer (read-only) for the team group in config/wa-identities.local.json:
PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python \
  $HOME/.claude/lib/server_mode_context.py

# Or for another chat, with a topic (replace <chat-id> with a real chat ID first):
PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python \
  $HOME/.claude/lib/server_mode_context.py "<chat-id>@g.us" "invoice deadline"

/mode server          # activate the posture for an unattended server session
```

## ⚠ Live-relay cutover is operator-gated

This skill ships the **composer + posture (additive, tested)**. Wiring
`compose_channel_context` into `lib/relay_dispatcher.py`'s spawn path mutates a
**live production surface** — it is a **separate, operator-ratified flip**, never
done autonomously (don't risk the always-on relay). Recommended cutover: inject
the composed preamble into the per-channel `listener-prompt.md` build, behind a
`GRIP_SERVER_MODE_CONTEXT=1` env flag, default OFF, with a regression test that
asserts per-channel isolation end-to-end.

## Falsification

Wrong if, over ~10 unattended server-days: warm context measurably worsens relay
replies (off-topic injection), OR the composer's budget/latency adds more cost
than the cold-start confusion it removes, OR per-channel isolation ever leaks one
channel's history into another (a hard bug — the test `test_per_channel_isolation`
is the guard). Track: relay reply quality + cross-channel-leak incidents (target 0).

## Related
`lib/server_mode_context.py` · `tests/test_server_mode_context.py` ·
`modes/definitions/server-mode.yaml` · `lib/relay_dispatcher.py` (cutover target) ·
`feedback_grip_presence_mesh_always_on` · `docs/auth-bucket-failover.md` ·
`rules/untrusted-content-capability-lock.md` · `skills/ultraclear` · `skills/saving-state`
