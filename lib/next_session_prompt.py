"""Minimal next-session continuation prompt — mint + clipboard-copy.

Origin: V>> 2026-07-18 — "the final output of a /next or a /save should be a
MINIMAL prompt to enter in the new session, and should be automatically copied
to the clipboard." Sharpened by a parallel broly design council
(``drafts/skill-continuation-prompt-spec.md``): reuse the canonical shims, gate
the clipboard so a background /save never clobbers it, and never egress a client
path via Universal Clipboard.

This is the single canonical, DRY substrate that ``/next`` (``skills/next``) and
``/save`` (``skills/saving-state``) call as their FINAL step. It composes with
warm-start (``lib/kono_preload.py`` reloads CLAUDE.md + the holistic kernel +
``rules/session-context.md`` on the next launch), so the prompt is a MINIMAL
pointer — never a duplicate of the ``/ultraprompt`` brief (``plans/ULTRAPROMPT-*.md``).

Reuse (syscall-doctrine — no reinvented boundaries):
  * clipboard  → ``grip_transfer.deliver(payload, method="clipboard")`` (canonical
                 transfer shim; never raises; + wl-copy added to its Linux ladder).
  * next_up    → ``next_step_block.read_latest_next_up`` (the same source warm-start
                 renders), with a ``session-context.md`` parse as fallback.
  * redaction  → ``next_step_block._SECRET_RE`` (token scrub) + a drafts/<client>
                 pointer-swap (G2: never egress a client path to the phone).

Determinism: the payload is a pure function of (next_action, brief, task, tasks,
artifacts) — NO timestamp / SHA / PID / random. ``selftest_idempotent()`` is the
loud Goodhart anchor. Every entry point is fail-soft: a clipboard miss, a missing
shim, or an unreadable file NEVER raises into a caller.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
try:  # canonical portability shim (rules/path-portability.md)
    from claude_dir import get_claude_dir
except Exception:  # pragma: no cover - defensive fallback, stdlib-only
    import os

    def get_claude_dir() -> Path:
        env = os.environ.get("CLAUDE_DIR", "").strip()
        return Path(env).expanduser().resolve() if env else Path.home() / ".claude"


__all__ = [
    "DEFAULT_CEILING",
    "build_prompt",
    "build_minimal_prompt",
    "mint",
    "gather_state",
    "copy_to_clipboard",
    "mint_and_deliver",
    "selftest_idempotent",
    "main",
]

# Char ceiling for the minimal prompt (~400, per V>> spec). It composes WITH
# warm-start, so it stays a short pointer, not a full brief.
DEFAULT_CEILING = 400
_ACTION_CAP = 250  # P1 next-action imperative cap (spec)

_HEAD = "Continue the GRIP session."
_WARM_NOTE = (
    "(Warm-start already loaded CLAUDE.md + holistic memory + session-context.)"
)
_NO_CLIP_MSG = (
    "[next-session-prompt] no clipboard shim available; prompt printed but not "
    "copied — copy it manually from above."
)
_NEXT_STEP_HEADING = "## next step"
_ACHIEVEMENT_HEADING = "## last achievement"

# Fallback token pattern if next_step_block._SECRET_RE cannot be imported.
_FALLBACK_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|bearer|auth|cred|private[_-]?key)"
    r"[\s:=]{0,5}['\"]?[A-Za-z0-9+/=_\-]{20,}"
)


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def _clean(value) -> str:
    return str(value).strip() if value is not None else ""


def _clean_list(value) -> list:
    if not value:
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _truncate(text: str, ceiling: int) -> str:
    if len(text) <= ceiling:
        return text
    if ceiling <= 1:
        return text[:ceiling]
    return text[: ceiling - 1].rstrip() + "…"


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _under_drafts(path: str) -> bool:
    """True for a client-scoped draft path (Universal-Clipboard egress risk)."""
    p = _norm(path)
    return p.startswith("drafts/") or "/drafts/" in p


def _basename(path: str) -> str:
    return _norm(path).rstrip("/").rsplit("/", 1)[-1]


def _safe_path(path: str) -> str:
    """Basename a drafts/<client> path so no client directory egresses (G2)."""
    return _basename(path) if _under_drafts(path) else path


def _redact(text: str) -> str:
    """Scrub token-shaped substrings before the payload can egress (G2)."""
    try:
        from next_step_block import _SECRET_RE as secret_re
    except Exception:
        secret_re = _FALLBACK_SECRET_RE
    return secret_re.sub("[REDACTED]", text)


# --------------------------------------------------------------------------- #
# Prompt assembly — P0 pointer > P1 action > P2 extras (never drop P0)
# --------------------------------------------------------------------------- #
def _pointer_segment(state: dict) -> str:
    """P0: brief-path + task pointer. Drops a drafts/<client> path for the
    ``task #id`` pointer (G2 — the task description resolves it in-session)."""
    brief = _clean(state.get("brief"))
    task = _clean(state.get("task"))
    if brief and _under_drafts(brief) and task:
        brief = ""  # client path ⇒ rely on the task pointer alone
    elif brief:
        brief = _safe_path(brief)
    if brief and task:
        return f"Brief: {brief} (task #{task}) - read first."
    if brief:
        return f"Brief: {brief} - read first."
    if task:
        return f"Pointer: task #{task} - read its brief first."
    return ""


