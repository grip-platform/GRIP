#!/usr/bin/env python3
"""wiring_perimeter_detector.py — flag 'declared input that reaches nothing'.

The class this kills reached N=4 before earning a shim (ultralearn subject,
ultrareason question, ultrajudge question, ultraread --council — see
plans/ULTRAPROMPT-hal-wiring-perimeter-2026-08-17.md §5 and
rules/knowledge-maturation-functor.md): a surface DECLARES an input — a CLI
flag, an operator argument, a dispatcher parameter — that no code ever reads,
so the artefact looks like it answered the caller while containing something
the caller never influenced.

Three statically detectable shapes, each calibrated against the real defect
that motivated it (tests/test_wiring_perimeter_detector.py pins all four
mandatory cases against the actual git objects):

A. ``dead-cli-flag`` — an ``argparse`` dest never read as ``args.<dest>``,
   with no indirection (``vars``/``getattr``/bare ``args`` passthrough /
   subparsers → abstain). The ultraread ``--council`` shape.
B. ``argv-blind-cli`` — a module with a ``__main__`` entrypoint that reads
   NO CLI input at all (no ``sys.argv``, no argparse/click/typer) while a
   same-named operator surface (``skills/<stem>/SKILL.md`` or
   ``commands/<stem>.md``) exists — so ``/<stem> anything`` is silently
   dropped. The pre-fix ultralearn shape. Requires ``repo_root``; abstains
   without it.
C. ``dead-dispatcher-no-live-path`` — a ``*dispatch*`` function with a
   parameter its body never reads, in a module that imports NO adapter, no
   HTTP client and no subprocess anywhere (function-level imports included).
   The pre-wiring ultrajudge shape. A dead param in a stub BESIDE a live
   dispatcher is the sanctioned labelled-fallback shape and is NOT flagged.

Detection is conservative by design: every indirection abstains, because a
false DENY on a legitimate pattern costs more than a missed WARN
(no-stubs.md precedent — WARN first, measure FP, then promote).
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

# argparse actions whose dest argparse itself consumes — never a dead flag.
_SELF_CONSUMED_ACTIONS = {"help", "version"}

# Dead BOOLEAN toggles are out of scope: the defect class is a silently
# dropped operator VALUE (--council frontier, a subject string), and the
# measured lib/ population's dead booleans were all documented deliberate
# no-ops ("accepted for parity; no-op"). Revisit per the rule's
# falsification clause if a real dead-boolean incident ever occurs.
_VALUELESS_ACTIONS = {"store_true", "store_false", "count"}

# Any import (top-level or function-level) whose module or bound name contains
# one of these marks the module as having a live dispatch path.
_LIVE_IMPORT_MARKERS = (
    "hal_llm_adapter", "urllib", "http", "requests", "aiohttp", "socket",
    "subprocess", "sovereign_council", "hal_council", "openai", "anthropic",
)

_CLI_FRAMEWORKS = frozenset(
    ("argparse", "optparse", "click", "typer", "fire", "docopt"))

_FuncDef = Union[ast.FunctionDef, ast.AsyncFunctionDef]


@dataclass(frozen=True)
class Finding:
    """One declared-but-unreachable input."""

    shape: str   # dead-cli-flag | argv-blind-cli | dead-dispatcher-no-live-path
    name: str    # the dest / module stem / "func.param"
    line: int
    detail: str


def _import_names(node: Union[ast.Import, ast.ImportFrom]) -> list[str]:
    """The module string plus every bound alias name of one import node."""
    mod = getattr(node, "module", "") or ""
    return [mod] + [a.name for a in node.names]


# ------------------------------------------------------------------ shape A


def _dest_of(call: ast.Call) -> Optional[tuple[str, str]]:
    """(dest, action) for an add_argument call, or None if underivable."""
    action = ""
    for kw in call.keywords:
        if kw.arg == "action" and isinstance(kw.value, ast.Constant):
            action = str(kw.value.value)
        if kw.arg == "dest" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value), action
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            name = arg.value
            if name.startswith("--"):
                return name[2:].replace("-", "_"), action
            if name.startswith("-"):
                continue  # short alias; wait for the long form or dest=
            return name, action
    return None


def _record_declared(node: ast.Call, dests: dict[str, int]) -> None:
    got = _dest_of(node)
    if got and got[1] not in (_SELF_CONSUMED_ACTIONS | _VALUELESS_ACTIONS):
        dests.setdefault(got[0], node.lineno)


def _declared_dests(tree: ast.AST) -> tuple[dict[str, int], bool]:
    """{dest: line} of add_argument calls + whether shape A must abstain
    (subparsers / parse_known_args are structurally indirect)."""
    dests: dict[str, int] = {}
    abstain = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr in ("add_subparsers", "parse_known_args"):
            abstain = True
        if node.func.attr == "add_argument":
            _record_declared(node, dests)
    return dests, abstain


def _parse_args_target(node: ast.AST) -> Optional[list[str]]:
    """Assignment-target names when node is ``X = <...>.parse_args(...)``."""
    if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
        return None
    func = node.value.func
    if not (isinstance(func, ast.Attribute) and func.attr == "parse_args"):
        return None
    return [t.id for t in node.targets if isinstance(t, ast.Name)]


def _args_names(tree: ast.AST) -> set[str]:
    """Names bound from a .parse_args() call (usually {'args'})."""
    names: set[str] = set()
    for node in ast.walk(tree):
        names.update(_parse_args_target(node) or [])
    return names


def _attr_reads(tree: ast.AST, args_names: set[str]) -> Optional[tuple[set[str], set[int]]]:
    """(dests read as args.<dest>, node ids of the attribute-base Names).
    None when vars()/getattr()/__dict__ indirection forces an abstain."""
    attr_base_ids: set[int] = set()
    consumed: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in args_names:
                attr_base_ids.add(id(node.value))
                if node.attr == "__dict__":
                    return None
                consumed.add(node.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("vars", "getattr"):
                return None
    return consumed, attr_base_ids


def _bare_args_use(tree: ast.AST, args_names: set[str],
                   attr_base_ids: set[int]) -> bool:
    """True when the args object is loaded bare (passed on / re-bound) —
    every dest must then be treated as consumed."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id in args_names
                and isinstance(node.ctx, ast.Load)
                and id(node) not in attr_base_ids):
            return True
    return False


