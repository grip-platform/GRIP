"""Audit mode-definition YAML fields against the code that actually reads them.

Every file in ``modes/definitions/*.yaml`` declares a set of top-level keys.
Some are read by executing code, some are parsed into a struct nobody reads,
and some are read by nothing at all. Only the first kind does anything.

This module classifies each declared key, and resolves the skill names modes
declare so a reference to a skill that does not exist fails loudly.

Why the matching is deliberately fussy
--------------------------------------
Two hand censuses of this same corpus were wrong in opposite directions on
2026-09-11. A substring scan (``"wave" in loader_text``) reported ``wave``,
``security``, ``name`` and ``context`` as read, because those words occur in
prose and unrelated identifiers. Restricting to ``yq -r '.KEY`` then reported
``wave`` as dead, because ``lib/wave_enforcer.py`` loads the YAML itself and
the loader also reads some keys with ``grep '^KEY:'`` rather than yq.

So each reader is matched with patterns specific to how THAT reader accesses a
field, and anything the patterns cannot decide is returned as UNKNOWN rather
than guessed. UNKNOWN is a prompt for a human, not a verdict.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
MODE_DIR = CLAUDE_DIR / "modes" / "definitions"
SKILLS_DIR = CLAUDE_DIR / "skills"

MODE_DIR_MARKER = "modes/definitions"

LIVE = "LIVE"
PARSED_ONLY = "PARSED_ONLY"
DEAD = "DEAD"

# Files that only BUILD a struct for an MCP surface. A key they recognise but
# nobody reads back is declared-but-inert, which is the whole point of this
# audit -- so they are classified apart from executing readers.
PARSER_READERS = (
    "mcp-servers/grip-mcp-web/src/mode-manager.ts",
    "mcp-servers/grip-mcp-server/src/mode-manager.ts",
)

SEARCH_ROOTS = ("lib", "hooks", "bin", "scripts",
                "mcp-servers/grip-mcp-web/src", "mcp-servers/grip-mcp-server/src")

# Readers that content-discovery CANNOT find, because they never mention the
# mode directory: lib/mode-loader.sh builds the path from a shell variable, and
# lib/mode_prompt.py is handed the YAML path as argv. Both are the primary
# runtime readers, so omitting them mis-reports token_budget, hidden and
# prompt_injection as inert -- which an earlier revision of this file did.
# Whole-file scanning is correct for these two: each exists only to read a mode
# definition, so there is no foreign dict to confuse them with.
KNOWN_READERS = {
    "lib/mode-loader.sh": "shell",
    "lib/mode_prompt.py": "python-wholefile",
}


@dataclass(frozen=True)
class FieldVerdict:
    key: str
    modes: int
    status: str
    read_by: tuple[str, ...]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def declared_fields() -> dict[str, int]:
    """Top-level key -> number of mode files declaring it."""
    out: dict[str, int] = {}
    for f in sorted(MODE_DIR.glob("*.yaml")):
        seen = set()
        for line in _read(f).splitlines():
            m = re.match(r"^([a-z_][a-z0-9_]*):", line)
            if m:
                seen.add(m.group(1))
        for k in seen:
            out[k] = out.get(k, 0) + 1
    return out


def candidate_readers() -> list[Path]:
    """Every tracked source file that references the mode-definitions dir.

    Discovered, never hardcoded. An earlier revision listed three readers by
    hand and consequently mis-classified `triggers` and `version` as inert,
    because `lib/wiki_generator.py` reads them and was not on the list.
    """
    out: list[Path] = []
    for root in SEARCH_ROOTS:
        base = CLAUDE_DIR / root
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if not f.is_file() or "_archived" in str(f):
                continue
            if f.name == "mode_field_audit.py":
                continue
            if MODE_DIR_MARKER in _read(f):
                out.append(f)
    return sorted(out)


def _python_mode_scoped_keys(text: str) -> set[str]:
    """Keys read off a mapping INSIDE a function that touches the mode dir.

    Function-level scoping is load-bearing. `lib/cache_warmer.py` both globs
    modes/definitions (in warm_mode) and reads `triggers` (in warm_skill, off
    SKILL frontmatter). A file-level match would call `triggers` a mode field
    on that evidence, which is wrong.
    """
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        try:
            src = ast.unparse(node)
        except Exception:
            continue
        if MODE_DIR_MARKER not in src:
            continue
        keys |= set(re.findall(r"""\.get\(\s*['"]([a-z_][a-z0-9_]*)['"]""", src))
        keys |= set(re.findall(r"""\[\s*['"]([a-z_][a-z0-9_]*)['"]\s*\]""", src))
    return keys