def _action_segment(state: dict) -> str:
    action = _clean(state.get("next_action"))[:_ACTION_CAP]
    return f"NEXT: {action}" if action else ""


def _content_extras(state: dict) -> list:
    """P2 content extras (tasks, artifacts). Warm-note is added separately."""
    segments = []
    tasks = _clean_list(state.get("tasks"))
    if tasks:
        segments.append("Also: " + ", ".join(f"#{t}" for t in tasks) + ".")
    artifacts = [_safe_path(a) for a in _clean_list(state.get("artifacts"))]
    if artifacts:
        segments.append("Artifacts: " + ", ".join(artifacts) + ".")
    return segments


def _fit_core(action: str, pointer: str, ceiling: int) -> str:
    """Assemble action+pointer under ceiling, always keeping the P0 pointer whole."""
    if not pointer:
        return _truncate(action, ceiling)
    if not action:
        return _truncate(pointer, ceiling)
    room = ceiling - len(pointer) - 1  # 1 for the join space
    if room <= 0:
        return _truncate(pointer, ceiling)  # P0 wins outright
    return f"{_truncate(action, room)} {pointer}"


def build_prompt(state: dict, ceiling: int = DEFAULT_CEILING) -> str:
    """Mint the minimal continuation prompt. Deterministic; fail-soft; may be
    empty. Guarantees: ``len(result) <= ceiling``; the P0 pointer (brief/task)
    is never dropped; empty state → ``""`` (NO-OP, no hollow prompt)."""
    pointer = _pointer_segment(state)   # P0
    action = _action_segment(state)     # P1
    extras = _content_extras(state)     # P2
    if not (pointer or action or extras):
        return ""  # NO-OP — nothing to hand off
    # Reading order: HEAD, action, pointer; keep-priority: pointer > action.
    core = " ".join(s for s in (action, pointer) if s)
    core = f"{_HEAD} {core}" if core else _HEAD
    if len(core) > ceiling:
        core = _fit_core(f"{_HEAD} {action}".strip(), pointer, ceiling)
    prompt = core
    for seg in (*extras, _WARM_NOTE):
        candidate = f"{prompt} {seg}"
        if len(candidate) <= ceiling:
            prompt = candidate
    return _redact(prompt)


# Aliases — the design spec references these names; keep DRY (one impl).
build_minimal_prompt = build_prompt
mint = build_prompt


