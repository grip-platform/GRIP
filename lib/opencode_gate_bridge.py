"""OpenCode -> GRIP safety-gate bridge.

GRIP's hard-DENY safety gates are Claude Code PreToolUse hooks wired in
``settings.json``. Under OpenCode (a different harness) those hooks never fire,
so the gates are silently OFF. This bridge lets an OpenCode plugin
(``tool.execute.before``) run the SAME content-based safety gates against an
OpenCode tool call and deny when a gate denies.

Scope is an explicit ALLOWLIST (``BRIDGED_GATE_SCRIPTS``): only the
content-based hard-DENY safety gates are bridged. Claude-Code-session or
advisory hooks (context-gate, sentinel, grip-first, dispatch-economics,
nextstep, skill-usage, relay-*, rtk/ansi passthroughs) are intentionally
EXCLUDED — they need CC session state or are advisory, not safety DENYs. The
bridge's promise is exactly these gates, no more (no false "full parity").

Fail-open per gate: a gate that errors / times out is skipped and never blocks
(matching Claude Code's own PreToolUse hook semantics); a gate's explicit
``exit 2`` is honored as a deny. The commands are read LIVE from settings.json
so the bridge tracks gate changes (DRY) while only running the allowlisted set.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from os.path import expandvars
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()

# Allowlist of content-based hard-DENY safety gates to enforce under a foreign
# harness. Keyed by script basename so the live settings.json command (whatever
# interpreter / path prefix it uses) is run verbatim when its script matches.
BRIDGED_GATE_SCRIPTS = frozenset({
    "sensitive-info-guard.py",      # secrets / financial / PII
    "no-stubs-gate.py",             # hollow-stub false-completeness
    "guardrail-evasion-gate.py",    # obfuscation-to-evade-a-scanner
    "cc-gate-hook.py",              # cyclomatic-complexity ceiling
    "quality-pretool-hook.py",      # DRY / KISS
    "grip-hal-repo-protection.py",  # cross-repo write protection
    "cross-org-comms-hook.py",      # cross-org leak boundary
    "gh-default-repo-gate.py",      # gh wrong-repo guard
    "channel-scope-gate.py",        # channel/client scope boundary
    "memory-guard.sh",              # MEMORY.md clobber guard
})

# OpenCode tool id -> Claude Code tool name (gate matchers key on CC names).
_OPENCODE_TO_CC = {
    "read": "Read", "write": "Write", "edit": "Edit", "patch": "Edit",
    "bash": "Bash", "webfetch": "WebFetch", "glob": "Glob", "grep": "Grep",
    "list": "Read", "task": "Task",
}

# OpenCode arg key -> Claude Code tool_input key (gates read the CC key names).
_ARG_KEY_MAP = {"filePath": "file_path", "newString": "new_string",
                "oldString": "old_string"}


def cc_tool_name(opencode_tool) -> str:
    """Map an OpenCode tool id to the Claude Code tool name gates match on.

    Unknown tools pass through title-cased so a new tool is still matched by a
    broad gate rather than silently skipped (fail-toward-checking)."""
    t = str(opencode_tool or "")
    return _OPENCODE_TO_CC.get(t.lower(), t[:1].upper() + t[1:])


def cc_tool_input(args) -> dict:
    """Map OpenCode call args to a Claude Code ``tool_input`` dict, keeping the
    original keys too so a gate reading either name finds the value."""
    if not isinstance(args, dict):
        return {}
    out = dict(args)
    for src, dst in _ARG_KEY_MAP.items():
        if src in args:
            out.setdefault(dst, args[src])
    return out


def to_cc_payload(opencode_payload: dict) -> dict:
    """Build a Claude-Code PreToolUse payload from an OpenCode tool-call event."""
    op = opencode_payload if isinstance(opencode_payload, dict) else {}
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": cc_tool_name(op.get("tool")),
        "tool_input": cc_tool_input(op.get("args")),
        "cwd": op.get("directory") or op.get("cwd") or "",
    }


def _command_tokens(command: str) -> list:
    """Shell-tokenise a hook command, tolerant of malformed quoting."""
    try:
        return shlex.split(command or "")
    except ValueError:
        return (command or "").split()


def _script_basenames(command: str) -> list:
    """Script basenames that appear as actual command TOKENS — each token's
    ``Path().name``, filtered to .py/.sh. Token-based (not raw-substring) so a
    hostile trailing ``# sensitive-info-guard.py`` cannot spoof the allowlist by
    mentioning a gate name in a comment/argument. Launcher-wrapped gates
    (``bash grip-python.sh hooks/x.py``) still resolve — the gate is its own
    token."""
    names = [Path(tok).name for tok in _command_tokens(command)]
    return [n for n in names if n.endswith((".py", ".sh"))]


def _bridged_scripts_in(command, scripts) -> list:
    """Allowlisted safety-gate basenames a command references (empty if none)."""
    return [b for b in _script_basenames(command) if b in scripts]


def _allowlisted_commands(hooks, scripts) -> list:
    """Commands within ONE matcher-group that invoke an allowlisted safety gate
    (the gate may be launcher-wrapped, so any referenced token counts)."""
    return [h.get("command", "") for h in (hooks or [])
            if _bridged_scripts_in(h.get("command", ""), scripts)]


def _group_matches(matcher, cc_tool) -> bool:
    """Whether a PreToolUse matcher full-matches the tool (Claude Code semantics).
    A malformed matcher regex matches nothing rather than raising."""
    try:
        return re.fullmatch(matcher or "", cc_tool) is not None
    except re.error:
        return False


def matching_gate_commands(pretooluse, cc_tool, scripts=BRIDGED_GATE_SCRIPTS):
    """Allowlisted commands whose matcher matches ``cc_tool`` (order preserved).

    Single pass over groups; the per-group allowlist filter is delegated to
    :func:`_allowlisted_commands` so neither function carries a nested loop."""
    cmds = []
    for group in pretooluse or []:
        if _group_matches(group.get("matcher", ""), cc_tool):
            cmds.extend(_allowlisted_commands(group.get("hooks", []), scripts))
    return cmds


def _default_settings_loader():
    data = json.loads((CLAUDE_DIR / "settings.json").read_text())
    return data.get("hooks", {}).get("PreToolUse", [])


# Gate commands must start with one of these launchers. Defence in depth: the
# bridge runs settings.json gate commands with shell=False and refuses any whose
# executable is not a known launcher, so a hostile non-launcher entry (e.g.
# ``rm -rf ~ # sensitive-info-guard.py`` — which the basename allowlist would
# otherwise pass via the trailing comment) is never executed. settings.json is
# GRIP-owned (same trust as the Claude Code hooks this mirrors); this is
# hardening, not a claim of safety against a fully hostile config.
_SAFE_LAUNCHERS = frozenset({"bash", "sh", "python", "python3", "env"})


def _safe_to_run(command) -> bool:
    """Whether a hook command's executable (first token) is a known launcher."""
    tokens = _command_tokens(command)
    return bool(tokens) and Path(tokens[0]).name in _SAFE_LAUNCHERS


