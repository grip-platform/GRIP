"""Quality Guardian fast-path checks — multi-language support (Phase 1).

Heuristics for DRY, KISS, BIG-O, YAGNI, SOLID (ISP/DIP), and GRASP (Feature Envy).
Designed to run in <50ms after every Write/Edit as a PostToolUse hook.

Severity semantics (enforced by quality-pretool-hook.py):
  ERROR = mechanical DENY (tool call blocked). Used for principles with >=97% precision.
  WARN  = advisory context injection (tool call allowed). Used for lower-precision checks.

DENY tier (ERROR): DRY, KISS, BIG-O, YAGNI, CC*, ISP (100% precision validated)
WARN tier (WARN):  DIP (97% precision), Feature Envy (89% precision after exclusions)

*CC is enforced by cc-gate-hook.py separately.

For deep analysis (full SOLID, full GRASP), use the Broly council via /converge.

Reuses gate_heuristics.detect_nested_loops (DRY — imported, not duplicated).
"""

import ast
import hashlib
import io
import re
import sys
import tokenize
from collections import namedtuple
from pathlib import Path

QualitySignal = namedtuple(
    "QualitySignal", ["principle", "severity", "message", "convergence_depth"]
)

# Severity levels
WARN = "warn"
ERROR = "error"

# Convergence depths (W-axis)
DETECTED = 1
UNDERSTOOD = 2
CODIFIED = 3


_DJANGO_FIELD_RE = re.compile(
    r"^\s*[A-Za-z_]\w*\s*=\s*models\.[A-Z]\w*\s*\("
)


def _structural_paren_counts(content: str) -> dict[int, tuple[int, int]]:
    """Per-line counts of structural ``(`` and ``)`` tokens.

    Uses :mod:`tokenize` so parens inside string literals, f-string text,
    and comments are excluded. Returns ``{line_1_indexed: (opens, closes)}``.

    On tokenize failure (mid-edit invalid Python, encoding edge case),
    returns an empty dict — callers fall back to naive ``str.count``.
    """
    counts: dict[int, list[int]] = {}
    try:
        tokens = tokenize.tokenize(io.BytesIO(content.encode("utf-8")).readline)
        for tok in tokens:
            if tok.type != tokenize.OP or tok.string not in ("(", ")"):
                continue
            line = tok.start[0]
            slot = counts.setdefault(line, [0, 0])
            slot[0 if tok.string == "(" else 1] += 1
    except (tokenize.TokenizeError, SyntaxError, IndentationError, ValueError):
        return {}
    return {ln: (o, c) for ln, (o, c) in counts.items()}


def _django_field_zones(lines: list[str], content: str | None = None) -> set[int]:
    """Return line indices inside Django field declarations.

    A zone starts at a line matching ``name = models.X(`` and extends
    until the opening parenthesis is balanced. Paren depth uses
    :func:`_structural_paren_counts` so parens inside string literals
    don't extend the zone past the actual field close — the failure mode
    that suppresses DRY checks on unrelated code below the field block.

    If tokenize fails (invalid Python mid-edit), falls back to the
    naive ``str.count`` count. Worst-case fallback behaviour: a single
    string-literal paren over-extends the zone by ~one block, which is
    bounded by the next field opener resetting the state machine.

    Single-pass state machine: O(n) over lines.
    """
    paren_counts: dict[int, tuple[int, int]] = {}
    if content is not None:
        paren_counts = _structural_paren_counts(content)

    zones: set[int] = set()
    inside = False
    depth = 0
    opened = False
    start = 0
    for i, line in enumerate(lines):
        if not inside and _DJANGO_FIELD_RE.match(line):
            inside = True
            start = i
            depth = 0
            opened = False
        if inside:
            if paren_counts:
                opens, closes = paren_counts.get(i + 1, (0, 0))
            else:
                opens = line.count("(")
                closes = line.count(")")
            if opens:
                opened = True
            depth += opens - closes
            if opened and depth <= 0:
                zones.update(range(start, i + 1))
                inside = False
    if inside:
        zones.update(range(start, len(lines)))
    return zones


