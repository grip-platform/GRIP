#!/usr/bin/env python3
"""
Identity Resolver — Cross-platform user identity mapping for GRIP.

Maps platform-specific IDs (Discord snowflake, WhatsApp JID, Slack user ID)
to canonical GRIP user identifiers. Enables provenance queries like
"what did V>> say across all platforms?"

Gen 497: Memory provenance architecture W5.
"""

import json
from pathlib import Path
from typing import Optional


CLAUDE_DIR = Path.home() / ".claude"
DEFAULT_MAP_PATH = CLAUDE_DIR / "data" / "identity-map.json"


class IdentityResolver:
    """Resolves platform-specific IDs to canonical GRIP user IDs."""

    def __init__(self, map_path: Optional[Path] = None):
        self.map_path = map_path or DEFAULT_MAP_PATH
        self._map: dict = self._load()

    def _load(self) -> dict:
        if self.map_path.exists():
            try:
                return json.loads(self.map_path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self):
        self.map_path.parent.mkdir(parents=True, exist_ok=True)
        self.map_path.write_text(json.dumps(self._map, indent=2))

    def resolve(self, platform_id: str, platform: str) -> Optional[str]:
        """Return canonical grip_user_id for a platform-specific ID, or None."""
        for user_id, entry in self._map.items():
            if entry.get("platform_ids", {}).get(platform) == platform_id:
                return user_id
        return None

    def resolve_name(self, platform_id: str, platform: str) -> Optional[str]:
        """Return display name for a platform-specific ID, or None."""
        user_id = self.resolve(platform_id, platform)
        if user_id:
            return self._map[user_id].get("display_name")
        return None

    def get_all_platforms(self, grip_user_id: str) -> dict:
        """Return all platform IDs for a canonical user."""
        entry = self._map.get(grip_user_id, {})
        return entry.get("platform_ids", {})

    def register(self, grip_user_id: str, platform: str, platform_id: str,
                 display_name: str = "", role: str = ""):
        """Add or update a platform identity mapping."""
        if grip_user_id not in self._map:
            self._map[grip_user_id] = {"display_name": display_name, "role": role, "platform_ids": {}}
        entry = self._map[grip_user_id]
        entry["platform_ids"][platform] = platform_id
        if display_name:
            entry["display_name"] = display_name
        if role:
            entry["role"] = role
        self._save()

    def list_users(self) -> list:
        """Return all registered users with their platform IDs."""
        return [
            {"grip_user_id": uid, **entry}
            for uid, entry in self._map.items()
        ]


# Singleton for import convenience
_resolver: Optional[IdentityResolver] = None


def get_resolver() -> IdentityResolver:
    global _resolver
    if _resolver is None:
        _resolver = IdentityResolver()
    return _resolver


def main():
    import argparse

    parser = argparse.ArgumentParser(description="GRIP Identity Resolver")
    sub = parser.add_subparsers(dest="command")

    resolve_p = sub.add_parser("resolve", help="Resolve platform ID to GRIP user")
    resolve_p.add_argument("--discord", help="Discord user ID")
    resolve_p.add_argument("--whatsapp", help="WhatsApp JID")
    resolve_p.add_argument("--slack", help="Slack user ID")

    reg_p = sub.add_parser("register", help="Register a user identity")
    reg_p.add_argument("grip_user_id", help="Canonical GRIP user ID")
    reg_p.add_argument("--platform", required=True, help="Platform name")
    reg_p.add_argument("--platform-id", required=True, help="Platform-specific ID")
    reg_p.add_argument("--name", default="", help="Display name")
    reg_p.add_argument("--role", default="", help="Role")

    sub.add_parser("list", help="List all registered users")

    args = parser.parse_args()
    resolver = IdentityResolver()

    if args.command == "resolve":
        for platform, pid in [("discord", args.discord), ("whatsapp", args.whatsapp), ("slack", args.slack)]:
            if pid:
                uid = resolver.resolve(pid, platform)
                name = resolver.resolve_name(pid, platform)
                print(json.dumps({"grip_user_id": uid, "display_name": name, "platform": platform, "platform_id": pid}))
                return
        print(json.dumps({"error": "Provide --discord, --whatsapp, or --slack"}))

    elif args.command == "register":
        resolver.register(args.grip_user_id, args.platform, args.platform_id, args.name, args.role)
        print(json.dumps({"status": "ok", "grip_user_id": args.grip_user_id}))

    elif args.command == "list":
        print(json.dumps(resolver.list_users(), indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
