"""No-Stubs / Completeness detector — pure-AST classifier for hollow "scrub" bodies.

Completeness is the integrity-axis design principle (handle: "No Scrubs"): given that
you ARE writing a symbol, you write it FULLY — never a hollow placeholder that names a
capability the code does not provide. It is the ORTHOGONAL complement of YAGNI (the
need-axis: whether to build), not a fold-in of it. The load-bearing harm is the FALSE
COMPLETENESS SIGNAL: a later reader sees the wired-looking symbol, assumes it is
implemented, builds on it, and confabulates. A loud-failing stub is a quieter lie, not
the truth — so ``raise NotImplementedError("… not wired yet")`` is a scrub, the same as
a silent ``pass``.

This module is the deterministic floor (L1) of the No-Stubs gate. It is PURE: parse
source text, return findings. No I/O, no environment, no network — trivially mockable.
The PreToolUse gate (``hooks/no-stubs-gate.py``) layers the introduced-vs-pre-existing
verdict policy on top; this module only classifies.

Design (hardened by the broly council + 3 adversaries — see rules/no-stubs.md):

  AST, never lexical. A lexical ``rg NotImplementedError`` gate yields false positives
  on every @abstractmethod body and template-method hook (the legit population is LARGER
  than the scrub population), and misses ``return {"error": "not implemented"}`` entirely.

  Detection unit = a FunctionDef/AsyncFunctionDef whose body (after an optional leading
  docstring) reduces to ONE hollow shape:
    * ``pass``                              — only a CANDIDATE if a drift-marker co-occurs
    * ``...`` (Ellipsis)                    — only a CANDIDATE if a drift-marker co-occurs
    * ``raise NotImplementedError[(...)]``  — the canonical skeleton shape
    * ``return <constant>``                 — only a CANDIDATE if the RETURNED VALUE's own
                                              string content carries a deferral PHRASE
                                              (RETURN_DRIFT_PHRASES) — NOT a bare marker
                                              token. A bare token (TODO/FIXME/stub/wip/tbd)
                                              in a returned value is legitimate DATA (an rg
                                              pattern ``"TODO|FIXME|HACK"``, an error catalog),
                                              not a stub signal. raise/pass/ellipsis keep the
                                              full DRIFT_MARKERS set (a raise message
                                              ``"TODO: wire this"`` IS a stub signal).
  A function with a real body is never a candidate (definition-scoped — an in-body raise
  inside otherwise-live logic is out of scope by construction; documented in rules/).

  Carve-outs (→ NO finding, silently legit), AST-walked not regex:
    * @abstractmethod / @abc.abstractmethod / @abstractproperty / @overload decorators
    * enclosing class with a Protocol base (bodies are signatures by language spec)
    * .pyi type-stub files (whole file is stubs)
    * same-module template-method base hook: a bare ``raise NotImplementedError`` whose
      method is overridden by a concrete (non-hollow) method on a same-module subclass
      (grip_notify.Backend / findmy_reader.Reader are the canonical instances)
    * honest empty value: ``return None/[]/{}/""/0/False`` — complete by construction,
      even with a deferral COMMENT (backlog/reducer is the canonical GOOD pattern)

  Classification of a residual candidate:
    * drift-marker present  -> "scrub"   (drift WINS even if an anti-marker co-occurs —
                                          "not supported … land in W3+" is a scrub)
    * clean anti-marker, no drift -> ALLOW (by-design-complete: a read-only __setitem__
                                          raising "unsupported" IS a complete impl)
    * bare raise, no marker, no carve-out -> "suspect"  (fail-safe-toward-review: an
                                          unmarked stub and a forgotten abstract hook are
                                          AST-indistinguishable without cross-module
                                          resolution; bias to WARN, never silent-allow,
                                          never hard-DENY)

The verdict mapping (scrub/suspect × introduced/pre-existing -> DENY/WARN/AUDIT) lives in
the gate, not here. Origin: V>> directive 2026-06-08; rules/no-stubs.md.
"""

from __future__ import annotations

