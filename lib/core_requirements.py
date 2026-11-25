"""The core dependency manifest: read it, and check that a Python can import it.

`requirements-core.txt` lists the packages a fresh GRIP install needs (GRIP#9399).
This module reads it with the standard library only, because it runs before
anything else is installed, and checks that the interpreter running it can import
each package. Installers run it with the venv's own Python:

    <venv-python> lib/core_requirements.py check-imports [--requirements PATH]

It prints one line per package and exits 0 when every package imports, 1 when
one does not, and 2 when the manifest cannot be read.

The manifest may hold only plain requirement lines. An option line (`-r`,
`--index-url`, `-e`) or a URL would make a stranger's pip fetch something this
file does not name, so the parser refuses one rather than skipping it. An
environment marker is refused too: the core list is the same on every platform,
and the import check could not honour a marker with the standard library.
"""
from __future__ import annotations

import argparse
import importlib
import re
import sys
from pathlib import Path

DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "requirements-core.txt"

# A project whose import name is not its normalised name with - turned into _.
IMPORT_NAMES = {"pyyaml": "yaml", "pycryptodome": "Crypto"}

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def normalise(name: str) -> str:
    """The PEP 503 form of a project name: lower case, and every run of
    '-', '_' or '.' becomes one '-'."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement(lineno: int, raw: str) -> "tuple[str, str] | None":
    """(normalised name, the requirement as written) for one line, or None."""
    line = raw.split("#", 1)[0].strip()
    if not line:
        return None
    if line.startswith("-") or "://" in line or " @ " in line:
        raise ValueError(f"line {lineno}: only plain requirements may appear here: {raw.strip()!r}")
    if ";" in line:
        # pip would skip a marker line on a platform it excludes, but the import
        # check cannot evaluate markers with the standard library, so it would
        # report a false "missing" there. The core list is the same everywhere.
        raise ValueError(f"line {lineno}: an environment marker is not allowed in the core "
                         f"list, which is the same on every platform: {raw.strip()!r}")
    match = _NAME.match(line)
    if match is None:
        raise ValueError(f"line {lineno}: no project name in {raw.strip()!r}")
    return normalise(match.group(0)), line


def parse_manifest(text: str) -> list[tuple[str, str]]:
    """Every requirement in a manifest, as (normalised name, line as written)."""
    entries = (_requirement(n, raw) for n, raw in enumerate(text.splitlines(), 1))
    return [entry for entry in entries if entry is not None]


def import_name(project: str) -> str:
    """The module a project is imported as."""
    project = normalise(project)
    return IMPORT_NAMES.get(project, project.replace("-", "_"))


def import_problem(project: str) -> "str | None":
    """Why this interpreter cannot import the project, or None when it can.

    Any exception counts, not only ImportError: a compiled extension built for
    another platform can raise OSError on import."""
    try:
        importlib.import_module(import_name(project))
    except Exception as exc:  # noqa: BLE001 -- every failure to import is the answer
        return f"{type(exc).__name__}: {exc}"
    return None


def check_imports(manifest: Path) -> int:
    """Print one line per package and return the exit code described above."""
    try:
        entries = parse_manifest(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read {manifest}: {exc}", file=sys.stderr)
        return 2
    problems = {project: import_problem(project) for project, _ in entries}
    for project, problem in problems.items():
        print(f"  {project}: {'OK' if problem is None else 'MISSING (' + problem + ')'}")
    missing = sorted(project for project, problem in problems.items() if problem)
    if missing:
        print(f"{len(missing)} of {len(entries)} core packages do not import with "
              f"{sys.executable}: {', '.join(missing)}")
        return 1
    print(f"all {len(entries)} core packages import with {sys.executable}")
    return 0


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-imports", help="check this Python imports every core package")
    check.add_argument("--requirements", default=str(DEFAULT_MANIFEST),
                       help="the manifest to check (default: requirements-core.txt)")
    args = parser.parse_args(argv)
    return check_imports(Path(args.requirements))


if __name__ == "__main__":
    raise SystemExit(main())
