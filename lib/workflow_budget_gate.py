"""Pure decision function for gating a Workflow-tool launch on cost.

Origin: rules/fan-out-spends-the-weekly-limit.md. A single Workflow run
spawned 273 default-type agents across 11 depths to converge one reply,
moving the weekly Claude limit from 0.04 to 0.36 (later 0.51) and stalling
four and a half hours at depth 8 with zero cache_read on every retry.

``decide`` is the one entry point. It never raises: any internal failure is
caught and returned as an ``allow`` decision carrying the error in its
reasons, so a bug in this module can never itself block a Workflow launch --
only the conditions it is built to catch can.

No network access anywhere in this module. The only I/O is reading the
script text handed in (inline or from a path already resolved by the
caller) and reading the HAL usage ledger the caller points at.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

# --- thresholds (module constants, per the rule's own falsification clause) ---

DENY_WEEKLY = 0.70
DENY_FIVE_HOUR = 0.85
WARN_WEEKLY = 0.50
MAX_AGENT_CAP = 48
TOKENS_PER_AGENT_HEAVY = 120_000
TOKENS_PER_AGENT_LIGHT = 60_000
OVERRIDE_MIN_REASON_LEN = 20
LIGHT_AGENT_TYPES = frozenset({"Plan", "Explore"})

# The cap is read from the code alone: comments are removed and string literals
# emptied, so a declaration or bounding use in a comment or a prompt string counts
# for nothing, and a "//" or "/*" inside a string never starts a comment that
# could swallow the real declaration (AGORA round 2 on GRIP#9733). A literal cap
# counts only when its statement ends right after it: "= 4 * args.n" is not a cap
# of 4, and nor is "= 4" with "* args.n" behind a comment or on the next line
# (GRIP#8707, AGORA on GRIP#9733). A Math.min with at least one bare integer
# operand is a sound bound too: the minimum never exceeds any operand, so the
# smallest literal bounds it. Its operands are split at the call's own commas,
# never inside a nested call. A literal fallback such as "args.n ?? 48" bounds
# nothing and stays refused.
_STRING = r"""(?P<str>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)"""
_TOKEN_RE = re.compile(_STRING + r"|(?P<comment>/\*.*?\*/|//[^\n]*)", re.S)
_CAP_RE = re.compile(r"const\s+GRIP_MAX_AGENTS\s*=\s*(\d+)")
_CAP_MIN_RE = re.compile(r"const\s+GRIP_MAX_AGENTS\s*=\s*Math\.min\(")
_CAP_DECLARED_RE = re.compile(r"const\s+GRIP_MAX_AGENTS\s*=")
_NEXT_TOKEN_RE = re.compile(r"[^\S\n]*(\n\s*)?(\S?)")
_WORD_RE = re.compile(r"[A-Za-z_$][\w$]*")
# A genuine BOUNDING use of the cap -- a loop/length/min comparison -- not
# merely a second textual mention. Closes a real bypass (Codex review,
# GRIP#8698): a script that names GRIP_MAX_AGENTS twice used to pass even
# when the second mention was a comment or an unrelated expression, leaving
# its actual agent() loop unbounded. This is still a heuristic over raw
# script text, not a JS parser -- it cannot prove the bound is HONOURED at
# runtime, only that the identifier is used the way a bound would be.
#
# The comparison alternatives accept EITHER operand order (<, <=, >, >=),
# not just "constant first". A real guard such as "if (spawned >=
# GRIP_MAX_AGENTS) throw" puts the constant SECOND, which the original pair
# ("<=? GRIP_MAX_AGENTS" / "GRIP_MAX_AGENTS >=?") could only match in the
# other direction -- so that guard was denied as having no bound at all, on
# a real workflow script that genuinely had one (GRIP#8698 follow-up, found
# by running the merged gate against workflow-spend-guard-wf_500f4dff-b44.js).
#
# EQUALITY (==, ===) is deliberately NOT in this alternation, having been
# tried and reverted (AGORA review, GRIP#8710): equality bounds nothing. A
# script with "if (GRIP_MAX_AGENTS === 4) log('configured')" plus an
# unbounded "parallel(items.map(... agent ...))" passed declared_cap() under
# the equality-widened regex, because the check only asserts the constant
# holds one value -- it says nothing about whether dispatch is capped by it.
_CAP_BOUND_RE = re.compile(
    r"[<>]=?\s*GRIP_MAX_AGENTS\b"
    r"|GRIP_MAX_AGENTS\s*[<>]=?"
    r"|Math\.min\([^)]*GRIP_MAX_AGENTS[^)]*\)"
    r"|\{\s*length:\s*GRIP_MAX_AGENTS\b"
    r"|\.slice\(\s*0\s*,\s*GRIP_MAX_AGENTS\b"
)
# "spawn(" counts alongside "agent(" (GRIP#8710 finding 2): every shipped
# workflow now routes its real dispatch calls through a spawn() counter
# wrapper that enforces GRIP_MAX_AGENTS as the RUN TOTAL, so the only literal
# "agent(" left in such a script is the one call inside spawn() itself. This
# function windows each match's "body" from its start to the NEXT match (or
# end of file) to decide whether that one call carries a bare schema with no
# lightweight agentType -- without this alternative, a script with only one
# "agent(" match (inside the wrapper) would let that match's body window
# swallow every later spawn() call to end-of-file, so a lightweight
# agentType on an EARLIER call silently masked a heavier LATER one.
_AGENT_CALL_RE = re.compile(r"(?:agent|spawn)\s*\(")
_AGENT_TYPE_RE = re.compile(r"agentType\s*:\s*['\"](\w+)['\"]")
_EFFORT_MAX_RE = re.compile(r"effort\s*:\s*['\"]max['\"]")
# Anchored to the START of a source line (optional leading whitespace only),
# not merely present anywhere in the text. Closes a real bypass (Codex
# review, GRIP#8698): a Workflow script that embeds attacker- or
# prompt-controlled text containing this marker inside a quoted string or
# template literal used to disable the whole gate, because the old pattern
# matched the marker anywhere, including inside a string's characters. A
# string literal that is ITSELF a standalone source line starting with "//"
# is a known, narrower residual this line-anchor does not close -- full JS
# tokenisation would, and is not attempted here (disproportionate for a
# heuristic cost gate).
_OVERRIDE_RE = re.compile(r"^[ \t]*//\s*grip-budget-override:\s*(.+)$", re.MULTILINE)


def read_script(tool_input: dict) -> str:
    """The Workflow script's source text: inline, from a scriptPath, or from
    a saved workflow's conventional location by name.

    ``tool_input['script']`` wins when present and non-empty. Otherwise
    ``tool_input['scriptPath']`` is read from disk. Otherwise
    ``tool_input['name']`` is resolved to ``.claude/workflows/<name>.js``
    under the current working directory -- Claude Code's own saved-workflow
    convention (``commands/mine-patterns.md`` documents calling one exactly
    this way: ``Workflow({name: '<saved>', args: {...}})``; a gate that only
    read ``script``/``scriptPath`` denied every one of GRIP's own seven
    saved workflows with a misleading "no cap declared" reason -- Codex
    review, GRIP#8698). All three missing, or unreadable, returns the empty
    string -- never raises.
    """
    script = tool_input.get("script")
    if isinstance(script, str) and script:
        return script
    script_path = tool_input.get("scriptPath")
    if isinstance(script_path, str) and script_path:
        try:
            return Path(script_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    name = tool_input.get("name")
    if isinstance(name, str) and name:
        try:
            return (Path.cwd() / ".claude" / "workflows" / f"{name}.js").read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            return ""
    return ""


def _unreadable_script_reason(tool_input: dict, script: str) -> str | None:
    """Why no script text was obtained, or None when ``script`` is non-empty
    (the normal case) or no script-identifying field was given at all -- an
    inline call with nothing supplied genuinely has no cap to declare, which
    is the existing, correct "no cap declared" message, not a read failure.
    """
    if script:
        return None
    name = tool_input.get("name")
    if isinstance(name, str) and name:
        return f"could not read the saved workflow '{name}' (.claude/workflows/{name}.js)"
    script_path = tool_input.get("scriptPath")
    if isinstance(script_path, str) and script_path:
        return f"could not read the Workflow script at {script_path}"
    return None


def _parse_iso(ts: object) -> float | None:
    """Epoch seconds for an ISO-8601 timestamp, or None if it cannot be read."""
    if not isinstance(ts, str) or not ts:
        return None
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _rows_with_utilisation(path: Path, cutoff: float) -> list[dict]:
    """Every row at/after ``cutoff`` carrying either utilisation key (its
    value may be null -- callers decide what to do with that), tagged with
    its own parsed timestamp under ``_ts`` for the pickers below."""
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(row, dict):
                    continue
                if "seven_day_utilization" not in row and "five_hour_utilization" not in row:
                    continue
                ts = _parse_iso(row.get("ts"))
                if ts is None or ts < cutoff:
                    continue
                row["_ts"] = ts
                rows.append(row)
    except OSError:
        return []
    return rows


def _newest_numeric(rows: list[dict], field: str) -> float | None:
    """``field``'s value from the newest row AMONG rows where it is numeric.

    A newer row whose value for THIS field is null is skipped for this field
    rather than blanking out an earlier valid reading (Codex review,
    GRIP#8698): the previous version let a null-valued newest row replace a
    numeric high reading, silently skipping the ceiling it should have hit.
    Each metric is picked independently, so a row missing one field never
    costs the other.
    """
    valid = [r for r in rows if isinstance(r.get(field), (int, float))]
    if not valid:
        return None
    return max(valid, key=lambda r: r["_ts"]).get(field)


def _account_for_session(rows: list[dict], session_id: str | None) -> object:
    """The ``account_label`` of THIS session's own newest row, or None."""
    if not session_id:
        return None
    mine = [r for r in rows if r.get("session_id") == session_id]
    if not mine:
        return None
    return max(mine, key=lambda r: r["_ts"]).get("account_label")


def _newest_rows_account(rows: list[dict]) -> object:
    """Fallback account scope when no session matched: the single newest
    row's account, or None when nothing carries one."""
    labelled = [r for r in rows if r.get("account_label")]
    if not labelled:
        return None
    return max(labelled, key=lambda r: r["_ts"]).get("account_label")


def latest_utilisation(ledger_path: str | Path, within_minutes: int = 30,
                        session_id: str | None = None) -> dict | None:
    """The account's current utilisation, scoped to one account.

    Returns ``{"seven_day_utilization": ..., "five_hour_utilization": ...}``
    picked independently from the newest numeric row for EACH metric, or
    ``None`` when the ledger is missing, empty, or has nothing that recent.

    The ledger is shared across every pooled/unpooled account on this
    machine (``account_label`` per row). Without scoping, a concurrent
    session on a DIFFERENT account could write a newer row that masks this
    account's real ceiling, in either direction (Codex review, GRIP#8698).
    When ``session_id`` is given, the scope is resolved from THAT session's
    own newest row; otherwise (or when nothing matches it) it falls back to
    the single newest row's account, which is the best available signal with
    no session to anchor on -- still an improvement over the previous
    unscoped pick, because it is at least used CONSISTENTLY to pick both
    metrics together rather than letting an unrelated row's null value
    corrupt one of them.
    """
    path = Path(ledger_path)
    if not path.is_file():
        return None
    cutoff = time.time() - within_minutes * 60
    rows = _rows_with_utilisation(path, cutoff)
    if not rows:
        return None
    account = _account_for_session(rows, session_id) or _newest_rows_account(rows)
    scoped = [r for r in rows if account is None or r.get("account_label") == account]
    return {
        "seven_day_utilization": _newest_numeric(scoped, "seven_day_utilization"),
        "five_hour_utilization": _newest_numeric(scoped, "five_hour_utilization"),
    }


def _blank(m: "re.Match") -> str:
    """An emptied string literal, or the space a comment leaves (a line break when
    the comment spans lines, because JavaScript reads it as one)."""
    text = m.group(0)
    if m.group("str"):
        return text[0] * 2
    return "\n" if "\n" in text else " "


def _code(script: str) -> str:
    """The script as code only: comments removed and string literals emptied."""
    return _TOKEN_RE.sub(_blank, script)


def _without_strings(script: str) -> str:
    """The script with each string literal emptied and its comments kept, so an
    override line quoted inside a prompt string is not read as one (GRIP#9747)."""
    return _TOKEN_RE.sub(lambda m: m.group(0)[0] * 2 if m.group("str") else m.group(0), script)


def _statement_ends(rest: str) -> bool:
    """True when nothing after a cap's value can extend it: the next token is
    ; , } or the end, or a new line starts with a token that cannot continue an
    expression, where JavaScript ends the statement. An operator, a dot or a
    bracket on the next line continues the expression, so it is not an end."""
    m = _NEXT_TOKEN_RE.match(rest)
    newline, nxt = m.group(1), m.group(2)
    if nxt in ("", ";", ",", "}"):
        return True
    if not newline:
        return False
    word = _WORD_RE.match(rest, m.end() - 1)
    if word:
        return word.group(0) not in ("in", "instanceof")
    return nxt.isdigit() or nxt in "\"'{!~"


def _min_operands(code: str, start: int) -> "tuple[list[str], int] | None":
    """The operands of the Math.min( call whose arguments start at *start*, split
    at the call's own commas, and the index after its closing parenthesis; None
    when the call never closes."""
    depth, parts, begin = 0, [], start
    for i in range(start, len(code)):
        ch = code[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}" and depth:
            depth -= 1
        elif ch in ")]}":
            parts.append(code[begin:i])
            return parts, i + 1
        elif ch == "," and not depth:
            parts.append(code[begin:i])
            begin = i + 1
    return None


def _declared_bound(code: str) -> int | None:
    """The bound the FIRST declaration in *code* (comments removed) states: a
    literal that ends its statement, or the smallest bare integer operand of a
    Math.min that does (GRIP#8707); else None. A later declaration is never read
    in place of an unreadable first one: in an inner scope it bounds nothing the
    outer dispatch does (GRIP#9747)."""
    first = _CAP_DECLARED_RE.search(code)
    if not first:
        return None
    literal = _CAP_RE.match(code, first.start())
    if literal:
        return int(literal.group(1)) if _statement_ends(code[literal.end():]) else None
    computed = _CAP_MIN_RE.match(code, first.start())
    found = _min_operands(code, computed.end()) if computed else None
    if not found or not _statement_ends(code[found[1]:]):
        return None
    literals = [int(op.strip()) for op in found[0] if op.strip().isdigit()]
    return min(literals) if literals else None


def declared_cap(script: str) -> int | None:
    """The declared ``GRIP_MAX_AGENTS`` cap, or None unless it actually bounds
    dispatch.

    Three requirements, all AND'd, read from the code with its comments
    removed: the constant is declared with a readable bound (a literal, or
    Math.min with a literal operand); its name is used at least twice (declared
    once, read back at least once); and at least one of those other uses is a
    genuine bounding shape (a loop comparison, ``Math.min``, a length or a
    slice) rather than merely the identifier appearing in an unrelated
    expression.
    """
    code = _code(script)
    bound = _declared_bound(code)
    if bound is None:
        return None
    if code.count("GRIP_MAX_AGENTS") < 2:
        return None
    if not _CAP_BOUND_RE.search(code):
        return None
    return bound


def bloat_signals(script: str) -> dict:
    """Heuristic signals that this script defaults to the heavy agent shape.

    ``schema_no_agent_type`` counts ``agent(...)`` calls that declare a
    ``schema:`` (judgement-only, no file edits) whose ``agentType:`` is
    either absent or not one of the lightweight types (``Plan``/
    ``Explore``) -- so a call that writes ``agentType: 'general-purpose'``
    still counts, because that value is exactly as heavy as the harness
    default this signal exists to flag (Codex review, GRIP#8698: the
    previous version suppressed the warning on the KEY'S presence alone,
    regardless of its value). ``effort_max`` counts calls that set
    ``effort: 'max'``.
    """
    starts = [m.start() for m in _AGENT_CALL_RE.finditer(script)]
    schema_no_type = 0
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(script)
        body = script[start:end]
        if "schema:" not in body:
            continue
        type_match = _AGENT_TYPE_RE.search(body)
        if type_match is None or type_match.group(1) not in LIGHT_AGENT_TYPES:
            schema_no_type += 1
    return {
        "schema_no_agent_type": schema_no_type,
        "effort_max": len(_EFFORT_MAX_RE.findall(script)),
    }


def override(script: str, env: dict) -> str | None:
    """The active override's reason, or None when no override applies."""
    if str(env.get("GRIP_WORKFLOW_BUDGET_GATE", "")) == "0":
        return "env GRIP_WORKFLOW_BUDGET_GATE=0"
    match = _OVERRIDE_RE.search(_without_strings(script))
    if match:
        reason = match.group(1).strip()
        if len(reason) >= OVERRIDE_MIN_REASON_LEN:
            return reason
    return None


def _estimate(cap: int | None) -> dict:
    if cap is None:
        return {"agents": None, "tokens_heavy": None, "tokens_light": None,
                "line": "agent count not declared"}
    heavy = cap * TOKENS_PER_AGENT_HEAVY
    light = cap * TOKENS_PER_AGENT_LIGHT
    return {
        "agents": cap,
        "tokens_heavy": heavy,
        "tokens_light": light,
        "line": f"{cap} agents x ~120K tokens (~60K with Plan/Explore) = ~{heavy:,} tokens",
    }


def _cap_suffix(cap: int | None) -> str:
    """Trailing note naming the declared cap, for a reason that does not
    already state it (AGORA suggestion, GRIP#8710): a reader should never
    have to cross-reference the verdict's separate ``cap`` field to see
    what the script declared."""
    return f" (declared cap: {cap})" if cap is not None else " (no cap declared)"


def _with_cap_suffix(reasons: list[str], cap: int | None) -> list[str]:
    """``reasons`` with the cap suffix appended, skipping any line that
    already mentions the cap (the ``_deny_for_cap`` messages do) so the two
    never double up."""
    suffix = _cap_suffix(cap)
    return [r if "cap" in r.lower() else r + suffix for r in reasons]


def _deny_for_ceiling(weekly: object, five_hour: object) -> str | None:
    """A reason to refuse outright, or None when neither ceiling is crossed."""
    if isinstance(weekly, (int, float)) and weekly >= DENY_WEEKLY:
        return (f"weekly Claude utilisation is {weekly:.0%}, at or past the {DENY_WEEKLY:.0%} "
                "ceiling -- wait for the window to reset; never engage the paid key "
                "(GRIP_CLAUDE_ALLOW_PAID) to push a fan-out through it")
    if isinstance(five_hour, (int, float)) and five_hour >= DENY_FIVE_HOUR:
        return (f"five-hour Claude utilisation is {five_hour:.0%}, at or past the "
                f"{DENY_FIVE_HOUR:.0%} ceiling -- wait for the window to reset; never engage "
                "the paid key (GRIP_CLAUDE_ALLOW_PAID) to push a fan-out through it")
    return None


def _no_cap_reason(code: str) -> str:
    """Why no cap could be read from *code*. GRIP#8707: a declared cap whose value
    is computed used to be told it was never declared."""
    if _CAP_DECLARED_RE.search(code) and _declared_bound(code) is None:
        return ("GRIP_MAX_AGENTS is declared, but its value is computed and its bound "
                "cannot be read: write a whole number, or Math.min(<n>, ...) with a "
                "whole-number operand (rules/fan-out-spends-the-weekly-limit.md)")
    return ("no GRIP_MAX_AGENTS cap is declared and used to bound dispatch in this "
            "Workflow script (rules/fan-out-spends-the-weekly-limit.md)")


def _deny_for_cap(cap: int | None, script: str) -> str | None:
    """A reason to refuse on the declared cap, or None when it is sound."""
    if cap is None:
        return _no_cap_reason(_code(script))
    if cap > MAX_AGENT_CAP:
        return f"declared agent cap {cap} exceeds the ceiling of {MAX_AGENT_CAP}"
    return None


def _warnings(script: str, weekly: object) -> list[str]:
    """Non-blocking reasons: a mid-range weekly figure, or shape bloat."""
    reasons = []
    if isinstance(weekly, (int, float)) and weekly >= WARN_WEEKLY:
        reasons.append(f"weekly Claude utilisation is {weekly:.0%}, at or past the "
                        f"{WARN_WEEKLY:.0%} warn level")
    bloat = bloat_signals(script)
    if bloat["schema_no_agent_type"]:
        reasons.append(
            f"{bloat['schema_no_agent_type']} agent() call(s) declare a schema with no "
            "lightweight agentType (defaults to, or stays on, the heavy agent type; "
            "prefer Plan or Explore)")
    if bloat["effort_max"]:
        reasons.append(f"{bloat['effort_max']} call(s) set effort: 'max'")
    return reasons


def decide(tool_input: dict, utilisation: dict | None, env: dict) -> dict:
    """The gate's one verdict: allow, warn, or deny a Workflow launch.

    Returns ``{"decision": ..., "reasons": [...], "estimate": {...}, "cap": ...}``.
    Never raises -- any internal error is reported as an allow, because a
    defect in this module must fail open, not block a launch it was never
    meant to judge.
    """
    try:
        script = read_script(tool_input)
        unreadable = _unreadable_script_reason(tool_input, script)
        cap = declared_cap(script)
        estimate = _estimate(cap)

        override_reason = override(script, env)
        if override_reason:
            return {"decision": "allow", "reasons": [f"override: {override_reason}"],
                    "estimate": estimate, "cap": cap}

        weekly = (utilisation or {}).get("seven_day_utilization")
        five_hour = (utilisation or {}).get("five_hour_utilization")

        reason = _deny_for_ceiling(weekly, five_hour) or unreadable or _deny_for_cap(cap, script)
        if reason:
            return {"decision": "deny", "cap": cap, "estimate": estimate,
                    "reasons": _with_cap_suffix([reason], cap)}

        warn_reasons = _with_cap_suffix(_warnings(script, weekly), cap)
        if warn_reasons:
            warn_reasons.append(estimate["line"])
            return {"decision": "warn", "cap": cap, "estimate": estimate, "reasons": warn_reasons}

        return {"decision": "allow", "cap": cap, "estimate": estimate, "reasons": []}
    except Exception as exc:  # fail open: a bug here must never block a launch
        return {"decision": "allow", "reasons": [
            f"workflow_budget_gate internal error, failing open: "
            f"{exc.__class__.__name__}: {exc}"], "estimate": {}, "cap": None}