def check_dry(content: str, filename: str = "", *, block_size: int = 5, min_copies: int = 3) -> list[QualitySignal]:
    """Detect copy-paste patterns via line-hash frequency.

    Hashes consecutive ``block_size``-line blocks (default 5) and flags any
    hash appearing at least ``min_copies`` times (default 3, i.e. the historical
    "> 2"). The two knobs let a caller tighten sensitivity — min_copies=2 catches
    a 2-copy duplication the default misses. Production callers pass neither and
    get byte-identical behaviour. O(n) single pass.

    Django models.py files: 5-line blocks dominated (>=3 of 5 lines) by
    field declarations (`name = models.ForeignKey(...)`, `models.CharField(...)`
    etc.) are skipped. Per-model field boilerplate is intentional declarative
    schema — extracting helpers obscures the model rather than clarifying it.
    Genuine duplication inside `save()`/`clean()`/methods is still flagged
    because those blocks aren't majority field-declaration lines.
    """
    signals = []
    lines = content.split("\n")
    if len(lines) < block_size:
        return signals

    zones = _django_field_zones(lines, content) if filename.endswith("models.py") else set()

    block_hashes: dict[str, list[int]] = {}
    for i in range(len(lines) - block_size + 1):
        if zones and sum(1 for j in range(i, i + block_size) if j in zones) >= (block_size // 2 + 1):
            continue
        block = "\n".join(line.strip() for line in lines[i : i + block_size])
        if not block.strip():
            continue
        h = hashlib.md5(block.encode()).hexdigest()[:12]
        if h not in block_hashes:
            block_hashes[h] = []
        block_hashes[h].append(i + 1)

    for locations in block_hashes.values():
        if len(locations) >= min_copies:
            signals.append(_dry_signal(block_size, locations))

    return signals


def _dry_signal(block_size: int, locations: list[int]) -> QualitySignal:
    """Build a DRY violation signal for a block repeated at ``locations``."""
    return QualitySignal(
        principle="DRY",
        severity=ERROR,
        message=(
            f"Duplicated {block_size}-line block appears {len(locations)} times "
            f"(lines {', '.join(str(loc) for loc in locations[:3])})"
        ),
        convergence_depth=DETECTED,
    )


def _kiss_signal(name: str, length: int, start_line: int) -> QualitySignal:
    """Build a KISS violation signal for an oversized function."""
    return QualitySignal(
        principle="KISS", severity=ERROR,
        message=f"Function '{name}' is {length} lines (line {start_line}). Threshold: 50.",
        convergence_depth=DETECTED)


def _check_kiss_close(signals, func_start, func_name, end_line):
    """Close a tracked function and emit signal if too long."""
    if func_start is None:
        return
    length = end_line - func_start
    if length > 50:
        signals.append(_kiss_signal(func_name, length, func_start + 1))


def check_kiss(content: str) -> list[QualitySignal]:
    """Detect over-complexity via function length. Threshold: 50 lines."""
    signals = []
    lines = content.split("\n")
    func_start = None
    func_name = ""
    func_indent = 0

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        match = re.match(r"(async\s+)?def\s+(\w+)", stripped)
        if match:
            _check_kiss_close(signals, func_start, func_name, i)
            func_start = i
            func_name = match.group(2)
            func_indent = indent
            continue

        if func_start is not None and stripped and indent <= func_indent:
            if not re.match(r"(async\s+)?def\s+", stripped):
                _check_kiss_close(signals, func_start, func_name, i)
                func_start = None

    _check_kiss_close(signals, func_start, func_name, len(lines))
    return signals


def check_big_o(content: str) -> list[QualitySignal]:
    """Detect nested loops via gate_heuristics (imported, not duplicated)."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from gate_heuristics import detect_nested_loops
    except ImportError:
        return []

    if detect_nested_loops(content):
        return [
            QualitySignal(
                principle="BIG-O",
                severity=ERROR,
                message="Nested loop detected — potential O(n^2). Consider sort-then-scan or hash-based approach.",
                convergence_depth=DETECTED,
            )
        ]
    return []


def _extract_import_name(part: str, is_from_import: bool) -> str:
    """Extract the effective name from an import part."""
    part = part.strip()
    if " as " in part:
        return part.split(" as ")[-1].strip()
    return part.strip() if is_from_import else part.split(".")[0].strip()


def _parse_names_from_import(names_str: str, is_from: bool) -> list[str]:
    """Parse comma-separated import names into a list of identifiers."""
    return [n for n in (_extract_import_name(p, is_from) for p in names_str.split(","))
            if n and n.isidentifier()]


_NOQA_RE = re.compile(
    r"#\s*noqa\b(?::\s*(?P<codes>[A-Z]+[0-9]+(?:[\s,]+[A-Z]+[0-9]+)*))?", re.IGNORECASE)


def _split_comment(stripped: str) -> tuple[str, str]:
    """Split a line at its first hash sign into the code before it and the comment.

    A Python import statement cannot hold a hash sign, so on an import line the
    first one starts the comment, and nothing after it is an imported name
    (GRIP#8855).
    """
    code, hash_sign, comment = stripped.partition("#")
    return code.rstrip(), hash_sign + comment


def _noqa_covers_unused_import(comment: str) -> bool:
    """True for a `# noqa` that names no codes, or names F401 (unused import)."""
    match = _NOQA_RE.search(comment)
    if match is None:
        return False
    codes = match.group("codes")
    return codes is None or "F401" in re.split(r"[\s,]+", codes.upper())


def _collect_imports(lines: list[str]) -> dict[str, int]:
    """Collect imported names and their line numbers.

    Names are read only from the code before a comment, and a line whose
    `# noqa` covers F401 adds none (GRIP#8855).
    """
    imported: dict[str, int] = {}
    for i, line in enumerate(lines):
        code, comment = _split_comment(line.strip())
        if _noqa_covers_unused_import(comment):
            continue
        m = re.match(r"from\s+(\S+)\s+import\s+(.+)", code)
        if m:
            module, names_str = m.group(1), m.group(2)
            # PEP 236/563: __future__ imports are compile-time directives,
            # not runtime names. Never flag them as unused.
            if module == "__future__":
                continue
            if names_str.strip() == "*":
                continue
            names = _parse_names_from_import(names_str, True)
            imported.update({n: i + 1 for n in names})
            continue
        m = re.match(r"import\s+(.+)", code)
        if m:
            names = _parse_names_from_import(m.group(1), False)
            imported.update({n: i + 1 for n in names})
    return imported


def check_yagni(content: str) -> list[QualitySignal]:
    """Detect dead code signals: unused imports. O(n) two-pass."""
    lines = content.split("\n")
    imported = _collect_imports(lines)
    if not imported:
        return []

    signals = []
    # Scan body for references (skip import lines themselves)
    body = "\n".join(
        line
        for i, line in enumerate(lines)
        if not re.match(r"\s*(from\s+|import\s+)", line)
    )

    for name, line_num in imported.items():
        # Check if name appears as a word boundary in the body
        if not re.search(r"\b" + re.escape(name) + r"\b", body):
            signals.append(
                QualitySignal(
                    principle="YAGNI",
                    severity=ERROR,
                    message=f"Unused import '{name}' at line {line_num}.",
                    convergence_depth=DETECTED,
                )
            )

    return signals


_STDLIB_TYPES = frozenset({
    "dict", "list", "set", "tuple", "frozenset", "str", "int", "float",
    "bool", "bytes", "bytearray", "complex", "range", "slice", "type",
    "object", "Exception", "ValueError", "TypeError", "KeyError",
    "Path", "PurePath", "OrderedDict", "defaultdict", "Counter", "deque",
})

_DATACLASS_DECORATORS = frozenset({"dataclass", "dataclasses.dataclass"})


def _safe_parse(content: str):
    """Parse Python AST, returning None on syntax errors."""
    try:
        return ast.parse(content)
    except SyntaxError:
        return None


def _is_dataclass(node: ast.ClassDef) -> bool:
    """Check if a class is decorated with @dataclass."""
    for dec in node.decorator_list:
        name = ""
        if isinstance(dec, ast.Name):
            name = dec.id
        elif isinstance(dec, ast.Attribute):
            name = f"{getattr(dec.value, 'id', '')}.{dec.attr}"
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                name = dec.func.id
            elif isinstance(dec.func, ast.Attribute):
                name = f"{getattr(dec.func.value, 'id', '')}.{dec.func.attr}"
        if name in _DATACLASS_DECORATORS:
            return True
    return False


def _get_classes(tree: ast.AST) -> list[ast.ClassDef]:
    """Extract all ClassDef nodes from an AST."""
    return [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]


def _get_functions(tree: ast.AST) -> list:
    """Extract all function/async function nodes from an AST."""
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _is_skippable_class(node: ast.ClassDef) -> bool:
    """Check if class should be excluded from ISP check."""
    if node.name.startswith("Test") or node.name.endswith("Test"):
        return True
    if _is_dataclass(node):
        return True
    bases = [getattr(b, "id", getattr(b, "attr", "")) for b in node.bases]
    return "NamedTuple" in bases or "TypedDict" in bases


def check_isp(content: str) -> list[QualitySignal]:
    """Detect fat interfaces: classes with >7 public methods (SOLID-ISP)."""
    tree = _safe_parse(content)
    if tree is None:
        return []
    signals = []
    for node in _get_classes(tree):
        if _is_skippable_class(node):
            continue
        public = [n for n in node.body
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and not n.name.startswith("_")]
        if len(public) > 7:
            signals.append(QualitySignal(
                principle="SOLID-ISP", severity=ERROR,
                message=(f"Class '{node.name}' has {len(public)} public methods "
                         f"(line {node.lineno}). Threshold: 7."),
                convergence_depth=CODIFIED))
    return signals


_DTO_SUFFIXES = ("Result", "Response", "Request", "Error", "Exception",
                 "Config", "Context", "Event", "Message", "Criterion",
                 "Decision", "Item", "Entry", "Record")


def _is_dto_class(name: str) -> bool:
    """Check if a class name looks like a data transfer object."""
    return any(name.endswith(s) for s in _DTO_SUFFIXES)


def _find_instantiations_in_func(func_node, local_imports: set) -> list[tuple]:
    """Find direct concrete instantiations in a function body. O(n) single walk."""
    hits = []
    for child in ast.iter_child_nodes(func_node):
        if isinstance(child, ast.Assign):
            val = child.value
        elif isinstance(child, ast.Return):
            val = child.value
        elif isinstance(child, ast.Expr):
            val = child.value
        else:
            continue
        if isinstance(val, ast.Call) and isinstance(val.func, ast.Name):
            name = val.func.id
            if name in local_imports and not _is_dto_class(name):
                hits.append((name, val.lineno))
    return hits


def check_dip(content: str) -> list[QualitySignal]:
    """Detect direct concrete class instantiation (SOLID-DIP)."""
    tree = _safe_parse(content)
    if tree is None:
        return []
    local_imports = _collect_local_imports(content)
    if not local_imports:
        return []
    signals = []
    skip_names = ("create", "build", "make", "factory")
    for func in _get_functions(tree):
        if func.name.startswith("test_") or func.name == "__init__":
            continue
        if any(kw in func.name for kw in skip_names):
            continue
        signals.extend(_dip_signals_for_func(func, local_imports))
    return signals


def _dip_signals_for_func(func, local_imports: set) -> list[QualitySignal]:
    """Build DIP signals for a single function's instantiations."""
    return [QualitySignal(
        principle="SOLID-DIP", severity=WARN,
        message=(f"Direct instantiation of '{cls_name}' in "
                 f"'{func.name}' (line {lineno}). Consider DI."),
        convergence_depth=DETECTED)
        for cls_name, lineno in _find_instantiations_in_func(func, local_imports)]


_STDLIB_MODULES = frozenset({
    "os", "sys", "json", "re", "math", "collections", "itertools",
    "functools", "datetime", "hashlib", "pathlib", "typing", "dataclasses",
    "abc", "enum", "logging", "subprocess", "time", "io", "contextlib",
    "unittest", "copy",
})


def _extract_class_names(names_str: str) -> list[str]:
    """Extract PascalCase identifiers from comma-separated import names."""
    result = []
    for p in names_str.split(","):
        name = _extract_import_name(p, True)
        if name and name[0].isupper() and name.isidentifier():
            result.append(name)
    return result


def _collect_local_imports(content: str) -> set[str]:
    """Collect class names imported from local/package modules (not stdlib)."""
    names = set()
    for line in content.split("\n"):
        m = re.match(r"\s*from\s+(\S+)\s+import\s+(.+)", line)
        if m and not m.group(1).startswith(tuple(_STDLIB_MODULES)):
            names.update(_extract_class_names(m.group(2)))
    return names - _STDLIB_TYPES


def _check_class_feature_envy(cls_node: ast.ClassDef) -> list[QualitySignal]:
    """Check all public methods in a class for Feature Envy."""
    signals = []
    for method in cls_node.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if method.name.startswith("_") or method.name.startswith("from_"):
            continue
        result = _analyse_method_references(method)
        if result:
            signals.append(result)
    return signals


def check_feature_envy(content: str) -> list[QualitySignal]:
    """Detect Feature Envy: method accessing another object excessively (GRASP)."""
    tree = _safe_parse(content)
    if tree is None:
        return []
    signals = []
    for cls_node in _get_classes(tree):
        signals.extend(_check_class_feature_envy(cls_node))
    return signals


def _analyse_method_references(method) -> QualitySignal | None:
    """Count self vs external attribute references in a method."""
    self_refs = 0
    external_refs: dict[str, int] = {}

    params = {a.arg for a in method.args.args if a.arg != "self"}

    for node in ast.walk(method):
        if not isinstance(node, ast.Attribute):
            continue
        if isinstance(node.value, ast.Name):
            if node.value.id == "self":
                self_refs += 1
            elif node.value.id in params:
                external_refs[node.value.id] = external_refs.get(node.value.id, 0) + 1

    for param, count in external_refs.items():
        if count > 3 and count > self_refs:
            return QualitySignal(
                principle="GRASP", severity=WARN,
                message=(f"Feature Envy: '{method.name}' accesses '{param}' "
                         f"{count} times vs self {self_refs} times "
                         f"(line {method.lineno}). Consider moving logic to "
                         f"'{param}'s class."),
                convergence_depth=DETECTED)
    return None


# ── Language Registry ────────────────────────────────────────────────

LANG_PYTHON = "python"
LANG_TYPESCRIPT = "typescript"
LANG_GO = "go"

EXTENSION_MAP: dict[str, str] = {
    ".py": LANG_PYTHON,
    ".ts": LANG_TYPESCRIPT,
    ".tsx": LANG_TYPESCRIPT,
    ".js": LANG_TYPESCRIPT,  # Same heuristics as TS
    ".jsx": LANG_TYPESCRIPT,
    ".mjs": LANG_TYPESCRIPT,
    ".go": LANG_GO,
}

SUPPORTED_EXTENSIONS = set(EXTENSION_MAP.keys())

# ── Language-Specific Patterns: KISS (function definitions) ──────────

#: TS/JS keywords that can stand directly before "(" at the start of a line:
#: "for (const msg of messages) {", "if (ok) {", "return (". None of them names
#: a function, so a line that starts with one never opens one. Until GRIP#9127
#: two loops and an if block in whatsapp.ts were reported as long functions
#: named "for" and "if", and every edit to that file was refused.
_TS_KEYWORDS_BEFORE_PAREN = (r"if|for|while|switch|catch|with|return|await|typeof|void|delete"
                             r"|yield|super")

_FUNC_PATTERNS: dict[str, re.Pattern] = {
    LANG_PYTHON: re.compile(r"(async\s+)?def\s+(\w+)"),
    LANG_TYPESCRIPT: re.compile(
        r"(?:export\s+)?(?:async\s+)?function\s+(\w+)"
        r"|(?!(?:" + _TS_KEYWORDS_BEFORE_PAREN + r")\b)(\w+)\s*(?::\s*\w+)?\s*\("
    ),
    LANG_GO: re.compile(r"func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\("),
}

# ── Language-Specific Patterns: KISS (block boundaries) ──────────────
# Python uses indentation; TS/JS/Go use braces.
_BRACE_LANGUAGES = {LANG_TYPESCRIPT, LANG_GO}


def _check_kiss_braces(content: str, lang: str) -> list[QualitySignal]:
    """KISS check for brace-delimited languages (TS/JS/Go). Single-pass O(n)."""
    signals = []
    pattern = _FUNC_PATTERNS.get(lang)
    if not pattern:
        return signals

    lines = content.split("\n")
    func_name = ""
    func_start = -1
    depth = 0
    tracking = False

    for i, line in enumerate(lines):
        stripped = line.lstrip()

        if not tracking:
            m = pattern.match(stripped)
            if m:
                func_name = next((g for g in m.groups() if g), "anonymous")
                func_start = i
                depth = 0
                tracking = False  # Wait for first brace

        depth += line.count("{") - line.count("}")

        if func_start >= 0 and not tracking and "{" in line:
            tracking = True

        if tracking and depth <= 0:
            length = i - func_start + 1
            if length > 50:
                signals.append(_kiss_signal(func_name, length, func_start + 1))
            tracking = False
            func_start = -1

    return signals


# ── Language-Specific Patterns: YAGNI (imports) ──────────────────────

def _parse_ts_destructured(names_str: str) -> list[str]:
    """Parse destructured TS/JS import names: '{ Foo, Bar as Baz }' -> ['Foo', 'Baz']."""
    return [n for n in (
        p.strip().split(" as ")[-1].strip() for p in names_str.split(",")
    ) if n and n.isidentifier()]


def _collect_imports_ts(lines: list[str]) -> dict[str, int]:
    """Collect imported names from TS/JS import statements."""
    imported: dict[str, int] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        # import { Foo, Bar } from 'module'
        m = re.match(r"import\s+\{([^}]+)\}\s+from\s+", stripped)
        if m:
            imported.update({n: i + 1 for n in _parse_ts_destructured(m.group(1))})
            continue
        # import Foo from 'module'
        m = re.match(r"import\s+(\w+)\s+from\s+", stripped)
        if m:
            imported[m.group(1)] = i + 1
            continue
        # const Foo = require('module')
        m = re.match(r"(?:const|let|var)\s+(\w+)\s*=\s*require\s*\(", stripped)
        if m:
            imported[m.group(1)] = i + 1
    return imported


def _collect_imports_go(lines: list[str]) -> dict[str, int]:
    """Collect imported package names from Go import statements."""
    imported: dict[str, int] = {}
    in_import_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import ("):
            in_import_block = True
            continue
        if in_import_block:
            if stripped == ")":
                in_import_block = False
                continue
            # "alias \"package/path\"" or "\"package/path\""
            m = re.match(r'(\w+)\s+"[^"]+"', stripped)
            if m:
                imported[m.group(1)] = i + 1
                continue
            m = re.match(r'"(?:[^"]*/)?([\w]+)"', stripped)
            if m:
                imported[m.group(1)] = i + 1
                continue
        # Single-line: import "package"
        m = re.match(r'import\s+"(?:[^"]*/)?([\w]+)"', stripped)
        if m:
            imported[m.group(1)] = i + 1
    return imported


def _strip_import_lines(lines: list[str], lang: str) -> str:
    """Build body text with import regions removed, language-aware."""
    if lang == LANG_TYPESCRIPT:
        return "\n".join(
            line for line in lines
            if not re.match(r"\s*(import\s+|const\s+\w+\s*=\s*require)", line)
        )
    if lang == LANG_GO:
        body_lines = []
        in_import_block = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import ("):
                in_import_block = True
                continue
            if in_import_block:
                if stripped == ")":
                    in_import_block = False
                continue
            if re.match(r'\s*import\s+"', stripped):
                continue
            body_lines.append(line)
        return "\n".join(body_lines)
    return "\n".join(lines)


def _check_yagni_for_lang(content: str, lang: str) -> list[QualitySignal]:
    """YAGNI check dispatched by language."""
    if lang == LANG_PYTHON:
        return check_yagni(content)

    lines = content.split("\n")
    if lang == LANG_TYPESCRIPT:
        imported = _collect_imports_ts(lines)
    elif lang == LANG_GO:
        imported = _collect_imports_go(lines)
    else:
        return []

    if not imported:
        return []

    body = _strip_import_lines(lines, lang)

    signals = []
    for name, line_num in imported.items():
        if not re.search(r"\b" + re.escape(name) + r"\b", body):
            signals.append(QualitySignal(
                principle="YAGNI", severity=ERROR,
                message=f"Unused import '{name}' at line {line_num}.",
                convergence_depth=DETECTED))
    return signals


# ── Language-Specific Patterns: BIG-O (loop detection) ───────────────

_LOOP_PATTERNS: dict[str, re.Pattern] = {
    LANG_PYTHON: re.compile(r"(for |while )\S"),
    LANG_TYPESCRIPT: re.compile(r"(for\s*\(|while\s*\(|\.forEach\s*\()"),
    LANG_GO: re.compile(r"for\s+"),
}


def _check_big_o_for_lang(content: str, lang: str) -> list[QualitySignal]:
    """BIG-O nested loop check dispatched by language."""
    if lang == LANG_PYTHON:
        return check_big_o(content)

    pattern = _LOOP_PATTERNS.get(lang)
    if not pattern:
        return []

    if lang in _BRACE_LANGUAGES:
        if _detect_nested_loops_braces(content, pattern):
            return [QualitySignal(
                principle="BIG-O", severity=ERROR,
                message="Nested loop detected — potential O(n^2). Consider sort-then-scan or hash-based approach.",
                convergence_depth=DETECTED)]
    return []


def _detect_nested_loops_braces(content: str, loop_pattern: re.Pattern) -> bool:
    """Detect nested loops in brace-delimited languages via depth tracking."""
    lines = content.split("\n")
    depth = 0
    loop_depth = -1

    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
            continue
        if "# O(1):" in line or "// O(1):" in line:
            continue

        if loop_pattern.search(stripped):
            if loop_depth >= 0 and depth > loop_depth:
                return True
            loop_depth = depth

        depth += line.count("{") - line.count("}")
        if depth <= loop_depth:
            loop_depth = -1

    return False


def detect_language(filename: str) -> str | None:
    """Detect language from file extension. Returns None if unsupported."""
    ext = Path(filename).suffix if filename else ""
    return EXTENSION_MAP.get(ext)


def check_all(content: str, filename: str) -> list[QualitySignal]:
    """Run all fast-path checks. Returns signals sorted by severity.

    Supports Python, TypeScript/JavaScript, and Go.
    Python: all 7 checks. TS/JS/Go: DRY, KISS, BIG-O, YAGNI (heuristic).
    ISP, DIP, Feature Envy require AST — Python only until tree-sitter (Phase 2).
    """
    lang = detect_language(filename)
    if lang is None:
        return []

    signals = []

    # DRY: language-agnostic (line hashing). filename enables Django models.py exemption.
    signals.extend(check_dry(content, filename))

    # KISS: language-specific function detection
    if lang == LANG_PYTHON:
        signals.extend(check_kiss(content))
    elif lang in _BRACE_LANGUAGES:
        signals.extend(_check_kiss_braces(content, lang))

    # BIG-O: language-specific loop detection
    signals.extend(_check_big_o_for_lang(content, lang))

    # YAGNI: language-specific import detection
    signals.extend(_check_yagni_for_lang(content, lang))

    # AST-based checks: Python only (Phase 2: tree-sitter for other languages)
    if lang == LANG_PYTHON:
        signals.extend(check_isp(content))
        signals.extend(check_dip(content))
        signals.extend(check_feature_envy(content))

    # Sort: errors first, then warns
    severity_order = {ERROR: 0, WARN: 1}
    signals.sort(key=lambda s: severity_order.get(s.severity, 2))

    return signals
