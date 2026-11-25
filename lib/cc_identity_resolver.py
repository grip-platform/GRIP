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
            match = re.match(r'^(\w+):\s*"?([^"]*)"?\s*$', stripped)
            if match:
                key = match.group(1)
                value = match.group(2).strip()
                if value:
                    result[key] = value

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
