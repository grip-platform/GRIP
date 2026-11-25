"""Holistic memory synthesiser — deterministic reducer over the memory substrate.

Produces a single bounded `HOLISTIC.md` kernel from ~860 memory files +
MEMORY.md index + learning/insights.jsonl, so a fresh session inherits the
latest synthesised mental model instead of a 704-line index that the
auto-memory loader truncates at ~200 lines.

Design (grounded broly council verdict 2026-05-16, PROCEED-WITH-REVISIONS):

  R1 (non-negotiable) — completeness ledger. Every PARAMOUNT/feedback memory
      is enumerated in-kernel with a one-line digest + its KONO recall key.
      The cap may compress bodies; it may NEVER drop a PARAMOUNT/feedback
      ledger entry. `verify()` returns any substrate PARAMOUNT/feedback file
      absent from a kernel — the CLI exits non-zero on any gap. This turns a
      silent drop into an explicit, counted, recoverable deferral.
  R2 — idempotency is defined over the substrate payload, not wall-clock.
      This function is pure over (memory files + index): same inputs →
      byte-identical kernel. Volatile fields (generation, timestamp) are the
      OUTER caller's concern and are excluded here by construction.
  R5 — this is a REDUCER over whatever substrate exists on disk. It does NOT
      capture or harvest insights (that is the auto-insight-memory pipeline).

Not LLM-augmented: kernel synthesis is value-generation, not a monotone
ALLOW/DENY gate (rules/llm-augmentation-pattern.md category boundary —
council Q6 confirmed). Deterministic + falsifiable by construction.

DIP: all substrate paths are injected; `default_substrate()` resolves the
real ones for the CLI. Path-portable (no hardcoded user paths).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

PRIOR_CAP_CHARS = 40_000  # the pre-2026-06-15 cap, retained so the artifact can
# emit an old→new delta that is falsifiable ON SIGHT (#3548): the cut is checkable
# from the kernel itself, not only the PR description. Bumping CAP_CHARS without
# bumping this would make the emitted "−X%" lie — that asymmetry IS the falsifier.
CAP_CHARS = 30_000  # lowered 40k→30k 2026-06-15: the lossless ledger compression
# (derivable recall keys omitted, ~11.5k freed) is banked as a real floor cut, not
# refilled with more inlined bodies. Governs BOTH HOLISTIC.md AND the P6 snapshot fold
# (lib/context_snapshot.fold_chain reuses this constant) — one knob, both warm-start paths.
_HEADER_RESERVE = 900  # chars reserved for the accounting/bound + delta header

# Path-portability shim (rules/path-portability.md): _GRIP_HOME resolves to
# $CLAUDE_DIR env (when set) or $HOME/.claude.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
from path_encoding import project_dir  # noqa: E402

_GRIP_HOME = get_claude_dir()

# Tier priority: lower number = higher inclusion priority. PARAMOUNT is an
# orthogonal never-drop flag layered on top (a PARAMOUNT memory of ANY tier
# is never dropped from the ledger).
_TIER_PRIORITY = {"feedback": 2, "project": 3, "user": 4, "reference": 5, "other": 6}
_PARAMOUNT_PRIORITY = 1

_INDEX_LINE = re.compile(r"^- .*?\[(?P<title>[^\]]+)\]\((?P<file>[^)]+\.md)\)\s*(?:—|-)?\s*(?P<hook>.*)$")
_PARAMOUNT_TOKEN = "PARAMOUNT"


@dataclass(frozen=True)
class MemoryEntry:
    filename: str
    title: str
    hook: str
    tier: str
    paramount: bool
    recall_key: str
    content_hash: str

    @property
    def sort_key(self) -> tuple[int, str]:
        prio = _PARAMOUNT_PRIORITY if self.paramount else _TIER_PRIORITY.get(self.tier, 6)
        return (prio, self.filename)


@dataclass
class KernelResult:
    text: str
    total: int
    inlined: int
    summarised: int
    deferred: int
    paramount_count: int
    feedback_count: int
    substrate_digest: str
    ledger_files: list[str] = field(default_factory=list)


_TIERS = ("feedback", "project", "reference", "user")


def _tier_of(filename: str, raw: str = "") -> str:
    """Classify a memory file into its tier.

    The filename prefix is a PROXY for the tier, not the tier itself, and it
    fails two ways: `/remember` slugifies with a hyphen while older files use an
    underscore, and some files carry no tier prefix at all. Both classes were
    silently returned as "other" and dropped from the synthesised kernel, which
    is eviction from reachability rather than a cosmetic mislabel — the pull
    tier has no second path behind it.

    So: accept either separator, then fall back to the frontmatter `type:`
    field, which is what the filename was standing in for all along.
    """
    for prefix in _TIERS:
        if filename.startswith(prefix + "_") or filename.startswith(prefix + "-"):
            return prefix
    if raw:
        m = re.search(r"^\s*type:\s*([a-z]+)\s*$", raw[:600], re.MULTILINE)
        if m and m.group(1) in _TIERS:
            return m.group(1)
    return "other"


def _is_paramount(hook: str, body_head: str) -> bool:
    """Mechanical PARAMOUNT detection — cheap string precheck (the same
    pattern auto-insight-memory.md hazard #3 uses). PARAMOUNT if the literal
    token appears in the index hook OR the file's head."""
    return _PARAMOUNT_TOKEN in hook or _PARAMOUNT_TOKEN in body_head


def _recall_key(title: str, filename: str) -> str:
    base = title.strip() or filename.removesuffix(".md").replace("_", " ")
    return re.sub(r"\s+", " ", base)[:80].strip()


# The hook is scanned only inside the leading frontmatter block. 2,000 chars
# covers the longest description in the substrate (652) plus its sibling keys,
# and bounds the read so a stray `description:` at column 0 further down a file
# body cannot be mistaken for frontmatter. Measured 2026-08-26: a 2,000- and a
# 4,000-char window find the identical 399 descriptions across 416 files.
_FRONTMATTER_SCAN_CHARS = 2_000
_DESCRIPTION = re.compile(r"^description:[ \t]*(?P<value>\S.*?)[ \t]*$", re.MULTILINE)
# YAML block-scalar introducers: `description: >` puts the text on the FOLLOWING
# lines, so the introducer itself is not a digest and must not be emitted as one.
_BLOCK_SCALARS = frozenset({">", "|", ">-", "|-", ">+", "|+"})
# A fallback hook is bounded so one long description cannot crowd several others
# out of the shared body budget (~11.6k chars against 416 entries). 160 sits just
# above the longest human-written index hook (136), so a fallback line reads like
# the hooks it stands in for rather than a paragraph, and at the description
# median (165), so about half pass through untouched.
_FALLBACK_HOOK_CHARS = 160


def _frontmatter_description(raw: str) -> str:
    """Frontmatter `description:` → a one-line hook, or "" when there is none.

    Second rung of the hook chain. 399 of 416 memory files carry a `description:`
    that nothing read, so a file with no MEMORY.md pointer got an empty hook and
    rendered as a contentless `— (see ledger)` body line — a line naming a file
    whose name the R1 ledger above already carries verbatim.
    """
    m = _DESCRIPTION.search(raw[:_FRONTMATTER_SCAN_CHARS])
    if not m:
        return ""
    value = re.sub(r"\s+", " ", m.group("value")).strip().strip("\"'").strip()
    if value in _BLOCK_SCALARS:
        return ""
    if len(value) > _FALLBACK_HOOK_CHARS:
        value = value[: _FALLBACK_HOOK_CHARS - 1].rstrip() + "…"
    return value


# A record may declare itself private: `private: true` in its frontmatter, at the
# top level or under `metadata:`. It is then left out of the kernel entirely --
# not ledgered, not inlined, not summarised, not counted. The kernel is a tracked
# file regenerated into the shared tree, so even a record's NAME in it is one
# commit from the repository, and `rules/personal-matters-stay-private.md` keeps
# personal matters out of GRIP by every route. The scrub in `_scrub` replaces
# denylisted names; it cannot know that a title describes a personal matter.
# Measured 2026-10-09: a `project` record on a personal matter of the
# operator's, whose own description said "local only, never in the repo",
# reached the shared tree's kernel as a summarised body line. Only the leading
# frontmatter block is read, so the same line in a body hides nothing
# (tests/test_holistic_private_records.py).
_PRIVATE = re.compile(r"^\s*private:\s*true\s*$", re.MULTILINE)


def _frontmatter(raw: str) -> str:
    """The leading ``---`` block of a record, read to its closing delimiter, or
    "" when the file has none.

    Deliberately NOT cut at ``_FRONTMATTER_SCAN_CHARS``: a long quoted
    description pushes a ``private: true`` line past any fixed window, and a
    marker that is cut off is a record that goes public (Codex on GRIP#7797).
    Only a block with no closing delimiter is bounded by the window.
    """
    if not raw.startswith("---"):
        return ""
    end = raw.find("\n---", 3)
    return raw[:end] if end != -1 else raw[:_FRONTMATTER_SCAN_CHARS]


def _is_private(raw: str) -> bool:
    """True when the record's frontmatter declares ``private: true``."""
    return bool(_PRIVATE.search(_frontmatter(raw)))


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _parse_index(memory_index: Path) -> dict[str, tuple[str, str]]:
    """MEMORY.md → {filename: (title, hook)}. Tolerant of the ⟶ steer prefix
    and both em-dash / hyphen separators."""
    out: dict[str, tuple[str, str]] = {}
    if not memory_index.exists():
        return out
    for line in memory_index.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _INDEX_LINE.match(line.strip())
        if m:
            out[m["file"]] = (m["title"].strip(), m["hook"].strip())
    return out


def _enumerate(memory_dir: Path, memory_index: Path) -> list[MemoryEntry]:
    """Mechanically enumerate EVERY memory file (R1), except one that declares
    itself private (`_is_private`). Index supplies the human title/hook; files
    missing from the index are still enumerated (filename-derived title) so
    nothing escapes the ledger. A private record is left out of the ledger and
    the body alike, and `verify` reads this same enumeration, so the omission
    is never reported as an R1 gap."""
    index = _parse_index(memory_index)
    entries: list[MemoryEntry] = []
    if not memory_dir.exists():
        return entries
    for path in sorted(memory_dir.glob("*.md")):
        fn = path.name
        if fn == "MEMORY.md":
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""
        if _is_private(raw):
            continue
        title, hook = index.get(fn, (fn.removesuffix(".md").replace("_", " "), ""))
        # Classification stays keyed on the INDEX hook, deliberately: the chain
        # below changes what a body line SAYS, never which tier an entry gets or
        # whether it is ledgered. So the only digest input it can move is
        # REDUCER_VERSION — which is exactly why that constant must be bumped.
        paramount = _is_paramount(hook, raw[:600])
        entries.append(
            MemoryEntry(
                filename=fn,
                title=title,
                hook=hook or _frontmatter_description(raw) or title,
                tier=_tier_of(fn, raw),
                paramount=paramount,
                recall_key=_recall_key(title, fn),
                content_hash=_hash(raw),
            )
        )
    return entries


# Bump when the reducer changes shape in a way the inputs cannot express:
# classification logic, ledger/body partitioning, header layout. The digest
# must be a function of the OUTPUT-determining state, not of the inputs alone.
#
# 2 → 3 (2026-08-26): the hook chain gained a `description:` fallback. Neither
# hashed field moves — the same files with the same bytes now render a different
# body — so without this bump `regenerate()` would compare equal, skip the write
# and leave the fix inert on disk until some unrelated memory file changed.
REDUCER_VERSION = "3"


def _substrate_digest(entries: list[MemoryEntry]) -> str:
    """R2: digest over the content set AND the reducer — pure, excludes wall-clock.

    The original hashed only `filename:content_hash`. That is deterministic, which
    was the point, but it made the digest blind to the transformation: changing how
    a file is classified produced a byte-identical digest, so `regenerate`'s
    compare-and-skip returned `changed: False` and never wrote the corrected
    kernel. A reducer fix stayed inert until some unrelated memory file happened to
    change. Tier is included because it is output-determining; REDUCER_VERSION
    covers reducer changes that no input field can express.
    """
    joined = "\n".join(
        f"{e.filename}:{e.content_hash}:{e.tier}"
        for e in sorted(entries, key=lambda e: e.filename)
    )
    return _hash(f"v{REDUCER_VERSION}\n{joined}")


def _ledger_line(e: MemoryEntry) -> str:
    """Terse, never-capped (R1). The filename is ALWAYS present (the R1
    completeness guarantee + what `verify()` checks). The recall key is emitted
    ONLY when it is a CUSTOM human title; when it equals the filename-derived
    default (underscores→spaces — ~2/3 of entries), the `:: recall=…` clause is
    OMITTED and the reader derives it on demand. Lossless w.r.t. the ledger's
    completeness+retrievability charter (the convention is stated in the ledger
    header), and saves ~11.5k chars across 200+ entries so the kernel stays
    loadable. The custom-title format is unchanged (the escape-valve)."""
    flag = "PARAMOUNT" if e.paramount else e.tier
    if e.recall_key == _recall_key("", e.filename):
        return f'- [{flag}] {e.filename}'
    return f'- [{flag}] {e.filename} :: recall=/recall "{e.recall_key}"'


def _place_body(e: MemoryEntry, used: int, cap: int) -> tuple[str, str | None]:
    """Pure body-placement decision → (disposition, line|None).

    disposition ∈ {inlined, summarised, deferred}. A PARAMOUNT/feedback entry
    whose body doesn't fit is *deferred* (counted) — never silently dropped,
    because the ledger above already guarantees its presence."""
    full = f"- **{e.title}** ({'PARAMOUNT' if e.paramount else e.tier}) — {e.hook or '(see ledger)'}"
    if used + len(full) + 1 <= cap:
        return "inlined", full
    if e.paramount or e.tier == "feedback":
        return "deferred", None
    if used + len(e.title) + 40 <= cap:
        return "summarised", f"- {e.title} — [deferred → /recall]"
    return "deferred", None


def _delta_line(prior_cap: int, nominal_cap: int) -> str:
    """#3548 — make the cap floor-cut falsifiable ON SIGHT. Emits the old→new
    cap and the realized % cut, computed LIVE from the two constants so a reader
    can check it against the kernel itself (not just the PR text). The cut is
    signed by convention: a CUT renders NEGATIVE (−X%, the floor shrank, matching
    the issue's own '−17.7%' framing) and a cap RAISE renders POSITIVE (+X% refill,
    the floor was given back) — that sign asymmetry is the on-sight falsifier."""
    change_pct = (nominal_cap - prior_cap) / prior_cap * 100  # cut ⇒ negative
    direction = "refill" if change_pct > 0 else "cut"
    return (
        f"**Delta:** cap {prior_cap:,} → {nominal_cap:,} chars "
        f"({change_pct:+.1f}% {direction}); the freed budget is banked as a floor, "
        f"not refilled with inlined bodies (#3548 falsifiable-on-sight)."
    )


def _build_header(
    digest: str, total: int, counts: dict[str, int],
    ledger_chars: int, nominal_cap: int, body_budget: int,
) -> str:
    if body_budget > 0:
        bound = (
            f"**Bound:** kernel target ≤ {nominal_cap:,} chars; ledger "
            f"({ledger_chars:,}, R1-exempt) + bodies (≤ {body_budget:,})."
        )
    else:
        bound = (
            f"**Bound:** the R1 completeness ledger ({ledger_chars:,} chars) "
            f"alone exceeds the {nominal_cap:,} target — R1 completeness takes "
            f"precedence; bodies fully deferred to KONO via the recall keys below "
            f"(honest gap, not a silent drop)."
        )
    return (
        f"# HOLISTIC.md — synthesised memory kernel\n"
        f"<!-- substrate-digest: {digest} -->\n\n"
        f"**Accounting:** {total} total · {counts['inlined']} inlined · "
        f"{counts['summarised']} summarised · {counts['deferred']} deferred-to-KONO · "
        f"{counts['paramount']} PARAMOUNT · {counts['feedback']} feedback.\n"
        f"{_delta_line(PRIOR_CAP_CHARS, nominal_cap)}\n"
        f"{bound}"
    )


def _assemble(entries: list[MemoryEntry], cap: int) -> KernelResult:
    """Coordinator: ledger first (never capped), then priority-ordered bodies
    placed via the pure `_place_body` decision. Counted, never silently
    dropped."""
    ordered = sorted(entries, key=lambda e: e.sort_key)
    digest = _substrate_digest(entries)
    ledger_entries = [e for e in ordered if e.paramount or e.tier == "feedback"]
    ledger_block = "\n".join(_ledger_line(e) for e in ledger_entries)

    placeholder = "%%HEADER%%"
    parts: list[str] = [
        placeholder, "",
        "## Completeness ledger (R1 — never elided; the cap compresses bodies, never this list)",
        "_Recall convention: a line with no `::` clause ⇒ key = filename, `_`→spaces (e.g. `feedback_x_y.md` → `/recall \"feedback x y\"`). A `:: recall=…` clause carries a custom title._",
        "", ledger_block, "",
        "## Synthesised model (priority-ordered; lower tiers defer to KONO)", "",
    ]
    # `cap` is the total-kernel target. The header + completeness ledger are
    # R1-exempt and always emitted in full; bodies get whatever remains
    # (council verdict: the cap compresses bodies; only the ledger itself
    # exceeding the cap may push the kernel over — honestly noted).
    body_budget = max(0, cap - len(ledger_block) - _HEADER_RESERVE)
    used = 200  # scaffold slack for the two section headings
    counts = {"inlined": 0, "summarised": 0, "deferred": 0}
    for e in ordered:
        disposition, line = _place_body(e, used, body_budget)
        counts[disposition] += 1
        if line is not None:
            parts.append(line)
            used += len(line) + 1

    counts["paramount"] = sum(1 for e in entries if e.paramount)
    counts["feedback"] = sum(1 for e in entries if e.tier == "feedback")
    header = _build_header(digest, len(entries), counts, len(ledger_block), cap, body_budget)
    text = "\n".join(parts).replace(placeholder, header).rstrip() + "\n"
    return KernelResult(
        text=text,
        total=len(entries),
        inlined=counts["inlined"],
        summarised=counts["summarised"],
        deferred=counts["deferred"],
        paramount_count=counts["paramount"],
        feedback_count=counts["feedback"],
        substrate_digest=digest,
        ledger_files=[e.filename for e in ledger_entries],
    )


def synthesise(memory_dir: Path, memory_index: Path, *, cap: int = CAP_CHARS) -> KernelResult:
    """Pure deterministic reduction of the memory substrate → bounded kernel.
    Same substrate ⇒ byte-identical text (R2 idempotency at this layer)."""
    return _assemble(_enumerate(memory_dir, memory_index), cap)


def verify(memory_dir: Path, memory_index: Path, kernel_text: str) -> list[str]:
    """R1 verification: substrate PARAMOUNT/feedback files absent from the
    kernel's ledger. Empty list ⇒ ledger is complete."""
    entries = _enumerate(memory_dir, memory_index)
    required = [e.filename for e in entries if e.paramount or e.tier == "feedback"]
    return [fn for fn in required if fn not in kernel_text]


_DIGEST_RE = re.compile(r"<!-- substrate-digest:\s*([0-9a-f]+)\s*-->")


def payload_digest(kernel_text: str) -> str:
    """R2 idempotency anchor. Two kernels are idempotent-equal iff this
    matches — independent of any volatile header (generation, timestamp) a
    future caller (W3/W4 wiring) stamps. Prefers the embedded
    substrate-digest; falls back to hashing from the ledger section onward
    so the contract holds even if the marker is absent."""
    m = _DIGEST_RE.search(kernel_text)
    if m:
        return m.group(1)
    idx = kernel_text.find("## Completeness ledger")
    return _hash(kernel_text[idx:] if idx != -1 else kernel_text)


def _body(kernel_text: str) -> str:
    """The kernel from its substrate-digest marker onward: everything a write
    must reproduce, without the chain-HEAD freshness stamp that sits above the
    marker. Comparing bodies is what lets a denylist change rewrite the kernel."""
    m = _DIGEST_RE.search(kernel_text)
    return kernel_text[m.start():] if m else kernel_text


def default_substrate() -> tuple[Path, Path]:
    """Resolve the real memory dir + index for this GRIP install.

    The harness encodes the project dir from the REAL (symlink-resolved) home
    cwd (lib/path_encoding.py), so a ~/.grip install — or a ~/.claude->~/.grip
    symlink — resolves to its own '-...--grip/memory'. The pre-#266 glob
    ('*--claude/memory') hardcoded the '.claude' home and, on a ~/.grip box,
    silently read another operator's '-...--claude' project instead. The slug
    then went through a hand-rolled copy of the encoding that left a Windows
    drive colon and backslashes in place, so pathlib discarded projects/ and
    read a stray '<parent>/-grip/memory' outside the install (GRIP#6821).
    Falls back to the home /memory only when no encoded project dir exists
    yet (fresh install). Path-portable: no home name is hardcoded."""
    exact = project_dir(_GRIP_HOME.resolve(), _GRIP_HOME) / "memory"
    mem = exact if exact.exists() else _GRIP_HOME / "memory"
    return mem, mem / "MEMORY.md"


_SIGNAL_PREFIX = "holistic-updated:"


def holistic_signal(digest: str) -> str:
    return f"{_SIGNAL_PREFIX}{digest}"


def parse_holistic_signal(text: str) -> str | None:
    """Extract the substrate digest from a mesh inbox line, else None."""
    i = text.find(_SIGNAL_PREFIX)
    if i == -1:
        return None
    return (text[i + len(_SIGNAL_PREFIX):].split() or [""])[0].strip() or None


def should_refresh(signal_digest: str | None, current_digest: str | None) -> bool:
    """A peer acts iff the broadcast digest differs from what it has loaded
    — ignore-own-echo + idempotent signal (same digest ⇒ no action storm).
    Push/pull ordering no longer matters: the signal is self-describing."""
    return bool(signal_digest) and signal_digest != current_digest


_BROADCAST_TIMEOUT_S = 2  # bounds the Stop-hook stall: synthesise + <=2s
                          # broadcast fits well inside the hook's 8s budget,
                          # so a hung mesh bus never meaningfully delays
                          # session end (council R3 — notification is
                          # best-effort and MUST NOT delay/break the stop).


def _default_broadcaster(message: str) -> None:
    """Best-effort digest-bearing mesh broadcast. Never raises and is
    latency-bounded — the kernel write is the critical path and is already
    durable before this is called; notification is best-effort (the mesh
    bus may be down or slow). A timed-out broadcast is self-healing: the
    digest is in the written kernel, so a peer that missed the signal
    reconciles on its next compare-and-skip regen (R3/R4 pull fallback).
    bin/mesh IS the mesh syscall shim (Rule 21 reuse)."""
    mesh = _GRIP_HOME / "bin" / "mesh"
    if not mesh.exists():
        return
    try:
        subprocess.run(
            [str(mesh), "broadcast", message],
            timeout=_BROADCAST_TIMEOUT_S, capture_output=True, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _scrub(text: str) -> str:
    """Replace every denylisted personal name in the kernel with an opaque token.

    rules/HOLISTIC.md is a tracked file that sessions regenerate in the shared
    tree, so whatever it holds is one commit away from the repository, and the
    memory records it folds in carry personal names. Raises when the redaction
    denylist cannot be loaded (missing, or still git-crypt ciphertext), so the
    caller writes nothing rather than an unscrubbed kernel. The path is read from
    the shield module at call time, so tests can supply a fixture list. No path is
    passed on purpose: that is what merges the operator's private overlay, whose
    entries are exactly the ones this kernel must never carry.
    """
    import redaction_shield as shield  # deferred: keep the launch path light
    return shield.Shield(shield.load_denylist()).redact(text).text


def regenerate(
    memory_dir: Path | None = None,
    memory_index: Path | None = None,
    *,
    out: Path | None = None,
    broadcaster=_default_broadcaster,
    cap: int = CAP_CHARS,
) -> dict:
    """Out-of-band: synthesise → compare-and-skip → atomic write → digest-
    bearing broadcast. NEVER called on the launch path (R4).

    R3 single-effective-writer: unchanged substrate ⇒ no write + no
    broadcast (idempotent no-op). Concurrent regenerations converge —
    every writer computes the SAME payload from the SAME substrate (R2),
    so last-write-wins via `os.replace` is safe and readers see
    old-or-new, never a torn file. `os.replace` (atomic rename) is the
    chosen mechanism over an advisory flock: simpler, no lock lifecycle,
    and sufficient because the content is deterministic.

    The write is skipped only when the scrubbed body is byte-identical to the
    one on disk. The substrate digest is taken before the scrub, so a name
    added to the denylist leaves it unchanged; skipping on the digest alone
    kept that name in the tracked file until some memory file changed.

    Returns ``changed`` and ``total``, ``digest`` (the substrate digest), and
    ``notified`` after a write. When the redaction shield cannot load, nothing
    is written: ``refused`` carries the reason and ``digest`` is None, so a
    caller can tell a refusal from an unchanged kernel.

    The broadcast carries the same substrate digest, so a rewrite caused only
    by a denylist change repeats the previous digest, and ``should_refresh``
    would treat it as an echo. Nothing consumes these signals today; they stay
    queue-only (skills/mesh/SKILL.md). A future consumer that must notice
    redaction changes should key on the written body, not on this digest."""
    if memory_dir is None or memory_index is None:
        memory_dir, memory_index = default_substrate()
    out = out or (_GRIP_HOME / "rules" / "HOLISTIC.md")
    result = synthesise(memory_dir, memory_index, cap=cap)
    # P0 write-side freshness stamp (design §1 commitment 3): tag the on-disk
    # HOLISTIC.md header with the current chain HEAD so a later phase can detect
    # staleness. Inert by construction — the stamp sits ABOVE the substrate-digest
    # marker, so ``payload_digest`` ignores it and the no-op write-skip guard below
    # is unchanged; nothing reads the stamp yet. Skipped when no chain HEAD exists
    # (a fresh install has nothing to stamp) → byte-identical legacy output.
    #
    # The scrub runs first and the stamp is added after it. The stamp is
    # metadata, not content, and a node id can look like a phone number to the
    # shield (precog-0821234567-… is a South African mobile), which turned the
    # stamp into "precog-[PHONE]-…" when the stamped text was scrubbed.
    try:
        scrubbed = _scrub(result.text)
    except Exception as exc:  # noqa: BLE001 — fail closed: an unscrubbed kernel is never written
        return {"changed": False, "refused": f"redaction shield unavailable: {exc}",
                "digest": None, "total": result.total}
    file_text = scrubbed
    try:
        from lib.context_chain import stamp_view_header  # deferred: avoid import cost/cycle
        from lib.context_head import read_head
        head_id = read_head()
        if head_id:
            file_text = stamp_view_header(scrubbed, head_id)
    except Exception:  # noqa: BLE001 — stamping is best-effort; never break the kernel write
        file_text = scrubbed
    new_digest = payload_digest(file_text)

    existing_text = None
    if out.exists():
        try:
            existing_text = out.read_text(encoding="utf-8")
        except OSError:
            existing_text = None
    if existing_text is not None and _body(existing_text) == _body(file_text):
        return {"changed": False, "digest": new_digest, "total": result.total}

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".md.tmp")
    tmp.write_text(file_text, encoding="utf-8")  # P0: stamped header, body unchanged
    tmp.replace(out)  # R3: atomic — readers see old-or-new, never torn
    # The write is now durable; notification is strictly best-effort and
    # MUST NOT propagate (council R3 — a broadcast failure never loses the
    # kernel write nor breaks the caller).
    notified = True
    try:
        broadcaster(holistic_signal(new_digest))
    except BaseException:  # noqa: BLE001 — INTENTIONAL, scoped, council-R3
        # Deliberately broader than `Exception`: this single best-effort
        # call runs inside the Stop hook, and the council R3 contract is
        # ABSOLUTE — a broadcast failure must NEVER break session end. The
        # kernel write is already durable (atomic os.replace above) and a
        # missed signal self-heals via the peer's next compare-and-skip
        # regen, so swallowing even SystemExit/KeyboardInterrupt here is
        # correct, not a smell. Scope is exactly one line; it does not
        # mask interrupts anywhere else.
        notified = False
    return {"changed": True, "digest": new_digest, "total": result.total, "notified": notified}


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    memory_dir, memory_index = default_substrate()
    out = _GRIP_HOME / "rules" / "HOLISTIC.md"

    if "--verify" in argv:
        try:
            kernel_text = out.read_text(encoding="utf-8")
        except OSError:
            print("[holistic] no kernel at rules/HOLISTIC.md — run synthesise first", file=sys.stderr)
            return 1
        missing = verify(memory_dir, memory_index, kernel_text)
        if missing:
            print(f"[holistic] VERIFY FAIL: {len(missing)} PARAMOUNT/feedback absent from ledger:", file=sys.stderr)
            for fn in missing[:20]:
                print(f"  - {fn}", file=sys.stderr)
            return 1
        print("[holistic] VERIFY OK: ledger complete (0 PARAMOUNT/feedback gaps)", file=sys.stderr)
        return 0

    r = regenerate(memory_dir, memory_index, out=out)
    if r.get("refused"):
        print(f"[holistic] refused: {r['refused']} -- rules/HOLISTIC.md was not written.", file=sys.stderr)
        return 1
    if r["changed"]:
        print(
            f"[holistic] kernel rebuilt: {r['total']} memories, digest "
            f"{r['digest']} — peers notified via mesh broadcast.",
            file=sys.stderr,
        )
    else:
        print(
            f"[holistic] no change (substrate digest {r['digest']} unchanged) "
            f"— skipped write + broadcast (idempotent).",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
