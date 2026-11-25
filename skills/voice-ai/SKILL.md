---
name: voice-ai
description: Launch and manage the hands-free chief-of-staff Voice AI — Open-LLM-VTuber (mic → local sherpa-onnx ASR → Claude through the HAL Gateway :4010 → edge_tts speech + Live2D avatar) at localhost:12393. Use when the user invokes /voice-ai, says "start the voice AI", "voice assistant", "talk to the avatar", or asks how the voice stack works. Distinct from /chat (voice-chatting skill), which speaks GRIP's terminal replies aloud — /voice-ai is the full-duplex avatar app.
---

# /voice-ai — Hands-free Voice AI on the HAL Gateway

## Commands

| Invocation | Action |
|------------|--------|
| `/voice-ai` or `voice-ai` | Idempotent start: check gateway → sync key from keychain → start server if down → open browser at `http://localhost:12393` |
| `voice-ai status` | Report gateway / server / config state, change nothing |
| `voice-ai stop` | Stop the voice server (never touches the HAL gateway) |

All three are `~/.claude/bin/voice-ai`. Run via Bash; the start path execs a
foreground server, so for a session-managed launch use `run_in_background`.

## Verification

E2E proof (text turn → Claude via :4010 → TTS audio, with a live watcher
asserting an ESTABLISHED connection to the gateway during the turn):

```bash
~/CodeTonight/Open-LLM-VTuber/.venv/bin/python ~/.claude/scripts/voice-ai-e2e-probe.py
```

PASS requires all three: non-empty LLM reply, ≥1 audio payload, gateway
traffic observed. First verified 2026-06-11 02:13 UTC.

## Architecture (load-bearing facts)

- App: `~/CodeTonight/Open-LLM-VTuber` (OUTSIDE git-crypt GRIP repo — never
  `git add` it, never push it).
- LLM wire: app's `claude_llm` provider, `base_url http://127.0.0.1:4010` —
  the HAL Gateway's verbatim Anthropic `/v1/messages` relay (launchd
  `com.grip.hal-gateway`, source `~/.hal/lib/hal/gateway.py`).
- Key custody: keychain `grip-anthropic-external` → piped over stdin into
  `lib/voice_ai_keysync.py` at every launch → `conf.yaml` chmod 600.
- ASR local (sherpa-onnx SenseVoice, auto-downloaded ~1GB); TTS edge_tts
  cloud (reply text leaves machine — documented trade-off).
- Port 12393 is registered in `rules/port-registry.md`.

Full operator doc: `docs/voice-ai-quickstart.md`.

## Boundaries

- This skill manages the LOCAL voice stack only. It never posts to channels.
- Do not edit `~/CodeTonight/Open-LLM-VTuber/conf.yaml`'s `llm_api_key` by
  hand — the launcher owns that line.
- Gateway down ≠ this skill's failure: `launchctl kickstart -k
  gui/$(id -u)/com.grip.hal-gateway` or escalate to the hal-gateway-startup
  agent.