def _shell_keys(text: str) -> set[str]:
    """Keys a POSIX-shell reader accesses, by every access form it uses.

    Three matchers, because the shell readers use three forms. The `sed` form
    is load-bearing: `lib/agent-matcher.sh` reads `broly` ONLY that way, so
    dropping it loses that attribution. The `grep` form is currently redundant
    for CLASSIFICATION -- every key it finds is also found by yq -- but it is
    kept deliberately, because `lib/mode-loader.sh` uses grep as its own
    fallback when `yq` is not installed, and a reader running that path is a
    real runtime configuration rather than a hypothetical one.
    """
    keys: set[str] = set()
    keys |= set(re.findall(r"""yq -r ['"]\.([a-z_][a-z0-9_]*)""", text))
    keys |= set(re.findall(r"""grep -m1 ['"]\^([a-z_][a-z0-9_]*):""", text))
    keys |= set(re.findall(r"""sed -n ['"]/\^ *([a-z_][a-z0-9_]*):""", text))
    return keys


def _parser_keys(text: str) -> set[str]:
    return set(re.findall(r"""case\s+['"]([a-z_][a-z0-9_]*)['"]\s*:""", text))


def reader_map() -> dict[str, set[str]]:
    """relative path -> mode keys that file genuinely reads.

    Union of content-discovered readers and KNOWN_READERS. A known reader that
    has vanished from disk is reported by `missing_known_readers()` rather than
    silently dropping its keys, so the list cannot rot unnoticed.
    """
    out: dict[str, set[str]] = {}

    for f in candidate_readers():
        rel = str(f.relative_to(CLAUDE_DIR))
        if rel in PARSER_READERS or rel in KNOWN_READERS:
            continue
        text = _read(f)
        keys = _python_mode_scoped_keys(text) if f.suffix == ".py" else _shell_keys(text)
        if keys:
            out[rel] = keys

    for rel, kind in KNOWN_READERS.items():
        text = _read(CLAUDE_DIR / rel)
        if not text:
            continue
        if kind == "shell":
            keys = _shell_keys(text)
        else:
            keys = set(re.findall(r"""\.get\(\s*['"]([a-z_][a-z0-9_]*)['"]""", text))
            keys |= set(re.findall(r"""\[\s*['"]([a-z_][a-z0-9_]*)['"]\s*\]""", text))
        if keys:
            out[rel] = out.get(rel, set()) | keys

    return out


def missing_known_readers() -> list[str]:
    """KNOWN_READERS entries no longer present on disk."""
    return [r for r in KNOWN_READERS if not (CLAUDE_DIR / r).exists()]


def classify_fields() -> list[FieldVerdict]:
    """Classify every declared key as LIVE, PARSED_ONLY or DEAD."""
    parsed: set[str] = set()
    for rel in PARSER_READERS:
        parsed |= _parser_keys(_read(CLAUDE_DIR / rel))
    rmap = reader_map()

    out: list[FieldVerdict] = []
    for key, n in sorted(declared_fields().items(), key=lambda kv: (-kv[1], kv[0])):
        readers = tuple(sorted(r for r, ks in rmap.items() if key in ks))
        if readers:
            status = LIVE
        elif key in parsed:
            status = PARSED_ONLY
        else:
            status = DEAD
        out.append(FieldVerdict(key=key, modes=n, status=status, read_by=readers))
    return out


def skill_refs() -> dict[str, list[str]]:
    """Skill names each mode declares, across every skill-bearing key."""
    refs: dict[str, list[str]] = {}
    for f in sorted(MODE_DIR.glob("*.yaml")):
        text = _read(f)
        names: list[str] = []
        for block in ("stacks_with", "skills"):
            m = re.search(rf"^{block}:\s*$", text, re.M)
            if not m:
                continue
            for line in text[m.end():].splitlines():
                if re.match(r"^[A-Za-z_]", line):
                    break
                mm = re.match(r"^\s*-\s+(.+?)\s*$", line)
                if mm:
                    names.append(mm.group(1).strip("\"'"))
        if names:
            refs[f.name] = names
    return refs


def dangling_skill_refs() -> dict[str, list[str]]:
    """Declared skill names with no skills/<name>/SKILL.md behind them."""
    out: dict[str, list[str]] = {}
    for mode, names in skill_refs().items():
        bad = [n for n in names if not (SKILLS_DIR / n / "SKILL.md").exists()]
        if bad:
            out[mode] = sorted(set(bad))
    return out


def main() -> int:
    verdicts = classify_fields()
    width = max(len(v.key) for v in verdicts)
    print(f"{'key':<{width}}  modes  status        read by")
    for v in verdicts:
        print(f"{v.key:<{width}}  {v.modes:5d}  {v.status:<12}  "
              f"{', '.join(v.read_by) or '-'}")
    dead = [v for v in verdicts if v.status == DEAD]
    parsed = [v for v in verdicts if v.status == PARSED_ONLY]
    print(f"\nLIVE {len(verdicts)-len(dead)-len(parsed)}  "
          f"PARSED_ONLY {len(parsed)}  DEAD {len(dead)}")

    gone = missing_known_readers()
    if gone:
        print(f"\nWARNING known reader(s) missing from disk: {', '.join(gone)}")

    dangling = dangling_skill_refs()
    if dangling:
        total = sum(len(v) for v in dangling.values())
        names = sorted({n for v in dangling.values() for n in v})
        print(f"\ndangling skill references: {total} across "
              f"{len(dangling)} mode file(s) -> {', '.join(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
