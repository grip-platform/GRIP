"""CC Identity Env Var Resolver (#1151).

Reads Claude Code organisation and account environment variables to
complement the manual identity configuration in facts/identity.md.
Falls back gracefully when env vars are unavailable.

Usage:
    from lib.cc_identity_resolver import resolve_identity

    identity = resolve_identity()
    # {"org_id": "org-xxx", "account_id": "acc-yyy", "name": "Laurie", ...}
"""
from __future__ import annotations

import os
import re
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
IDENTITY_FILE = CLAUDE_DIR / "facts" / "identity.md"

# Known CC env vars for identity (checked in order of specificity)
ENV_VARS = {
    "org_id": [
        "CLAUDE_CODE_ORGANIZATION_UUID",  # CC 2.1.51+ — the name CC actually sets
        "CLAUDE_CODE_ORG_UUID",           # never shipped; kept as a legacy fallback
        "CLAUDE_CODE_ORG_ID",
        "ANTHROPIC_ORG_ID",
        "CLAUDE_ORG_ID",
    ],
    "account_id": [
        "CLAUDE_CODE_ACCOUNT_UUID",   # CC 2.1.51+
        "CLAUDE_CODE_ACCOUNT_ID",
        "ANTHROPIC_ACCOUNT_ID",
        "CLAUDE_ACCOUNT_ID",
    ],
    "account_email": [
        "CLAUDE_CODE_USER_EMAIL",     # CC 2.1.51+
        "CLAUDE_CODE_ACCOUNT_EMAIL",
        "ANTHROPIC_ACCOUNT_EMAIL",
    ],
    "team_id": [
        "CLAUDE_CODE_TEAM_ID",
        "ANTHROPIC_TEAM_ID",
    ],
}


def _read_env_var(candidates: list[str]) -> str | None:
    """Return the first non-empty env var value from the candidate list."""
    for var in candidates:
        val = os.environ.get(var, "").strip()
        if val:
            return val
    return None


# A fully quoted value first, so a "#" inside quotes survives; then a bare or
# loosely quoted value. Either may carry a trailing "# comment", as every line of
# facts/identity.md.template does (GRIP#10006: the old pattern rejected them all).
# As in YAML, "#" starts a comment only after whitespace, so `tag: a#b` keeps "a#b".
_QUOTED_FIELD = re.compile(r'^(\w+):\s*"([^"]*)"(?:\s+#.*)?\s*$')
_LOOSE_FIELD = re.compile(r'^(\w+):\s*"?([^"]*?)"?(?:\s+#.*)?\s*$')


def _field(line: str) -> tuple[str, str] | None:
    """``(key, value)`` for a ``key: value`` line, or None when the line is not one.

    As in YAML, an unquoted value that starts with "#" is a comment, so
    `signature:   # Your command prefix (optional)` is empty, never that text.
    """
    quoted = _QUOTED_FIELD.match(line)
    if quoted:
        return quoted.group(1), quoted.group(2).strip()
    loose = _LOOSE_FIELD.match(line)
    if not loose:
        return None
    value = loose.group(2).strip()
    return loose.group(1), "" if value.startswith("#") else value


def _parse_identity_file(path: Path | None = None) -> dict:
    """Parse facts/identity.md for manual identity configuration.

    Extracts YAML-like fields from the fenced code block.
    """
    if path is None:
        path = IDENTITY_FILE
    result = {}
    if not path.exists():
        return result

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return result

    in_yaml = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "```yaml":
            in_yaml = True
            continue
        if stripped == "```" and in_yaml:
            break
        if in_yaml:
            field = _field(stripped)
            if field and field[1]:
                result[field[0]] = field[1]

    return result


def resolve_identity(identity_path: Path | None = None) -> dict:
    """Resolve identity by merging env vars with manual config.

    Priority:
    1. CC env vars (most authoritative — set by the SDK)
    2. facts/identity.md fields (manual fallback)

    Returns a dict with all resolved fields. Missing fields are omitted.
    """
    # Start with manual config as the base
    identity = _parse_identity_file(identity_path)

    # Layer env vars on top (higher priority)
    for field, candidates in ENV_VARS.items():
        val = _read_env_var(candidates)
        if val is not None:
            identity[field] = val

    # Add source metadata
    sources = []
    for field, candidates in ENV_VARS.items():
        val = _read_env_var(candidates)
        if val is not None:
            sources.append(f"{field}:env")
    if identity_path is None:
        id_path = IDENTITY_FILE
    else:
        id_path = identity_path
    if id_path.exists():
        sources.append("identity.md")
    identity["_sources"] = sources

    return identity


def get_org_id() -> str | None:
    """Convenience: return the org ID or None."""
    return resolve_identity().get("org_id")


def get_account_id() -> str | None:
    """Convenience: return the account ID or None."""
    return resolve_identity().get("account_id")
