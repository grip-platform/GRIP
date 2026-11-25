"""Which instruction files a session loads, and how long each one is.

Claude Code warns when the files it loads at startup exceed a character budget:
max(120k, window x 0.05 x 3), so 150k on a 1M-context model and 120k on a
200K-context subagent. It counts characters as JavaScript string length, which
is UTF-16 code units, so a character outside the Basic Multilingual Plane
counts twice. :func:`utf16_len` reproduces that unit exactly.

What loads is decided by the same config the harness uses,
config/rules-scope.json, read through lib/rules_scope.py so the two can never
disagree about which rule is core and which is extended. A rule carrying
``paths:`` frontmatter loads only when a matching file is read, so it is
reported separately and left out of the always-loaded total. So is a
main-tier rule: it is kept out of native loading and handed to the main
session whole by a SessionStart hook, so Claude Code never counts it.

Everything is resolved against an explicit ``root``. It is never read from the
shared tree by default: a verification read of ~/.grip answers questions about
whatever branch that checkout is on (rules/shared-tree-is-not-canonical.md).
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import rules_scope  # noqa: E402

LEXICON_REL = "rules/00-lexicon.md"
AGENTS_REL = "AGENTS.md"
# A checkout made with core.autocrlf=true writes these files with CRLF line endings,
# so each newline of the frontmatter fence may carry a carriage return (GRIP#9178).
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

# Every git-crypt blob begins with this header. A checkout without the key (CI,
# a fresh clone) holds the ciphertext, which is not text at all.
GIT_CRYPT_MAGIC = b"\x00GITCRYPT\x00"


@dataclass(frozen=True)
class Surface:
    """One instruction file: where it is, which tier it loads in, its text.

    ``encrypted`` means this checkout holds git-crypt ciphertext, so the text
    is unknown here and the surface contributes nothing to any figure. Callers
    must report such files rather than let them vanish from a total.
    """

    rel: str
    tier: str
    text: str
    encrypted: bool = False

    @property
    def chars(self) -> int:
        return utf16_len(self.text)

    @property
    def nbytes(self) -> int:
        return len(self.text.encode("utf-8"))


def load_text(path: Path) -> tuple[str, bool]:
    """(text, encrypted) for one file; ciphertext yields ("", True), never a crash."""
    raw = path.read_bytes()
    if raw.startswith(GIT_CRYPT_MAGIC):
        return "", True
    return raw.decode("utf-8"), False


def utf16_len(text: str) -> int:
    """Length in UTF-16 code units: JavaScript's string length, Claude Code's unit."""
    return len(text.encode("utf-16-le")) // 2


def frontmatter(text: str) -> str:
    """The YAML frontmatter block of a markdown file, or an empty string."""
    match = _FRONTMATTER_RE.match(text)
    return match.group(1) if match else ""


def is_path_scoped(text: str) -> bool:
    """True when the file declares ``paths:`` and so loads only on a match."""
    return any(line.startswith("paths:") for line in frontmatter(text).splitlines())


def _read(root: Path, rel: str, tier: str) -> Surface | None:
    path = root / rel
    if not path.is_file():
        return None
    text, encrypted = load_text(path)
    return Surface(rel=rel, tier=tier, text=text, encrypted=encrypted)


def scope_config(root: Path) -> dict:
    """The rules-scope partition for this root; an absent config means all-core."""
    path = root / "config" / "rules-scope.json"
    return rules_scope.load_config(path) if path.is_file() else {"extended": []}


def _rule_tier(name: str, text: str, cfg: dict) -> str:
    kind = rules_scope.classify(name, cfg)
    if kind in ("extended", "main-tier", "command-moment"):
        return kind
    return "path-scoped" if is_path_scoped(text) else "core-rule"


def rules(root: Path) -> list[Surface]:
    """Every rules/*.md under ``root``, tiered: core-rule, path-scoped, extended or main-tier."""
    cfg = scope_config(root)
    out = []
    for path in sorted((root / "rules").glob("*.md")):
        text, encrypted = load_text(path)
        tier = _rule_tier(path.name, text, cfg)
        out.append(Surface(rel=f"rules/{path.name}", tier=tier, text=text, encrypted=encrypted))
    return out


def encrypted(root: Path) -> list[str]:
    """Every rule this checkout holds only as ciphertext (no git-crypt key here)."""
    return [s.rel for s in rules(root) if s.encrypted]


def always_loaded(root: Path) -> list[Surface]:
    """The files every session loads at startup: both CLAUDE.md files and core rules."""
    heads = [_read(root, "CLAUDE.md", "claude-md"),
             _read(root, ".claude/CLAUDE.md", "claude-md")]
    core = [s for s in rules(root) if s.tier == "core-rule"]
    return [s for s in heads if s is not None] + core


def by_tier(root: Path, tier: str) -> list[Surface]:
    """Every rule surface in one tier (core-rule, path-scoped, extended or main-tier)."""
    return [s for s in rules(root) if s.tier == tier]


def lexicon(root: Path) -> Surface | None:
    """The LEXICON file, when this root has adopted KERN."""
    return _read(root, LEXICON_REL, "lexicon")


def agents(root: Path) -> Surface | None:
    """AGENTS.md, the surface every non-Claude harness reads."""
    return _read(root, AGENTS_REL, "agents")