import ast
import itertools
from dataclasses import dataclass
from typing import Optional

__all__ = [
    "StubFinding",
    "analyze_source",
    "DRIFT_MARKERS",
    "RETURN_DRIFT_PHRASES",
    "ANTI_MARKERS",
]

# Deferral markers — "not done yet". Their presence in a hollow body's raise-message,
# docstring, adjacent comment, or returned-string content marks a SCRUB. The list is
# non-exhaustive BY CONSTRUCTION (an author can always coin a new phrase) — which is
# exactly why an unmarked hollow body fails to "suspect" (WARN), never to silent-allow.
DRIFT_MARKERS: tuple[str, ...] = (
    "not wired",
    "not yet",
    "not implemented",  # covers "not yet implemented", "interface only, not implemented"
    "to be implemented",
    "wired later",
    "wire later",
    "wire when",
    "wires this",
    "p2 wires",
    "p3 wires",
    "p2/p3",
    "phase 2",
    "phase 3",
    "phase n",
    "land in w",
    "w3+",
    "w-future",
    "skeleton",
    "stub",
    "placeholder",
    "coming soon",
    "isn't here yet",
    "isnt here yet",
    "for now",
    "tbd",
    "wip",
    "scaffold",
    "todo",
    "fixme",
    "interface only",
)

# Return-shape deferral PHRASES — the SUBSET of drift that signals a fake error-contract
# return (``return {"error": "not yet implemented"}``). The return shape is special: a
# returned value's string content is frequently DATA, not intent — an rg pattern
# ``"TODO|FIXME|HACK"``, a lint config, an error catalog naming markers. So a bare marker
# token (todo/fixme/stub/wip/tbd/scaffold) in a returned value is NOT a stub. Only an
# explicit deferral PHRASE — one that reads as "this capability is not provided" rather
# than as data — marks a return-shape scrub. raise/pass/ellipsis keep the FULL DRIFT_MARKERS
# set (their marker text is the author's intent, never returned data). _norm folding applies
# here too (so "not-yet-implemented"/"not_yet_implemented" match the space-form phrases).
RETURN_DRIFT_PHRASES: tuple[str, ...] = (
    "not implemented",
    "not yet",
    "not wired",
    "wired later",
    "wire later",
    "coming soon",
    "placeholder",
    "phase 2",
    "phase 3",
    "phase n",
    "interface only",
    "to be implemented",
    "not supported yet",
)

# Anti-markers — "complete by design / contract". They force ALLOW ONLY when NO drift
# marker co-occurs (drift always wins — the more specific, falsifiable signal).
ANTI_MARKERS: tuple[str, ...] = (
    "intentionally",
    "unsupported",
    "read-only",
    "read only",
    "readonly",
    "immutable",
    "by design",
    "by-design",
    "not supported",
    "must override",
    "must be overridden",
    "subclass must",
    "subclasses override",
    "subclasses must",
    "override this",
    "requires",  # runtime-dependency boundary: "requires grip-channel MCP / SLACK_BOT_TOKEN"
    "not applicable",
)


@dataclass(frozen=True)
class StubFinding:
    """One hollow-body classification. ``classification`` is "scrub" (drift present) or
    "suspect" (bare hollow, fail-safe). Carve-outs produce NO StubFinding."""

    qualified_name: str
    lineno: int
    shape: str  # "pass" | "ellipsis" | "raise" | "return"
    classification: str  # "scrub" | "suspect"
    signal: str  # the drift marker matched, or "" for suspect
    reason: str


# --------------------------------------------------------------------------- #
# Body-shape classification
# --------------------------------------------------------------------------- #

_FuncDef = (ast.FunctionDef, ast.AsyncFunctionDef)


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """Return the body without a leading string-literal docstring."""
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _is_notimplemented_raise(stmt: ast.stmt) -> bool:
    """True for ``raise NotImplementedError`` or ``raise NotImplementedError(...)``."""
    if not isinstance(stmt, ast.Raise) or stmt.exc is None:
        return False
    exc = stmt.exc
    name = exc.func if isinstance(exc, ast.Call) else exc
    return isinstance(name, ast.Name) and name.id == "NotImplementedError"


