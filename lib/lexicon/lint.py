"""Every mechanical KERN check, returned as a list of findings.

The checks, by subject:

  LEXICON   parses strictly; within its character budget; at its own fixed
            point; no definition uses a term defined at or after it; no two
            terms are one typo apart (the Hamming guard).
  kernel    header parses and names its own file; its scope is a defined term;
            it is at the codec's fixed point (no defined meaning left in
            English); encode(expand(k)) == k (the Round-Trip Seal); no defined
            term sits inside a command, where it would be run literally.
  annex     exists; the ledger reconciles in both directions; the stored
            ledger still matches (the Annex Warden); an annex of a git-crypt
            encrypted rule is itself encrypted, or it would publish the rule.
  terms     each term pays its way: used in enough kernels to justify its
            definition, and single-token where possible. These are warnings
            until ``strict``, because a term can be young.

Nothing here edits a file. The verifier is never the author: these checks read
what is on disk and report; a human or the ledger writer decides what changes.
"""
from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from . import kernel as kern
from . import ledger, surfaces
from .codec import Codec
from .lexicon import (Lexicon, LexiconError, forward_references, normalise, parse,
                      too_close)
from .tokencount import BytesEstimate, CountError, Counter, Tiktoken

LEXICON_CHAR_LIMIT = 3_000
MIN_KERNEL_USES = 3


@dataclass(frozen=True)
class Finding:
    level: str  # error | warn
    check: str
    where: str
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


def kernels(root: Path) -> list[tuple[str, str]]:
    """(rel, text) for every readable rules/*.md written as a kernel.

    Ciphertext is skipped here and reported by :func:`lint` instead: without
    the git-crypt key nobody can tell whether it is a kernel.
    """
    return [(s.rel, s.text) for s in surfaces.rules(root)
            if not s.encrypted and s.rel != surfaces.LEXICON_REL and kern.is_kernel(s.text)]


def _annex_text(root: Path, rule: str) -> tuple[str, bool] | None:
    """(text, encrypted) of a rule's annex, or None when there is no annex."""
    annex = ledger.annex_path(root, rule)
    return surfaces.load_text(annex) if annex.is_file() else None


def ledger_rows(root: Path) -> dict[str, list[dict]]:
    """Freshly computed ledger rows for every kernel whose annex is readable here."""
    rows = {}
    for _rel, text in kernels(root):
        k = kern.parse(text)
        annex = _annex_text(root, k.rule)
        if annex is not None and annex[1]:
            continue  # ciphertext here: the ledger is written from a decrypted checkout
        rows[k.rule] = ledger.entries(list(k.clauses), annex[0] if annex else "")
    return rows


# ── LEXICON ──────────────────────────────────────────────────────────────────

def lint_lexicon(text: str, where: str = surfaces.LEXICON_REL) -> tuple[Lexicon | None, list[Finding]]:
    try:
        lex = parse(text)
    except LexiconError as exc:
        return None, [Finding("error", "lexicon-parse", where, str(exc))]
    found = []
    size = surfaces.utf16_len(text)
    if size > LEXICON_CHAR_LIMIT:
        found.append(Finding("error", "lexicon-budget", where,
                             f"{size:,} chars, over the {LEXICON_CHAR_LIMIT:,} limit"))
    if normalise(text) != text:
        found.append(Finding("error", "lexicon-fixed-point", where,
                             "a definition could use an earlier term but spells it out; "
                             "run `grip-lexicon normalise` and review the diff"))
    found += [Finding("error", "lexicon-order", where,
                      f"{code} uses {term}, which is defined at or after it")
              for code, term in forward_references(lex)]
    found += [Finding("error", "hamming-guard", where,
                      f"{a} and {b} are one typo apart; rename one")
              for a, b in too_close(lex)]
    return lex, found


# ── kernels ──────────────────────────────────────────────────────────────────

def _header_findings(rel: str, k: kern.Kernel, lex: Lexicon) -> list[Finding]:
    found = []
    stem = rel.split("/")[-1][:-3]
    if k.rule != stem:
        found.append(Finding("error", "kernel-name", rel,
                             f"header names {k.rule!r} but the file is {stem!r}"))
    if k.scope and f"@{k.scope}" not in lex.codes:
        found.append(Finding("error", "kernel-scope", rel,
                             f"scope @{k.scope} is not a defined term"))
    return found


def _codec_findings(rel: str, body: str, codec: Codec) -> list[Finding]:
    found = []
    residual = codec.residual(body)
    if residual:
        found.append(Finding("error", "kernel-fixed-point", rel,
                             f"{residual} defined meaning(s) still spelled out; "
                             "use the term, or `grip-lexicon encode`"))
    if codec.encode(codec.expand(body)) != body:
        found.append(Finding("error", "round-trip", rel,
                             "encode(expand(kernel)) differs from the kernel: the English "
                             "view would not read back to what the model sees"))
    found += [Finding("error", "term-in-command", rel,
                      f"{code} appears inside a command, where it would be run literally")
              for code in codec.codes_in_commands(body)]
    return found