# --------------------------------------------------------------------------- #
# State gathering — reuse the canonical next_up reader, fall back to the
# rendered session-context.md (fail-soft, never raises)
# --------------------------------------------------------------------------- #
def _read_text(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _debullet(line: str) -> str:
    stripped = line.strip()
    for marker in ("- ", "* ", "• "):
        if stripped.startswith(marker):
            return stripped[len(marker):].strip()
    head, sep, rest = stripped.partition(". ")
    if sep and head.isdigit() and rest:
        return rest.strip()
    return stripped


def _extract_section(text: str, heading: str) -> str:
    """First non-empty body line under a ``## heading`` (lowercased), debulleted."""
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## "):
            in_section = stripped.lower().startswith(heading)
            continue
        if in_section and stripped:
            return _debullet(stripped)
    return ""


def _from_instance_index() -> dict:
    """Reuse the canonical next_up reader warm-start uses (no drift). Fail-soft."""
    try:
        from next_step_block import read_latest_next_up
        nu = read_latest_next_up(Path.cwd())
    except Exception:
        return {}
    if not isinstance(nu, dict):
        return {}
    remaining = [r for r in (nu.get("remaining") or []) if str(r).strip()]
    return {"next_action": str(remaining[0]).strip()} if remaining else {}


def _from_session_context(base: Path) -> dict:
    text = _read_text(base / "rules" / "session-context.md")
    if not text:
        return {}
    nxt = _extract_section(text, _NEXT_STEP_HEADING)
    if nxt:
        return {"next_action": nxt}
    ach = _extract_section(text, _ACHIEVEMENT_HEADING)
    return {"next_action": f"continue from: {ach}"} if ach else {}


def gather_state(claude_dir: Optional[Path] = None) -> dict:
    """Auto-derive ``{next_action}`` from canonical GRIP session state.

    In production (``claude_dir is None``): try the canonical instance-index
    next_up first, then the rendered ``rules/session-context.md``. When
    ``claude_dir`` is given (tests), read only that dir's session-context — the
    instance-index probe (which reads the live cwd) is skipped for hermeticity.
    Returns ``{}`` when nothing is found. Never raises.
    """
    if claude_dir is None:
        inst = _from_instance_index()
        if inst:
            return inst
        return _from_session_context(get_claude_dir())
    return _from_session_context(Path(claude_dir))


# --------------------------------------------------------------------------- #
# Clipboard — delegate to the canonical grip_transfer shim (DRY). Fail-soft.
# --------------------------------------------------------------------------- #
def copy_to_clipboard(text: str, deliver_fn: Optional[Callable] = None) -> bool:
    """Copy ``text`` to the system clipboard via ``grip_transfer.deliver``.

    Returns ``True`` only on a real clipboard write (``method == "clipboard"``);
    a stdout fallback, missing shim, or error is a graceful ``False`` + stderr
    note. NEVER raises. ``deliver_fn`` is an injectable seam for tests (default:
    ``grip_transfer.deliver``) so no real clipboard is touched under test.
    """
    if deliver_fn is None:
        try:
            from grip_transfer import deliver as deliver_fn
        except Exception:
            print(_NO_CLIP_MSG, file=sys.stderr)
            return False
    try:
        result = deliver_fn(text, method="clipboard")
    except Exception as exc:  # best-effort: a clipboard miss cannot break /next
        print(f"[next-session-prompt] clipboard delivery failed: {exc}", file=sys.stderr)
        return False
    if not isinstance(result, dict):
        return False
    ok = bool(result.get("ok")) and result.get("method") == "clipboard"
    if not ok:
        print(f"[next-session-prompt] not copied ({result.get('detail', 'no clipboard tool')}); "
              "copy it manually from above.", file=sys.stderr)
    return ok


def mint_and_deliver(
    state: dict,
    clip: bool = False,
    ceiling: int = DEFAULT_CEILING,
    deliver_fn: Optional[Callable] = None,
) -> tuple:
    """Mint the prompt and (if ``clip``) copy it. Returns ``(prompt, copied)``.

    ``clip`` DEFAULTS to ``False`` (G1: a background/mid-loop /save must never
    clobber the operator's clipboard; only an interactive session-end handoff
    opts in). An empty prompt (NO-OP state) is never copied.
    """
    prompt = build_prompt(state, ceiling=ceiling)
    copied = bool(prompt) and clip and copy_to_clipboard(prompt, deliver_fn=deliver_fn)
    return prompt, copied


def selftest_idempotent(samples: Optional[list] = None) -> bool:
    """Loud Goodhart anchor: minting is a deterministic pure projection.

    Asserts two mints of the same state are byte-identical (no timestamp/PID
    leaked in) for each sample. Raises ``AssertionError`` on any drift.
    """
    if samples is None:
        samples = [
            {"next_action": "wire the receipt verifier into CI",
             "brief": "plans/ULTRAPROMPT-x.md", "task": 42,
             "artifacts": ["lib/next_session_prompt.py"]},
            {"next_action": "resume"},
            {},
        ]
    for state in samples:
        first = build_prompt(state)
        assert first == build_prompt(state), f"non-deterministic mint for {state!r}"
    return True


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv: Optional[list]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="next_session_prompt",
        description="Mint + clipboard-copy the minimal next-session prompt.",
    )
    parser.add_argument("--from", dest="from_json", default=None,
                        help="JSON state file {next_action,brief,task,tasks,artifacts}.")
    parser.add_argument("--next", dest="next_action", default=None,
                        help="Explicit next action (overrides auto-gather).")
    parser.add_argument("--brief", default=None, help="Path to the /ultraprompt brief.")
    parser.add_argument("--task", default=None, help="Pointer-task id for the brief.")
    parser.add_argument("--artifact", dest="artifacts", action="append", default=[],
                        help="Artifact/candidate path (repeatable).")
    parser.add_argument("--clip", dest="clip", action="store_true", default=None,
                        help="Force clipboard copy on (default when interactive).")
    parser.add_argument("--no-clip", dest="clip", action="store_false",
                        help="Print only; do not touch the clipboard.")
    parser.add_argument("--ceiling", type=int, default=DEFAULT_CEILING,
                        help=f"Char ceiling (default {DEFAULT_CEILING}).")
    parser.add_argument("--selftest", action="store_true",
                        help="Run selftest_idempotent() and exit.")
    return parser.parse_args(argv)


