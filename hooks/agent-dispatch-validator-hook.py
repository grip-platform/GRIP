#!/usr/bin/env python3
"""Agent dispatch validator — detect premature envelope close.

Fires on TaskCompleted (the canonical event for Task/Agent tool completion).
Inspects the agent's result text. Flags results that look like intent-
declaration without completion-evidence — the hallmark of agent runtime
terminating after a no-tool-call planning turn.

Detection logic (mechanical, not LLM-driven):

1. INTENT phrases — present-progressive declarations of about-to-do work.
   Examples: "Now I'll build", "Starting with the Python modules",
   "I will implement", "Let me begin".

2. COMPLETION evidence — concrete artefacts that prove work happened.
   Examples: branch names (feat/..., fix/...), "X passed", "READY FOR PR",
   "files changed: N", numeric counts, commit shas, "pushed to origin".

Verdict: if INTENT detected AND NO completion evidence → DISPATCH_INCOMPLETE.
Emits a [DISPATCH_INCOMPLETE] signal to stderr (visible to main session per
Rule 10 PARAMOUNT). Never blocks — main session decides recovery.

Origin: 4th recurrence of envelope-close failure 2026-05-04. V>> escalation
during /auto wave-mode W2 dispatch on issue #2521. Three prior failures
in /evo polling-loop documented in rules/evo-dispatch-protocol.md.

Telemetry: appends one JSONL record per fire to data/dispatch-failures.jsonl.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

CLAUDE_DIR = os.path.join(os.environ.get("HOME", "/tmp"), ".claude")
TELEMETRY = os.path.join(CLAUDE_DIR, "data", "dispatch-failures.jsonl")

# Intent-declaration patterns — present-progressive about-to-do phrasing.
# Word-boundary anchored so they don't match inside other words.
_INTENT_PATTERNS = [
    r"\b(?:I|I'll|I will|Let me|Now I|Now,? I)\b[^.]{0,80}\b(?:now|next|begin|begin to|start|starting|continue|build|building|implement|implementing|add|adding|fix|fixing|create|creating|write|writing|proceed|proceeding|go|move on)\b",
    r"\bStarting (?:with|by|on|the)\b",
    r"\bI'?m (?:going to|about to|ready to)\b",
    r"\b(?:Next|First|Then),? (?:I'?ll|I will|let me)\b",
    r"\bI have all the context\b",
    r"\bI'll (?:now|next|then|first|begin)\b",
    # Passive-waiting patterns — "waiting for X to complete" is intent without action.
    # Origin: 2026-05-04 /evo bg agent failure ("Acknowledged. Waiting for evo to complete.")
    r"\bWaiting for\b[^.]{0,80}\b(?:to (?:complete|finish|return|come back)|result)\b",
    r"\b(?:I'?ll|I will) wait\b",
    # Pure acknowledgement without substance — agent ack'd but did nothing.
    r"^\s*(?:Acknowledged|Got it|OK|Understood|Sure|Will do)\.?\s*$",
]

# Completion-evidence patterns — concrete proof the agent finished its job.
_COMPLETION_PATTERNS = [
    r"\b(?:feat|fix|chore|docs|test|refactor|perf|style)/[a-z0-9_/-]+\b",  # branch names
    r"\b\d+\s+(?:passed|failed|skipped|tests?\s+pass)\b",                   # test counts
    r"READY FOR PR\b",                                                       # explicit
    r"\bbranch (?:pushed|created|merged)\b",
    r"\b(?:commit|sha)[:\s]+[a-f0-9]{7,40}\b",                              # commit sha
    r"\bfiles changed[:\s]+\d+",                                             # diff stat
    r"\bpushed to (?:origin|remote)\b",
    r"\bPR #\d+\b",                                                          # PR number
    r"\bnone\b.*\bissues\b|\bno (?:issues|errors|failures|blockers)\b",     # explicit clean
]

_INTENT_RE = re.compile("|".join(_INTENT_PATTERNS), re.IGNORECASE)
_COMPLETION_RE = re.compile("|".join(_COMPLETION_PATTERNS), re.IGNORECASE)


def _parse_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return {}


_DEBUG_LOG = os.path.join(CLAUDE_DIR, "data", "dispatch-failures-debug.jsonl")
_DEBUG_FIRES_KEPT = 20


def _debug_dump(hook_input: dict) -> None:
    """One-shot schema-discovery dump. Caps at _DEBUG_FIRES_KEPT entries.

    Records the top-level keys + a 400-char prefix of each top-level string
    value, so we can verify the actual SubagentStop / TaskCompleted payload
    schema on the live runtime. Self-throttling — stops writing after N fires.
    """
    try:
        if os.path.exists(_DEBUG_LOG):
            with open(_DEBUG_LOG) as fh:
                if sum(1 for _ in fh) >= _DEBUG_FIRES_KEPT:
                    return
        os.makedirs(os.path.dirname(_DEBUG_LOG), exist_ok=True)
        snap = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "top_level_keys": sorted(hook_input.keys()) if isinstance(hook_input, dict) else [],
            "string_previews": {
                k: (v[:400] if isinstance(v, str) else f"<{type(v).__name__}>")
                for k, v in (hook_input.items() if isinstance(hook_input, dict) else [])
            },
        }
        with open(_DEBUG_LOG, "a") as fh:
            fh.write(json.dumps(snap, separators=(",", ":")) + "\n")
    except OSError:
        pass


# Canonical SubagentStop / TaskCompleted paths (priority order).
_CANONICAL_PATHS = (
    ("result",),                     # SubagentStop top-level result
    ("output",),                     # alt top-level
    ("text",),                       # alt top-level
    ("summary",),                    # alt top-level
    ("agent_result",),               # legacy probe
    ("tool_response", "result"),     # PostToolUse-shaped wrapper
    ("tool_response", "content"),
    ("tool_response", "summary"),
    ("tool_response", "output"),
    ("response", "result"),
    ("response", "content"),
    ("payload", "result"),
    ("payload", "text"),
    ("data", "result"),
    ("data", "text"),
)

_FALLBACK_MIN_LEN = 20

# Metadata keys whose values are NOT agent result text — excluded from the
# last-ditch fallback so a bare agent_id UUID can't be mislabelled as a result.
# Origin 2026-05-29 (context-gate sub-agent investigation): the fallback grabbed
# agent_id on a canonical-path miss, tagging 417/545 telemetry rows
# SUSPICIOUSLY_BRIEF on a bare UUID — pure noise that buried real signal.
_METADATA_KEYS = frozenset({
    "agent_id", "agent_type", "session_id", "session", "transcript_path",
    "tool_name", "hook_event_name", "cwd", "permission_mode", "uuid",
})
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _walk(d: dict, path: tuple) -> object:
    """Traverse a dict via a tuple of keys; return None on any miss."""
    cur: object = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _stringify(value: object) -> str:
    """Coerce a probe result into a non-empty string or ""."""
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, list):
        joined = "\n".join(str(c) for c in value if c)
        if joined.strip():
            return joined
    return ""


def _extract_result_text(hook_input: dict) -> str:
    """Pull the agent's result text from any plausible payload shape.

    Origin: 2026-05-11 council finding. Prior probes returned empty against
    the actual SubagentStop payload schema. This version probes a wider
    canonical surface, then falls back to any substantive top-level string.
    """
    if not isinstance(hook_input, dict):
        return ""
    for path in _CANONICAL_PATHS:
        text = _stringify(_walk(hook_input, path))
        if text:
            return text
    for key, value in hook_input.items():
        if key in _METADATA_KEYS:
            continue
        text = _stringify(value)
        stripped = text.strip()
        if len(stripped) >= _FALLBACK_MIN_LEN and not _UUID_RE.match(stripped):
            return text
    return ""


def _classify(text: str) -> dict:
    """Return {intent: bool, completion: bool, verdict: str}.

    Verdicts:
    - TOO_SHORT — text < 20 chars, can't classify
    - DISPATCH_INCOMPLETE — intent without completion (action declaration with no proof)
    - SUSPICIOUSLY_BRIEF — short text (< 120 chars) with no completion evidence and no
      intent text either, e.g. "Acknowledged." or "Done." Likely premature termination.
    - AMBIGUOUS — neither intent nor completion, but text is substantive enough to
      possibly be a legitimate non-deliverable response (e.g. "Cannot find the file
      you mentioned, please clarify which one.")
    - OK — completion evidence present (with or without intent prose).
    """
    if not text or len(text.strip()) < 20:
        return {"intent": False, "completion": False, "verdict": "TOO_SHORT"}

    stripped = text.strip()
    has_intent = bool(_INTENT_RE.search(text))
    has_completion = bool(_COMPLETION_RE.search(text))

    if has_intent and not has_completion:
        verdict = "DISPATCH_INCOMPLETE"
    elif has_completion:
        verdict = "OK"
    elif len(stripped) < 120:
        # Short response with no completion evidence — almost certainly premature.
        # The /evo "Acknowledged. Waiting for evo to complete." shape lands here even
        # if the regex didn't match an intent verb.
        verdict = "SUSPICIOUSLY_BRIEF"
    else:
        verdict = "AMBIGUOUS"

    return {"intent": has_intent, "completion": has_completion, "verdict": verdict}


def _record(verdict: dict, agent_id: str, agent_type: str, snippet: str) -> None:
    try:
        os.makedirs(os.path.dirname(TELEMETRY), exist_ok=True)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "agent_id": agent_id[:12] if agent_id else "",
            "agent_type": agent_type,
            "verdict": verdict["verdict"],
            "intent": verdict["intent"],
            "completion": verdict["completion"],
            "snippet": snippet[:200],
            "session": os.environ.get("CLAUDE_SESSION_ID", "")[:12],
        }
        with open(TELEMETRY, "a") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Canonical verification layer (GRIP verify-canonical, 2026-05-16; grounded
# design-check #1866). Advisory-only, fail-safe, NEVER blocks. Verifies a
# decision-gating "merged"/"done" assertion against canonical reality, not
# the agent's own summary. `merged` uses a FRESH `gh pr view` API call
# (stale-ref-immune by construction). `done` vs origin/main is gated behind
# a stale-ref-age guard: a not-provably-fresh local ref yields UNVERIFIED,
# never CONFIRMED/REFUTED — a stale local ref must not launder into false
# authority (the Devil's-Advocate mitigation). Soundness invariant (in this
# module, not per-callsite): CONFIRMED is reachable ONLY from a successful
# canonical fetch+compare — never from absence-of-contradiction or a label.
# lib/verify_canonical.py shim + /verify-canonical playbook deferred (YAGNI:
# N=2 independent callsites first — llm-augmentation-pattern.md discipline).
# ---------------------------------------------------------------------------

import subprocess  # noqa: E402  (stdlib; canonical layer only)

_DEFAULT_REPO = os.environ.get("GRIP_CANONICAL_REPO", "grip-platform/GRIP")
_REF_FRESH_MAX_AGE_S = 3600  # origin/main local ref older than 1h ⇒ not fresh

_MERGED_CLAIM_RE = re.compile(
    r"\bPR\s*#(\d{1,6})\b[^.\n]{0,40}\bmerged\b"
    r"|\bmerged\b[^.\n]{0,40}\bPR\s*#(\d{1,6})\b"
    r"|\b#(\d{1,6})\b[^.\n]{0,20}\bMERGED\b",
    re.IGNORECASE,
)
# Deliberately narrow: only an explicit `origin/main:<path>` mention, so
# false-extraction is ~zero (soundness over coverage).
_DONE_PATH_RE = re.compile(r"\borigin/main:([A-Za-z0-9._/-]{3,200})")


def _canonical_runner(args: list, timeout: float = 8.0) -> tuple:
    """Default DIP-injected runner → (rc, stdout, stderr). Never raises.

    Tests patch HOOK._canonical_runner with a zero-network fake.
    """
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as exc:  # noqa: BLE001 — fail-safe boundary
        return 1, "", repr(exc)


def _origin_main_fresh(claude_dir: str = CLAUDE_DIR) -> bool:
    """True only if the local origin/main tracking ref is provably recent.

    Unknown / older than _REF_FRESH_MAX_AGE_S ⇒ False (⇒ UNVERIFIED). The
    stale-ref guard: a never-fetched session must not get a confidently
    wrong CONFIRMED/REFUTED from a stale local ref.
    """
    for rel in ("refs/remotes/origin/main", "FETCH_HEAD"):
        try:
            if time.time() - os.path.getmtime(
                os.path.join(claude_dir, ".git", rel)
            ) <= _REF_FRESH_MAX_AGE_S:
                return True
        except OSError:
            continue
    return False


def _extract_canonical_claims(text: str) -> list:
    claims: list = []
    seen: set = set()
    for m in _MERGED_CLAIM_RE.finditer(text):
        pr = m.group(1) or m.group(2) or m.group(3)
        key = ("merged", pr)
        if key not in seen:
            seen.add(key)
            claims.append({"kind": "merged", "target": pr})
    for m in _DONE_PATH_RE.finditer(text):
        key = ("done", m.group(1))
        if key not in seen:
            seen.add(key)
            claims.append({"kind": "done", "target": m.group(1)})
    return claims


def _check_merged(pr: str, runner) -> dict:
    """Fresh `gh pr view` API — stale-ref-immune by construction."""
    base = {"kind": "merged", "target": f"#{pr}", "source": f"gh pr view {pr}"}
    rc, out, err = runner(
        ["gh", "pr", "view", pr, "--repo", _DEFAULT_REPO, "--json", "state"]
    )
    if rc != 0 or not out.strip():
        return {**base, "status": "UNVERIFIED", "evidence": (err or "no output")[:120]}
    try:
        state = json.loads(out).get("state", "")
    except (json.JSONDecodeError, AttributeError):
        return {**base, "status": "UNVERIFIED", "evidence": "unparseable gh json"}
    if state == "MERGED":
        return {**base, "status": "CONFIRMED", "evidence": "state=MERGED"}
    if state in ("OPEN", "CLOSED"):
        return {**base, "status": "REFUTED", "evidence": f"state={state}"}
    return {**base, "status": "UNVERIFIED", "evidence": f"state={state or '?'}"}


def _check_done_path(path: str, runner) -> dict:
    base = {"kind": "done", "target": path,
            "source": f"git show origin/main:{path}"}
    if not _origin_main_fresh():
        return {**base, "status": "UNVERIFIED",
                "evidence": "origin/main ref stale/unverifiable — not trusting local"}
    rc, _out, err = runner(
        ["git", "-C", CLAUDE_DIR, "cat-file", "-e", f"origin/main:{path}"]
    )
    if rc == 0:
        return {**base, "status": "CONFIRMED", "evidence": "present on fresh origin/main"}
    if err == "" or "Not a valid object name" in (err or "") or "exists on disk" in (err or ""):
        return {**base, "status": "REFUTED", "evidence": "absent on fresh origin/main"}
    return {**base, "status": "UNVERIFIED", "evidence": (err or "git error")[:120]}


def _canonical_verdicts(text: str, runner=None) -> list:
    """Fail-safe: any error ⇒ [] (behave exactly as the pre-canonical hook)."""
    run = runner or _canonical_runner
    try:
        out: list = []
        for c in _extract_canonical_claims(text):
            out.append(
                _check_merged(c["target"], run) if c["kind"] == "merged"
                else _check_done_path(c["target"], run)
            )
        return out
    except Exception:  # noqa: BLE001 — absolute fail-safe; never break dispatch
        return []


def _record_canonical(canon: list, agent_id: str, agent_type: str) -> None:
    try:
        os.makedirs(os.path.dirname(TELEMETRY), exist_ok=True)
        with open(TELEMETRY, "a") as fh:
            fh.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "agent_id": (agent_id or "")[:12],
                "agent_type": agent_type,
                "canonical": [
                    {"k": c["kind"], "t": c["target"], "s": c["status"]}
                    for c in canon
                ],
                "session": os.environ.get("CLAUDE_SESSION_ID", "")[:12],
            }, separators=(",", ":")) + "\n")
    except OSError:
        pass


def main() -> int:
    hook_input = _parse_stdin()
    _debug_dump(hook_input)  # one-shot schema discovery (auto-throttles)
    text = _extract_result_text(hook_input)
    verdict = _classify(text)

    agent_id = hook_input.get("agent_id", "")
    agent_type = hook_input.get("agent_type", "unknown")

    _record(verdict, agent_id, agent_type, text)

    if verdict["verdict"] == "DISPATCH_INCOMPLETE":
        sys.stderr.write(
            "[DISPATCH_INCOMPLETE] Agent result shows intent-declaration "
            "without completion evidence. Verify worktree state before "
            "trusting the result. See rules/agent-dispatch-protocol.md.\n"
        )
    elif verdict["verdict"] == "SUSPICIOUSLY_BRIEF":
        sys.stderr.write(
            "[DISPATCH_SUSPICIOUSLY_BRIEF] Agent result is short and lacks "
            "completion evidence (no branch/test count/file list/PR). "
            "Verify worktree state before trusting the result. "
            "See rules/agent-dispatch-protocol.md.\n"
        )

    # Canonical verification — advisory, fail-safe, NEVER blocks dispatch.
    try:
        canon = _canonical_verdicts(text)
        for c in (c for c in canon if c["status"] == "REFUTED"):
            sys.stderr.write(
                f"[CANONICAL_REFUTED] Agent asserted {c['kind']} {c['target']} "
                f"but {c['source']} says {c['evidence']}. Do NOT trust the claim "
                f"— verify origin/main. See rules/verify-canonical-not-claimed.md.\n"
            )
        if not any(c["status"] == "REFUTED" for c in canon):
            for c in (c for c in canon if c["status"] == "UNVERIFIED"):
                sys.stderr.write(
                    f"[CANONICAL_UNVERIFIED] Could not verify {c['kind']} "
                    f"{c['target']} against canonical ({c['evidence']}). Verify "
                    f"origin/main before trusting. See rules/verify-canonical-not-claimed.md.\n"
                )
        if canon:
            _record_canonical(canon, agent_id, agent_type)
    except Exception:  # noqa: BLE001 — canonical layer must never break dispatch
        pass

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
