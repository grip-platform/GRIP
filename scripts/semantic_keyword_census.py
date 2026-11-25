#!/usr/bin/env python3
"""Census of GRIP's literal keyword floors that sit behind a verdict.

The measurement instrument for ``docs/measurements/2026-10-05-semantic-keyword-census.md``
(task 1 of the general semantic keyword floor programme). Deterministic, stdlib only.
It walks ``lib/*.py``, ``lib/gates/*.py`` and ``hooks/*.py`` under a root and scores
each module on observable properties; the judgement step -- floor or parser -- is
done by a reader over the survivors, never by this script.

  floor    - a module-level name bound to >=3 string literals or re.compile calls
             whose NAME says it is a keyword/pattern/marker list, or a regex
             literal that is an alternation of >=3 words
  verdict  - the module emits allow/deny/warn/fire/label/verdict vocabulary
  bound    - a hook imports it, directly or through one lib module
  live     - settings.json binds the hook (or a hook that binds the module)
  l2       - it already imports an LLM second layer
  audit    - it names a logs/*.jsonl file (deployment-distribution data)
  gap      - a rule file names the module in a paragraph about misses/recall

CLI: ``grip-py scripts/semantic_keyword_census.py [ROOT]`` prints JSON.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

FLOOR_NAME = re.compile(
    r"(?i)(pattern|keyword|marker|phrase|term|word|signal|indicator|deny|block"
    r"list|regex|_re$|_res$|_rx$|_rxs$|lexicon|vocab|cue|trigger|idiom|stem"
    r"|secret|sensitive|financial|pii|noun|verb|categor|exempt|fake|owner|token"
    r"|template|hedg|filler|shape|intent|jargon)"
)
WORD_ALT = re.compile(r"\((?:\?:)?(?:\?i\))?(?:\\b)?[A-Za-z][\w' -]*(?:\|[A-Za-z][\w' -]*){2,}", re.I)
VERDICT = re.compile(
    r"permissionDecision|\bDENY\b|\bALLOW\b|\bWARN\b|[\"']deny[\"']|[\"']allow[\"']"
    r"|[\"']warn[\"']|\bverdict\b|\bVerdict\b|\bDecision\b|\bfires?\b|\bkinds\b"
    r"|\bblocked\b|\bshould_block\b|\blabel\b"
)
L2 = re.compile(
    r"infer_classify|classify_intent|hal_llm_adapter|mechanical_then_llm"
    r"|llm_augment|semantic_keyword|hal_client|HALClient|call_llm|ask_llm"
    r"|sovereign_council|kono_search|embed"
)
AUDIT = re.compile(r"logs/[\w.-]+\.jsonl")
GAP_WORDS = re.compile(
    r"(?i)\b(miss(es|ed)?|recall|false[- ]negative|novel phrasing|lexical"
    r"|undercount|evade|evasion|Goodhart|paraphras|synonym|wording)\b"
)
IMPORT = re.compile(r"^\s*(?:from\s+(?:lib\.)?([\w.]+)\s+import|import\s+(?:lib\.)?([\w.]+))", re.M)


@dataclass
class Context:
    root: Path
    bound_by: dict[str, set[str]]
    settings: str
    rules: dict[str, str]
    totals: dict[str, int] = field(default_factory=lambda: {"with_floor": 0, "with_floor_and_verdict": 0})


def _is_compile(f: ast.AST) -> bool:
    return (isinstance(f, ast.Attribute) and f.attr == "compile") or (
        isinstance(f, ast.Name) and f.id == "compile"
    )


def _counts_as_literal(sub: ast.AST) -> bool:
    if isinstance(sub, ast.Constant):
        return isinstance(sub.value, str) and len(sub.value) > 1
    return isinstance(sub, ast.Call) and _is_compile(sub.func)


def literal_count(node: ast.AST) -> int:
    """How many string literals / re.compile calls a module-level value holds."""
    return sum(1 for sub in ast.walk(node) if _counts_as_literal(sub))


def _assigned(node: ast.stmt) -> Optional[tuple[str, ast.AST]]:
    if isinstance(node, ast.Assign):
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        return (names[0], node.value) if names else None
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
        return (node.target.id, node.value)
    return None


def _floor_of(node: ast.stmt) -> Optional[tuple[str, int]]:
    got = _assigned(node)
    if got is None:
        return None
    name, value = got
    if not isinstance(value, (ast.Tuple, ast.List, ast.Set, ast.Dict, ast.Call)):
        return None
    if not FLOOR_NAME.search(name):
        return None
    n = literal_count(value)
    return (name, n) if n >= 3 else None


def floors_in(tree: ast.Module) -> list[tuple[str, int]]:
    return [f for f in (_floor_of(node) for node in tree.body) if f is not None]


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def _imports_of(h: Path) -> set[str]:
    return {(m.group(1) or m.group(2)).split(".")[-1] for m in IMPORT.finditer(read_text(h))}


def hook_imports(hooks: list[Path], libs: list[Path]) -> dict[str, set[str]]:
    """module stem -> hooks that import it, directly or through one lib module."""
    bound: dict[str, set[str]] = {}
    for mod, hook in [(mod, h.name) for h in hooks for mod in _imports_of(h)]:
        bound.setdefault(mod, set()).add(hook)
    by_stem = {lib.stem: lib for lib in libs}
    direct = [(mod, hooks_) for mod, hooks_ in list(bound.items()) if mod in by_stem]
    hop = [(mod2, f"{hook} via {mod}") for mod, hooks_ in direct for mod2 in _imports_of(by_stem[mod]) for hook in sorted(hooks_)]
    for mod2, via in hop:
        bound.setdefault(mod2, set()).add(via)
    return bound


def _mentions_gap(text: str, p: Path) -> bool:
    paras = [para for para in text.split("\n\n") if p.stem in para or p.name in para]
    return any(GAP_WORDS.search(para) for para in paras)


def rule_gaps(p: Path, rules: dict[str, str]) -> list[str]:
    """Rules that name the module inside a paragraph talking about misses."""
    return [name for name, text in rules.items() if _mentions_gap(text, p)]


def _is_live(p: Path, ctx: Context) -> bool:
    if p.name in ctx.settings:
        return True
    hooks = ctx.bound_by.get(p.stem.replace("-", "_"), set())
    return any(h.split(" via ")[0] in ctx.settings for h in hooks)


def row_for(p: Path, ctx: Context) -> Optional[dict]:
    text = read_text(p)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    floors = floors_in(tree)
    word_alts = len(WORD_ALT.findall(text))
    if not floors and word_alts == 0:
        return None
    ctx.totals["with_floor"] += 1
    if not VERDICT.search(text):
        return None
    ctx.totals["with_floor_and_verdict"] += 1
    doc = (ast.get_docstring(tree) or "").strip()
    return {
        "file": str(p.relative_to(ctx.root)),
        "floors": [f"{n}({c})" for n, c in floors],
        "word_alts": word_alts,
        "bound_hooks": sorted(ctx.bound_by.get(p.stem.replace("-", "_"), set())),
        "live": _is_live(p, ctx),
        "l2": sorted(set(L2.findall(text))),
        "audits": sorted(set(AUDIT.findall(text))),
        "gap_rules": rule_gaps(p, ctx.rules),
        "doc": doc.splitlines()[0][:90] if doc else "",
    }


def run_census(root: Path) -> dict:
    """Scan one install root; returns {"totals": ..., "rows": [...]}."""
    root = root.resolve()
    files = sorted(root.glob("lib/*.py")) + sorted(root.glob("lib/gates/*.py")) + sorted(root.glob("hooks/*.py"))
    hooks = [p for p in files if p.parent.name == "hooks"]
    ctx = Context(
        root=root,
        bound_by=hook_imports(hooks, [p for p in files if p.parent.name != "hooks"]),
        settings=read_text(root / "settings.json"),
        rules={p.name: read_text(p) for p in sorted(root.glob("rules/*.md"))},
    )
    rows = [r for r in (row_for(p, ctx) for p in files) if r is not None]
    return {"totals": {"files": len(files), **ctx.totals}, "rows": rows}


def main(argv: Optional[list[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path.cwd()
    print(json.dumps(run_census(root), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