def _build_state(args: argparse.Namespace) -> dict:
    """Merge state: auto-gather < --from JSON < explicit flags."""
    state: dict = {}
    if not args.next_action and not args.from_json:
        state.update(gather_state())
    if args.from_json:
        try:
            loaded = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state.update(loaded)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"[next-session-prompt] could not read --from {args.from_json}: {exc}",
                  file=sys.stderr)
    for key, val in (("next_action", args.next_action), ("brief", args.brief),
                     ("task", args.task)):
        if val:
            state[key] = val
    if args.artifacts:
        state["artifacts"] = list(state.get("artifacts") or []) + list(args.artifacts)
    return state


def main(argv: Optional[list] = None) -> int:
    """CLI: print the minimal prompt to stdout AND (by default) copy to clipboard.

    Default copies (the CLI is the interactive surface); ``--no-clip`` disables.
    Programmatic callers use ``mint_and_deliver`` (clip defaults OFF — G1).
    """
    args = _parse_args(argv)
    if args.selftest:
        selftest_idempotent()
        print("selftest_idempotent: OK")
        return 0
    clip = True if args.clip is None else args.clip  # primary spec: default copies
    state = _build_state(args)
    prompt, copied = mint_and_deliver(state, clip=clip, ceiling=args.ceiling)
    if not prompt:
        print("[next-session-prompt] nothing to hand off (no next action or brief).",
              file=sys.stderr)
        return 0
    print(prompt)
    if copied:
        print("[next-session-prompt] copied to clipboard — paste into the new session.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
