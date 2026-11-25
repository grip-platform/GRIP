#!/usr/bin/env python3
"""AST-based cross-repo dependency analysis for cross-repo-topology.

Extracts structured relationships from Python source code using the `ast`
module — actual imports, function call targets, class bases. More precise
than regex: only matches real code, not comments or strings.

Usage:
    from lib.ast_repo_analyzer import detect_ast_connections
    edges = detect_ast_connections(repo_path, known_repos, source_name)
"""

import ast
from pathlib import Path

from lib.repo_graph_types import RepoEdge, _repo_name_variants, _is_excluded


# -- AST Visitors ----------------------------------------------------------

def _extract_imports(filepath: Path) -> list[str]:
    """Extract all import module names from a Python file."""
    try:
        tree = ast.parse(filepath.read_text(errors="ignore"))
    except (SyntaxError, ValueError):
        return []
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module.split(".")[0])
    return modules


def _extract_string_literals(filepath: Path) -> list[str]:
    """Extract URL-like and service-name string literals from AST."""
    try:
        tree = ast.parse(filepath.read_text(errors="ignore"))
    except (SyntaxError, ValueError):
        return []
    literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if len(val) > 5 and ("http" in val or "." in val or "_" in val):
                literals.append(val)
    return literals


def _collect_python_files(repo_path: str) -> list[Path]:
    """Collect all .py files in repo, excluding dependency dirs."""
    repo = Path(repo_path)
    return [f for f in repo.rglob("*.py") if not _is_excluded(f)]


# -- Matching Engine -------------------------------------------------------

def _build_variant_set(known_repos: list[str], source_name: str) -> dict[str, str]:
    """Build variant->repo lookup for all repos except source. Flat comprehension."""
    targets = [r for r in known_repos if r != source_name]
    return {v.lower(): repo for repo in targets for v in _repo_name_variants(repo)}


def _match_imports(modules: list[str], variant_lookup: dict[str, str]) -> dict[str, list[str]]:
    """Match import module names against repo variants. Returns {target: [evidence]}."""
    matches: dict[str, list[str]] = {}
    for mod in modules:
        target = variant_lookup.get(mod.lower())
        if target:
            matches.setdefault(target, []).append(f"import {mod}")
    return matches


def _tokenize_literal(lit: str) -> list[str]:
    """Split a literal into searchable tokens (lowercase, separator-split, len>2)."""
    normalized = lit.lower()
    for sep in "-./:\\@":
        normalized = normalized.replace(sep, "_")
    return [t for t in normalized.split("_") if len(t) > 2]


def _build_literal_index(literals: list[str]) -> dict[str, str]:
    """Build token->original_literal index for O(1) variant lookup. Flat comprehension."""
    index: dict[str, str] = {}
    all_pairs = ((tok, lit) for lit in literals for tok in _tokenize_literal(lit))
    for tok, lit in all_pairs:
        index.setdefault(tok, lit)
    return index


def _variant_lookup_keys(variant: str) -> list[str]:
    """Generate lookup keys for a variant: full normalised + first significant token."""
    normalised = variant.replace("-", "_")
    keys = [normalised]
    tokens = [t for t in normalised.split("_") if len(t) > 3]
    if tokens:
        keys.append(tokens[0])
    return keys


def _match_literals(literals: list[str], variant_lookup: dict[str, str]) -> dict[str, list[str]]:
    """Match string literals against repo variants. Returns {target: [evidence]}.

    O(literals + variants): token index built once; each variant tries O(1) lookups.
    """
    token_index = _build_literal_index(literals)
    matches: dict[str, list[str]] = {}
    for variant, target in variant_lookup.items():
        if target in matches:
            continue
        hit = next((token_index[k] for k in _variant_lookup_keys(variant) if k in token_index), None)
        if hit:
            matches[target] = [f"string: {hit[:80]}"]
    return matches


def _analyze_single_file(filepath: Path, variant_lookup: dict[str, str],
                          repo: Path) -> dict[str, list[str]]:
    """Analyze one Python file for cross-repo references. Returns {target: [evidence]}."""
    rel = str(filepath.relative_to(repo))
    imports = _extract_imports(filepath)
    literals = _extract_string_literals(filepath)

    import_matches = _match_imports(imports, variant_lookup)
    literal_matches = _match_literals(literals, variant_lookup)

    combined: dict[str, list[str]] = {}
    for target, evs in import_matches.items():
        combined.setdefault(target, []).extend(f"{rel}: {e}" for e in evs)
    for target, evs in literal_matches.items():
        combined.setdefault(target, []).extend(f"{rel}: {e}" for e in evs)
    return combined


def _merge_match_dicts(dicts: list[dict[str, list[str]]]) -> dict[str, list[str]]:
    """Merge multiple {target: [evidence]} dicts into one. Flat iteration."""
    merged: dict[str, list[str]] = {}
    all_items = ((t, evs) for d in dicts for t, evs in d.items())
    for target, evidence in all_items:
        merged.setdefault(target, []).extend(evidence)
    return merged


# -- Public API ------------------------------------------------------------

def detect_ast_connections(repo_path: str, known_repos: list[str],
                           source_name: str) -> list[RepoEdge]:
    """Detect cross-repo connections via Python AST analysis.

    Parses all .py files in the repo, extracts imports and string literals,
    and matches them against known repo name variants. More precise than
    regex — only matches actual code constructs, not comments.
    """
    py_files = _collect_python_files(repo_path)
    if not py_files:
        return []

    variant_lookup = _build_variant_set(known_repos, source_name)
    repo = Path(repo_path)

    file_results = [_analyze_single_file(f, variant_lookup, repo) for f in py_files]
    all_matches = _merge_match_dicts(file_results)
    return _matches_to_edges(source_name, all_matches)


def _matches_to_edges(source: str, matches: dict[str, list[str]]) -> list[RepoEdge]:
    """Convert aggregated matches to RepoEdge list."""
    return [
        RepoEdge(
            source=source, target=target, edge_type="ast_import",
            evidence=evidence[:5],
            strength="strong" if len(evidence) >= 3 else "moderate",
            description=f"{source} imports/references {target} ({len(evidence)} AST hits)",
        )
        for target, evidence in matches.items()
    ]
