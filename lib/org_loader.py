#!/usr/bin/env python3
"""
GRIP Organization Loader

Multi-organization isolation for GRIP framework.
Detects active org and provides access to org-scoped extensions.

Detection priority:
  1. GRIP_ORG env var (explicit override)
  2. git config grip.org (auto per-repo via includeIf)
  3. Falls back to "default" (core GRIP only)

Usage:
  python3 org_loader.py detect
  python3 org_loader.py list-skills
  python3 org_loader.py list-modes
  python3 org_loader.py list-commands
  python3 org_loader.py profile
"""

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypedDict

# Configure module logger
logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
ORGS_DIR = CLAUDE_DIR / "organizations"

# Required fields in profile.json
PROFILE_REQUIRED_FIELDS = ["name", "slug"]

# Profile schema version (bump on breaking changes)
PROFILE_SCHEMA_VERSION = "1.1"


class OrgProfile(TypedDict, total=False):
    """TypedDict schema for organization profile.json.

    Required fields:
        name: Human-readable organization name
        slug: URL-safe identifier (must match directory name)

    Optional fields:
        github_org: GitHub organization name
        slack_workspace: Slack workspace name
        slack_workspace_id: Slack workspace ID (e.g., "T047860LZ")
        slack_mcp_connected: Whether Slack MCP is configured
        slack_api_access: Slack API configuration
        description: Organization description
        team: Team member configurations
        notes: Additional notes
    """
    # Required
    name: str
    slug: str
    # Optional
    github_org: str
    slack_workspace: str
    slack_workspace_id: str
    slack_mcp_connected: bool
    slack_api_access: Dict[str, Any]
    description: str
    team: Dict[str, Any]
    notes: str
    connectors: List[Dict[str, Any]]
    incognito: bool
    restrictions: Dict[str, Any]
    auto_cleanup: bool
    session_ttl_minutes: int


class ProfileValidationError(Exception):
    """Raised when profile.json validation fails."""
    pass


def _require_fields(profile: Dict[str, Any], profile_path: Path) -> None:
    """Raise ProfileValidationError if any PROFILE_REQUIRED_FIELDS is absent."""
    missing_fields = [
        field for field in PROFILE_REQUIRED_FIELDS
        if field not in profile
    ]
    if missing_fields:
        raise ProfileValidationError(
            f"Profile '{profile_path}' missing required fields: {', '.join(missing_fields)}. "
            f"Required fields are: {', '.join(PROFILE_REQUIRED_FIELDS)}"
        )


def _warn_schema_version(profile: Dict[str, Any], profile_path: Path) -> None:
    """Log a warning on a schema_version outside the compatible set (non-breaking)."""
    profile_version = profile.get("schema_version", "1.0")
    compatible_versions = {"1.0", "1.1"}
    if profile_version not in compatible_versions:
        logger.warning(
            "Profile '%s' schema_version '%s' not in compatible versions %s",
            profile_path, profile_version, compatible_versions
        )


def _validate_field_types(profile: Dict[str, Any], profile_path: Path) -> None:
    """Raise ProfileValidationError if 'name'/'slug' are not strings."""
    if not isinstance(profile.get("name"), str):
        raise ProfileValidationError(
            f"Profile '{profile_path}': 'name' must be a string, got {type(profile.get('name')).__name__}"
        )
    if not isinstance(profile.get("slug"), str):
        raise ProfileValidationError(
            f"Profile '{profile_path}': 'slug' must be a string, got {type(profile.get('slug')).__name__}"
        )


def _collect_slack_id_warnings(profile: Dict[str, Any]) -> List[str]:
    """Non-fatal warnings for Slack workspace/user IDs that fail their format."""
    warnings: List[str] = []

    # Top-level slack_workspace_id
    workspace_id = profile.get("slack_workspace_id")
    if workspace_id and isinstance(workspace_id, str):
        if not re.match(r"^T[A-Z0-9]{8,}$", workspace_id):
            warnings.append(
                f"slack_workspace_id '{workspace_id}' does not match expected format ^T[A-Z0-9]{{8,}}$"
            )

    # Slack API access workspace IDs
    slack_api = profile.get("slack_api_access", {})
    if isinstance(slack_api, dict):
        for key in ("bot_token_workspace_id", "user_token_workspace_id"):
            ws_id = slack_api.get(key)
            if ws_id and isinstance(ws_id, str):
                if not re.match(r"^T[A-Z0-9]{8,}$", ws_id):
                    warnings.append(
                        f"slack_api_access.{key} '{ws_id}' does not match expected format ^T[A-Z0-9]{{8,}}$"
                    )

    # Team member Slack user IDs
    team = profile.get("team")
    if isinstance(team, list):
        for member in team:
            if not isinstance(member, dict):
                continue
            member_name = member.get("name", "unknown")
            slack_id = member.get("slack_id")
            if slack_id and isinstance(slack_id, str):
                if not re.match(r"^U[A-Z0-9]{8,}$", slack_id):
                    warnings.append(
                        f"team member '{member_name}' slack_id '{slack_id}' does not match expected format ^U[A-Z0-9]{{8,}}$"
                    )

    return warnings


def _collect_connector_warnings(profile: Dict[str, Any]) -> List[str]:
    """Non-fatal warnings for a malformed connectors[] block (schema 1.1+)."""
    warnings: List[str] = []
    connectors = profile.get("connectors")
    if connectors is not None:
        if not isinstance(connectors, list):
            warnings.append("'connectors' must be a list")
        else:
            for i, connector in enumerate(connectors):
                if not isinstance(connector, dict):
                    warnings.append(f"connectors[{i}] must be a dict")
                    continue
                if "type" not in connector:
                    warnings.append(f"connectors[{i}] missing required 'type' field")
    return warnings


