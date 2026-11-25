"""Resolve a recorded skill/command name to the gene it should credit.

The usage recorder writes whatever name the Skill tool was invoked with, and that
is frequently a COMMAND name -- ``/recall``, ``/save``, ``/create-pr``. The genome
keys on SKILL-DIRECTORY names -- ``recall``, ``saving-state``,
``pr-automation``. Joining the two by exact string silently discards every
command-named record: measured over the whole recorder history (31 distinct names),
exact matching joins 16, a rate of 0.52. The other 15 record real usage that can
never reach a gene.

The mapping is not invented here and is not a hand-maintained alias table -- it is
read from the delegation each command file already states about itself, in one of
two forms:

    Skill(skill="recall")                           # an explicit call
    ~/.claude/skills/pr-automation/SKILL.md         # a path reference

Both are the command's own declaration of what it runs, so the map cannot drift
from reality the way a separate table would: rename the skill and the command file
that points at it is what breaks, which is exactly where the error belongs.

**Ambiguity fails closed.** A command file may name more than one gene -- a "see
also", a changelog line, a composed sub-skill. Crediting the wrong gene is worse
than crediting none, because it manufactures evidence for a capability that was
never used. Where several genes are named, this resolves ONLY when exactly one is
related to the command by name (``pair`` -> ``pair-mode``, not ``genome-breeding``);
otherwise it returns None and the name stays unmatched. Measured on the live tree,
2 of 390 command files are ambiguous, and the affinity rule settles one of them.

Result on the recorder's full history: 0.52 -> 0.77, with no wrong-gene credits.

Pure, stdlib-only, no I/O beyond reading the command file it is asked about.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

# A command file's own statement of what it delegates to.
_CALL_RE = re.compile(r"""Skill\(\s*skill\s*=\s*["']([A-Za-z0-9_-]+)["']""")
_PATH_RE = re.compile(r"skills/([A-Za-z0-9_-]+)/SKILL\.md")

# A recorded name becomes a path component, and it arrives from skill-usage.jsonl —
# written from whatever the Skill tool was invoked with, which is model-supplied and
# therefore untrusted (rules/untrusted-content-capability-lock.md). Without this a
# name like "../../etc/passwd" would read outside commands_dir. What comes back is
# only ever a gene name or None, so it is a weak oracle rather than a disclosure —
# but the traversal is real and the guard is one line. Allowlist, never denylist.
_SAFE_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")


def _gene_candidates(text: str, gene_names: Iterable[str]) -> set:
    """Genes named by this command file, via either delegation form."""
    genes = set(gene_names)
    found = {m.group(1) for m in _CALL_RE.finditer(text)}
    found |= {m.group(1) for m in _PATH_RE.finditer(text)}
    return found & genes


def _disambiguate(name: str, candidates: set) -> Optional[str]:
    """One candidate wins outright; several resolve only on name affinity.

    Returning None is a deliberate outcome, not a failure: an unmatched name is
    logged and costs nothing, whereas a guessed one credits a gene that was never
    invoked and is indistinguishable downstream from real evidence.
    """
    if len(candidates) == 1:
        return next(iter(candidates))
    if not candidates:
        return None
    related = [c for c in candidates if name in c or c in name]
    return related[0] if len(related) == 1 else None


def resolve_skill_to_gene(
    name: str,
    gene_names: Iterable[str],
    commands_dir: Path,
) -> Optional[str]:
    """Return the gene ``name`` credits, or None when it cannot be settled safely.

    A name that is already a gene resolves to itself without touching the disk.
    Any other name must be a plain identifier before it is allowed to become a
    path component — see ``_SAFE_NAME_RE``.
    """
    genes = set(gene_names)
    if name in genes:
        return name
    if not _SAFE_NAME_RE.fullmatch(name or ""):
        return None
    try:
        text = (Path(commands_dir) / f"{name}.md").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None
    return _disambiguate(name, _gene_candidates(text, genes))