def _scan_dead_flags(tree: ast.AST) -> list[Finding]:
    dests, abstain = _declared_dests(tree)
    if abstain or not dests:
        return []
    names = _args_names(tree)
    if not names:
        return []
    reads = _attr_reads(tree, names)
    if reads is None:
        return []
    consumed, attr_base_ids = reads
    if _bare_args_use(tree, names, attr_base_ids):
        return []
    return [
        Finding("dead-cli-flag", dest, line,
                f"argparse declares '{dest}' but no code reads args.{dest} — "
                "a caller's input silently reaches nothing")
        for dest, line in sorted(dests.items()) if dest not in consumed
    ]


# ------------------------------------------------------------------ shape B


def _has_main_guard(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _mentions_dunder_name(node.test):
            return True
    return False


def _mentions_dunder_name(test: ast.AST) -> bool:
    for sub in ast.walk(test):
        if isinstance(sub, ast.Name) and sub.id == "__name__":
            return True
    return False


def _reads_cli_input(tree: ast.AST) -> bool:
    """True when the module can receive CLI input at all."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "argv":
            return True
        if isinstance(node, ast.Name) and node.id == "argv":
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if _CLI_FRAMEWORKS & set(_import_names(node)):
                return True
    return False


def _companion_surface(path: str, repo_root: Path) -> Optional[str]:
    stem = Path(path).stem
    for rel in (Path("skills") / stem / "SKILL.md", Path("commands") / f"{stem}.md"):
        if (repo_root / rel).is_file():
            return str(rel)
    return None


def _scan_argv_blind(tree: ast.AST, path: str,
                     repo_root: Optional[Path]) -> list[Finding]:
    if repo_root is None or not _has_main_guard(tree) or _reads_cli_input(tree):
        return []
    companion = _companion_surface(path, repo_root)
    if not companion:
        return []
    return [Finding(
        "argv-blind-cli", Path(path).stem, 1,
        f"{companion} gives operators a /{Path(path).stem} surface, but this "
        "module's entrypoint reads no CLI input at all — any argument the "
        "operator types is silently dropped",
    )]


# ------------------------------------------------------------------ shape C


def _is_live_import(node: ast.AST) -> bool:
    if not isinstance(node, (ast.Import, ast.ImportFrom)):
        return False
    hay = " ".join(_import_names(node))
    return any(marker in hay for marker in _LIVE_IMPORT_MARKERS)


def _has_live_import(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if _is_live_import(node):
            return True
    return False


def _is_signature_only(func: _FuncDef) -> bool:
    """True for contract bodies (docstring / pass / ...) — Protocol and ABC
    signatures read nothing by definition and are never actors
    (no-stubs.md carve-out applied here)."""
    for stmt in func.body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # docstring or bare `...`
        return False
    return True


def _dead_params(func: _FuncDef) -> list[str]:
    if _is_signature_only(func):
        return []
    params = [a.arg for a in (func.args.posonlyargs + func.args.args
                              + func.args.kwonlyargs)
              if a.arg not in ("self", "cls") and not a.arg.startswith("_")]
    loaded = {n.id for n in ast.walk(func)
              if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return [p for p in params if p not in loaded]


def _dispatcher_findings(func: _FuncDef) -> list[Finding]:
    return [
        Finding(
            "dead-dispatcher-no-live-path", f"{func.name}.{param}",
            func.lineno,
            f"dispatcher {func.name}() declares '{param}' but its body never "
            "reads it, and the module imports no adapter, HTTP client or "
            "subprocess — the input reaches nothing",
        )
        for param in _dead_params(func)
    ]


def _scan_dead_dispatchers(tree: ast.AST) -> list[Finding]:
    if _has_live_import(tree):
        return []
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and "dispatch" in node.name.lower()):
            findings.extend(_dispatcher_findings(node))
    return findings


# ------------------------------------------------------------------- public


def analyze_source(source: str, path: str = "",
                   repo_root: Optional[Path] = None) -> list[Finding]:
    """All wiring-perimeter findings for one module. Never raises on bad
    source — an unparseable file returns [] (fail-open; a broken detector
    must not block writes)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    return (_scan_dead_flags(tree)
            + _scan_argv_blind(tree, path, repo_root)
            + _scan_dead_dispatchers(tree))


def _scan_file(file: str, root: Optional[Path]) -> int:
    p = Path(file)
    try:
        source = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"{file}: unreadable ({exc})", file=sys.stderr)
        return 0
    findings = analyze_source(source, path=str(p), repo_root=root)
    for f in findings:
        print(f"{file}:{f.line}: [{f.shape}] {f.name} — {f.detail}")
    return len(findings)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Flag declared inputs that reach nothing")
    parser.add_argument("files", nargs="+", help="Python files to scan")
    parser.add_argument("--repo-root", help="Repo root for skill/command "
                        "companion checks (enables the argv-blind shape)")
    args = parser.parse_args()

    root = Path(args.repo_root) if args.repo_root else None
    total = sum(_scan_file(file, root) for file in args.files)
    raise SystemExit(1 if total else 0)


if __name__ == "__main__":
    main()
