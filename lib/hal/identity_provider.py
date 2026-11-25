"""HAL Identity Provider — user/org identity without any external harness.

Reads identity from facts/identity.md (GRIP native) with zero external
dependency. No environment variables from any external tool required.

Usage:
    from lib.hal.identity_provider import get_identity

    identity = get_identity()
    print(identity.name, identity.signature, identity.role)
"""
import os
import re
from typing import Optional

from lib.hal.home import HAL_HOME as _HAL_HOME
_IDENTITY_PATH = os.path.join(_HAL_HOME, "facts", "identity.md")
_TEAM_PATH = os.path.join(_HAL_HOME, "facts", "team.md")
# key: value, optionally quoted, optionally followed by a "# comment".
_YAML_KV_RE = re.compile(r'^(\w+):\s*"?([^"#]*?)"?\s*(?:#.*)?$')


class Identity:
    """User identity resolved from GRIP's own sources."""

    def __init__(
        self,
        name: str = "",
        signature: str = "",
        role: str = "",
        mode: str = "solo",
    ):
        self.name = name
        self.signature = signature
        self.role = role
        self.mode = mode

    def to_dict(self) -> dict:
        return {
            "name": self.name, "signature": self.signature,
            "role": self.role, "mode": self.mode,
        }


def get_identity(identity_path: Optional[str] = None) -> Identity:
    """Resolve identity from facts/identity.md."""
    path = identity_path or _IDENTITY_PATH
    raw = _read_file(path)
    if not raw:
        return Identity()
    return _parse_identity(raw)


def _parse_identity(raw: str) -> Identity:
    """Parse identity fields from facts/identity.md."""
    fields: dict[str, str] = {}
    for line in _identity_lines(raw):
        match = _YAML_KV_RE.match(line)
        if match:
            fields[match.group(1)] = match.group(2)

    return Identity(
        name=fields.get("name", ""),
        signature=fields.get("signature", ""),
        role=fields.get("role", ""),
        mode=fields.get("mode", "solo"),
    )


def _identity_lines(raw: str) -> list[str]:
    """The stripped lines that hold the identity fields.

    facts/identity.md.template keeps them in its first ```yaml block, ahead of
    an example block that must not be read. The /activate-grip wizard
    (lib/activate_grip_state.build_identity_md, since 737548a75) writes them
    as bare lines with no fence, so a file without a fence is read whole.
    """
    lines = [line.strip() for line in raw.split("\n")]
    if "```yaml" not in lines:
        return lines
    block = lines[lines.index("```yaml") + 1:]
    return block[:block.index("```")] if "```" in block else block


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return ""
