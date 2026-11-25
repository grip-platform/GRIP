#!/usr/bin/env python3
"""
Safe JSON utilities for GRIP.

Consolidates JSON loading/saving patterns to eliminate DRY violations.
All Python files should use these instead of repeated try/except blocks.

Usage:
    sys.path.insert(0, str(Path.home() / ".claude" / "lib"))
    from json_utils import safe_load_json, safe_save_json, safe_load_jsonl

IMPORTANT: safe_load_json returns `default` on error (file missing, corrupt).
Use `is None` checks, NOT `not data` checks, to distinguish missing files
from files containing falsy values like {} or [].
"""

import json
from pathlib import Path
from typing import Any, List, Union


def safe_load_json(path: Union[str, Path], default: Any = None) -> Any:
    """
    Safely load JSON from a file, returning default on any error.

    Args:
        path: Path to JSON file
        default: Value to return if file doesn't exist or is invalid

    Returns:
        Parsed JSON data or default value

    Note:
        Use `if result is None` to check for missing/corrupt files.
        Do NOT use `if not result` -- empty dicts/lists are valid JSON.
    """
    try:
        return json.loads(Path(path).read_text())
    except (json.JSONDecodeError, OSError, FileNotFoundError, UnicodeDecodeError):
        return default


def safe_save_json(path: Union[str, Path], data: Any, indent: int = 2) -> bool:
    """
    Safely save data as JSON to a file.

    Args:
        path: Path to JSON file
        data: Data to serialise
        indent: Indentation level (default 2)

    Returns:
        True if successful, False otherwise
    """
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=indent, default=str))
        return True
    except (OSError, TypeError):
        return False


def is_git_crypt_encrypted(path: Union[str, Path]) -> bool:
    """
    Check if a file is git-crypt encrypted by reading the magic header.

    Git-crypt encrypted files start with a \\x00GITCRYPT magic header (9 bytes).

    Args:
        path: Path to check

    Returns:
        True if file has git-crypt header, False otherwise
    """
    try:
        with open(path, "rb") as f:
            header = f.read(9)
        return header == b"\x00GITCRYPT"
    except OSError:
        return False


def safe_load_jsonl(path: Union[str, Path]) -> List[dict]:
    """
    Safely load JSONL (JSON Lines) from a file.

    Args:
        path: Path to JSONL file

    Returns:
        List of parsed JSON objects, or empty list on error
    """
    try:
        lines = Path(path).read_text(errors="replace").strip().split('\n')
        results = []
        for line in lines:
            if line.strip():
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return results
    except (OSError, FileNotFoundError, UnicodeDecodeError):
        return []