def git_crypt_filtered(root: Path, rels: list[str]) -> dict[str, bool] | None:
    """Which paths carry the git-crypt filter attribute, or None if git cannot say."""
    try:
        proc = subprocess.run(["git", "-C", str(root), "check-attr", "filter", "--", *rels],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = {}
    for line in proc.stdout.splitlines():
        path, _, value = line.partition(": filter: ")
        out[path] = value.strip() == "git-crypt"
    return out


def _annex_findings(root: Path, rel: str, k: kern.Kernel, stored: dict) -> list[Finding]:
    # Forward slashes on every system: git prints a backslash path quoted and escaped,
    # so the answer for it would be filed under a name this lookup never asks for.
    annex_rel = ledger.annex_path(root, k.rule).relative_to(root).as_posix()
    loaded = _annex_text(root, k.rule)
    if loaded is None:
        return [Finding("error", "annex-missing", rel, f"no annex at {annex_rel}")]
    text, encrypted = loaded
    if encrypted:
        return [Finding("warn", "annex-unreadable", annex_rel,
                        "git-crypt ciphertext in this checkout; the ledger is checked "
                        "on a decrypted checkout")] + _encryption_findings(root, rel, annex_rel)
    clauses = list(k.clauses)
    found = [Finding("error", f"ledger-{g.kind}", annex_rel, g.detail)
             for g in ledger.audit(clauses, text)]
    found += [Finding("error", "annex-warden", annex_rel, g.detail)
              for g in ledger.warden(k.rule, ledger.entries(clauses, text), stored)]
    return found + _encryption_findings(root, rel, annex_rel)


def _encryption_findings(root: Path, rel: str, annex_rel: str) -> list[Finding]:
    flags = git_crypt_filtered(root, [rel, annex_rel])
    if flags is None:
        return [Finding("error", "annex-encryption", annex_rel,
                        "could not read git attributes, so encryption parity is unverified")]
    if flags.get(rel) and not flags.get(annex_rel):
        return [Finding("error", "annex-encryption", annex_rel,
                        f"{rel} is git-crypt encrypted but its annex is not; "
                        "add the annex path to .gitattributes before committing it")]
    return []


def lint_kernel(root: Path, rel: str, text: str, lex: Lexicon, codec: Codec,
                stored: dict) -> list[Finding]:
    try:
        k = kern.parse(text)
    except kern.KernelError as exc:
        return [Finding("error", "kernel-parse", rel, str(exc))]
    body = kern.body(text)
    return (_header_findings(rel, k, lex) + _codec_findings(rel, body, codec)
            + _annex_findings(root, rel, k, stored))


# ── terms ────────────────────────────────────────────────────────────────────

def _default_counter() -> Counter:
    try:
        probe = Tiktoken("o200k_base")
        probe.count("probe")
        return probe
    except CountError:
        return BytesEstimate()


def lint_terms(lex: Lexicon, codec: Codec, bodies: list[str], strict: bool,
               counter: Counter | None = None) -> list[Finding]:
    counter = counter or _default_counter()
    level = "error" if strict else "warn"
    found = []
    for entry in lex.vocabulary:
        kernel_uses = sum(1 for b in bodies if codec.uses(entry.code, b))
        occurrences = sum(codec.uses(entry.code, b) for b in bodies)
        saving = occurrences * (counter.count(f" {entry.meaning}") - counter.count(f" {entry.code}"))
        cost = counter.count(f"{entry.code} = {entry.meaning}")
        if kernel_uses < MIN_KERNEL_USES:
            found.append(Finding(level, "term-uses", entry.code,
                                 f"used in {kernel_uses} kernel(s); a term earns its place "
                                 f"in {MIN_KERNEL_USES}"))
        if saving <= cost:
            found.append(Finding(level, "term-pays", entry.code,
                                 f"saves {saving} tokens ({counter.id}) against a "
                                 f"{cost}-token definition"))
        if counter.count(f" {entry.code}") > 1:
            found.append(Finding("warn", "term-tokens", entry.code,
                                 f"costs {counter.count(' ' + entry.code)} tokens "
                                 f"({counter.id}); one is preferred"))
    return found


# ── the whole root ───────────────────────────────────────────────────────────

def lint(root: Path, strict: bool = False, counter: Counter | None = None) -> list[Finding]:
    """Run every check over ``root``. An empty list means clean."""
    lex_surface = surfaces.lexicon(root)
    found_kernels = kernels(root)
    if lex_surface is None:
        if found_kernels:
            return [Finding("error", "lexicon-missing", surfaces.LEXICON_REL,
                            f"{len(found_kernels)} kernel(s) but no LEXICON to decode them")]
        return ([Finding("error", "not-adopted", surfaces.LEXICON_REL,
                         "no LEXICON and no kernels: KERN is not adopted in this root")]
                if strict else [])
    if lex_surface.encrypted:
        return [Finding("error", "lexicon-encrypted", surfaces.LEXICON_REL,
                        "the LEXICON is git-crypt ciphertext; every harness must be able "
                        "to read it, so it must be plaintext")]
    lex, found = lint_lexicon(lex_surface.text)
    if lex is None:
        return found
    stored = ledger.load(root)
    codec = lex.codec()
    for rel, text in found_kernels:
        found += lint_kernel(root, rel, text, lex, codec, stored)
    found += [Finding("warn", "encrypted-unlinted", rel,
                      "git-crypt ciphertext in this checkout; lint it on a decrypted checkout")
              for rel in surfaces.encrypted(root)]
    bodies = [kern.body(text) for _, text in found_kernels]
    return found + lint_terms(lex, codec, bodies, strict, counter)