def _is_empty_literal(node: Optional[ast.expr]) -> bool:
    """True for the honest-empty return values (None / [] / {} / () / "" / 0 / False)."""
    if node is None:
        return True
    if isinstance(node, ast.Constant):
        return node.value in (None, "", 0, False) or node.value == 0.0
    if isinstance(node, (ast.List, ast.Dict, ast.Tuple, ast.Set)):
        return not getattr(node, "elts", getattr(node, "keys", []))
    return False


def _shape_of(body: list[ast.stmt]) -> Optional[str]:
    """Classify a docstring-stripped body into a hollow shape, or None if it has real
    logic. A docstring-only body counts as ``pass`` (implicitly returns None)."""
    if not body:
        return "pass"  # docstring-only function
    if len(body) != 1:
        return None
    stmt = body[0]
    if isinstance(stmt, ast.Pass):
        return "pass"
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
        return "ellipsis"
    if _is_notimplemented_raise(stmt):
        return "raise"
    if isinstance(stmt, ast.Return):
        return "return"
    return None


# --------------------------------------------------------------------------- #
# Marker scanning
# --------------------------------------------------------------------------- #


def _string_constants(node: ast.AST) -> list[str]:
    """All string-literal values reachable inside an AST node (e.g. a returned dict's
    messages, or a raise's argument)."""
    out: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            out.append(child.value)
    return out


def _norm(text: str) -> str:
    """Lowercase + fold hyphens/underscores to spaces so 'not-yet-implemented' and
    'not_yet_implemented' match the space-form markers (drift AND anti)."""
    return text.lower().replace("-", " ").replace("_", " ")


def _first_drift(text: str) -> str:
    low = _norm(text)
    for m in DRIFT_MARKERS:
        if m in low:
            return m
    return ""


def _first_return_drift(text: str) -> str:
    """Return-shape drift: PHRASES only (RETURN_DRIFT_PHRASES), never bare marker tokens —
    a returned value's string content is data as often as intent (rg pattern / error catalog)."""
    low = _norm(text)
    for m in RETURN_DRIFT_PHRASES:
        if m in low:
            return m
    return ""


def _has_anti(text: str) -> bool:
    low = _norm(text)
    return any(m in low for m in ANTI_MARKERS)


def _docstring_of(func: ast.AST) -> str:
    try:
        return ast.get_docstring(func) or ""  # type: ignore[arg-type]
    except TypeError:
        return ""


# --------------------------------------------------------------------------- #
# Carve-out detection
# --------------------------------------------------------------------------- #

_STUB_DECORATORS = {
    "abstractmethod",
    "abstractproperty",
    "abc.abstractmethod",
    "abc.abstractproperty",
    "overload",
    "typing.overload",
}


