"""
headroom_shim.py — syscall-doctrine shim for HEADROOM library-only compress().

Stable API: compress_tool_output(messages, *, min_tokens=500) -> CompressResult | passthrough

Design:
- Library-only path (compress() API) — zero network egress, zero telemetry.
- Runs the scoped Python 3.13 interpreter at ~/CodeTonight/vendor/headroom/.venv313
  so the GRIP-venv (3.14, no cp314 wheel yet) is never mutated.
- FAILS OPEN: any error (missing interpreter, import failure, _core missing,
  exception inside compress()) returns the original messages unchanged with
  tokens_saved=0. The caller never sees a hard failure.
- Triggers only on JSON/array or log-shaped tool results above min_tokens to
  match the measured win profile (50-63% on JSON, ~98% on logs, 0% on code/prose).
- Shadow-trap guard: subprocess cwd is set to /tmp so the headroom/ source tree
  in the vendor checkout never shadows the installed wheel (learned-insights f47b0f96).

Exogenous anchor (E2b):
  tests/test_headroom_shim.py asserts tokens_saved>0 AND losslessness-on-retrieval
  on a fixed JSON fixture — the rung is killed if HEADROOM ever silently drops a
  load-bearing fact.

Version pin: headroom-ai==0.24.0 in .venv313.
Revisit when cp314 wheels ship so the GRIP venv can be used directly.

Syscall doctrine ref: rules/syscall-doctrine.md
Eval + safety notes: drafts/headroom-eval-2026-06-11.md §5
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- Constants -----------------------------------------------------------------

# HEADROOM_VENV313 env var overrides the default path — set it on machines where
# the headroom venv lives elsewhere (portability; the shim fails-open if absent).
_DEFAULT_VENV313 = Path.home() / "CodeTonight/vendor/headroom/.venv313"
_VENV313 = Path(os.environ["HEADROOM_VENV313"]).expanduser() if "HEADROOM_VENV313" in os.environ else _DEFAULT_VENV313
_PYTHON313 = _VENV313 / "bin" / "python"

# Telemetry is proxy-path-only in headroom; library path has zero egress.
# Belt-and-suspenders: set the opt-out env var in the subprocess anyway.
_HEADROOM_ENV = {**os.environ, "HEADROOM_TELEMETRY": "off"}

_MIN_TOKENS_DEFAULT = 500


# --- Result type ---------------------------------------------------------------

@dataclass
class CompressResult:
    messages: list[dict[str, Any]]
    tokens_before: int = 0
    tokens_after: int = 0
    tokens_saved: int = 0
    compression_ratio: float = 1.0
    transforms_applied: list[str] = field(default_factory=list)
    passthrough: bool = False  # True when shim fell back (no savings or error)
    error: str | None = None


# --- Internal helpers ----------------------------------------------------------

def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token estimate: len(text)/4 (GPT-style approximation)."""
    text = json.dumps(messages, ensure_ascii=False)
    return max(1, len(text) // 4)


def _is_json_or_log_shaped(messages: list[dict[str, Any]]) -> bool:
    """
    Return True if at least one tool-role message looks like JSON data or log lines.
    Protects code and prose from being routed — HEADROOM already protects them
    internally, but gating avoids the subprocess call cost entirely.
    """
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "tool" and isinstance(content, str):
            stripped = content.strip()
            # JSON array/object
            if stripped and stripped[0] in "{[":
                return True
            # Log-like: many newlines relative to length
            if content.count("\n") > 10:
                return True
    return False


def _run_compress_subprocess(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Invoke headroom.compress() in a subprocess using the scoped 3.13 interpreter.
    Passes messages as JSON via stdin, returns result dict via stdout.
    cwd=/tmp to avoid the headroom/ source-tree shadow trap.
    """
    payload = json.dumps(messages, ensure_ascii=False)
    script = (
        "import sys, json; "
        "from headroom import compress; "
        "msgs = json.loads(sys.stdin.read()); "
        "r = compress(msgs); "
        "out = {"
        "'messages': [m if isinstance(m, dict) else dict(m) for m in r.messages], "
        "'tokens_before': r.tokens_before, "
        "'tokens_after': r.tokens_after, "
        "'tokens_saved': r.tokens_saved, "
        "'compression_ratio': r.compression_ratio, "
        "'transforms_applied': list(r.transforms_applied)"
        "}; "
        "print(json.dumps(out))"
    )
    result = subprocess.run(
        [str(_PYTHON313), "-c", script],
        input=payload,
        capture_output=True,
        text=True,
        cwd="/tmp",
        env=_HEADROOM_ENV,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"headroom subprocess exit {result.returncode}: {result.stderr[:300]}"
        )
    return json.loads(result.stdout)


# --- Public API ----------------------------------------------------------------

def compress_tool_output(
    messages: list[dict[str, Any]],
    *,
    min_tokens: int = _MIN_TOKENS_DEFAULT,
) -> CompressResult:
    """
    Compress a list of OpenAI-style messages (tool outputs) via HEADROOM's
    library-only compress() API.

    Returns a CompressResult. On any error or when the interpreter is missing,
    returns passthrough=True with the original messages and tokens_saved=0.

    Args:
        messages: OpenAI-style message list. Only tool-role messages with
                  JSON/log-shaped content above min_tokens are routed through
                  HEADROOM; others pass through unchanged.
        min_tokens: Minimum estimated token count to trigger compression.
                    Default 500 (below this the subprocess call cost dominates).
    """
    # Guard: interpreter must exist
    if not _PYTHON313.exists():
        return CompressResult(
            messages=messages,
            tokens_before=_estimate_tokens(messages),
            tokens_after=_estimate_tokens(messages),
            passthrough=True,
            error=f"headroom interpreter not found: {_PYTHON313}",
        )

    tokens_before_est = _estimate_tokens(messages)

    # Guard: below token floor — skip subprocess cost
    if tokens_before_est < min_tokens:
        return CompressResult(
            messages=messages,
            tokens_before=tokens_before_est,
            tokens_after=tokens_before_est,
            passthrough=True,
        )

    # Guard: content shape — only JSON/log content benefits
    if not _is_json_or_log_shaped(messages):
        return CompressResult(
            messages=messages,
            tokens_before=tokens_before_est,
            tokens_after=tokens_before_est,
            passthrough=True,
        )

    try:
        data = _run_compress_subprocess(messages)
        return CompressResult(
            messages=data["messages"],
            tokens_before=data["tokens_before"],
            tokens_after=data["tokens_after"],
            tokens_saved=data["tokens_saved"],
            compression_ratio=data["compression_ratio"],
            transforms_applied=data["transforms_applied"],
            passthrough=data["tokens_saved"] == 0,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open contract
        return CompressResult(
            messages=messages,
            tokens_before=tokens_before_est,
            tokens_after=tokens_before_est,
            passthrough=True,
            error=str(exc),
        )


def retrieve_original(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Attempt CCR retrieval (expand compressed messages back to original).
    Placeholder: headroom's CCR retrieve is available via the proxy/MCP path;
    the library-only compress() stores the cache in-process. Since this shim
    runs compress() in a subprocess, the CCR store is not shared across calls.
    Full retrieval support requires either the proxy mode (out of scope for E2b)
    or a persistent in-process headroom session (future work).

    For now: returns messages unchanged (safe passthrough — the compressed form
    is already usable; retrieval is a follow-up capability).
    """
    return messages
