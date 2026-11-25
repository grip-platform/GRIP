"""REA evidence as an idempotent fold -- dogfood domain #7 of lib/dogfood_kernel.py.

REA (npm ``rea-agents``, the reverse-engineering MCP tool server GRIP attaches
per launch) returns Evidence JSON. Two inspections of an UNCHANGED target should
describe the same bytes, but a raw payload carries run-specific noise:
timestamps, durations, run and session ids, temp paths, and lists whose order
is incidental. ``normalise`` strips that noise and sorts incidental lists, so a
second run hashes identically to the first; ``residual_volatile`` counts how
much noise a payload still carries (0 iff ``normalise`` is a no-op); the pair
is the domain object ``ReaEvidenceDomain`` that ``generic_proof`` checks by
double application (f(f(x)) == f(x)). ``ground_truth_check`` compares an
evidence payload against facts known from a target's public source, so a REA
claim can be marked checked, missing or contradicted rather than believed.

``VOLATILE_KEYS`` and the value nets are INFERRED (written before the first
real two-run diff on ripgrep 15.2.0 calibrates them); the fixture pair under
``tests/fixtures/rea/`` plants exactly the noise this module strips, and the
over-strip test guards against a normaliser that strips so much it becomes
trivially idempotent. First draft by Gemini 3.1 Pro via OpenCode (2026-10-10),
reviewed and restructured by Claude Fable 5.1.
"""
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

# Importable as ``lib.rea_evidence`` (the registry name) AND runnable as a
# script (``bin/grip-py lib/rea_evidence.py prove ...``): a script run puts
# lib/ on sys.path, not the repository root, so the root is added here.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from lib.dogfood_kernel import GenericProof, generic_proof  # noqa: E402

# INFERRED until the first real two-run diff calibrates it.
VOLATILE_KEYS = frozenset({
    "timestamp", "started_at", "finished_at", "created_at", "generated_at",
    "duration_ms", "elapsed_ms", "run_id", "request_id", "session_id",
    "tool_call_id", "pid", "hostname", "cache_hit", "tmp_dir", "workdir",
})

ISO8601_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
)
TEMP_PATH_REGEX = re.compile(r"^(?:/var/folders/|/tmp/|/private/tmp/).*")
# No UUID net, deliberately: a UUID-shaped value can BE evidence (a Mach-O
# LC_UUID identifies the build; two binaries differing only there must not
# hash equal). Run/session/tool-call ids are volatile by KEY, above.
# (Codex review of GRIP#7966.)
_VOLATILE_VALUE_NETS = (ISO8601_REGEX, TEMP_PATH_REGEX)

# Lists whose element order carries no meaning, so sorting them is lossless.
# Everything else keeps its order: an instruction sequence, a trace, a call
# path or a probe list changes meaning when reordered (Codex review of
# GRIP#7966). INFERRED from the fixture shape and REA's inspect-macho output;
# widen only with a reason.
UNORDERED_KEYS = frozenset({
    "linked_libraries", "features", "limitations", "evidence_links",
    "unexplored_branches", "hypotheses", "contradictions", "tags", "symbols",
    "imports", "exports", "architectures", "segments", "sections",
})


