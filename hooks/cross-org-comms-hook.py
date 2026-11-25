#!/usr/bin/env python3
"""Gate R: Cross-Org Communications Content Gate.

Blocks Bash tool calls that send Slack messages containing stakeholder
names from a different org than the recipient. Fails open on any error.

Response shapes come from lib/hook_response.py — the old top-level
{"decision": "allow"|"deny"} + sys.exit(1) combination was invalid
vocabulary AND a non-blocking exit code, so this gate's deny never
actually enforced until the 2026-07-08 fix.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.environ.get("HOME", ""), ".claude", "lib"))

REGISTRY_PATH = Path.home() / ".claude" / "data" / "stakeholder-registry.json"


def load_registry():
    return json.loads(REGISTRY_PATH.read_text())


def extract_slack_payload(command: str) -> dict | None:
    """Extract JSON payload from a curl chat.postMessage command."""
    match = re.search(r"-d\s+'([^']+)'", command) or re.search(r'-d\s+"([^"]+)"', command)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def resolve_recipient_org(channel_id: str, registry: dict) -> tuple[str | None, str | None]:
    """Returns (person, org) or (None, None) if channel unknown."""
    for ch_id, info in {**registry.get("dm_channels", {}), **registry.get("channels", {})}.items():
        if ch_id == channel_id:
            return info.get("person"), info.get("org")
    return None, None


def _overlay_person_names() -> dict[str, str]:
    """Private person names from the redaction-shield overlay, lower(alias) -> canonical.

    lib/redaction_shield.py already resolves and loads the operator's private
    overlay (config/guardian.local.json, or $GRIP_REDACTION_OVERLAY) -- reused
    here rather than a second loader (rules/personal-matters-stay-private.md).
    An overlay entry carries no org of its own, so a name in it is foreign to
    every recipient org by construction: that is the whole reason a private
    name belongs in the overlay rather than in this tracked registry with an
    org attached. Returns {} wherever no overlay is configured, or it cannot
    be read, so a misconfigured OPTIONAL overlay never weakens the registry
    check this gate already does on its own.
    """
    try:
        from redaction_shield import load_overlay
        entries = load_overlay().entries
    except Exception as exc:  # the registry check still runs; never lose the overlay silently
        print(f"[gate-r] private overlay not read ({type(exc).__name__}); "
              "checking the tracked registry only", file=sys.stderr)
        return {}
    return {
        candidate.lower(): entry.canonical
        for entry in entries
        if entry.cls == "person"
        for candidate in ([entry.canonical] if entry.compile_canonical else [])
        + [alias for alias, _case_sensitive in entry.aliases]
        if candidate
    }


def find_cross_org_names(text: str, recipient_org: str, registry: dict) -> list[str]:
    """Return stakeholder names/aliases from a different org found in text.

    Single pass over the text: all foreign names/aliases compile into one
    alternation, and matches map back to canonical names via a lookup dict.
    """
    alias_to_name: dict[str, str] = {
        candidate.lower(): name
        for name, info in registry.get("stakeholders", {}).items()
        if info.get("org") != recipient_org
        for candidate in [name, *info.get("aliases", [])]
        if candidate
    }
    # Private overlay names carry no org, so they are foreign to every
    # recipient and are added unconditionally; setdefault leaves a
    # tracked-registry spelling's own org-aware resolution in place where the
    # same spelling is already present.
    for alias, canonical in _overlay_person_names().items():
        alias_to_name.setdefault(alias, canonical)
    if not alias_to_name:
        return []
    pattern = re.compile(
        r'\b(' + '|'.join(re.escape(c) for c in alias_to_name) + r')\b',
        re.IGNORECASE,
    )
    found: list[str] = []
    for match in pattern.finditer(text):
        name = alias_to_name[match.group(1).lower()]
        if name not in found:
            found.append(name)
    return found


def _adjudicate(payload: dict, registry: dict) -> None:
    """Emit the allow/deny verdict for one Slack payload."""
    from hook_response import emit, pretool_decision

    channel_id = payload.get("channel", "")
    text = payload.get("text", "")

    person, recipient_org = resolve_recipient_org(channel_id, registry)
    if not recipient_org:
        # Unknown channel — warn but allow
        emit(pretool_decision("allow", (
            f"[Gate R] Channel {channel_id} not in stakeholder registry. "
            "Verify recipient org manually."
        )))
        return

    cross_org = find_cross_org_names(text, recipient_org, registry)
    if cross_org:
        names_str = ", ".join(cross_org)
        emit(pretool_decision("deny", (
            f"[Gate R: CROSS-ORG CONTENT] Message to {person} ({recipient_org}) "
            f"contains references to: {names_str}. "
            "This may leak stakeholder information across org boundaries. "
            "Ask whether this disclosure is intentional before sending."
        )))


def main():
    try:
        data = json.loads(sys.stdin.read())
        if data.get("tool_name") != "Bash":
            sys.exit(0)

        command = data.get("tool_input", {}).get("command", "")
        if "chat.postMessage" not in command:
            sys.exit(0)

        payload = extract_slack_payload(command)
        if not payload:
            sys.exit(0)

        _adjudicate(payload, load_registry())

    except Exception:
        # Fail open — never block on hook errors
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
