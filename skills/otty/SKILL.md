---
name: otty
description: Otty terminal integration — status card, doctor, and otty-pad (write prompts in a real editor pane, ship them into any Otty pane running any harness — Claude Code, Codex, OpenCode). Use when the operator invokes /otty, asks to send a prompt to another pane/session, wants the prompt-pad editor pane, or asks about Otty agent badges/hooks. Detection-gated — every surface is inert on machines without Otty (Windows teammates), full power where Otty lives.
---

# /otty — Otty ⇄ GRIP integration

Otty (otty.sh) is the GPU-native terminal built for code agents. GRIP's
integration has four layers, all routed through `lib/otty_transport.py`
(syscall doctrine — no direct `otty` spawns anywhere else):

| Layer | Artefact | What it gives |
|---|---|---|
| Transport | `lib/otty_transport.py` | pane list/split/capture/badge, `send_prompt` (bracketed-paste send-keys), `state:claude` reporting (`watch:claude` is raw-CLI, step 5 below — not a transport function) |
| Prompt pad | `bin/otty-pad` | write prompts in $EDITOR or any watched file; ship to any pane, any harness; `---` separates prompts; broadcast with `--all` |
| Lifecycle hooks | `hooks/otty-state-hook.sh` (+ `lib/gated_machine_hooks.py`) | Claude sessions badge their Otty tab processing/idle/awaiting — via Otty's own code-signed hook, wrapped so it is INERT without Otty |
| Cockpit | `bin/grip-otty` | status card + doctor (`--fix` enables send-keys + re-gates hooks) |

## Command surface

```bash
grip-otty                 # status: panes, agent sessions (★), send-keys, hook gating
grip-otty doctor [--fix]  # health checks; --fix enables ipc-allow-send-keys + gates hooks
otty-pad                  # pick an agent pane → $EDITOR loop → save+quit sends
otty-pad --split          # the pad as its own Otty split pane
otty-pad --watch ideas.md # any editor, any app: a `---` line + save ships the block
otty-pad --all --send "run the tests"   # broadcast to every agent pane
otty-pad --list           # panes, agents starred
```

## Invocation protocol (for GRIP)

1. `/otty` or "otty status" → run `bin/grip-otty` and show the card.
2. "send X to the <title> pane/session" → `lib/otty_transport.send_prompt`
   after resolving the pane id from `pane_list()` by title/cwd match. NEVER
   pass an empty pane id (it targets the FOCUSED pane — live incident
   2026-07-02).
3. "open the prompt pad" → `otty-pad --split` (focused split, size 35%).
4. Long command the operator should see finish → wrap in `otty watch <cmd>`
   (progress badge + system notification, and it feeds Otty's OSC 9;4).
5. Chain-after-idle orchestration → `otty watch:claude <session-id>
   --timeout-secs N` blocks until that session is idle (exit 0 idle,
   4 unknown id, 9 timeout).

## Prerequisites (once per machine)

- `otty config set ipc-allow-send-keys true` **and** `otty config reload` —
  send-keys is off by default, and a running app does not pick the key up
  without the reload. `grip-otty doctor --fix` does both.
- Hooks: Otty's Settings → Agents → "Install Hooks" writes RAW absolute-path
  entries into settings.json. GRIP replaces them with the gated wrapper
  (`lib/gated_machine_hooks.py --apply`). If Otty is ever reinstalled and its
  installer re-adds raw entries, `grip-otty doctor` flags the drift.

## Detection gating (the team contract)

Every hook command committed to settings.json is a `/bin/sh` wrapper that
exits 0 in ~2ms when the integration is absent: no Otty app → silent no-op
(Windows teammates: F>>, A>>, K>>), Otty present (V>>) → exec's Otty's
own bundled hook. The same pattern gates the prompt-cache-keepalive CC-plugin
hooks on the GRIP venv. Never wire a machine-local absolute path into
settings.json directly — extend `lib/gated_machine_hooks.py::SPECS`.

## Cross-refs

- `docs/otty-integration.md` — full topology (absorb-graph) + best-of-Otty guide
- `rules/syscall-doctrine.md` — why all otty calls go through the shim
- `tests/test_otty_transport.py` · `tests/test_otty_pad.py` ·
  `tests/test_gated_machine_hooks.py` — 50 mocked tests, no Otty needed