def canonical_bytes(obj: Any) -> bytes:
    """Stable bytes for hashing and comparing: sorted keys, no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def is_primitive(val: Any) -> bool:
    return val is None or isinstance(val, (bool, int, float, str))


def _is_volatile_value(val: Any) -> bool:
    return isinstance(val, str) and any(net.match(val) for net in _VOLATILE_VALUE_NETS)


def _strip_volatile_recursive(obj: Any, counter: list[int]) -> Any:
    """Drop volatile keys, blank volatile values; count every leaf touched."""
    if isinstance(obj, dict):
        kept = {k: v for k, v in obj.items() if k not in VOLATILE_KEYS}
        counter[0] += len(obj) - len(kept)
        return {k: _strip_volatile_recursive(v, counter) for k, v in kept.items()}
    if isinstance(obj, list):
        return [_strip_volatile_recursive(item, counter) for item in obj]
    if _is_volatile_value(obj):
        counter[0] += 1
        return ""
    return obj


def strip_volatile(obj: Any) -> Any:
    return _strip_volatile_recursive(obj, [0])


def _sorted_list(items: list) -> list:
    """Primitives by (type name, value); anything else by canonical bytes."""
    if all(is_primitive(i) for i in items):
        try:
            return sorted(items, key=lambda x: (type(x).__name__, x))
        except TypeError:
            return sorted(items, key=str)
    return sorted(items, key=canonical_bytes)


def _canonical_lists_recursive(obj: Any, counter: list[int], unordered: bool = False) -> Any:
    """Sort lists under UNORDERED_KEYS into canonical order; count each that
    moved. Lists under any other key keep their order (it may be semantic)."""
    if isinstance(obj, dict):
        return {k: _canonical_lists_recursive(v, counter, k in UNORDERED_KEYS) for k, v in obj.items()}
    if isinstance(obj, list):
        items = [_canonical_lists_recursive(item, counter, unordered) for item in obj]
        if not unordered:
            return items
        ordered = _sorted_list(items)
        counter[0] += int(ordered != items)
        return ordered
    return obj


def canonical_lists(obj: Any) -> Any:
    return _canonical_lists_recursive(obj, [0])


def normalise(obj: Any) -> Any:
    return canonical_lists(strip_volatile(obj))


def evidence_sha256(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def residual_volatile(obj: Any) -> int:
    """Volatile leaves plus lists out of canonical order; 0 iff normalise is a no-op."""
    counter = [0]
    _strip_volatile_recursive(obj, counter)
    _canonical_lists_recursive(obj, counter)
    return counter[0]


@dataclass(frozen=True)
class ReaEvidenceDomain:
    """The kernel's domain contract: fold, residual, zero_residual."""
    fold = staticmethod(normalise)
    residual = staticmethod(residual_volatile)
    zero_residual: int = 0


def evidence_proof(obj: Any) -> GenericProof[Any, int]:
    return generic_proof(ReaEvidenceDomain(), obj)


# --- ground truth -----------------------------------------------------------------