def _default_runner(command: str, stdin_text: str):
    """Run a gate command with the payload on stdin -> (exit_code, output).

    Tokenised (shlex) and run with ``shell=False`` — no shell-metacharacter
    interpretation; ``$VAR`` tokens are expanded explicitly. A command whose
    executable is not an allowlisted launcher is refused. Fail-open: any refusal
    or execution error returns ``(0, "")`` so a broken/odd gate skips rather than
    blocks (Claude Code PreToolUse semantics)."""
    if not _safe_to_run(command):
        return 0, ""
    try:
        tokens = [expandvars(t) for t in _command_tokens(command)]
        proc = subprocess.run(
            tokens, shell=False, input=stdin_text, capture_output=True,
            text=True, timeout=20, cwd=str(CLAUDE_DIR))
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception:
        return 0, ""


def _hook_denies(code: int, output: str):
    """Whether a Claude Code PreToolUse hook result is a DENY, with the reason.

    Claude Code hooks signal deny EITHER via exit code 2 OR via a JSON stdout
    object with ``permissionDecision == "deny"`` (or a top-level
    ``decision == "deny"``) at exit 0 — the structured form GRIP's gates use
    (e.g. cc-gate emits ``{"decision": "deny", "reason": "..."}`` exit 0). Both
    are honored; anything else is allow (fail-open)."""
    if code == 2:
        return True, (output or "").strip() or "denied (exit 2)"
    for line in (output or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        hso = obj.get("hookSpecificOutput")
        hso = hso if isinstance(hso, dict) else {}
        if obj.get("decision") == "deny" or hso.get("permissionDecision") == "deny":
            return True, str(obj.get("reason")
                             or hso.get("permissionDecisionReason") or "denied")
    return False, ""


def decide(opencode_payload, *, settings_loader=_default_settings_loader,
           runner=_default_runner) -> dict:
    """Run the bridged GRIP safety gates against an OpenCode tool call.

    Returns ``{"decision": "deny"|"allow", "reasons": [...], "gates_run": [...]}``.
    A bridged gate denies via exit 2 OR a JSON-stdout ``permissionDecision`` /
    ``decision`` == "deny" (see :func:`_hook_denies`); any other result -> that
    gate is skipped (fail-open). ``settings_loader``/``runner`` are DIP-injected
    so tests run with zero subprocess / filesystem access."""
    payload = to_cc_payload(opencode_payload)
    stdin_text = json.dumps(payload)
    reasons, gates_run = [], []
    try:
        pretooluse = settings_loader()
    except Exception:
        pretooluse = []
    for cmd in matching_gate_commands(pretooluse, payload["tool_name"]):
        scripts = _bridged_scripts_in(cmd, BRIDGED_GATE_SCRIPTS)
        gates_run.extend(scripts)
        code, out = runner(cmd, stdin_text)
        denied, reason = _hook_denies(code, out)
        if denied:
            label = scripts[0] if scripts else "gate"
            reasons.append(reason or f"denied by {label}")
    return {"decision": "deny" if reasons else "allow",
            "reasons": reasons, "gates_run": gates_run}
