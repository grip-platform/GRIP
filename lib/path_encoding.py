r"""
Path Encoding Library (Unified)
Single source of truth for Claude Code's project path encoding

FORMULA:
    Replace '/', '\', ':', '.' and ' ' with '-'
    Example (POSIX):   ~/.claude       -> -Users-you--claude
    Example (Windows): C:\Users\<first> <last>\.grip -> C--Users-<first>-<last>--grip

    The backslash, colon and space rules are NOT cosmetic. Without them a
    Windows path encodes to something that is still an ABSOLUTE PATH, and
    every caller composes the slug as ``root / "projects" / slug``. pathlib
    DISCARDS the left operand when the right one is absolute:

        Path("C:/x/projects") / r"C:\Users\x\-grip"  ==  Path("C:~/-grip")

    which is how GRIP silently wrote a parallel memory tree into the
    operator's home directory instead of <CLAUDE_DIR>/projects/. The same
    collapse let a pytest tmp_path fixture escape into $HOME. See
    rules/path-portability.md.

USAGE:
    from path_encoding import encode_project_path, encode_current_path
    encoded = encode_project_path("~/.claude")
    encoded = encode_current_path()

    # Readers that must also find data written under the pre-2.0 slug:
    from path_encoding import project_dir
    memory = project_dir(project_path) / "memory"

VERSION: 2.0.0
DATE: 2026-08-24
"""

from __future__ import annotations

import os
from pathlib import Path

#: Characters collapsed to '-'. Derived empirically from N=20 real Claude Code
#: project directories (2026-08-24), among them a Windows username with a space:
#: "C:\Users\<first> <last>\.grip" -> "C--Users-<first>-<last>--grip",
#: ".../CodeTonight/Remotion Videos" -> "...-CodeTonight-Remotion-Videos",
#: ".../example.co.za" -> "...-example-co-za".
_ENCODED_CHARS = ('/', '\\', ':', '.', ' ')
_ENCODE_TABLE = str.maketrans({ch: '-' for ch in _ENCODED_CHARS})

#: The pre-2.0 formula, FROZEN. Read-only compatibility and migration use only.
#: Never call this to build a path that will be WRITTEN to.
_LEGACY_TABLE = str.maketrans({'/': '-', '.': '-'})

#: Characters that can make a slug absolute on POSIX or NT. A string holding
#: none of these cannot be absolute on either, so no ntpath/posixpath probing
#: is needed to prove a slug is safe to join onto a root.
_PATHISH_CHARS = ('/', '\\', ':')


def encode_project_path(path: str | Path) -> str:
    r"""Encode filesystem path to Claude's project directory format.

    Guarantees, each asserted by tests/test_path_encoding.py:

      * RELATIVE - the result contains no '/', '\' or ':', so joining it onto
        a root can never escape that root;
      * IDEMPOTENT - encode(encode(p)) == encode(p);
      * SLASH-FLAVOUR AGNOSTIC - "C:/x/.y" and r"C:\x\.y" encode identically;
      * BACKWARD COMPATIBLE - byte-identical to the pre-2.0 formula for any
        path containing none of '\', ':' or ' ', i.e. every ordinary POSIX
        install. See legacy_encode_project_path for the exceptions.

    Args:
        path: Filesystem path (str or Path)

    Returns:
        Encoded path string (e.g., '-Users-you--claude')
    """
    return str(path).translate(_ENCODE_TABLE)


def legacy_encode_project_path(path: str | Path) -> str:
    """The pre-2.0 slug. READ-ONLY compatibility and migration use only.

    Retained so an existing POSIX install whose project path contains a space
    can still FIND memory written under the old slug - see project_dir. On
    Windows the legacy slug is absolute and is therefore rejected by
    project_dir rather than joined; that location is a bug artefact outside
    projects/ and is recovered by a one-time migration, never by a resolver.

    Args:
        path: Filesystem path (str or Path)

    Returns:
        The slug this path would have produced before VERSION 2.0.0
    """
    return str(path).translate(_LEGACY_TABLE)


def encode_current_path() -> str:
    """Encode current working directory."""
    return encode_project_path(os.getcwd())


def _is_relative_slug(slug: str) -> bool:
    """True when ``slug`` is a safe single path component to join onto a root."""
    return bool(slug) and not any(ch in slug for ch in _PATHISH_CHARS)


def project_slug_candidates(path: str | Path) -> tuple[str, ...]:
    """Slugs to look for: current first, then the legacy slug when it differs.

    The legacy slug is offered only when it is a usable relative component; an
    absolute legacy slug (every Windows path) is dropped.
    """
    slug = encode_project_path(path)
    legacy = legacy_encode_project_path(path)
    if legacy != slug and _is_relative_slug(legacy):
        return (slug, legacy)
    return (slug,)


def project_dir(
    project_path: str | Path | None = None,
    claude_dir: str | Path | None = None,
) -> Path:
    """Resolve ``<claude_dir>/projects/<slug>`` for ``project_path``.

    Prefers the current slug. Falls back to the legacy slug ONLY when that
    directory exists on disk and the current one does not, so a POSIX install
    whose path contains a space keeps reading the directory its data is
    already in. When neither exists the CURRENT slug is returned, so new data
    is always written to the correct location and any legacy tree drains
    rather than becoming permanent.

    Deliberately NOT folded into encode_project_path: that function is called
    at module import in ~10 modules, its result is frozen into module-level
    constants, and writers must never be pinned to a legacy directory.

    Args:
        project_path: Project directory (default: cwd)
        claude_dir: GRIP install directory (default: resolved via claude_dir shim)

    Returns:
        Path to the project's directory under ``projects/``

    Raises:
        ValueError: if the computed slug is not a relative component. This
            cannot happen with the current formula and is a loud tripwire
            against a future edit that reintroduces the absolute-slug bug.
    """
    if claude_dir is None:
        # Lazy import keeps this module free of import-order coupling and
        # importable by callers that already hold their own CLAUDE_DIR.
        from claude_dir import get_claude_dir

        claude_dir = get_claude_dir()
    projects = Path(claude_dir) / "projects"
    # NOT wrapped in Path(): pathlib normalises separators, so on Windows
    # Path("/a/My Proj") stringifies with backslashes and the legacy slug then
    # looks absolute and is dropped. Encoding the caller's value verbatim keeps
    # this identical to the encode_project_path(project_path) every caller
    # already computes.
    target = project_path if project_path is not None else Path.cwd()

    slug = encode_project_path(target)
    if not _is_relative_slug(slug):
        raise ValueError(
            f"non-relative project slug {slug!r} for {str(target)!r}: joining "
            "it would escape the projects/ root"
        )
    current = projects / slug
    if current.exists():
        return current

    for legacy in project_slug_candidates(target)[1:]:
        legacy_path = projects / legacy
        if legacy_path.exists():
            return legacy_path
    return current


def decode_project_path(encoded: str) -> str:
    """Decode Claude project directory name back to filesystem path.

    NOT the inverse of encode_project_path, and never was: encoding collapses
    several distinct characters onto '-', so decoding cannot tell them apart.
    It returns a POSIX-shaped best guess and is retained for CLI/debug display
    only - it has no production caller. Do not use it to build a real path.

    Args:
        encoded: Encoded path string

    Returns:
        Best-effort decoded path
    """
    if encoded.startswith('-'):
        return '/' + encoded[1:].replace('-', '/')
    return encoded.replace('-', '/')
