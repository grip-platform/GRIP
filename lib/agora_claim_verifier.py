"""Deterministic verifier for AGORA "undefined symbol / missing import" claims.

Origin: 2026-05-30. PR #3315 (grip-platform/GRIP) cost ~10 AGORA review
cycles chasing a phantom blocker: the Gemini judge repeatedly returned
REQUEST_CHANGES citing *"`gen` / `count_inventory` / `inspect` referenced
with no import -> NameError on load in tests/test_reference_docs.py"* when all
three imports are present (lines 17, 25, 27 of the FULL file). Root cause:
speakers + judge reason from the truncated ``gh pr diff`` hunk (changed lines
+ ~3 lines of context), so an import statement on an UNCHANGED line is
invisible -> a symbol used in a changed line looks unimported.

Shell extension: 2026-06-02. The SAME diff-truncation failure recurred on
SHELL variables across PRs #3400/#3402/#3404 — the canonical case being #3404
r1's *"critical blocker: $CLAUDE_DIR is undefined (no fallback shown)"* when
``bin/grip-yolo:35`` is literally ``CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"``,
just outside the 4000-char hunk. The original verifier was Python-AST-only and
undefined-symbol-phrasing-only, so it could not refute it on three axes:
(1) ``bin/grip-yolo`` has no extension → the cited file wasn't extracted;
(2) "no fallback" / "not set" weren't recognised as unbound phrasings;
(3) the shell binding forms ``X="${X:-…}"`` / ``export X`` / ``read X`` /
``for X in`` / ``local X=`` weren't recognised. All three are now handled —
when the cited file is a shell script (``.sh``/``.bash``, a ``#!…bash|sh``
shebang, or an extensionless ``bin/*`` file) the verifier checks for a shell
binding instead of a Python/cross-language one.

This module is the deterministic floor (mechanical, NO LLM) that refutes such
claims against the REAL file on disk BEFORE the judge weights them. Pattern:
``rules/llm-augmentation-pattern.md`` (mechanical floor + LLM recall) and
``rules/exogenous-anchor-law.md`` (a deterministic check the judge cannot
predict-to-please).

SOUNDNESS (load-bearing): the verifier only ever REFUTES a *specific*
"undefined symbol" claim by POSITIVELY confirming the symbol is bound in the
real file. It never asserts MERGE and never invents a blocker — so it cannot
hide a genuine bug, it can only neutralise a provably-false symbol claim.
Conservative by construction: when the file is missing/unreadable or the
symbol cannot be positively confirmed, the claim STANDS (no refutation).

Cross-ref memory ``feedback_agora_judge_advisory_ignore_false_positives.md``
(the ``Math.min(...)`` false-positive class — same diff-truncation root cause).

Public API
----------
verify_symbol_claims(speaker_text, changed_files, repo_root) -> list[RefutedClaim]
format_judge_verification_note(refuted) -> str   # injected into the judge prompt
format_refutation_render_block(refuted) -> str   # rendered into the PR comment
verify_temporal_claims(speaker_text, today=None) -> list[TemporalClaim]
format_temporal_gate_note(claims, today=None) -> str   # injected into the judge prompt
format_temporal_defer_warning(claims) -> str           # banner on the DEFER issue
"""
from __future__ import annotations

import ast
import keyword
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

# Trigger phrases that signal a speaker is claiming a symbol is unbound. Matched
# case-insensitively as a substring of a single line (plus a 1-line window).
# The final group adds SHELL-specific unbound phrasings ("no fallback", "not
# set", "unset", "no default") so a claim about a shell variable's missing
# default-expansion is caught alongside the Python "no import / NameError" set.
_TRIGGER_RE = re.compile(
    r"(?:no\s+import|not\s+imported|missing\s+import|without\s+(?:an?\s+)?import|"
    r"un-?imported|isn'?t\s+imported|never\s+imported|"
    r"name\s*error|undefined(?:\s+(?:name|symbol|reference|variable))?|"
    r"not\s+defined|never\s+defined|undeclared|unresolved\s+reference|"
    r"no\s+fallback|not\s+set|isn'?t\s+set|un-?set|no\s+default)",
    re.IGNORECASE,
)

# Symbol carriers, in precedence order:
#   1. Backtick-quoted identifiers ("`gen` referenced with no import") — the
#      high-precision carrier used by Python claims.
#   2. Shell ``$VAR`` / ``${VAR}`` sigil forms ("$CLAUDE_DIR is undefined") —
#      shell claims rarely backtick the variable; the dollar-sigil IS the
#      precision marker there.
_BACKTICK_IDENT_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")
_DOLLAR_IDENT_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")

# File-path tokens cited near a claim. Two forms: (1) any token with a
# recognised code/doc extension; (2) an extensionless ``bin/<name>`` path — the
# common GRIP shell-executable shape (``bin/grip-yolo``) the extension form
# would otherwise drop.
_FILE_PATH_RE = re.compile(
    r"\b([\w./-]+\.(?:py|pyi|js|jsx|mjs|cjs|ts|tsx|go|rs|rb|java|kt|c|cc|cpp|h|hpp|"
    r"cs|php|swift|scala|sh|bash|json|yaml|yml|toml|md|txt|rst))\b"
    r"|\b(bin/[\w.-]+)\b"
)

# Identifiers that are never a real "undefined symbol" (language noise / prose).
_SYMBOL_STOPLIST = frozenset(keyword.kwlist) | {
    "self", "cls", "None", "True", "False",
    "import", "imports", "from", "module", "function", "method", "class",
    "variable", "name", "symbol", "code", "file", "test", "value", "type",
    "NameError", "ImportError", "ModuleNotFoundError", "undefined", "null",
}

_PY_EXTENSIONS = (".py", ".pyi")
_SHELL_EXTENSIONS = (".sh", ".bash")
_CODE_EXTENSIONS = _PY_EXTENSIONS + (
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".rb",
    ".java", ".kt", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".php",
    ".swift", ".scala", ".sh", ".bash",
)
# A ``#!`` first line naming a sh/bash/zsh interpreter — marks an
# extensionless file (``bin/grip-yolo``) as a shell script.
_SHELL_SHEBANG_RE = re.compile(r"^#!.*\b(?:ba|z)?sh\b")

_MAX_FILE_BYTES = 2_000_000   # skip pathologically large files
_MAX_FILES_PER_SYMBOL = 60    # bound the candidate scan


