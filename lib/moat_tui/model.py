"""moat_tui.model — the DATA layer for ``grip-moat``.

The ONLY module in the package that touches disk, and the ONLY one that calls
the shipped moat libraries. It owns path pinning, byte-offset indexing,
seek-hydration, root discovery, the three joins, and the four verify legs.

**COMPOSITION, NEVER REIMPLEMENTATION.** Every verification here calls a shipped
function — ``lib.precog.idr`` (``read_idr_chain``/``content_addr``),
``lib.idr_forest`` (``empty_forest``/``add_idr``/``build_chain_forest``/
``verify_chain_integrity``/``forest_merkle_root``/``replay_chain``/
``forest_inclusion_proof``/``verify_forest_inclusion``), ``lib.prove_it``
(``render``), ``lib.der_receipt`` (``build_receipt``/``verify_receipt``). Even
the belief chain's genesis anchor is BOUND from ``lib.context_chain`` rather than
restated, because a verifier INPUT that drifts fabricates a verdict just as
surely as a reimplemented digest. This module recomputes no digest, no
signature, and no citation match of its own. A TUI that reimplemented any of
them would be rendering theatre.

**READ-ONLY BY CONSTRUCTION.** It never calls ``append_idr``, ``checkpoint``,
``append_context``, ``record_proveit_provenance``, or
``build_legal_receipt(record=True)``. ``build_receipt`` is always called with
``do_stamp=False`` and ``verify_receipt`` with ``check_anchor=False`` unless the
caller explicitly asks to go online, so the default path touches no network.

**HONESTY IS THE FEATURE.** ``LegResult.status`` carries ``unavailable`` and
``no-data`` beside ``verified``/``degraded``/``broken``. Three live conditions
produce a FALSE red and each is reported as "cannot verify", never "TAMPERED":

* the HMAC signing key is absent — verifying would call ``_ensure_signing_key``,
  which GENERATES AND WRITES a fresh random key, turning every live record
  BROKEN (and violating read-only intent);
* ``cryptography`` is missing — a failed Ed25519 leg is indistinguishable from a
  forged signature;
* the live belief chain's structurally-orphaned root — a missing link, not a
  forgery, so it is reported ORPHANED ROOT and the belief leg verifies over a
  snapshot-rooted SCOPE that yields a genuine green to break.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# --------------------------------------------------------------------------
# Import bootstrap. The moat libraries use BOTH ``from lib.x import y`` (repo
# root on sys.path) and bare ``from x import y`` (lib/ on sys.path) — e.g.
# ``lib/context_head.py`` does a bare ``from task_namespace import ...``. Both
# entries are therefore mandatory. ``bin/grip-moat`` sets PYTHONPATH too; this
# keeps a direct ``python3 -c "import lib.moat_tui.model"`` working as well.
# --------------------------------------------------------------------------
_PKG_DIR = Path(__file__).resolve().parent          # .../lib/moat_tui
_LIB_DIR = _PKG_DIR.parent                          # .../lib
_REPO_ROOT = _LIB_DIR.parent                        # repo root
for _p in (str(_REPO_ROOT), str(_LIB_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.audit_chain import SIGNING_KEY_FILE, Verdict  # noqa: E402
from lib.idr_forest import (  # noqa: E402
    EXOGENOUS_ANCHOR_PATTERNS,
    Forest,
    ForestInclusionError,
    IdrForestError,
    _verify_node,
    add_idr,
    build_chain_forest,
    empty_forest,
    forest_inclusion_proof,
    forest_merkle_root,
    iter_nodes_topo,
    replay_chain,
    verify_chain_integrity,
    verify_forest_inclusion,
)
from lib.precog.idr import PrecogIDR, content_addr, read_idr_chain  # noqa: E402

# The genesis anchor the belief chain roots into. BOUND to its canonical
# definition rather than hand-copied: this constant is an INPUT to a verifier, so
# a silent divergence from ``lib.context_chain`` would make ``build_chain_forest``
# raise and pin the belief pane to a permanent grey ORPHANED ROOT — manufacturing
# exactly the false signal this module exists to prevent. Underscore-private is
# still the only canonical source, the same justification under which
# ``_verify_node`` is imported below; the literal survives only as a
# last-known-good fallback so a rename degrades to stale-but-working instead of
# taking the whole TUI down with an ImportError. Importing ``context_chain`` is
# side-effect free and does NOT imply use of its session-namespaced
# ``chain_path()`` / ``head_path()`` helpers, which ``resolve_paths`` still shuns.
try:
    from lib.context_chain import _GENESIS_ANCHOR as GENESIS_ANCHOR  # noqa: E402
except (ImportError, AttributeError):  # pragma: no cover - defensive fallback
    GENESIS_ANCHOR = "council:context-chain-genesis"

# Statuses. Mirrors the vocabulary render.py paints; ``unavailable`` and
# ``no-data`` are grey, NEVER red — "cannot verify" must not read as tampered.
STATUS_VERIFIED = "verified"
STATUS_DEGRADED = "degraded"
STATUS_BROKEN = "broken"
STATUS_UNAVAILABLE = "unavailable"
STATUS_NO_DATA = "no-data"


class LegName:
    """The four verify legs, by their display name.

    ``render._leg_name`` upper-cases and compares against these, so they are the
    join key between a ``LegResult`` and the pane that draws it.
    """

    IDR = "IDR"
    BELIEF = "BELIEF"
    CITE = "CITE"
    DER = "DER"

    ALL: tuple[str, ...] = (IDR, BELIEF, CITE, DER)


# --------------------------------------------------------------------------
# Plain data
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MoatPaths:
    """Every data path the TUI reads, PINNED — never session-namespaced."""

    root: Path
    idr: Path
    context: Path
    context_head: Path
    council: Path
    signing_key: Path
    prove_it_dir: Path

    @property
    def line(self) -> str:
        """One-line provenance for the header — shows WHICH root was resolved."""
        return str(self.root)


@dataclass(frozen=True)
class IndexEntry:
    """One indexed ledger line. Deliberately tiny — no dataclass hydration."""

    offset: int
    id: str
    kind: str
    ts: str
    depth: int


@dataclass(frozen=True)
class LedgerIndex:
    """Byte-offset index over a JSONL ledger.

    ``raw_lines`` is tracked SEPARATELY from ``len(entries)`` so a malformed or
    truncated line is VISIBLE as a count mismatch, instead of being silently
    absorbed the way ``read_idr_chain``'s bare ``except: continue`` absorbs it.
    """

    path: Path
    entries: tuple[IndexEntry, ...] = ()
    raw_lines: int = 0
    by_id: Mapping[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def dropped(self) -> int:
        """Raw non-blank lines that did not yield an entry."""
        return max(0, self.raw_lines - len(self.entries))


@dataclass(frozen=True)
class ForestBuild:
    """A built IDR forest plus the rejections that were NOT swallowed.

    Rejection cascades are why ``rejected`` exists: one missing anchor rejects a
    parent, whose children then dangle — 8 lost records from a single root cause
    that looks like 8 independent corruptions.
    """

    forest: Forest
    roots: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...] = ()
    loaded: int = 0
    raw: int = 0


@dataclass(frozen=True)
class DecisionRow:
    """One browsable row of the forest list."""

    node_id: str
    kind: str
    ts: str
    depth: int
    addr: str
    status: str


@dataclass(frozen=True)
class BeliefScope:
    """A snapshot-rooted belief window — the source of a genuine green.

    Whole-chain ``verify_context_chain()`` is BROKEN live for a STRUCTURAL
    reason (its root delta names a predecessor absent from both the live and the
    archive file), so rendering it raw would give a permanent red and destroy the
    tamper contrast.
    """

    nodes: tuple[PrecogIDR, ...] = ()
    snapshot_id: str = ""
    boundary: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class LegResult:
    """One verify leg's honest verdict.

    ``status`` is one of verified / degraded / broken / unavailable / no-data.
    The last two are the honesty carve-outs — a faked red is worse than no
    feature, so a condition we cannot check reports "cannot verify".
    """

    name: str
    status: str
    headline: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class DecisionView:
    """THE JOIN — one screen for one decision, all four legs resolved."""

    row: DecisionRow
    idr: PrecogIDR | None
    believed_by: tuple[IndexEntry, ...] = ()
    council_record: Mapping[str, Any] | None = None
    legs: tuple[LegResult, ...] = ()
    scope: BeliefScope | None = None


# --------------------------------------------------------------------------
# Paths + capability probes
# --------------------------------------------------------------------------


def resolve_paths(claude_dir: str | Path | None = None) -> MoatPaths:
    """PIN every data path explicitly from ``$CLAUDE_DIR`` (default
    ``$HOME/.claude``) — NEVER from cwd and NEVER via ``chain_path()`` /
    ``head_path()``.

    THE KILLER GOTCHA this closes: those helpers insert a ``CLAUDE_SESSION_ID``
    segment, so inside a Claude Code session the TUI would read a non-existent
    ``context-<sid>.jsonl``, get 0 nodes, and — because the shipped verifier calls
    an empty chain vacuously VERIFIED — render "0 records, VERIFIED": a fully
    green screen over nothing. It also defends the worktree case, which has no
    ``state/precog`` at all.
    """
    if claude_dir is None:
        env = (os.environ.get("CLAUDE_DIR") or "").strip()
        root = Path(env) if env else Path.home() / ".claude"
    else:
        root = Path(claude_dir)
    root = root.expanduser()
    precog = root / "state" / "precog"
    return MoatPaths(
        root=root,
        idr=precog / "idr.jsonl",
        context=precog / "context.jsonl",
        context_head=precog / "context-HEAD.txt",
        council=root / "state" / "sovereign-council",
        signing_key=root / "state" / "audit" / "audit-signing-key",
        prove_it_dir=root / "state" / "prove-it",
    )


def key_present(paths: MoatPaths) -> bool:
    """True iff the HMAC signing key exists and is non-empty.

    MUST be probed before any HMAC verification. ``_ensure_signing_key()``
    GENERATES AND WRITES a fresh random key when absent, which both violates
    read-only intent and turns every live record BROKEN — indistinguishable from
    real tampering. False here means render "cannot verify, key absent".

    Checks BOTH the pinned path and the one ``lib.audit_chain`` actually resolved
    at import time (its ``SIGNING_KEY_FILE`` constant binds ``CLAUDE_DIR`` on
    import, so a later env change does not move it). The verifier will use the
    latter, so a green here must mean THAT file exists.
    """
    for candidate in (paths.signing_key, SIGNING_KEY_FILE):
        try:
            if not candidate.is_file() or candidate.stat().st_size == 0:
                return False
        except OSError:
            return False
    return True


def crypto_available() -> bool:
    """True iff Ed25519 support imported successfully in ``lib.der_receipt``.

    Distinguishes "DER authenticity leg failed" from "cryptography not
    installed" — without this, a missing dependency renders as a forged
    signature. Inspects the shipped module's own availability flag rather than
    re-importing independently, so the answer is what the verifier will do.
    """
    try:
        from lib import der_receipt
    except Exception:  # noqa: BLE001 - absent module is simply "unavailable"
        return False
    return bool(getattr(der_receipt, "_HAVE_ED25519", False))


def head_id(paths: MoatPaths) -> str:
    """The belief-chain HEAD node id at load time, or "" when unresolvable.

    Read ONCE and displayed as captured: the chain is appended to live, so two
    reads within a session are not guaranteed to see the same tip.
    """
    try:
        return paths.context_head.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# --------------------------------------------------------------------------
# Byte-offset indexing + seek hydration
# --------------------------------------------------------------------------


def _entry_from(raw: bytes, offset: int) -> IndexEntry | None:
    """Parse the five index fields from one raw JSONL line. None when unusable."""
    try:
        d = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict):
        return None
    node_id = d.get("id")
    if not isinstance(node_id, str) or not node_id:
        return None
    depth = d.get("depth")
    return IndexEntry(
        offset=offset,
        id=node_id,
        kind=str(d.get("kind") or ""),
        ts=str(d.get("ts") or ""),
        depth=depth if isinstance(depth, int) else 0,
    )


def build_index(path: Path) -> LedgerIndex:
    """Single forward pass recording ONLY ``(offset, id, kind, ts, depth)`` per
    line — never building dataclasses. Measured live: 6173 records in 0.018 s
    over 4.4 MB.

    Binary mode is deliberate: ``tell()`` is disabled during text-file iteration,
    so offsets are accumulated from raw byte lengths and are exact for ``seek``.
    """
    entries: list[IndexEntry] = []
    by_id: dict[str, int] = {}
    raw_lines = 0
    if not path.is_file():
        return LedgerIndex(path=path)
    offset = 0
    with open(path, "rb") as fh:
        for raw in fh:
            start = offset
            offset += len(raw)
            if not raw.strip():
                continue
            raw_lines += 1
            entry = _entry_from(raw, start)
            if entry is None:
                continue
            by_id[entry.id] = len(entries)
            entries.append(entry)
    return LedgerIndex(
        path=path, entries=tuple(entries), raw_lines=raw_lines, by_id=by_id
    )


def _construct(d: Mapping[str, Any]) -> PrecogIDR | None:
    """Build a PrecogIDR via the canonical back-compat idiom.

    Copied from ``read_idr_chain``: ``if k in d`` is the back-compat key — legacy
    records predate optional fields (e.g. ``decision_anatomy``), and without the
    guard a missing key would raise and drop the record.
    """
    try:
        return PrecogIDR(
            **{k: d[k] for k in PrecogIDR.__dataclass_fields__ if k in d}
        )
    except (KeyError, TypeError):
        return None


def hydrate_at(path: Path, offset: int) -> PrecogIDR | None:
    """``seek(offset)`` + ``readline()`` + ``json.loads`` for exactly ONE record."""
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            raw = fh.readline()
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        d = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return _construct(d) if isinstance(d, dict) else None


def hydrate(index: LedgerIndex, node_id: str) -> PrecogIDR | None:
    """``by_id`` lookup then ``hydrate_at``.

    The drill-in primitive: pressing enter on a row costs ONE seek, not a 4.4 MB
    parse.
    """
    pos = index.by_id.get(node_id)
    if pos is None:
        return None
    return hydrate_at(index.path, index.entries[pos].offset)


def hydrate_window(index: LedgerIndex, start: int, count: int) -> list[PrecogIDR]:
    """Hydrate a bounded slice, short at end-of-ledger.

    The bound is what keeps memory flat: a verdict needs a few dozen nodes,
    never 6173.
    """
    if start < 0 or count <= 0:
        return []
    out: list[PrecogIDR] = []
    for entry in index.entries[start : start + count]:
        node = hydrate_at(index.path, entry.offset)
        if node is not None:
            out.append(node)
    return out


# --------------------------------------------------------------------------
# The IDR (decision) ledger
# --------------------------------------------------------------------------


def load_idr_ledger(paths: MoatPaths) -> list[PrecogIDR]:
    """All decision records in file order, via the shipped ``read_idr_chain``.

    ``idr.jsonl`` is ~1.0 MB / 413 records, so a full parse is correct here and
    IS composition — the streaming constraint targets the 4.4 MB belief chain.
    ``path=`` is passed explicitly to pin this dashboard to one ledger file;
    the default (``_default_idr_path``) re-resolves from the environment on
    every call.
    """
    return read_idr_chain(predecessor_id=None, path=paths.idr)


def discover_roots(records: Sequence[PrecogIDR]) -> tuple[str, ...]:
    """Discover the exogenous anchors actually referenced by the data.

    MANDATORY, not optional. Live IDR ids are ``precog-<epoch>-<8hex>`` and never
    match ``EXOGENOUS_ANCHOR_PATTERNS``, so an INVENTED root silently drops
    records — ``empty_forest(("human:probe",))`` loses 8 of 413 to a rejection
    cascade. Scans every ``predecessor_idr`` that is NOT a known node id AND DOES
    match a shipped prefix. Live this yields exactly one root and a 413-node
    VERIFIED forest.
    """
    known = {r.id for r in records}
    found: list[str] = []
    seen: set[str] = set()
    for rec in records:
        pred = rec.predecessor_idr
        if not pred or pred in known or pred in seen:
            continue
        if any(pred.startswith(p) for p in EXOGENOUS_ANCHOR_PATTERNS):
            seen.add(pred)
            found.append(pred)
    return tuple(found)


def build_idr_forest(
    records: Sequence[PrecogIDR], roots: tuple[str, ...]
) -> ForestBuild:
    """Fold ``add_idr`` over records, CAPTURING every ``IdrForestError`` into
    ``rejected`` with its reason instead of swallowing it.

    The count is surfaced on screen so a future corruption is visible rather than
    absorbed.
    """
    raw = len(records)
    if not roots:
        return ForestBuild(
            forest=Forest(roots=(), nodes={}, edges={}),
            roots=(),
            rejected=tuple((r.id, "no exogenous root discovered") for r in records),
            loaded=0,
            raw=raw,
        )
    try:
        forest = empty_forest(roots)
    except IdrForestError as exc:
        return ForestBuild(
            forest=Forest(roots=(), nodes={}, edges={}),
            roots=roots,
            rejected=(("<roots>", str(exc)),),
            loaded=0,
            raw=raw,
        )
    rejected: list[tuple[str, str]] = []
    for rec in records:
        try:
            forest = add_idr(forest, rec)
        except IdrForestError as exc:
            rejected.append((rec.id, str(exc)))
    return ForestBuild(
        forest=forest,
        roots=roots,
        rejected=tuple(rejected),
        loaded=len(forest.nodes),
        raw=raw,
    )


def _node_status(idr: PrecogIDR, *, have_key: bool) -> str:
    """Per-node verdict, honest about the absent-key case.

    ``_verify_node`` is underscore-private but is the ONLY way to get per-node
    status — ``verify_chain_integrity`` collapses the whole forest to one value
    and cannot drive a per-row table.
    """
    if not have_key:
        return STATUS_UNAVAILABLE
    return str(getattr(_verify_node(idr), "value", STATUS_UNAVAILABLE))


def decision_rows(build: ForestBuild, *, have_key: bool = True) -> tuple[DecisionRow, ...]:
    """The browsable forest list, in ``iter_nodes_topo`` order (depth, id) so
    ordering is reproducible across runs.
    """
    rows: list[DecisionRow] = []
    for node in iter_nodes_topo(build.forest):
        idr = node.idr
        rows.append(
            DecisionRow(
                node_id=idr.id,
                kind=idr.kind,
                ts=idr.ts,
                depth=idr.depth,
                addr=content_addr(asdict(idr)),
                status=_node_status(idr, have_key=have_key),
            )
        )
    return tuple(rows)


def search_rows(rows: Sequence[DecisionRow], query: str) -> tuple[int, ...]:
    """Pure, case-insensitive substring match over node_id, kind and ts.

    Pure so it is unit-testable with no ledger present.
    """
    q = (query or "").strip().lower()
    if not q:
        return tuple(range(len(rows)))
    return tuple(
        i
        for i, r in enumerate(rows)
        if q in r.node_id.lower() or q in r.kind.lower() or q in r.ts.lower()
    )


# --------------------------------------------------------------------------
# The belief (context) chain
# --------------------------------------------------------------------------


def _snapshot_positions(index: LedgerIndex) -> list[int]:
    return [i for i, e in enumerate(index.entries) if e.kind == "context-snapshot"]


def _linear_prefix(nodes: Sequence[PrecogIDR]) -> list[PrecogIDR]:
    """Longest prefix where each node's predecessor IS the previous node.

    A window taken from file order can contain an orphan fragment (~64 live
    records are not reachable from HEAD). Trimming to the linear prefix is what
    makes the scope a genuine chain that ``build_chain_forest`` accepts — and the
    trim is reported via ``BeliefScope.truncated`` rather than hidden.
    """
    if not nodes:
        return []
    out = [nodes[0]]
    for node in nodes[1:]:
        if node.predecessor_idr != out[-1].id:
            break
        out.append(node)
    return out


def snapshot_scope(index: LedgerIndex, *, back: int = 0, limit: int = 64) -> BeliefScope:
    """Select a snapshot-rooted belief window — THE source of a genuine green.

    ``back=N`` picks the Nth-from-last snapshot to widen the window (``back=0``
    gave 6 nodes live; a larger ``back`` gives a meatier demo). ``boundary`` is
    ``nodes[0].predecessor_idr``, required by ``build_chain_forest`` because a
    reaped chain's root snapshot names an ARCHIVED predecessor.
    """
    snaps = _snapshot_positions(index)
    if not snaps:
        return BeliefScope()
    pick = snaps[max(0, len(snaps) - 1 - max(0, back))]
    window = hydrate_window(index, pick, limit)
    nodes = _linear_prefix(window)
    if not nodes:
        return BeliefScope()
    return BeliefScope(
        nodes=tuple(nodes),
        snapshot_id=nodes[0].id,
        boundary=nodes[0].predecessor_idr,
        truncated=len(nodes) < len(window) or (pick + limit) < len(index.entries),
    )


def index_citations(
    index: LedgerIndex, *, addrs: Iterable[str]
) -> Mapping[str, tuple[str, ...]]:
    """The belief -> decision join, done HONESTLY.

    ``resolve_citation`` is FAIL-OPEN by contract (any error returns
    "RESOLVED"), so a read failure is indistinguishable from a real hit. This
    scans ``decision.records_idr`` itself and matches against the real
    content-address set. Live truth: 148 cited deltas, 148/148 genuinely
    present, 0 dangling.

    Streams the ledger with a cheap raw-bytes prefilter — a line without the
    literal ``records_idr`` cannot carry the key, so it is skipped without a
    parse. Returns ``addr -> citing belief node ids``.
    """
    wanted = set(addrs)
    hits: dict[str, list[str]] = {}
    if not wanted or not index.path.is_file():
        return {}
    needle = b"records_idr"
    with open(index.path, "rb") as fh:
        for raw in fh:
            if needle not in raw:
                continue
            try:
                d = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            decision = d.get("decision") if isinstance(d, dict) else None
            if not isinstance(decision, dict):
                continue
            addr = decision.get("records_idr")
            if isinstance(addr, str) and addr in wanted:
                hits.setdefault(addr, []).append(str(d.get("id") or ""))
    return {k: tuple(v) for k, v in hits.items()}


def believed_by(
    index: LedgerIndex,
    citations: Mapping[str, tuple[str, ...]],
    addr: str,
) -> tuple[IndexEntry, ...]:
    """Index entries for the belief nodes citing ``addr`` — ids and metadata only.

    Deliberately returns ``IndexEntry`` (id / kind / ts / depth), NEVER hydrated
    payloads: the belief chain may carry client or personal content, so the
    panel renders identifiers and counts, not belief text.
    """
    out: list[IndexEntry] = []
    for node_id in citations.get(addr, ()):
        pos = index.by_id.get(node_id)
        if pos is not None:
            out.append(index.entries[pos])
    return tuple(out)


# --------------------------------------------------------------------------
# Sovereign-council records (the DER leg's input)
# --------------------------------------------------------------------------


def load_council_records(paths: MoatPaths) -> Mapping[str, dict]:
    """Load ``state/sovereign-council/*.json`` keyed by ``idr_id``.

    This is the join into the decision list, and it is what supplies the DER leg
    its input — DER emits no standing receipt store, so receipts are BUILT live
    (offline) from these records. Verified live: 136/137 resolve into
    ``idr.jsonl``.
    """
    out: dict[str, dict] = {}
    if not paths.council.is_dir():
        return out
    for path in sorted(paths.council.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        key = record.get("idr_id")
        if isinstance(key, str) and key:
            out[key] = record
    return out


# --------------------------------------------------------------------------
# The four verify legs — each COMPOSES a shipped function
# --------------------------------------------------------------------------


def verify_idr_leg(build: ForestBuild, *, have_key: bool = True) -> LegResult:
    """Composes ``verify_chain_integrity`` + ``forest_merkle_root``.

    Returns ``unavailable`` when the signing key is absent rather than reporting
    the resulting FALSE broken. The Merkle root is the headline number the
    tamper pane watches move.
    """
    nodes = len(build.forest.nodes)
    if nodes == 0:
        return LegResult(
            LegName.IDR, STATUS_NO_DATA, "no decisions loaded",
            note="an empty ledger is NO DATA, never a green",
        )
    root = forest_merkle_root(build.forest)
    detail: dict[str, Any] = {
        "nodes": nodes,
        "raw": build.raw,
        "rejected": len(build.rejected),
        "merkle_root": root,
        "roots": build.roots,
    }
    if not have_key:
        return LegResult(
            LegName.IDR, STATUS_UNAVAILABLE, root[:16], detail,
            note="signing key absent — verifying would WRITE a new random key",
        )
    verdict = verify_chain_integrity(build.forest)
    return LegResult(LegName.IDR, str(verdict.value), root[:16], detail)


def _orphan_leg(detail: dict[str, Any], exc: Exception) -> LegResult:
    """The STRUCTURAL outcome — a missing link, reported as "cannot verify".

    UNAVAILABLE, not BROKEN. The forest could not be BUILT, so no signature was
    ever checked — asserting red here would claim a forgery we have not
    established. It renders grey with a loud ORPHANED ROOT headline, so it can
    never be mistaken for tampering. This is the live whole-chain resting state;
    painting it red would make the baseline indistinguishable from the tamper
    demo, which is exactly the conflation the design forbids.
    """
    detail["structural_error"] = str(exc)
    return LegResult(
        LegName.BELIEF, STATUS_UNAVAILABLE, "ORPHANED ROOT", detail,
        note="structural: a missing link, NOT a forgery — no signature was checked",
    )


def _replay_digest(forest: Forest, node_id: str, detail: dict[str, Any]) -> None:
    """Record the deterministic replay digest, or why replay was refused."""
    try:
        detail["replay_digest"] = replay_chain(forest, node_id).replay_digest
    except IdrForestError as exc:
        detail["replay_error"] = str(exc)


# A status word with no scope is the defect this whole surface exists to avoid.
# The belief leg verifies a SNAPSHOT WINDOW, not the whole chain: measured live on
# 2026-08-24 it returned "verified" over 4 nodes while the ledger held 16,205
# records, and the 4 lived only in `detail` where no reader looks. A green whose
# scope is invisible reads as a green over everything.
#
# Sub-1% coverage is not a footnote, so it is stated as the leg's own note and
# carried into the headline by the renderer. The window is legitimate -- this
# does not downgrade the verdict, which is true of what it checked. It stops the
# verdict being read as a claim about what it did NOT check.
_BELIEF_SCOPE_FLOOR = 0.99


def _belief_scope_note(checked: int, total: int | None) -> str:
    """State what a belief verdict actually covers, in the note the reader sees."""
    if not total or total <= 0:
        return "scope unknown - window size not supplied by caller"
    if checked >= total:
        return f"full chain: all {total} belief records"
    pct = 100.0 * checked / total
    shown = f"{pct:.2f}%" if pct < _BELIEF_SCOPE_FLOOR else f"{pct:.1f}%"
    return (
        f"SNAPSHOT WINDOW ONLY: {checked} of {total} belief records ({shown}) - "
        f"says nothing about the other {total - checked}"
    )


def verify_belief_leg(
    scope: BeliefScope,
    *,
    have_key: bool = True,
    total_records: int | None = None,
) -> LegResult:
    """Composes ``build_chain_forest`` -> ``verify_chain_integrity`` +
    ``forest_merkle_root`` + ``replay_chain``.

    Catches ``IdrForestError`` and reports it as ORPHANED ROOT (structural),
    STRICTLY distinct from a signature mismatch reported as tampering —
    conflating them is what makes the live chain look forged when it is merely
    unlinked.
    """
    if not scope.nodes:
        return LegResult(
            LegName.BELIEF, STATUS_NO_DATA, "no belief scope",
            note="0 belief records is NO DATA, never a green",
        )
    detail: dict[str, Any] = {
        "nodes": len(scope.nodes),
        "snapshot_id": scope.snapshot_id,
        "boundary": scope.boundary,
        "truncated": scope.truncated,
    }
    try:
        forest = build_chain_forest(
            list(scope.nodes),
            genesis_anchor=GENESIS_ANCHOR,
            snapshot_boundary=scope.boundary,
        )
    except IdrForestError as exc:
        return _orphan_leg(detail, exc)
    root = forest_merkle_root(forest)
    detail["merkle_root"] = root
    if not have_key:
        return LegResult(
            LegName.BELIEF, STATUS_UNAVAILABLE, root[:16], detail,
            note="signing key absent — cannot verify",
        )
    verdict = verify_chain_integrity(forest)
    _replay_digest(forest, scope.nodes[-1].id, detail)
    return LegResult(
        LegName.BELIEF,
        str(verdict.value),
        root[:16],
        detail,
        note=_belief_scope_note(len(scope.nodes), total_records),
    )


def _tally_from_decision(decision: Mapping[str, Any]) -> dict[str, int] | None:
    """Stored aggregate tally on a ``prove-it-artifact`` IDR, or None."""
    keys = ("tally_verified", "tally_fuzzy", "tally_not_found")
    if not any(k in decision for k in keys):
        return None
    return {
        "verified": int(decision.get("tally_verified") or 0),
        "fuzzy": int(decision.get("tally_fuzzy") or 0),
        "not_found": int(decision.get("tally_not_found") or 0),
    }


def _cite_status(tally: Mapping[str, int]) -> str:
    total = sum(int(tally.get(k, 0)) for k in ("verified", "fuzzy", "not_found"))
    if total == 0:
        return STATUS_NO_DATA
    if int(tally.get("not_found", 0)) > 0:
        return STATUS_BROKEN
    if int(tally.get("fuzzy", 0)) > 0:
        return STATUS_DEGRADED
    return STATUS_VERIFIED


def _cite_leg_from_tally(tally: Mapping[str, int], rate: float | None) -> LegResult:
    total = sum(int(tally.get(k, 0)) for k in ("verified", "fuzzy", "not_found"))
    grounded = int(tally.get("verified", 0)) + int(tally.get("fuzzy", 0))
    pct = rate if rate is not None else (grounded / total if total else 0.0)
    detail = dict(tally)
    detail["grounding_rate"] = pct
    note = ""
    if int(tally.get("fuzzy", 0)):
        note = "fuzzy is typographic-only; the bundled independent verifier is stricter"
    return LegResult(
        LegName.CITE,
        _cite_status(tally),
        f"{pct * 100:.0f}% grounded ({grounded}/{total})" if total else "no citations",
        detail,
        note=note,
    )


def verify_cite_leg(idr: PrecogIDR | None, spec: Mapping[str, Any] | None = None) -> LegResult:
    """Two honest sources, no invention.

    When ``spec`` is given, calls the READ-ONLY ``prove_it.render(spec)`` — never
    ``main()``, never ``build_legal_receipt``, which default to WRITING the live
    ledgers. Otherwise, when the selected decision is a ``prove-it-artifact``
    IDR, reads its stored aggregate tally. Any other decision yields ``no-data``
    — NOT red. Emits counts and rates only, never quote or source payloads.
    """
    if spec is not None:
        try:
            from lib.prove_it import render as prove_it_render
            _html, prov = prove_it_render(dict(spec))
        except Exception as exc:  # noqa: BLE001 - a render failure is not a red citation
            return LegResult(
                LegName.CITE, STATUS_UNAVAILABLE, "cannot verify",
                {"error": type(exc).__name__},
                note="prove_it.render failed — not a citation failure",
            )
        tally = dict(prov.get("tally") or {})
        return _cite_leg_from_tally(tally, prov.get("grounding_rate"))
    if idr is None or not isinstance(idr.decision, dict):
        return LegResult(LegName.CITE, STATUS_NO_DATA, "no citation data")
    tally = _tally_from_decision(idr.decision)
    if tally is None:
        return LegResult(
            LegName.CITE, STATUS_NO_DATA, "no citation data",
            note="this decision kind carries no cite.verify tally",
        )
    rate = idr.decision.get("grounding_rate")
    return _cite_leg_from_tally(tally, float(rate) if isinstance(rate, (int, float)) else None)


def _der_status(result: Mapping[str, Any]) -> str:
    return STATUS_VERIFIED if result.get("ok") else STATUS_BROKEN


def verify_der_leg(record: Mapping[str, Any] | None, *, offline: bool = True) -> LegResult:
    """Composes ``build_receipt(record, do_stamp=not offline)`` then
    ``verify_receipt(receipt, check_anchor=not offline)``.

    Offline is the TUI default — both flags otherwise shell out to the
    OpenTimestamps calendar over the network. The timestamp leg is reported but
    INFORMATIONAL: ``ok`` gates on exactly three legs and excludes it. Returns
    ``unavailable`` when ``cryptography`` is missing instead of a red
    authenticity leg. Renders model names, ``memory_head`` and the seal only —
    never the prompt, speaker answers, or judge synthesis, which the record
    carries in full.
    """
    if record is None:
        return LegResult(LegName.DER, STATUS_NO_DATA, "no council record for this decision")
    if not crypto_available():
        return LegResult(
            LegName.DER, STATUS_UNAVAILABLE, "crypto unavailable",
            note="cryptography (Ed25519) not installed — cannot verify, NOT a failed signature",
        )
    from lib.der_receipt import build_receipt, verify_receipt

    try:
        receipt = build_receipt(dict(record), do_stamp=not offline)
        result = verify_receipt(receipt, check_anchor=not offline)
    except Exception as exc:  # noqa: BLE001 - a build failure is not a forged receipt
        return LegResult(
            LegName.DER, STATUS_UNAVAILABLE, "cannot build receipt",
            {"error": type(exc).__name__},
            note=str(exc)[:200],
        )
    checks = dict(result.get("checks") or {})
    return LegResult(
        LegName.DER,
        _der_status(result),
        "ok" if result.get("ok") else "FAILED",
        checks,
        note="timestamp is informational — ok gates on fixity + authenticity + joint-causation",
    )


def build_der_receipt(record: Mapping[str, Any] | None) -> dict | None:
    """Build ONE offline DER receipt (``do_stamp=False``) or None.

    The tamper session takes this as its mutable subject, so it is built once
    here and never rebuilt inside the key loop.
    """
    if record is None or not crypto_available():
        return None
    from lib.der_receipt import build_receipt

    try:
        return build_receipt(dict(record), do_stamp=False)
    except Exception:  # noqa: BLE001 - absent crypto / malformed record
        return None


# --------------------------------------------------------------------------
# THE JOIN + selective disclosure
# --------------------------------------------------------------------------


def build_decision_view(
    row: DecisionRow,
    *,
    build: ForestBuild,
    index: LedgerIndex,
    citations: Mapping[str, tuple[str, ...]],
    council: Mapping[str, dict],
    scope: BeliefScope,
    paths: MoatPaths,
) -> DecisionView:
    """THE JOIN — assemble one screen from all four legs for a selected decision.

    Hydrates the IDR, resolves citing belief nodes via CONTENT ADDRESS (the join
    is one-way: belief -> decision; the IDR holds no pointer back), looks up the
    council record by ``idr_id``, and runs all four verify legs. This single call
    is what the DECISION screen renders and what tamper mode takes as its
    subject.
    """
    have_key = key_present(paths)
    node = build.forest.nodes.get(row.node_id)
    idr = node.idr if node is not None else None
    record = council.get(row.node_id)
    legs = (
        verify_idr_leg(build, have_key=have_key),
        verify_belief_leg(scope, have_key=have_key, total_records=len(index)),
        verify_cite_leg(idr),
        verify_der_leg(record),
    )
    return DecisionView(
        row=row,
        idr=idr,
        believed_by=believed_by(index, citations, row.addr),
        council_record=record,
        legs=legs,
        scope=scope,
    )


def inclusion_proof(build: ForestBuild, node_id: str) -> tuple[Mapping[str, Any] | None, bool]:
    """Composes ``forest_inclusion_proof`` then round-trips it through
    ``verify_forest_inclusion``.

    Proves ONE decision is committed to the root without revealing any other —
    the selective-disclosure pane. Returns ``(None, False)`` on
    ``ForestInclusionError`` for an unknown node rather than raising into the
    key loop.
    """
    try:
        proof = forest_inclusion_proof(build.forest, node_id)
    except (ForestInclusionError, IdrForestError):
        return None, False
    verified = verify_forest_inclusion(
        proof["content_addr"], proof["proof"], proof["forest_root"]
    )
    return proof, bool(verified)


# --------------------------------------------------------------------------
# Rendered prove-it artifacts (the only per-citation records on disk)
# --------------------------------------------------------------------------

_PROVENANCE_RE = re.compile(
    r"""id=['"]provenance['"]\s*>(.*?)</script>""", re.S
)

# Excluded by policy: financial provenance is sensitive-info class
# (rules/no-sensitive-info-in-grip.md) and must not be surfaced.
_PROVENANCE_DENY = ("billing", "invoice", "financial")


def load_embedded_provenance(path: Path) -> dict | None:
    """Extract the JSON block embedded in a rendered prove-it artifact.

    The only place per-citation records stand on disk. Returns tallies and rates
    for the cite pane; callers must not surface payloads. The financial artifact
    in that directory is excluded by policy.
    """
    if any(token in path.name.lower() for token in _PROVENANCE_DENY):
        return None
    try:
        html = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _PROVENANCE_RE.search(html)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


__all__ = [
    "GENESIS_ANCHOR",
    "STATUS_VERIFIED",
    "STATUS_DEGRADED",
    "STATUS_BROKEN",
    "STATUS_UNAVAILABLE",
    "STATUS_NO_DATA",
    "LegName",
    "MoatPaths",
    "IndexEntry",
    "LedgerIndex",
    "ForestBuild",
    "DecisionRow",
    "BeliefScope",
    "LegResult",
    "DecisionView",
    "Verdict",
    "resolve_paths",
    "key_present",
    "crypto_available",
    "head_id",
    "build_index",
    "hydrate_at",
    "hydrate",
    "hydrate_window",
    "load_idr_ledger",
    "discover_roots",
    "build_idr_forest",
    "decision_rows",
    "search_rows",
    "snapshot_scope",
    "index_citations",
    "believed_by",
    "load_council_records",
    "verify_idr_leg",
    "verify_belief_leg",
    "verify_cite_leg",
    "verify_der_leg",
    "build_der_receipt",
    "build_decision_view",
    "inclusion_proof",
    "load_embedded_provenance",
]