def validate_profile(profile: Dict[str, Any], profile_path: Path) -> OrgProfile:
    """Validate profile.json against schema.

    Args:
        profile: Parsed JSON data
        profile_path: Path to profile.json (for error messages)

    Returns:
        Validated OrgProfile

    Raises:
        ProfileValidationError: If required fields are missing or invalid
    """
    # Fatal checks first (order preserved: missing fields -> types), then
    # non-fatal warnings collected in the same order as before.
    _require_fields(profile, profile_path)
    _warn_schema_version(profile, profile_path)
    _validate_field_types(profile, profile_path)

    warnings = _collect_slack_id_warnings(profile) + _collect_connector_warnings(profile)
    for warning in warnings:
        logger.warning("Profile '%s': %s", profile_path, warning)

    return profile  # type: ignore


def detect_org() -> str:
    """Detect active organization.

    Priority:
      1. GRIP_ORG env var
      2. git config grip.org
      3. "default"
    """
    # 1. Explicit env var
    env_org = os.environ.get("GRIP_ORG", "").strip()
    if env_org:
        return env_org

    # 2. Git config (works with includeIf for per-repo config)
    try:
        result = subprocess.run(
            ["git", "config", "grip.org"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except subprocess.TimeoutExpired:
        logger.warning("git config grip.org timed out after 5 seconds")
    except FileNotFoundError:
        logger.warning("git command not found - cannot read grip.org config")
    except subprocess.SubprocessError as e:
        logger.warning("git config grip.org failed: %s", str(e))

    # 3. Default
    return "default"


def get_org_dir(org: str) -> Optional[Path]:
    """Return organization directory if it exists."""
    if org == "default":
        return None
    org_dir = ORGS_DIR / org
    if org_dir.is_dir():
        return org_dir
    return None


def _list_org_dir(
    org: str,
    subdir: str,
    filter_fn: Callable[[Path], bool],
    transform_fn: Callable[[Path], str]
) -> List[str]:
    """Generic helper to list items from an org subdirectory.

    Args:
        org: Organization slug
        subdir: Subdirectory name (e.g., "skills", "modes", "commands", "vendor")
        filter_fn: Function to filter items (receives Path, returns bool)
        transform_fn: Function to transform Path to output string

    Returns:
        Sorted list of transformed item names
    """
    org_dir = get_org_dir(org)
    if not org_dir:
        return []
    target_dir = org_dir / subdir
    if not target_dir.is_dir():
        return []
    return sorted(
        transform_fn(item) for item in target_dir.iterdir()
        if filter_fn(item)
    )


def list_org_skills(org: str) -> List[str]:
    """List skill directories from org's skills/ folder."""
    return _list_org_dir(
        org,
        "skills",
        filter_fn=lambda d: d.is_dir() and (d / "SKILL.md").exists(),
        transform_fn=lambda d: d.name
    )


def list_org_modes(org: str) -> List[str]:
    """List mode YAML files from org's modes/ folder."""
    return _list_org_dir(
        org,
        "modes",
        filter_fn=lambda f: f.suffix in (".yaml", ".yml"),
        transform_fn=lambda f: f.stem.replace("-mode", "")
    )


def list_org_commands(org: str) -> List[str]:
    """List command MD files from org's commands/ folder."""
    return _list_org_dir(
        org,
        "commands",
        filter_fn=lambda f: f.suffix == ".md",
        transform_fn=lambda f: f.stem
    )


def list_org_vendor(org: str) -> List[str]:
    """List vendor directories from org's vendor/ folder."""
    return _list_org_dir(
        org,
        "vendor",
        filter_fn=lambda d: d.is_dir(),
        transform_fn=lambda d: d.name
    )


def get_profile(org: str, validate: bool = True) -> Optional[OrgProfile]:
    """Read and optionally validate organization profile.json.

    Args:
        org: Organization slug
        validate: If True, validate against schema (default: True)

    Returns:
        Validated OrgProfile dict, or None if org/profile doesn't exist

    Raises:
        ProfileValidationError: If validate=True and profile is invalid
        json.JSONDecodeError: If profile.json contains invalid JSON
        OSError: If profile.json cannot be read
    """
    org_dir = get_org_dir(org)
    if not org_dir:
        return None
    profile_path = org_dir / "profile.json"
    if not profile_path.exists():
        return None

    profile_data = json.loads(profile_path.read_text())

    if validate:
        return validate_profile(profile_data, profile_path)

    return profile_data  # type: ignore


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: org_loader.py detect|list-skills|list-modes|list-commands|list-vendor|profile")
        sys.exit(1)

    command = sys.argv[1]
    org = detect_org()

    if command == "detect":
        print(org)

    elif command == "list-skills":
        for skill in list_org_skills(org):
            print(skill)

    elif command == "list-modes":
        for mode in list_org_modes(org):
            print(mode)

    elif command == "list-commands":
        for cmd in list_org_commands(org):
            print(cmd)

    elif command == "list-vendor":
        for vendor in list_org_vendor(org):
            print(vendor)

    elif command == "profile":
        try:
            profile = get_profile(org)
            if profile:
                print(json.dumps(profile, indent=2))
            else:
                print("{}")
        except ProfileValidationError as e:
            logger.error("Profile validation failed: %s", e)
            print("{}")
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in profile.json: %s", e)
            print("{}")
        except OSError as e:
            logger.error("Failed to read profile.json: %s", e)
            print("{}")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
