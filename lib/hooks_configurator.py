#!/usr/bin/env python3
"""Install canonical GRIP hooks into Claude Code settings.

Compatibility entry point retained for ``grip init`` and onboarding. Hook
definitions come only from ``config/hooks.registry.json`` and its generated
``settings.reference.json`` projection; this module contains no hook wiring.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from claude_dir import get_claude_dir  # noqa: E402
from hooks_registry import (  # noqa: E402
    compile_claude_hooks,
    hook_identity,
    load_registry,
    render_settings_reference,
)
from settings_merge import merge_settings  # noqa: E402


class HooksConfigurator:
    """Validate, merge, and report the canonical hook installation."""

    def __init__(self, claude_dir: Path | None = None):
        self.claude_dir = claude_dir or get_claude_dir()
        self.settings_path = self.claude_dir / "settings.json"
        self.hooks_dir = self.claude_dir / "hooks"
        self.lib_dir = self.claude_dir / "lib"
        self.registry_path = self.claude_dir / "config" / "hooks.registry.json"
        self.reference_base_path = (
            self.claude_dir / "config" / "settings.reference.base.json"
        )
        self.reference_path = (
            self.claude_dir / "config" / "settings.reference.json"
        )

    @staticmethod
    def _load_object(path: Path, *, missing_ok: bool = False) -> dict[str, Any]:
        if missing_ok and not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: top-level JSON must be an object")
        return data

    def _canonical(self) -> tuple[dict[str, Any], dict[str, Any]]:
        registry = load_registry(self.registry_path)
        base = self._load_object(self.reference_base_path)
        reference = self._load_object(self.reference_path)
        expected = render_settings_reference(registry, base)
        if reference != expected:
            raise ValueError(
                "generated settings.reference.json is stale; run "
                "python3 lib/hooks_registry.py generate"
            )
        return registry, reference

    def get_hooks_config(self) -> dict[str, Any]:
        """Return the generated current-schema Claude hook configuration."""
        _registry, reference = self._canonical()
        return {"hooks": reference["hooks"]}

    def read_settings(self) -> dict[str, Any]:
        """Read settings strictly; corrupt JSON is never treated as empty."""
        return self._load_object(self.settings_path, missing_ok=True)

    def write_settings(self, settings: dict[str, Any], *, backup: bool = True) -> bool:
        """Atomically write settings, optionally retaining the prior file."""
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            if backup and self.settings_path.is_file():
                backup_path = self.settings_path.with_suffix(".json.bak")
                backup_path.write_bytes(self.settings_path.read_bytes())
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.settings_path.parent,
                delete=False,
            ) as handle:
                json.dump(settings, handle, indent=2)
                handle.write("\n")
                temporary = Path(handle.name)
            os.replace(temporary, self.settings_path)
            return True
        except (OSError, TypeError, ValueError) as exc:
            print(f"Error writing settings: {exc}", file=sys.stderr)
            return False

    @staticmethod
    def _binding_counter(hooks: dict[str, Any]) -> Counter:
        counter: Counter = Counter()
        for event, groups in hooks.items():
            for group in groups:
                group_fields = {
                    key: value for key, value in group.items() if key != "hooks"
                }
                group_key = json.dumps(
                    group_fields, sort_keys=True, separators=(",", ":")
                )
                for hook in group.get("hooks", []):
                    hook_key = json.dumps(hook, sort_keys=True, separators=(",", ":"))
                    counter[(event, group_key, hook_key)] += 1
        return counter

    def hooks_configured(self) -> bool:
        """Return true only when every canonical install binding is present."""
        try:
            registry, _reference = self._canonical()
            settings = self.read_settings()
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        expected = self._binding_counter(
            compile_claude_hooks(registry, "install")
        )
        actual = self._binding_counter(settings.get("hooks", {}))
        return all(actual[binding] >= count for binding, count in expected.items())

    def configure(self, force: bool = False) -> bool:
        """Idempotently merge canonical hooks while preserving user additions."""
        del force  # retained for CLI/API compatibility; merge is always idempotent
        try:
            _registry, reference = self._canonical()
            existing = self.read_settings()
            merged = merge_settings(reference, existing)
            if not self.settings_path.exists():
                merged.setdefault(
                    "permissions", {"allow": [], "defaultMode": "default"}
                )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[GRIP] Hooks configuration failed: {exc}", file=sys.stderr)
            return False
        if merged == existing:
            print("[GRIP] Hooks already current", file=sys.stderr)
            return True
        if self.write_settings(merged):
            print("[GRIP] Canonical hooks configured successfully", file=sys.stderr)
            return True
        return False

    def verify_hook_scripts(self) -> dict[str, bool]:
        """Verify every install-profile GRIP script referenced by the registry."""
        try:
            registry, _reference = self._canonical()
        except (OSError, ValueError, json.JSONDecodeError):
            return {"registry": False}
        results: dict[str, bool] = {}
        for groups in compile_claude_hooks(registry, "install").values():
            for group in groups:
                for hook in group["hooks"]:
                    identity = hook_identity(hook)
                    if identity.startswith("inline."):
                        continue
                    candidates = (
                        self.hooks_dir / identity,
                        self.lib_dir / identity,
                    )
                    results[identity] = any(candidate.is_file() for candidate in candidates)
        return dict(sorted(results.items()))

    def get_status(self) -> dict[str, Any]:
        return {
            "settings_exists": self.settings_path.exists(),
            "hooks_configured": self.hooks_configured(),
            "hook_scripts": self.verify_hook_scripts(),
            "settings_path": str(self.settings_path),
            "hooks_dir": str(self.hooks_dir),
            "registry_path": str(self.registry_path),
        }

    def ensure_hook_scripts_executable(self) -> int:
        """Ensure shell hooks are executable; Python hooks run via Python."""
        count = 0
        for script in self.hooks_dir.glob("*.sh"):
            mode = script.stat().st_mode
            if not mode & stat.S_IXUSR:
                script.chmod(mode | stat.S_IXUSR)
                count += 1
        return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["configure", "status", "verify", "fix-permissions"]
    )
    parser.add_argument("--force", "-f", action="store_true")
    parser.add_argument("--json", "-j", action="store_true")
    args = parser.parse_args()
    configurator = HooksConfigurator()

    if args.command == "configure":
        return 0 if configurator.configure(force=args.force) else 1
    if args.command == "fix-permissions":
        print(f"Fixed permissions on {configurator.ensure_hook_scripts_executable()} scripts")
        return 0

    status = (
        configurator.get_status()
        if args.command == "status"
        else configurator.verify_hook_scripts()
    )
    if args.json:
        print(json.dumps(status, indent=2))
    elif args.command == "status":
        print(f"Settings exists: {status['settings_exists']}")
        print(f"Hooks configured: {status['hooks_configured']}")
        print(f"Registry: {status['registry_path']}")
        for name, exists in status["hook_scripts"].items():
            print(f"  - {name}: {'OK' if exists else 'MISSING'}")
    else:
        for name, exists in status.items():
            print(f"{name}: {'OK' if exists else 'MISSING'}")
    return 0 if all(status.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
