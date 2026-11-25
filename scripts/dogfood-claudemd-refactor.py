#!/usr/bin/env python3
"""Dogfood lib/hal/sysmsg.py on the CLAUDE.md corpus refactor (Opus 4.8).

Demonstrates the mid-conversation system-message primitive end-to-end:
loads a crown-jewel CLAUDE.md as the base system prompt ONCE, then drives a
narrative-drafting instruction as a sys_update DELTA — never restating the
base. Prints the cache-preserving structural proof (deterministic) and
attempts a live HAL draft (fail-safe).

Hybrid dogfood (V>> 2026-05-28): the primitive drafts the narrative; the
operator applies the surgical count/line edits under /hitl.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.hal.sysmsg import SysMsgUpdate, apply_update  # noqa: E402
from lib.hal.types import ChatMessage  # noqa: E402

CANON = {"skills": 373, "agents": 39, "commands": 89, "rules": 33,
         "rules_paramount": 30, "hooks": 83, "modes": 31, "lib": "500+"}

DELTA = SysMsgUpdate(text=(
    "Draft the replacement 'current power' narrative for GRIP's CLAUDE.md "
    "(stale lines: Session Resurrection / 30-35% savings / ~210 playbooks). "
    f"Canonical counts: {CANON}. Every capability claim MUST name a real file "
    "and be falsifiable. No over-claiming. Do not restate this base prompt."
))


def structural_proof(base: str) -> None:
    """Deterministic falsifiable dogfood: the delta appends WITHOUT restating base."""
    hist = [ChatMessage(role="user", content="refactor the corpus")]
    out = apply_update(hist, DELTA, provider="anthropic")
    assert out[-1].role == "system", "delta must append as a system turn"
    assert base not in out[-1].content, "base prompt must NOT be restated in the delta"
    assert out[:len(hist)] == hist, "earlier turns must stay byte-identical (cache-warm)"
    print(f"[dogfood] structural proof PASS: delta={len(out[-1].content)} chars; "
          f"base={len(base)} chars NOT restated; earlier turns byte-identical (cache-warm).")


def live_draft(base: str) -> None:
    """Attempt a real HAL draft via Conversation.send(sys_update=...). Fail-safe."""
    try:
        from lib.hal.conversation import Conversation
        conv = Conversation(model="anthropic/claude-opus-4-8[1m]", system_prompt=base)
        resp = conv.send("Produce the new current-power narrative.", sys_update=DELTA)
        print("[dogfood] LIVE DRAFT (HAL Conversation + sys_update):\n" + (resp.text or "(empty)"))
    except Exception as e:  # noqa: BLE001 — fail-safe: structural proof is the core evidence
        print(f"[dogfood] live draft unavailable ({type(e).__name__}: {e}); "
              "structural proof above is the deterministic dogfood evidence.")


if __name__ == "__main__":
    target = _ROOT / ".claude" / "CLAUDE.md"
    base = target.read_text()
    structural_proof(base)
    live_draft(base)
