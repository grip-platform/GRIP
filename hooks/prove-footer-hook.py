#!/usr/bin/env python3
"""Per-response prove-it footer — the GRIP dogfood of GRASP's L1 floor.

Stop hook, DEFAULT OFF: arms only when ``GRIP_PROVE_FOOTER=1``. When the
turn's final assistant message carries a fenced ``grasp-cite`` spec block
(sources + citations for the claims it made), the GRASP footer verifies
every quote against its source and the provenance card surfaces as a
systemMessage — the moat proving the model's own salient claims, on GRIP
itself first. A fabricated quote renders ✗; it cannot pass.

The message is the Stop payload's own copy when it carries one, because the
transcript is written separately and can lag behind the Stop event, ending on
the previous message (GRIP#9075). The transcript's tail is the fallback. It
supplies the model only when it ends on the same message: a lagging transcript
names the previous message's model, which can differ after a model switch, so
the card then records the model as unknown rather than as the wrong one.

Fail-open BY CONTRACT: any error — missing grasp install, malformed spec,
unreadable transcript — exits 0 with no output. A provenance footer must
never break the session it is proving.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_SPEC_RE = re.compile(r"```grasp-cite\s*\n(.*?)```", re.DOTALL)


def _grasp_search_paths() -> list[Path]:
    """Candidate grasp import roots, most explicit first: the operator's
    ``GRASP_CHECKOUT`` override, then the standard pipx venv layouts
    (grasp is pure stdlib Python, so site-packages path injection is
    interpreter-portable). No personal paths are hardcoded."""
    paths: list[Path] = []
    override = os.environ.get("GRASP_CHECKOUT", "").strip()
    if override:
        paths.append(Path(override).expanduser())
    for venv_glob in (".local/pipx/venvs/grasp-provenance/lib/python*/site-packages",
                      ".local/share/pipx/venvs/grasp-provenance/lib/python*/site-packages"):
        paths.extend(sorted(Path.home().glob(venv_glob)))
    return [p for p in paths if p.exists()]


def _purge_grasp_modules() -> None:
    """A half-imported grasp from a wrong root poisons retries — clear it."""
    for name in [m for m in sys.modules
                 if m == "grasp" or m.startswith("grasp.")]:
        del sys.modules[name]


def _import_render_footer():
    """grasp from the interpreter env, an operator override, or a pipx
    install; None = absent (the hook then no-ops — fail-open)."""
    try:
        from grasp.footer import render_footer
        return render_footer
    except ImportError:
        pass
    for root in _grasp_search_paths():
        _purge_grasp_modules()
        sys.path.insert(0, str(root))
        try:
            from grasp.footer import render_footer
            return render_footer
        except ImportError:
            sys.path.remove(str(root))
    return None


_TAIL_BYTES = 256 * 1024  # generous for one turn; small files read whole


def _read_tail_lines(transcript_path: str) -> list[str]:
    """The transcript's last ~256KB as complete lines. A long session's
    footer stays O(tail), never O(whole file)."""
    path = Path(transcript_path)
    size = path.stat().st_size
    with open(path, "rb") as fh:
        if size > _TAIL_BYTES:
            fh.seek(size - _TAIL_BYTES)
            fh.readline()  # drop the partial first line after the seek
        data = fh.read()
    return data.decode("utf-8", errors="replace").splitlines()


def _last_assistant_message(transcript_path: str) -> tuple[str, str]:
    """(text, model) of the final assistant message, scanned tail-first."""
    for line in reversed(_read_tail_lines(transcript_path)):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        message = entry.get("message", {})
        parts = [c.get("text", "") for c in message.get("content", [])
                 if isinstance(c, dict) and c.get("type") == "text"]
        if parts:
            return "\n".join(parts), message.get("model", "")
    return "", ""


def _payload_text(payload: dict) -> str:
    """The payload's own copy of the final message; "" when it has none or the
    shared reader cannot be imported (the transcript then decides, as before)."""
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from lib.transcript_text import payload_message_text
    except ImportError:
        return ""
    return payload_message_text(payload)


def _footer_for(payload: dict) -> str:
    transcript = payload.get("transcript_path") or ""
    tail_text, tail_model = (_last_assistant_message(transcript)
                             if transcript and Path(transcript).exists() else ("", ""))
    text = _payload_text(payload) or tail_text
    model = tail_model if text.strip() == tail_text.strip() else ""
    match = _SPEC_RE.search(text)
    if not match:
        return ""  # no source-bound claims declared this turn
    spec = json.loads(match.group(1))
    spec.setdefault("response", text)
    render_footer = _import_render_footer()
    if render_footer is None:
        return ""
    result = render_footer(spec, model=model or "unknown")
    return result.text if result.emitted else ""


def main() -> int:
    if os.environ.get("GRIP_PROVE_FOOTER") != "1":
        return 0  # default off — the dogfood is opt-in
    try:
        payload = json.load(sys.stdin)
        footer = _footer_for(payload) if isinstance(payload, dict) else ""
        if footer:
            print(json.dumps({"systemMessage": footer}))
    except Exception:  # noqa: BLE001 — fail-open by contract (docstring)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