@dataclass(frozen=True)
class RefutedClaim:
    """A speaker's "undefined symbol" claim that was mechanically refuted.

    ``symbol`` is bound (imported/defined/assigned) at ``evidence`` (a
    ``relpath:lineno`` reference) inside ``file``. ``claim_snippet`` is the
    speaker text window that made the (false) claim, for human traceability.
    """
    symbol: str
    file: str
    evidence: str
    claim_snippet: str


# ---------------------------------------------------------------------------
# Claim detection (speaker text → candidate (symbol, file_hint) pairs)
# ---------------------------------------------------------------------------

def _iter_windows(text: str, trigger: re.Pattern):
    """Yield (window_text, line_index) for each line matching ``trigger``.

    The window is the trigger line plus one line of context on each side, so a
    claim split across two lines ("uses ``foo``\\nwhich is not imported") is
    still captured. Shared by every claim gate in this module so a new gate
    supplies only its trigger pattern (DRY).
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if trigger.search(line):
            yield "\n".join(lines[max(0, i - 1): i + 2]), i


def _iter_claim_windows(text: str):
    """Yield windows around unbound-symbol trigger phrases."""
    return _iter_windows(text, _TRIGGER_RE)


def _symbols_in(window: str) -> list[str]:
    """Candidate symbols in a window, minus language/prose noise.

    Backtick-quoted identifiers (Python carrier) AND ``$VAR`` / ``${VAR}``
    sigil forms (shell carrier) are collected, deduplicated, in first-seen
    order. A single-letter backtick is dropped as prose; a single-letter
    dollar-sigil (``$x``) is KEPT — the explicit ``$`` makes it a deliberate
    variable reference, not prose.
    """
    out: list[str] = []
    for sym in _BACKTICK_IDENT_RE.findall(window):
        if len(sym) >= 2:  # single-letter backticks are almost always prose
            _push_symbol(sym, out)
    for sym in _DOLLAR_IDENT_RE.findall(window):
        _push_symbol(sym, out)
    return out


def _push_symbol(sym: str, out: list[str]) -> None:
    """Append ``sym`` to ``out`` unless it is prose-noise or already present."""
    if sym in _SYMBOL_STOPLIST or sym.lower() in _SYMBOL_STOPLIST:
        return
    if sym not in out:
        out.append(sym)


def _files_in(window: str) -> list[str]:
    """Code/doc/shell file paths cited inside a window (deduplicated, in order).

    ``_FILE_PATH_RE`` has two alternation groups (extension form, ``bin/*``
    form), so each match is a 2-tuple with exactly one non-empty element —
    coalesce to that element.
    """
    out: list[str] = []
    for groups in _FILE_PATH_RE.findall(window):
        f = next((g for g in groups if g), "")
        if f and f not in out:
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# Symbol-binding evidence (real file on disk → is the symbol actually bound?)
# ---------------------------------------------------------------------------

def _read_source(abs_path: Path) -> Optional[str]:
    """Read a file's text; None on missing / too-large / unreadable."""
    try:
        if not abs_path.is_file() or abs_path.stat().st_size > _MAX_FILE_BYTES:
            return None
        return abs_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _python_bound_names(source: str) -> dict[str, int]:
    """Map name -> first lineno for names bound at import or module scope.

    Captures: every imported name (anywhere — ``import``/``from import``,
    module- or function-local) plus module-level ``def``/``class`` and
    module-level assignment targets. This is exactly the set that refutes
    "no import" / "NameError on load" without over-reaching into deeply
    nested local-only bindings. Raises ``SyntaxError`` on unparseable source
    (caller falls back to regex).
    """
    tree = ast.parse(source)
    bound: dict[str, int] = {}

    def add(name: str, lineno: int) -> None:
        if name and name != "*" and name not in bound:
            bound[name] = lineno

    _collect_imports(tree, add)
    _collect_module_level(tree, add)
    return bound


def _collect_imports(tree: ast.AST, add) -> None:
    """Record every imported name anywhere in the tree (module- or function-local)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            _add_import_aliases(node, add)


def _add_import_aliases(node, add) -> None:
    """Bind each alias of one import node (``import x`` vs ``from m import x``)."""
    plain = isinstance(node, ast.Import)
    for alias in node.names:
        name = (alias.asname or alias.name.split(".")[0]) if plain else (alias.asname or alias.name)
        add(name, node.lineno)


def _collect_module_level(tree: ast.Module, add) -> None:
    """Record module-level def/class names and assignment targets ("on load")."""
    for node in tree.body:
        _add_module_node(node, add)


def _add_module_node(node, add) -> None:
    """Bind names introduced by one module-level statement."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        add(node.name, node.lineno)
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            _add_target_names(target, node.lineno, add)
    elif isinstance(node, ast.AnnAssign):
        _add_target_names(node.target, node.lineno, add)


def _add_target_names(target: ast.AST, lineno: int, add) -> None:
    """Record ast.Name targets within an assignment (handles tuple unpacking)."""
    if isinstance(target, ast.Name):
        add(target.id, lineno)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _add_target_names(elt, lineno, add)


def _regex_defined_lineno(symbol: str, source: str) -> Optional[int]:
    """Fallback (non-Python / unparseable): first line that binds ``symbol``.

    Matches import statements and common cross-language definition/assignment
    forms. Lower precision than the Python AST path; only used when AST is
    unavailable.
    """
    s = re.escape(symbol)
    # Single alternation (one regex, one scan) — ``\bimport\b.*\bX\b`` already
    # covers the ``from mod import X`` form, so no separate from-import branch.
    combined = re.compile(
        rf"\bimport\b.*\b{s}\b"
        rf"|\brequire\b.*\b{s}\b"
        rf"|\b(?:def|class|func|function|fn|interface|type|struct|enum|trait)\s+{s}\b"
        rf"|\b(?:const|let|var|val)\s+{s}\b"
        rf"|^\s*{s}\s*[:=]"
        rf"|\b{s}\s*=\s*(?:function\b|\()"
    )
    for lineno, line in enumerate(source.splitlines(), start=1):
        if combined.search(line):
            return lineno
    return None


def _is_shell_file(rel_path: str, source: str) -> bool:
    """True when the file is a shell script — by extension, by ``bin/*`` path,
    or by a ``#!…sh`` shebang on the first line.

    The ``bin/*`` rule matches GRIP's extensionless executables
    (``bin/grip-yolo``); the shebang confirms shell-ness for those and for any
    other extensionless interpreter script.
    """
    lower = rel_path.lower()
    if lower.endswith(_SHELL_EXTENSIONS):
        return True
    if rel_path.startswith("bin/") and "." not in rel_path.rsplit("/", 1)[-1]:
        return True
    first_line = source.split("\n", 1)[0]
    return bool(_SHELL_SHEBANG_RE.match(first_line))


def _shell_bound_lineno(symbol: str, source: str) -> Optional[int]:
    """First line that BINDS shell variable ``symbol``; None if never bound.

    Recognised binding forms (word-boundary anchored so ``FOO`` is never
    matched by a binding of ``FOOBAR``):
      - assignment: ``FOO=…`` (incl. the outer assignment in
        ``FOO="${FOO:-x}"`` — that ``FOO=`` IS the binding);
      - assign-default idiom: ``${FOO:=x}`` (``:=`` ASSIGNS FOO; the set-default
        form ``: "${FOO:=x}"`` has no leading ``FOO=``);
      - ``export FOO`` / ``export FOO=…``;
      - ``local FOO=…`` / ``declare``/``typeset``/``readonly`` FOO;
      - ``read [-flags] FOO`` (incl. multiple names);
      - ``for FOO in …`` loop variable.

    SOUNDNESS: returns a line ONLY when a real binding is present.
      - A bare *use* (``echo "$FOO"``) is NOT a binding → no refutation.
      - CRITICAL (AGORA #3405 Speaker C): ``${FOO:-x}`` (``:-``) is *default
        SUBSTITUTION at a use site* — it expands to FOO-or-``x`` but does NOT
        assign FOO. Only ``${FOO:=x}`` (``:=``) assigns. So ``:-`` is
        deliberately EXCLUDED from the binding regex; treating it as a binding
        would falsely refute a real "FOO never set" bug in ``echo "${FOO:-x}"``.
    """
    s = re.escape(symbol)
    combined = re.compile(
        rf"^\s*{s}="
        rf"|\$\{{{s}:="  # ${FOO:=x} — assign-default ONLY (NOT :- substitution)
        rf"|^\s*export\s+(?:[\w]+\s+)*{s}\b"
        rf"|^\s*(?:local|declare|typeset|readonly)\s+(?:-\S+\s+)*(?:[\w]+=\S*\s+)*{s}\b"
        rf"|^\s*read\s+(?:-\S+\s+)*(?:[\w]+\s+)*{s}\b"
        rf"|^\s*for\s+{s}\s+in\b"
    )
    for lineno, line in enumerate(source.splitlines(), start=1):
        if combined.search(line):
            return lineno
    return None


def _symbol_evidence(symbol: str, rel_path: str, repo_root: Path) -> Optional[str]:
    """Return ``rel_path:lineno`` if ``symbol`` is bound in the real file, else None."""
    abs_path = (repo_root / rel_path)
    source = _read_source(abs_path)
    if source is None:
        return None
    if rel_path.lower().endswith(_PY_EXTENSIONS):
        try:
            bound = _python_bound_names(source)
        except SyntaxError:
            bound = {}
        if symbol in bound:
            return f"{rel_path}:{bound[symbol]}"
        # Python file that failed to parse → fall through to regex.
        if bound:
            return None
    if _is_shell_file(rel_path, source):
        sh_lineno = _shell_bound_lineno(symbol, source)
        return f"{rel_path}:{sh_lineno}" if sh_lineno else None
    lineno = _regex_defined_lineno(symbol, source)
    return f"{rel_path}:{lineno}" if lineno else None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _candidate_files(file_hints: list[str], changed_files: list[str], repo_root: Path) -> list[str]:
    """Resolve which on-disk files to check a symbol against.

    Cited files (that exist) are checked first; then all changed code files
    that exist. Bounded by ``_MAX_FILES_PER_SYMBOL``.
    """
    seen: list[str] = []

    def push(rel: str) -> None:
        if rel and rel not in seen and (repo_root / rel).is_file():
            seen.append(rel)

    for rel in file_hints:
        push(rel)
    for rel in changed_files:
        # Code/doc-extension files, plus extensionless ``bin/*`` executables
        # (GRIP shell scripts like ``bin/grip-yolo`` that carry no suffix).
        if rel.lower().endswith(_CODE_EXTENSIONS) or _is_extensionless_bin(rel):
            push(rel)
    return seen[:_MAX_FILES_PER_SYMBOL]


def _is_extensionless_bin(rel: str) -> bool:
    """True for a ``bin/<name>`` path whose final component has no extension."""
    return rel.startswith("bin/") and "." not in rel.rsplit("/", 1)[-1]


def verify_symbol_claims(
    speaker_text: str,
    changed_files: list[str],
    repo_root,
) -> list[RefutedClaim]:
    """Refute false "undefined symbol / missing import" claims in speaker text.

    For each backtick-quoted symbol appearing next to a trigger phrase, check
    whether the symbol is actually bound in the cited file (or, if none cited,
    any changed code file). A positive binding REFUTES the claim.

    Args:
        speaker_text: combined Speaker B + C output (the graded reviewers).
        changed_files: code file paths from the PR diff (full relpaths).
        repo_root: directory the PR is checked out into (cwd in CI).

    Returns:
        One ``RefutedClaim`` per refuted symbol (deduplicated by symbol+file).
    """
    if not speaker_text or not speaker_text.strip():
        return []
    root = Path(repo_root)
    changed = [f for f in (changed_files or []) if isinstance(f, str)]

    refuted: list[RefutedClaim] = []
    seen_keys: set[tuple[str, str]] = set()
    for window, _idx in _iter_claim_windows(speaker_text):
        refuted.extend(_refute_window(window, changed, root, seen_keys))
    return refuted


def _refute_window(
    window: str,
    changed: list[str],
    root: Path,
    seen_keys: set[tuple[str, str]],
) -> list[RefutedClaim]:
    """Refute every backtick-symbol claim in one window. Single loop over symbols."""
    symbols = _symbols_in(window)
    if not symbols:
        return []
    candidates = _candidate_files(_files_in(window), changed, root)
    snippet = _trim_snippet(window)
    out: list[RefutedClaim] = []
    for symbol in symbols:
        hit = _first_evidence(symbol, candidates, root, seen_keys)
        if hit:
            rel, evidence = hit
            seen_keys.add((symbol, rel))
            out.append(RefutedClaim(symbol, rel, evidence, snippet))
    return out


def _first_evidence(
    symbol: str,
    candidates: list[str],
    root: Path,
    seen_keys: set[tuple[str, str]],
) -> Optional[tuple[str, str]]:
    """First (rel_path, evidence) where ``symbol`` is bound; None if unbound everywhere."""
    for rel in candidates:
        if (symbol, rel) in seen_keys:
            continue
        evidence = _symbol_evidence(symbol, rel, root)
        if evidence:
            return rel, evidence
    return None


def _trim_snippet(window: str, limit: int = 220) -> str:
    """Collapse a claim window to a single trimmed line for display."""
    flat = " ".join(window.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Rendering (judge prompt note + PR-comment block)
# ---------------------------------------------------------------------------

def format_judge_verification_note(refuted: list[RefutedClaim]) -> str:
    """Build the MECHANICAL VERIFICATION note injected into the judge prompt.

    Returns ``"(none)"`` when there is nothing to report, so the judge prompt
    always has a well-formed slot.
    """
    if not refuted:
        return "(none — no symbol claims were mechanically refuted)"
    lines = [
        f'- `{c.symbol}` was claimed unbound ("no import" / "undefined" / '
        f'"NameError"), but it IS bound at {c.evidence} in the full file.'
        for c in refuted
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Quote-or-downgrade gate (GRIP #555)
#
# Measured 2026-07-18 across HAL PRs #501/#503/#505: 11 of 12 speaker concerns
# were false, and 3 of 3 BLOCKING concerns were false — including one naming a
# method (`_load_from_config`) absent from the file it reviewed. Root cause:
# any single concern carrying a file:line was a MANDATORY hard block, with
# nothing checking whether it was true, while the speaker prompt simultaneously
# demanded 3-4 concerns and called silence "the failure mode".
#
# This gate does not silence concerns — it makes raising one cheap and BLOCKING
# on one expensive. To block, the speaker must quote the offending source line
# verbatim, and that quote must be found in the real file on disk. Unquotable
# or unfindable concerns are still reported; they just cannot gate the merge.
# Deterministic by construction: a byte comparison against the CI checkout,
# never a model judgement.
# ---------------------------------------------------------------------------

QUOTE_VERIFIED = "QUOTE_VERIFIED"
QUOTE_NOT_FOUND = "QUOTE_NOT_FOUND"
UNQUOTABLE = "UNQUOTABLE"

# "path/to/file.py:123" / "`path/to/file.py:123`" / "path/to/file.py line 123"
_FILE_LINE_RE = re.compile(
    r"`?([\w./-]+\.(?:py|pyi|js|jsx|mjs|cjs|ts|tsx|go|rs|rb|java|kt|c|cc|cpp|"
    r"h|hpp|cs|php|swift|scala|sh|bash|json|yaml|yml|toml)|bin/[\w.-]+)`?"
    r"(?::|\s+line\s+)(\d+)"
)
# A verbatim quote line: a markdown blockquote ("> code"). Diff markers (+/-)
# and backticks INSIDE the quote are stripped by _normalise_code_line.
#
# This comment used to promise "or a fenced/inline backtick line", a branch the
# pattern has never implemented (GRIP#5749). The comment is corrected rather
# than the regex widened, and that choice is deliberate: an inline backtick span
# in ordinary prose ("the `helper` call is clean") would become a quote
# candidate, and a short one can match a real source line exactly — PROMOTING a
# compliment to blocking-eligible. Removing false blocks is the whole reason
# this gate exists (GRIP#555: 3 of 3 blocking concerns were false). Widening is
# a real option and safer once concern-section scoping is in, since praise is
# then no longer scanned at all — but it is a decision, not a tidy-up.
_QUOTE_LINE_RE = re.compile(r"^\s*>\s?(.+)$")
_UNQUOTABLE_RE = re.compile(r"\bUNQUOTABLE\b", re.IGNORECASE)
# A bracketed token in the shield's shape: [EMAIL], [JID], [PERSON-1], [PHONE-3].
_PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Z_]*(?:-\d+)?\]")


@dataclass(frozen=True)
class QuotedLineVerdict:
    """One speaker concern, judged on whether its quoted line really exists.

    ``status`` is one of ``QUOTE_VERIFIED`` / ``QUOTE_NOT_FOUND`` /
    ``UNQUOTABLE``. Only ``QUOTE_VERIFIED`` may drive REQUEST_CHANGES;
    everything else is reported without blocking the merge. ``via_redaction``
    is True when the quote matched only the redacted form of the line, so a
    placeholder in it came from the shield rather than from the source.
    """
    file: str
    line_no: Optional[int]
    quote: str
    status: str
    matched_line: Optional[int]
    concern_snippet: str
    via_redaction: bool = False

    @property
    def blocking_eligible(self) -> bool:
        """True only when the quoted line was found in the real file."""
        return self.status == QUOTE_VERIFIED

    @property
    def line_drifted(self) -> bool:
        """Quote exists but not at the cited line — a soft citation-drift signal.

        Never blocking on its own: diff-hunk arithmetic legitimately shifts
        line numbers, so drift is reported for humans, not enforced.
        """
        return (
            self.status == QUOTE_VERIFIED
            and self.line_no is not None
            and self.matched_line is not None
            and self.line_no != self.matched_line
        )


def _normalise_code_line(raw: str) -> str:
    """Collapse a source line to a whitespace- and marker-insensitive form.

    Strips diff markers, markdown blockquote/backtick decoration and internal
    whitespace runs, so a speaker that re-indents or wraps a quote in backticks
    still matches the real line.
    """
    text = raw.strip()
    text = text.lstrip(">").strip()
    if text[:1] in {"+", "-"} and not text.startswith(("--", "++")):
        text = text[1:]
    text = text.strip().strip("`").strip()
    return " ".join(text.split())


_HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$")
_HEADING_NUM_RE = re.compile(r"^\s*\d+[.)]\s*")
_CONCERNS_HEADING_RE = re.compile(r"(?:[a-z]+\s+)?concerns?", re.IGNORECASE)


def _is_concerns_heading(title: str) -> bool:
    """True for "Concerns", "Concrete concerns", "Scope concerns".

    Anchored on the WHOLE normalised heading, never a substring. A speaker-level
    heading such as "3. Technical concerns + suggestions — xAI Grok 4.3" does
    mention concerns, but it spans that speaker's suggestions and hypotheticals
    too, so a substring test would re-open most of the body this is meant to
    exclude. Checked against the real heading vocabulary of ten councils: 17 of
    17 classified correctly.
    """
    return bool(_CONCERNS_HEADING_RE.fullmatch(_HEADING_NUM_RE.sub("", title.strip())))


# A concerns section whose first line states there are none, as a complete
# statement: "None.", "No concerns.", "No substantiated concerns.", "0 concrete
# concerns", and the other word orders councils use: "No substantiated technical
# concerns.", "No concerns substantiated by the diff.", "No concerns substantiated
# from the provided diff hunks." Those last three opened 9 of the 45 sittings on
# GRIP#7296-#7355 (measured 2026-10-05) and were read as concerns, and the next 28
# sittings added "diff hunk" and "No concrete concerns identified in the visible
# diff." Since the long tail kept growing, the technical speaker's prompt now asks
# for the exact opener "None." on a clean review (lib/agora_pr_review.py); these
# forms remain for the speakers that reword it. A QUALIFIED none -- "None
# blocking, but …", "No blocking concerns. Minor: …", "No concerns substantiated,
# except …" -- does not match, because what follows it is still a concern. Only
# observed forms are accepted: matching too much would drop a real concern, which
# is the worse error for a review gate.
_NONE_MARKER_RE = re.compile(
    r"^\W*(?:none"
    r"|(?:no|0)\s+(?:(?:substantiated\s+technical|substantiated|concrete)\s+)?concerns?"
    r"(?:\s+(?:substantiated|identified)"
    r"(?:\s+(?:by|in|from)\s+the\s+(?:(?:provided|visible)\s+)?diff(?:\s+hunks?)?)?)?"
    r")\s*(?:[.!]|$)",
    re.IGNORECASE,
)


def _says_there_are_none(line: str) -> bool:
    """True when a concerns section opens by saying it has none (GRIP#6697)."""
    return bool(_NONE_MARKER_RE.match(line.replace("*", "")))


def _concern_line_numbers(lines: list[str]) -> Optional[set[int]]:
    """Line indices inside a concerns section; None when the text has NO headings.

    Two distinct cases, and keeping them apart is the whole point:

    * **No headings at all** -> None, and the caller scans everything. This is
      the pre-existing behaviour and several real reviews depend on it, raising
      a concern as a bare bullet. A review that cites without sections is a
      different condition from one that has sections and cites outside them;
      conflating those two is the mistake
      ``lib/agora_pr_review.py::_cites_foreign_paths`` was written to undo.
    * **Headings, but none of them a concerns section** -> an EMPTY set, so
      nothing is extracted. An earlier draft returned None here too, which meant
      a review made entirely of praise sections was still scanned whole and its
      citations still reported as concerns — the very defect being fixed. Two
      tests in tests/test_agora_concern_scoping.py caught that draft.

    A concerns section whose first line says there are none contributes nothing
    either (GRIP#6697): the citations after "None." are the files the speaker
    checked, not defects, and each used to be reported as a downgraded concern.

    Skipping is always the safe direction: a citation not extracted keeps
    whatever weight it already had, because a verdict only ever REMOVES blocking
    eligibility.
    """
    inside = opening = False
    saw_heading = False
    out: set[int] = set()
    for i, line in enumerate(lines):
        head = _HEADING_RE.match(line)
        if head:
            saw_heading = True
            inside = opening = _is_concerns_heading(head.group(1))
            continue
        if opening and line.strip():
            opening = False
            inside = not _says_there_are_none(line)
        if inside:
            out.add(i)
    return out if saw_heading else None


def _iter_concern_blocks(text: str):
    """Yield ``(rel_path, line_no, block_text)`` per file:line-citing concern.

    A concern block runs from its citation line up to the next citation, so a
    quote on a following line belongs to the concern that cited it.

    SCOPED to concerns sections when the review has any (GRIP#5749). Every
    citation used to open a block, so a path named while PRAISING the diff was
    reported back to the author as a downgraded concern. Measured across ten
    councils: of 8 blocks extracted, 4 sat under "Concrete concerns" and 4
    under "Code quality impression" or "Architectural alignment" — half were
    endorsements. On GRIP#5724 the block announced "2 concerns downgraded" and
    both were compliments, one of them inside a section whose opening words
    were "No substantiated concerns."

    Conservative by construction: scoping can only yield FEWER blocks, and a
    verdict only ever REMOVES blocking eligibility, so a citation not extracted
    here keeps whatever weight it already had. It cannot wave a defect through.
    """
    lines = text.splitlines()
    scope = _concern_line_numbers(lines)
    starts: list[tuple[int, str, int]] = []
    for i, line in enumerate(lines):
        if scope is not None and i not in scope:
            continue
        match = _FILE_LINE_RE.search(line)
        if match:
            starts.append((i, match.group(1), int(match.group(2))))
    for idx, (i, rel, line_no) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        yield rel, line_no, "\n".join(lines[i:end])


def _extract_quote(block: str) -> Optional[str]:
    """First blockquote line in ``block``; None when absent or UNQUOTABLE.

    The citation line itself is skipped — a concern that merely mentions
    ``file.py:12`` inside a blockquote has not quoted the source.
    """
    if _UNQUOTABLE_RE.search(block):
        return None
    for line in block.splitlines():
        match = _QUOTE_LINE_RE.match(line)
        if not match:
            continue
        candidate = _normalise_code_line(match.group(1))
        if candidate and not _FILE_LINE_RE.fullmatch(candidate):
            return candidate
    return None


def _find_quote_in_file(quote: str, rel: str, root: Path) -> Optional[int]:
    """1-indexed line number where ``quote`` matches ``rel``; None if absent."""
    source = _read_source(root / rel)
    if source is None:
        return None
    lines = source.splitlines()
    for i, line in enumerate(lines, start=1):
        if _normalise_code_line(line) == quote:
            return i
    # A speaker quotes the text it was sent, and that text passed the redaction
    # shield first (lib/review_redaction.py), so a line holding a name or an
    # identifier comes back quoted in redacted form. Compare it the same way.
    redact = _redactor()
    for i, line in enumerate(lines, start=1):
        if _normalise_code_line(redact(line)) == quote:
            return i
    return None


def _matches_raw(quote: str, rel: str, root: Path) -> bool:
    """True when ``quote`` matches a line of ``rel`` as written, before any redaction."""
    source = _read_source(root / rel)
    return source is not None and any(
        _normalise_code_line(line) == quote for line in source.splitlines())


def _redactor():
    """lib/review_redaction.redact, whichever way this module was imported."""
    try:
        from lib.review_redaction import redact
    except ImportError:
        from review_redaction import redact
    return redact


def verify_quoted_lines(text: str, root: Path) -> list[QuotedLineVerdict]:
    """Judge every file:line-citing concern in ``text`` against the real files.

    Returns one verdict per concern. Fail-open on unreadable files: a concern
    whose file cannot be read is UNQUOTABLE (reported, non-blocking) rather
    than silently promoted to blocking.
    """
    out: list[QuotedLineVerdict] = []
    for rel, line_no, block in _iter_concern_blocks(text):
        snippet = _trim_snippet(block)
        quote = _extract_quote(block)
        if not quote:
            out.append(QuotedLineVerdict(
                rel, line_no, "", UNQUOTABLE, None, snippet))
            continue
        matched = _find_quote_in_file(quote, rel, root)
        status = QUOTE_VERIFIED if matched is not None else QUOTE_NOT_FOUND
        via_redaction = matched is not None and not _matches_raw(quote, rel, root)
        out.append(QuotedLineVerdict(
            rel, line_no, quote, status, matched, snippet, via_redaction))
    return out


def _placeholder_note(v: QuotedLineVerdict) -> str:
    """Say whether a verified quote's bracketed placeholder is the shield's or the source's."""
    if not _PLACEHOLDER_RE.search(v.quote):
        return ""
    if v.via_redaction:
        return (" Its bracketed placeholder came from the redaction shield, so a concern"
                " about the placeholder's own text is an artefact.")
    return " Its bracketed placeholder is literal in the source, not a redaction."


def format_quote_gate_note(verdicts: list[QuotedLineVerdict]) -> str:
    """Build the QUOTE GATE note injected into the judge prompt.

    Returns a well-formed placeholder when there is nothing to report, so the
    judge prompt always has a populated slot.
    """
    if not verdicts:
        return "(none — no file:line concerns were raised)"
    rows = []
    for v in verdicts:
        if v.status == QUOTE_VERIFIED:
            where = f"{v.file}:{v.matched_line}"
            drift = " (cited line differs — citation drift, not a defect)" if v.line_drifted else ""
            rows.append(f"- QUOTE-VERIFIED — `{where}` matches the real file{drift}. MAY block."
                        f"{_placeholder_note(v)}")
        elif v.status == QUOTE_NOT_FOUND:
            rows.append(
                f"- QUOTE_NOT_FOUND — `{v.file}` contains no line matching the "
                f"speaker's quote. MUST NOT drive REQUEST_CHANGES."
            )
        else:
            rows.append(
                f"- UNQUOTABLE — the concern on `{v.file}` quoted no source "
                f"line. MUST NOT drive REQUEST_CHANGES."
            )
    return "\n".join(rows)


def format_quote_gate_render_block(verdicts: list[QuotedLineVerdict]) -> str:
    """Render the quote-gate notice shown in the AGORA PR comment.

    Empty string when every concern is blocking-eligible, so callers can
    concatenate freely.
    """
    downgraded = [v for v in verdicts if not v.blocking_eligible]
    if not downgraded:
        return ""
    rows = "\n".join(
        f"- `{v.file}"
        + (f":{v.line_no}" if v.line_no is not None else "")
        + f"` — {v.status}"
        + (f" (quoted: `{v.quote[:80]}`)" if v.quote else " (no source line quoted)")
        for v in downgraded
    )
    n = len(downgraded)
    noun = "concern" if n == 1 else "concerns"
    return (
        f"> **{n} {noun} downgraded to non-blocking.** To block a merge a "
        "concern must quote the offending source line, and that line must be "
        "found in the real file. These were not — they are recorded for the "
        "author but did not gate this merge:\n"
        f"{rows}\n\n"
    )


def format_refutation_render_block(refuted: list[RefutedClaim]) -> str:
    """Render the refutation notice shown in the AGORA PR comment.

    Empty string when nothing was refuted (so callers can concatenate freely).
    """
    if not refuted:
        return ""
    rows = "\n".join(
        f"- `{c.symbol}` — bound at `{c.evidence}` (speaker claimed it was unbound)"
        for c in refuted
    )
    n = len(refuted)
    noun = "claim" if n == 1 else "claims"
    return (
        f"> **⚠ {n} blocker {noun} mechanically refuted.** A deterministic "
        "AST/grep check against the full source file found these symbols ARE "
        "bound — the speaker saw only the truncated diff hunk, not the import "
        "line. Do not loop on these:\n"
        f"{rows}\n\n"
    )


# ---------------------------------------------------------------------------
# Preventive layer: symbol-binding context
#
# verify_symbol_claims above is a CURE. It refutes a phantom "undefined symbol /
# missing import" blocker AFTER a speaker has already emitted it, the judge has
# already weighed it, and (on a REQUEST_CHANGES) a convergence loop has already
# begun chasing it. The claim is only POSSIBLE because the speaker is shown a
# truncated diff hunk and cannot see the import line that binds the symbol.
#
# This builds that missing evidence UP FRONT and hands it to the speaker, so the
# false claim is never formed. Cure and prevention are complementary, not
# redundant: the cure still catches whatever this misses (a binding shape the
# regex does not know, a file too large to read), and the cure's refutation
# count is precisely the metric that says whether this is working.
#
# Deterministic by construction — a line scan of the real files on disk, never a
# model judgement.
# ---------------------------------------------------------------------------

# A line that BINDS a name: module imports (Python + JS/TS), top-level
# declarations, and CommonJS require assignment. Deliberately broad and shallow
# — a false positive costs a few harmless context lines, whereas a false
# negative restores the exact blind spot this exists to close.
_BINDING_LINE_RE = re.compile(
    r"""^\s*(?:
        import\s
      | from\s+\S+\s+import\b
      | (?:export\s+)?(?:default\s+)?(?:async\s+)?(?:const|let|var|function|class)\s+
      | def\s
      | [A-Za-z_$][\w$]*\s*=\s*require\(
    )""",
    re.VERBOSE,
)

# A SIDE-EFFECT import binds nothing into the namespace: `import 'polyfill';`
# (JS) or `import "./styles.css"`. The binding pattern above matches them, but
# listing one under a banner that says "a symbol appearing here IS bound" would
# be a false assurance — precisely the over-claim this layer must not make,
# since its whole purpose is to talk a reviewer OUT of raising a concern. They
# are excluded. Deferred concern 1 of issue #4228.
#
# The trailing-comment branch closes deferred concern 1 of issue #4232: an
# anchored `$` after the quote meant `import './polyfill'; // sets up fetch`
# fell through to _BINDING_LINE_RE and WAS listed as binding a symbol. That is
# the worst shape to miss, because an import binding nothing is exactly the one
# a author annotates — so the commented form is the common case in real source,
# not the exotic one. Only real JS/TS comment syntax is accepted (`//`, `/*`);
# a `#` cannot open a comment on a line that already parses as a JS import.
_SIDE_EFFECT_IMPORT_RE = re.compile(
    r"""^\s*import\s+['"][^'"]+['"]\s*;?\s*(?://.*|/\*.*)?$""",
)

# Cap on files scanned for context. Overridable because a wide refactor can
# exceed it, and past the cap the preventive layer silently stops covering the
# remaining files (they fall back to the post-hoc cure). Deferred concern 2 of
# issue #4228.
_MAX_CONTEXT_FILES_DEFAULT = 12
_MAX_BINDINGS_PER_FILE = 40
_MAX_BINDING_LINE_CHARS = 160


def _max_context_files() -> int:
    """Resolve the file cap, honouring ``AGORA_BINDING_CONTEXT_MAX_FILES``.

    Falls back to the default on anything unparseable or non-positive — a
    malformed env var must not silently disable the layer.
    """
    raw = os.environ.get("AGORA_BINDING_CONTEXT_MAX_FILES", "").strip()
    if not raw:
        return _MAX_CONTEXT_FILES_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return _MAX_CONTEXT_FILES_DEFAULT
    return value if value > 0 else _MAX_CONTEXT_FILES_DEFAULT


def extract_binding_lines(
    source: str, *, limit: int = _MAX_BINDINGS_PER_FILE,
) -> list[tuple[int, str]]:
    """Return ``(lineno, text)`` for each line of ``source`` that binds a name.

    Line numbers are 1-based so they can be cited directly. Each line is
    stripped and clamped to keep the assembled block inside the prompt budget.
    """
    out: list[tuple[int, str]] = []
    for lineno, raw in enumerate(source.splitlines(), start=1):
        if len(out) >= limit:
            break
        if _SIDE_EFFECT_IMPORT_RE.match(raw):
            continue  # binds no name — see _SIDE_EFFECT_IMPORT_RE
        if _BINDING_LINE_RE.match(raw):
            out.append((lineno, raw.strip()[:_MAX_BINDING_LINE_CHARS]))
    return out


def build_binding_context(
    files, repo_root, *, max_files: int | None = None,
) -> str:
    """Build the speaker-prompt block naming the REAL binding sites.

    Returns ``""`` when nothing useful can be read, so a caller may append it
    unconditionally. Only code files are scanned; unreadable or binding-free
    files are skipped silently — this is best-effort context, never a gate.
    """
    root = Path(repo_root)
    cap = _max_context_files() if max_files is None else max_files
    blocks: list[str] = []
    for rel in sorted(files)[:cap]:
        if not rel.endswith(_CODE_EXTENSIONS):
            continue
        source = _read_source(root / rel)
        if source is None:
            continue
        bindings = extract_binding_lines(source)
        if not bindings:
            continue
        rows = "\n".join(f"  {n}: {text}" for n, text in bindings)
        blocks.append(f"### {rel}\n{rows}")

    if not blocks:
        return ""
    return (
        "## Symbol binding context (read from the FULL files on disk)\n\n"
        "These are the REAL binding sites in the changed files, including lines "
        "the truncated diff above does NOT show. A symbol that appears here IS "
        "bound — do not raise an undefined-symbol, missing-import or NameError "
        "concern about it. If you believe a symbol is unbound, check this list "
        "first.\n\n" + "\n\n".join(blocks) + "\n"
    )


# ---------------------------------------------------------------------------
# Temporal-paradox gate (GRIP #4466)
#
# Observed 2026-08-10 across follow-up issues #4270, #4271 and #4300: a speaker
# read genuine, already-past dates in a diff ("Origin 2026-07-14", "Verified
# 2026-08-01"), decided they were impossible, and filed the concern as
# "Temporal incoherence / False provenance" recommending a [SIMULATION] marker
# or a date "correction".
#
# Root cause: a model's training cutoff sits before the wall clock, so it infers
# that any date it does not recognise must be fabricated. Nothing in the AGORA
# prompt path told any model what day it is — ``import datetime`` did not appear
# once in either this module or lib/agora_pr_review.py.
#
# Why it matters more than an ordinary false positive: acting on it corrupts the
# provenance GRIP exists to guarantee. A reviewer asking us to stamp
# [SIMULATION] on a true dated record is asking us to falsify the record.
#
# SOUNDNESS (mirrors the symbol gate above): this gate only ever REFUTES a
# *specific* temporal claim, and only by POSITIVELY confirming that every date
# it cites is on or before the real current date. A date genuinely in the future
# is NOT refuted — the concern passes through untouched and is judged on the
# normal rules. Conservative by construction: no dates parsed ⇒ no refutation.
#
# There is deliberately no cutoff year anywhere in this module. The comparison
# is against ``date.today()`` (injectable as ``today=`` for tests), so the gate
# keeps working in 2027 and 2035 without an edit.
# ---------------------------------------------------------------------------

TEMPORAL_REFUTED = "TEMPORAL_REFUTED"
"""Every date the concern cites is on or before today — a training-cutoff artefact."""

TEMPORAL_FUTURE = "TEMPORAL_FUTURE"
"""The concern cites at least one date strictly after today — not refuted."""

TEMPORAL_UNDATED = "TEMPORAL_UNDATED"
"""Temporal phrasing but no parseable date — cannot be mechanically settled."""

# Phrases that mark a concern as challenging a date on temporal-plausibility
# grounds. Drawn from the real #4270/#4271/#4300 text plus the marker forms the
# issue names. Matched case-insensitively per line (with a 1-line window).
_TEMPORAL_TRIGGER_RE = re.compile(
    r"temporal\s+(?:paradox|incoherence|inconsisten|impossib|anomal)|"
    r"false\s+provenance|"
    r"\[\s*(?:SIMULATION|FICTIONAL|HYPOTHETICAL|SPECULATIVE)\s*\]|"
    r"(?:simulation|fictional|hypothetical)\s+(?:marker|tag|label|disclaimer)|"
    r"future[-\s]dated|dated\s+in\s+the\s+future|"
    r"(?:date|time-?stamp|year)s?\s+(?:that\s+)?(?:are\s+|is\s+|lie\s+|fall\s+)?in\s+the\s+future|"
    r"from\s+the\s+future|"
    r"impossible\s+(?:date|time-?stamp|year)|"
    r"(?:has|have)\s+not\s+(?:yet\s+)?(?:happened|occurred|arrived)|"
    r"(?:yet\s+to\s+(?:occur|happen))|"
    r"(?:date|time-?stamp|year)s?\s+(?:cannot|can'?t|could\s+not|couldn'?t)\s+"
    r"(?:be|have|yet)",
    re.IGNORECASE,
)

# ISO-8601 calendar dates — GRIP's convention throughout (Origin: 2026-07-14).
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

# Bare 4-digit year, used ONLY when the window carries no full ISO date
# ("timestamps cite dates in 2026"). Restricted to 19xx/20xx so line counts and
# port numbers cannot masquerade as years.
_BARE_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


@dataclass(frozen=True)
class TemporalClaim:
    """A speaker concern that challenged a date on temporal-plausibility grounds.

    ``status`` is one of the three module constants. ``cited`` holds the date
    (or bare-year) tokens found in the claim window, verbatim; ``future`` is the
    subset strictly after the comparison date. ``claim_snippet`` is the speaker
    text that made the claim, for human traceability.
    """
    status: str
    cited: tuple[str, ...]
    future: tuple[str, ...]
    claim_snippet: str


def _parse_window_dates(window: str, today: date) -> tuple[list[str], list[str]]:
    """Return (cited_tokens, future_tokens) for one claim window.

    ISO dates are preferred; a bare year is consulted only when the window
    contains no ISO-SHAPED token at all.

    The shape test, not the parse result, gates the fallback — deliberately. An
    ISO-shaped token that will not parse (``2026-13-45``) leaves the window
    ambiguous, and falling through to its bare year would refute the concern on
    the strength of a date nobody can read. Conservative by construction, as the
    symbol gate above is: what cannot be positively confirmed refutes nothing.
    """
    cited: list[str] = []
    future: list[str] = []
    saw_iso_shape = False
    for match in _ISO_DATE_RE.finditer(window):
        saw_iso_shape = True
        try:
            parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        cited.append(match.group(0))
        if parsed > today:
            future.append(match.group(0))
    if cited or saw_iso_shape:
        return cited, future
    for match in _BARE_YEAR_RE.finditer(window):
        cited.append(match.group(0))
        if int(match.group(0)) > today.year:
            future.append(match.group(0))
    return cited, future


def verify_temporal_claims(
    speaker_text: str,
    today: Optional[date] = None,
) -> list[TemporalClaim]:
    """Classify every temporal-plausibility concern in ``speaker_text``.

    Args:
        speaker_text: combined Speaker B + C output (the graded reviewers).
        today: comparison date; defaults to the real current date. Injectable so
            tests pin behaviour without a hardcoded year and without the wall
            clock making them flap.

    Returns:
        One ``TemporalClaim`` per triggering window, deduplicated by snippet.
    """
    if not speaker_text or not speaker_text.strip():
        return []
    now = today or date.today()
    claims: list[TemporalClaim] = []
    seen: set[str] = set()
    for window, _idx in _iter_windows(speaker_text, _TEMPORAL_TRIGGER_RE):
        snippet = _trim_snippet(window)
        if snippet in seen:
            continue
        seen.add(snippet)
        cited, future = _parse_window_dates(window, now)
        if not cited:
            status = TEMPORAL_UNDATED
        elif future:
            status = TEMPORAL_FUTURE
        else:
            status = TEMPORAL_REFUTED
        claims.append(
            TemporalClaim(
                status=status,
                cited=tuple(cited),
                future=tuple(future),
                claim_snippet=snippet,
            )
        )
    return claims


def _temporal_claim_line(claim: TemporalClaim, now: date) -> str:
    """Render one classified temporal claim as a judge-prompt bullet."""
    if claim.status == TEMPORAL_REFUTED:
        return (
            f"- REFUTED — the concern challenges {', '.join(claim.cited)} as "
            f"temporally impossible, but every one of those dates is on or "
            f"before the current date {now.isoformat()}. They are real and in "
            f"the past. This concern MUST NOT drive REQUEST_CHANGES, and its "
            f'recommendation must not be adopted. Concern: "{claim.claim_snippet}"'
        )
    if claim.status == TEMPORAL_FUTURE:
        return (
            f"- NOT REFUTED — the concern cites {', '.join(claim.future)}, which "
            f"is after the current date {now.isoformat()}. A genuinely future "
            f"date may be worth raising, so this one is NOT neutralised; judge "
            f'it on the normal rules. Concern: "{claim.claim_snippet}"'
        )
    return (
        f"- UNDATED — temporal phrasing with no parseable date, so it cannot be "
        f"settled mechanically. Treat with suspicion: the current date is "
        f'{now.isoformat()}. Concern: "{claim.claim_snippet}"'
    )


def format_temporal_gate_note(
    claims: list[TemporalClaim],
    today: Optional[date] = None,
) -> str:
    """Build the TEMPORAL GATE note injected into the judge prompt.

    Returns a well-formed placeholder when there is nothing to report, so the
    judge prompt always has a filled slot.
    """
    now = today or date.today()
    if not claims:
        return (
            f"(none — no concern challenged a date on temporal-plausibility "
            f"grounds. The current date is {now.isoformat()}.)"
        )
    return "\n".join(_temporal_claim_line(c, now) for c in claims)


def format_temporal_defer_warning(claims: list[TemporalClaim]) -> str:
    """Warning banner prepended to a DEFER follow-up issue body.

    A deferred concern becomes a GitHub issue that a later agent picks up and
    acts on. Without this banner the corruption propagates one hop further: the
    agent "fixes" a true date because the issue text told it to. Empty string
    when nothing was refuted, so a clean review adds no noise.
    """
    refuted = [c for c in claims if c.status == TEMPORAL_REFUTED]
    if not refuted:
        return ""
    dates = sorted({d for c in refuted for d in c.cited})
    return (
        "> [!WARNING]\n"
        "> **Do not act on the temporal concern(s) below.** A deterministic "
        f"check confirmed that {', '.join(dates)} "
        f"{'is' if len(dates) == 1 else 'are'} on or before the current date, "
        "so the dated record is accurate. The reviewing model's training cutoff "
        "predates it (GRIP #4466).\n"
        ">\n"
        "> Never \"correct\" a date, and never add a `[SIMULATION]` or "
        "`[FICTIONAL]` marker to dated provenance, on a reviewer's say-so — "
        "that falsifies a true record. A human must confirm the date is "
        "genuinely wrong first.\n"
    )