def _decorator_names(func: ast.AST) -> set[str]:
    names: set[str] = set()
    for dec in getattr(func, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        names.add(_dotted_name(target))
    return names


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    return ""


def _base_names(cls: ast.ClassDef) -> set[str]:
    return {_dotted_name(b) for b in cls.bases}


def _is_protocol_class(cls: ast.ClassDef) -> bool:
    return any(b.split(".")[-1] == "Protocol" for b in _base_names(cls))


# --------------------------------------------------------------------------- #
# Module-level walk
# --------------------------------------------------------------------------- #


def _concrete_methods(cls: ast.ClassDef) -> list[str]:
    """Names of cls's methods that are a real override — NOT a bare hollow
    (pass / ... / raise NotImplementedError). A method that RETURNS a value is a
    concrete override (e.g. ``def _probe(self): return True``)."""
    return [
        item.name
        for item in cls.body
        if isinstance(item, _FuncDef)
        and _shape_of(_strip_docstring(item.body)) not in ("pass", "ellipsis", "raise")
    ]


def _base_simple_names(cls: ast.ClassDef) -> set[str]:
    """Simple (last dotted-component) names of cls's base classes."""
    return {b.split(".")[-1] for b in _base_names(cls)}


def _concrete_override_index(module: ast.Module) -> dict[tuple[str, str], bool]:
    """Map (base_class_simple_name, method_name) -> True when SOME class in this module
    subclasses that base and provides a concrete override of the method. Used for the
    template-method-base-hook carve-out (grip_notify.Backend / findmy_reader.Reader).

    No nested loop: ONE generator over classes; the per-class base x method product is an
    itertools.product CALL and the cross-class flatten is itertools.chain — both calls,
    not loop constructs. Bounded by the module's own size (a handful of classes x methods)."""
    classes = [n for n in module.body if isinstance(n, ast.ClassDef)]
    per_class = (
        itertools.product(_base_simple_names(cls), _concrete_methods(cls))
        for cls in classes
    )
    return dict.fromkeys(itertools.chain.from_iterable(per_class), True)


def analyze_source(src: str, *, path: str = "<unknown>") -> list[StubFinding]:
    """Parse ``src`` and return a StubFinding for every hollow body that is NOT carved
    out. ``path`` selects the .pyi whole-file carve-out. A syntax error yields no
    findings (the gate fails open — a parser cannot adjudicate unparseable text)."""
    if path.endswith(".pyi"):
        return []
    try:
        module = ast.parse(src)
    except SyntaxError:
        return []

    overrides = _concrete_override_index(module)
    findings: list[StubFinding] = []
    _walk(module.body, prefix="", cls=None, overrides=overrides, out=findings)
    return findings


def _walk(
    body: list[ast.stmt],
    *,
    prefix: str,
    cls: Optional[ast.ClassDef],
    overrides: dict[tuple[str, str], bool],
    out: list[StubFinding],
) -> None:
    for node in body:
        if isinstance(node, ast.ClassDef):
            _walk(node.body, prefix=f"{prefix}{node.name}.", cls=node, overrides=overrides, out=out)
        elif isinstance(node, _FuncDef):
            finding = _classify_function(node, prefix=prefix, cls=cls, overrides=overrides)
            if finding is not None:
                out.append(finding)
            # nested functions: methods/closures inside this function are walked too
            _walk(node.body, prefix=f"{prefix}{node.name}.", cls=None, overrides=overrides, out=out)


def _is_carved_out(
    func: ast.AST,
    cls: Optional[ast.ClassDef],
    shape: str,
    overrides: dict[tuple[str, str], bool],
) -> bool:
    """Shape-independent carve-outs: abstract/overload decorator, Protocol base, or a
    same-module template-method base hook (a bare raise overridden by a concrete subclass)."""
    if _decorator_names(func) & _STUB_DECORATORS:
        return True
    if cls is not None and _is_protocol_class(cls):
        return True
    if shape == "raise" and cls is not None:
        name = func.name  # type: ignore[attr-defined]
        # self-name check is base-independent — must run even when cls has NO bases
        # (the template-method ROOT class, e.g. grip_notify.Backend, has no bases of its own)
        if overrides.get((cls.name, name)):
            return True
        for base in _base_names(cls):
            if overrides.get((base.split(".")[-1], name)):
                return True
    return False


def _intent_haystack(shape: str, body: list[ast.stmt], docstring: str) -> Optional[str]:
    """The text whose markers carry the author's intent. Returns None when the body is an
    honest empty value (return None/[]/{}/"") — a complete-by-construction carve-out."""
    if shape == "return":
        ret = body[0].value if isinstance(body[0], ast.Return) else None
        if _is_empty_literal(ret):
            return None
        return " ".join(_string_constants(ret)) if ret is not None else ""
    if shape == "raise":
        return " ".join(_string_constants(body[0])) + " " + docstring
    return docstring  # pass / ellipsis: only the docstring/comment carries intent


def _verdict(qualified: str, lineno: int, shape: str, haystack: str) -> Optional[StubFinding]:
    """Drift wins over anti; bare pass/ellipsis/return need a drift signal to be a
    candidate; a bare unmarked raise falls through to 'suspect' (fail-safe to review).

    Severity by shape: only a ``raise`` or fake-``return`` NAMES a capability it fails to
    provide — the corrosive false-completeness signal — so those are 'scrub' (DENY-class).
    An empty ``pass``/``...`` names nothing; a casual deferral comment on a no-op is at most
    'suspect' (WARN-class), never a hard scrub (the verified corpus has zero bare-pass scrubs,
    and empty no-op overrides are overwhelmingly legitimate — adversary A1).

    Drift detection is SHAPE-AWARE: the ``return`` shape keys on RETURN_DRIFT_PHRASES
    (deferral phrases only) because a returned value's string content is frequently DATA —
    a bare marker token (TODO/FIXME/stub) in a returned dict is an rg pattern or error
    catalog, not a stub. raise/pass/ellipsis use the full DRIFT_MARKERS set (their text is
    authorial intent, never returned data)."""
    drift = _first_return_drift(haystack) if shape == "return" else _first_drift(haystack)
    if shape in ("pass", "ellipsis", "return") and not drift:
        return None  # empty no-op / honest default — not a candidate without a drift signal
    if drift:
        scrub = shape in ("raise", "return")
        return StubFinding(
            qualified_name=qualified,
            lineno=lineno,
            shape=shape,
            classification="scrub" if scrub else "suspect",
            signal=drift,
            reason=(
                f"hollow {shape} body with deferral marker {drift!r} — "
                + ("a scrub (false completeness signal — names a capability it does not provide)"
                   if scrub
                   else "an empty body with a deferral hint (review whether intentional)")
            ),
        )
    if _has_anti(haystack):
        return None  # clean anti-marker, no drift — by-design-complete (e.g. read-only __setitem__)
    return StubFinding(
        qualified_name=qualified,
        lineno=lineno,
        shape=shape,
        classification="suspect",
        signal="",
        reason="bare raise NotImplementedError with no marker and no abstract/override carve-out — fail-safe to review",
    )


def _classify_function(
    func: ast.AST,
    *,
    prefix: str,
    cls: Optional[ast.ClassDef],
    overrides: dict[tuple[str, str], bool],
) -> Optional[StubFinding]:
    body = _strip_docstring(func.body)  # type: ignore[attr-defined]
    shape = _shape_of(body)
    if shape is None:
        return None
    if _is_carved_out(func, cls, shape, overrides):
        return None
    haystack = _intent_haystack(shape, body, _docstring_of(func))
    if haystack is None:
        return None  # honest empty value (reducer GOOD pattern)
    qualified = f"{prefix}{func.name}"  # type: ignore[attr-defined]
    return _verdict(qualified, getattr(func, "lineno", 0), shape, haystack)


def _scan_one(path: str, include_suspect: bool) -> int:
    """Scan ONE file: print its reportable findings, return its scrub count. Extracted so
    the CLI has no nested loop (one loop here, one over paths in _main)."""
    import sys

    try:
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
    except OSError as exc:
        print(f"[skip] {path}: {exc}", file=sys.stderr)
        return 0
    reportable = [
        f for f in analyze_source(src, path=path)
        if f.classification == "scrub" or include_suspect
    ]
    for f in reportable:
        print(f"{path}:{f.lineno}\t{f.classification.upper()}\t{f.qualified_name}\t{f.reason}")
    return sum(1 for f in reportable if f.classification == "scrub")


def _main(argv: Optional[list[str]] = None) -> int:
    """Scan files for scrubs. Exit 1 if any 'scrub' (drift-marked) finding exists."""
    import argparse

    ap = argparse.ArgumentParser(description="No-Stubs / Completeness scanner (AST).")
    ap.add_argument("paths", nargs="+", help="Python files to scan")
    ap.add_argument("--include-suspect", action="store_true", help="also report 'suspect' hollow bodies")
    args = ap.parse_args(argv)
    return 1 if sum(_scan_one(p, args.include_suspect) for p in args.paths) else 0


if __name__ == "__main__":  # pragma: no cover - thin CLI
    raise SystemExit(_main())
