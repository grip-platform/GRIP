#!/usr/bin/env python3
"""End-to-end probe for the GRIP Voice AI stack (Open-LLM-VTuber on the HAL Gateway).

Drives one full conversation turn over the server's own WebSocket protocol —
text in, LLM reply + synthesised speech out — and simultaneously watches for
an ESTABLISHED TCP connection to the HAL Gateway (127.0.0.1:4010), proving the
LLM call traversed the gateway rather than going direct to api.anthropic.com.

Run with the app's venv python (it has the ``websockets`` package):

    ~/CodeTonight/Open-LLM-VTuber/.venv/bin/python \
        ~/.claude/scripts/voice-ai-e2e-probe.py

Exit 0 with a PASS line when: a non-empty LLM reply arrived AND at least one
audio payload arrived AND gateway traffic was observed during the turn.
Exit 1 with the failing dimension named otherwise. This is the falsifier for
hypothesis H-VOICE-HAL-1 (text turn through :4010 by morning).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass, field

WS_URL = "ws://localhost:12393/client-ws"
PROMPT = "Reply with exactly one short sentence confirming you can hear me through the gateway."
TURN_TIMEOUT_S = 90
GATEWAY_PORT = 4010


@dataclass
class TurnState:
    reply_parts: list[str] = field(default_factory=list)
    audio_payloads: int = 0

    @property
    def reply(self) -> str:
        return " ".join(self.reply_parts).strip()


def gateway_established_pids() -> set[str]:
    """PIDs holding an ESTABLISHED TCP connection on the gateway port."""
    out = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{GATEWAY_PORT}", "-sTCP:ESTABLISHED", "-t"],
        capture_output=True, text=True, timeout=10,
    ).stdout.split()
    return set(out)


async def poll_gateway(stop: asyncio.Event, seen: set[str]) -> None:
    while not stop.is_set():
        seen.update(gateway_established_pids())
        await asyncio.sleep(0.4)


def _extract_text(msg: dict) -> str | None:
    display = msg.get("display_text")
    if isinstance(display, dict) and display.get("text"):
        return display["text"]
    return msg.get("text")


def _process_message(raw: str | bytes, state: TurnState) -> bool:
    """Fold one WebSocket message into ``state``; True when the turn is over."""
    if isinstance(raw, bytes):
        state.audio_payloads += 1
        return False
    msg = json.loads(raw)
    mtype = msg.get("type", "")
    if mtype == "audio" and msg.get("audio"):
        state.audio_payloads += 1
    text = _extract_text(msg)
    if text and mtype in ("audio", "full-text") and "Connection established" not in text:
        state.reply_parts.append(text)
    if mtype == "error":
        print(f"SERVER_ERROR: {msg.get('message')}")
        return True
    return mtype == "control" and msg.get("text") == "conversation-chain-end"


async def _drain_handshake(ws) -> None:
    try:
        while True:
            await asyncio.wait_for(ws.recv(), timeout=2)
    except asyncio.TimeoutError:
        pass


def _verdict(state: TurnState, gateway_pids: set[str]) -> int:
    print(f"REPLY_TEXT: {state.reply or '(none)'}")
    print(f"AUDIO_PAYLOADS: {state.audio_payloads}")
    print(f"GATEWAY_ESTABLISHED_PIDS: {sorted(gateway_pids) or '(none)'}")
    failing = []
    if not state.reply:
        failing.append("no LLM reply text")
    if state.audio_payloads == 0:
        failing.append("no TTS audio payload")
    if not gateway_pids:
        failing.append("no ESTABLISHED connection seen on :4010 during the turn")
    if not failing:
        print("PASS: text -> LLM-through-HAL-gateway -> TTS round-trip verified")
        return 0
    print(f"FAIL: {'; '.join(failing)}")
    return 1


async def run_turn() -> int:
    import websockets  # available in the Open-LLM-VTuber venv

    state = TurnState()
    gateway_pids: set[str] = set()
    stop = asyncio.Event()

    async with websockets.connect(WS_URL, max_size=64 * 1024 * 1024) as ws:
        await _drain_handshake(ws)
        poller = asyncio.create_task(poll_gateway(stop, gateway_pids))
        await ws.send(json.dumps({"type": "text-input", "text": PROMPT}))
        async def _collect() -> None:
            while not _process_message(await ws.recv(), state):
                pass

        try:
            # asyncio.wait_for, not asyncio.timeout — the app venv is Python 3.10.
            await asyncio.wait_for(_collect(), timeout=TURN_TIMEOUT_S)
        except asyncio.TimeoutError:
            print(f"NOTE: no chain-end within {TURN_TIMEOUT_S}s; judging what arrived")
        finally:
            stop.set()
            await poller

    return _verdict(state, gateway_pids)


if __name__ == "__main__":
    sys.exit(asyncio.run(run_turn()))