def _leaves(obj: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Every scalar leaf with its KEY path (list indices omitted), depth-first:
    ``{"a": {"b": [1, 2]}}`` yields ``(("a","b"), 1)`` and ``(("a","b"), 2)``."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _leaves(value, path + (str(key),))
    elif isinstance(obj, list):
        for item in obj:
            yield from _leaves(item, path)
    else:
        yield path, obj


def _facts(truth: dict) -> Iterator[tuple[str, str, tuple[tuple[str, ...], ...], bool]]:
    """Flatten the truth file into (name, expected text, key paths, from-a-list).

    Two forms per entry. Flat: ``"version": "15.2.0"`` -- the name is the one
    key path. Structured: ``"version": {"expect": "15.2.0", "keys":
    ["subject.version", "data.version"]}`` -- the fact is expected under any of
    those dotted key paths, matched as a SUFFIX of a leaf's key path, so the
    schema of the evidence (REA's, not ours) decides where a fact lives and an
    unrelated key of the same last name elsewhere is not read as a contradiction.
    A list ``expect`` yields one fact per element.
    """
    for name, spec in truth.items():
        if name.startswith("_"):  # "_comment" and friends document the file; they are not facts
            continue
        expected = spec.get("expect") if isinstance(spec, dict) else spec
        keys = spec.get("keys", [name]) if isinstance(spec, dict) else [name]
        paths = tuple(tuple(k.split(".")) for k in keys)
        if isinstance(expected, list):
            yield from ((name, str(v), paths, True) for v in expected)
        else:
            yield name, str(expected), paths, False


def _under(leaf_path: tuple[str, ...], key_paths: tuple[tuple[str, ...], ...]) -> bool:
    return any(leaf_path[-len(k):] == k for k in key_paths if len(k) <= len(leaf_path))


def _holds(value: Any, expected: str, from_list: bool) -> bool:
    """A scalar fact needs equality; a list-member fact (a library, a feature)
    may sit inside a longer string such as a dylib path."""
    if not isinstance(value, (str, int, float, bool)):
        return False
    text = str(value)
    return text == expected or (from_list and expected in text)


def _classify_fact(leaves: list, fact) -> tuple[str, str]:
    """One expected fact -> verdict + detail line.

    checked        stated under one of its declared key paths -- the only tier
                   credited as a checked fact.
    contradiction  a leaf exists under a declared key path, and none states it.
    seen_elsewhere the text occurs in some string leaf, never under its paths
                   (a version number inside a file path): reported, not credited
                   (Codex review of GRIP#7966).
    missing        none of the above.
    """
    name, expected, paths, from_list = fact
    candidates = [v for p, v in leaves if _under(p, paths)]
    if any(_holds(v, expected, from_list) for v in candidates):
        return "checked", f"Checked under {'/'.join('.'.join(p) for p in paths)}: {expected}"
    if candidates:
        return "contradiction", f"Contradiction: {name} expected {expected}, found {candidates[:4]}"
    if any(isinstance(v, str) and expected in v for _, v in leaves):
        return "seen_elsewhere", f"Seen elsewhere, not under its key: {name} -> {expected}"
    return "missing", f"Missing: {name} -> {expected}"


def ground_truth_check(evidence: dict, truth: dict) -> dict:
    """Compare evidence against known facts.

    Returns {"checked", "contradictions", "seen_elsewhere", "missing", "details"}.
    See ``_facts`` for the two truth-file forms and ``_classify_fact`` for the
    tiers. Only a fact stated under a declared key path is credited.
    """
    leaves = list(_leaves(evidence))
    counts = {"checked": 0, "contradiction": 0, "seen_elsewhere": 0, "missing": 0}
    details: list[str] = []
    for fact in _facts(truth):
        verdict, detail = _classify_fact(leaves, fact)
        counts[verdict] += 1
        details.append(detail)
    return {"checked": counts["checked"], "contradictions": counts["contradiction"],
            "seen_elsewhere": counts["seen_elsewhere"], "missing": counts["missing"],
            "details": details}


# --- CLI -------------------------------------------------------------------------

def _load(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _cmd_normalise(args: list[str]) -> int:
    print(canonical_bytes(normalise(_load(args[0]))).decode("utf-8"))
    return 0


def _cmd_sha(args: list[str]) -> int:
    print(evidence_sha256(normalise(_load(args[0]))))
    return 0


def _cmd_prove(args: list[str]) -> int:
    """Exit 0 iff both runs normalise to one sha256 and both folds are idempotent."""
    runs = [_load(args[0]), _load(args[1])]
    shas = [evidence_sha256(normalise(r)) for r in runs]
    proofs = [evidence_proof(r) for r in runs]
    for i, run in enumerate(runs):
        print(f"sha{i + 1}: {shas[i]} (residual: {residual_volatile(run)}, idempotent: {proofs[i].idempotent})")
    ok = shas[0] == shas[1] and all(p.idempotent for p in proofs)
    print("IDEMPOTENT" if ok else "NOT IDEMPOTENT")
    return 0 if ok else 1


def _cmd_ground_truth(args: list[str]) -> int:
    result = ground_truth_check(normalise(_load(args[0])), _load(args[1]))
    print(json.dumps(result, indent=2))
    return 0 if result["contradictions"] == 0 else 1


# command -> (handler, number of file arguments it needs)
_COMMANDS = {
    "normalise": (_cmd_normalise, 1),
    "sha": (_cmd_sha, 1),
    "prove": (_cmd_prove, 2),
    "ground-truth": (_cmd_ground_truth, 2),
}

USAGE = ("usage: rea_evidence.py normalise <in.json> | sha <in.json> | "
         "prove <run1.json> <run2.json> | ground-truth <evidence.json> <truth.json>")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    handler, arity = _COMMANDS.get(cmd, (None, 0))
    if handler is None or len(argv) - 2 < arity:
        print(USAGE, file=sys.stderr)
        return 1
    return handler(argv[2:2 + arity])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
