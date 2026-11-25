"""next_step_block — shared selector + renderer for the '## Next Step' block.

Single owner of the D2-correct instance selector and the canonical next-step
renderer. Both kono_preload and unified_memory_brief import from here so the
logic lives in exactly one place (DRY, council §3.1).

This module contains volatile, per-session content that the *pure*
holistic_synthesiser kernel deliberately excludes (R2 idempotency): the
next-step block is recomputed FRESH every warm-start / session-end so
"continue" reflects the canonical latest-instance state, not a frozen
verbatim carry-forward.  It is the single source replacing the divergent
unified_memory_brief copy (D2 bug #2910 twin).

DIP: all functions accept a project_path argument only — no global state.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path.home() / ".claude"

# C1 idempotent path guard — same pattern as kono_preload.
if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from path_encoding import encode_project_path
except ImportError:  # pragma: no cover — always present in a real install
    encode_project_path = None


def select_latest_instance(instances: list) -> Optional[dict]:
    """The ONE D2-correct instance selector — every index reader routes here.

    Returns the chronologically-latest entry by ``max(serialized_at)``, or
    None for an empty / non-list input. Pure (no I/O).

    Filters to dict entries BEFORE selecting, so the result is ALWAYS a dict or
    None — never a stray non-dict element. (AGORA #3425 r2 Speaker C: guarding
    only inside the ``max`` key still RETURNED a non-dict when the whole list
    was non-dict — all keys collapse to "" — and callers doing ``.get(...)`` on
    the result then crashed with AttributeError.)

    Why this is the single owner (DRY, council §3.1): concurrent or retried
    saves append OUT of wall-clock order, so positional-last (``instances[-1]``)
    is NOT the newest. The splash (``grip_generation``), the memory line, and
    this module all MUST select through this function so they can never again
    disagree on "which instance is latest" — the 430-vs-484 bug class that
    ``grip_generation`` was created to kill and that silently regressed when a
    second reader hand-rolled ``instances[-1]``.
    """
    if not isinstance(instances, list):
        return None
    dict_entries = [i for i in instances if isinstance(i, dict)]
    if not dict_entries:
        return None
    return max(dict_entries, key=lambda i: i.get("serialized_at", ""))


def read_instance_index(project_path: Path) -> Optional[dict]:
    """Read the chronologically-latest instance entry from the grip index.

    D2-correct: selects via ``select_latest_instance`` (max(serialized_at)),
    NOT instances[-1].  Concurrent or retried saves append out of wall-clock
    order; positional-last != newest.

    Returns the index *entry* dict (not the full instance file) or None on
    any miss (missing index, empty list, JSON error, OSError).
    """
    if encode_project_path is None:
        return None
    encoded = encode_project_path(project_path)
    index_path = CLAUDE_DIR / "projects" / encoded / "grip" / "index.json"
    if not index_path.exists():
        return None
    try:
        with open(index_path, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
        return select_latest_instance(data.get("instances", []))
    except (json.JSONDecodeError, OSError):
        return None


def read_latest_next_up(project_path: Path) -> Optional[dict]:
    """Read the latest instance's next_up dict (D2-correct max(serialized_at)).

    Reuses read_instance_index for the D2-correct selector, then reads the
    full instance file for the top-level next_up dict.  R4 graceful: any
    miss returns None, never raises.
    """
    instance = read_instance_index(project_path)
    if not instance:
        return None
    iid = instance.get("instance_id")
    if not iid or encode_project_path is None:
        return None
    inst_file = (
        CLAUDE_DIR / "projects" / encode_project_path(project_path)
        / "grip" / f"{iid}.json"
    )
    if not inst_file.exists():
        return None
    try:
        data = json.loads(inst_file.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None
    nu = data.get("next_up")
    return nu if isinstance(nu, dict) else None


def next_step_lines(next_up: Optional[dict]) -> list:
    """Render the canonical '## Next Step' block from a next_up dict.

    Empty/None input -> [] (section omitted — same graceful pattern as
    achievement/KONO blocks).  Caps: remaining[:5], blockers[:3].
    """
    if not isinstance(next_up, dict):
        return []
    remaining = [r for r in (next_up.get("remaining") or []) if r][:5]
    blockers = [b for b in (next_up.get("blockers") or []) if b][:3]
    if not remaining and not blockers:
        return []
    out = ["## Next Step", ""]
    out.extend(f"- {item}" for item in remaining)
    out.extend(f"- [blocked] {b}" for b in blockers)
    out.append("")
    return out


# ---------------------------------------------------------------------------
# §4.1 + §13.2  Unified real-time next_up synthesis (additive — no existing
# symbol is modified; reversible by deleting from here to end of file)
# ---------------------------------------------------------------------------

_SCOPE_ID_FILE = ".grip-project-id"
_NEXTUP_SIGNAL_PREFIX = "nextstep-updated:"
_NO_SECRET_MAX_CHARS = 4096
# Fields allowed in a cross-machine snapshot — no secrets, no paths outside project.
_SNAPSHOT_ALLOWED_KEYS = frozenset({"remaining", "blockers", "notes", "serialized_at", "scope"})
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|bearer|auth|cred|private[_-]?key)"
    r"[\s:=]{0,5}['\"]?[A-Za-z0-9+/=_\-]{20,}"
)


def _slug_id_file(project_path: Path) -> Optional[str]:
    """Read .grip-project-id; return a SANITISED slug or None.

    The slug MUST NOT contain '/' or ':' — they would break _enc_scope
    injectivity (two distinct keys → one snapshot dir → cross-scope
    collision, a §13.7-clause-2 firewall breach). Council said slug(...);
    sanitise to [a-z0-9._-], collapse runs, strip; empty ⇒ None (fall
    through to git:/path: — fail-safe, never widens).
    """
    candidate = Path(project_path) / _SCOPE_ID_FILE
    try:
        raw = candidate.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw.lower()).strip("-")
    return slug or None


def _normalise_git_remote(url: str) -> Optional[str]:
    """§13.2 normalise: url → 'host/owner/repo' or None (fall through to path:).

    Steps 1-6 verbatim from §13.2:
    1. strip whitespace; reject if empty or contains control/Unicode-C chars
    2. strip leading scheme
    3. canonicalise scp-form and URL-form; drop port; owner NEVER stripped
    4. strip trailing .git and /
    5. lowercase HOST only (owner/repo are case-sensitive)
    6. must match ^[a-z0-9.\\-]+/[^/\\s]+/[^/\\s]+$
    """
    url = url.strip()
    if not url:
        return None
    # Step 1 — reject ANY Unicode category C* (Cc/Cf/Cs/Co/Cn): control AND
    # format (zero-width/BOM) chars. Homoglyph / zero-width identity-forgery
    # firewall — §13.2 obligation (iv) (ASCII-only ctrl check missed Cf:
    # U+200B/U+FEFF pass the step-6 regex, forging a colliding scope).
    if any(unicodedata.category(c)[0] == "C" for c in url):
        return None
    # Step 2 — strip scheme
    url = re.sub(r"^(https?://|ssh://|git\+ssh://|git://)", "", url)
    # Step 3 — canonicalise
    # scp-form host:path — but NOT host:<port>/path (that is URL-form with
    # an explicit port, e.g. ssh://git@host:22/o/r): the (?!\d+/) negative
    # lookahead routes a port to the URL-form branch (U11 — one repo, every
    # URL spelling MUST unify to one scope; the port mis-parse split it).
    scp = re.match(r"^(?:[^@]+@)?([^:/]+):(?!\d+/)(.+)$", url)
    if scp:
        host, rest = scp.group(1), scp.group(2)
    else:
        # URL-form: [user@]host[:port]/owner/repo
        m = re.match(r"^(?:[^@]+@)?([^/:]+)(?::\d+)?/(.+)$", url)
        if not m:
            return None
        host, rest = m.group(1), m.group(2)
    # Step 4 — strip trailing .git and /
    rest = re.sub(r"\.git$", "", rest).rstrip("/")
    # Step 5 — lowercase HOST only
    host = host.lower()
    result = f"{host}/{rest}"
    # Step 6 — must match host/owner/repo structure
    if not re.match(r"^[a-z0-9.\-]+/[^/\s]+/[^/\s]+$", result):
        return None
    return result


def project_scope_key(project_path: Path) -> str:
    """§13.2 machine-invariant scope key.

    Precedence: id: > git: > path:
    Never widens on failure — always falls through to the narrower path: key.
    """
    p = Path(project_path)
    # Tier 1: explicit .grip-project-id override
    slug = _slug_id_file(p)
    if slug:
        return f"id:{slug}"
    # Tier 2: git remote origin → normalised host/owner/repo
    try:
        result = subprocess.run(
            ["git", "-C", str(p), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
            normalised = _normalise_git_remote(raw)
            if normalised:
                return f"git:{normalised}"
    except (OSError, subprocess.SubprocessError):
        pass
    # Tier 3: local-only path fallback (realpath to prevent symlink forks)
    if encode_project_path is None:
        return f"path:{str(os.path.realpath(p))}"
    return f"path:{encode_project_path(os.path.realpath(p))}"


def _enc_scope(scope: str) -> str:
    """Filesystem-safe, INJECTIVE directory token for a scope key (§13.4/§13.5).

    sha256(scope)[:32] — injective by collision-resistance (128-bit prefix;
    birthday-safe far beyond any realistic scope cardinality).

    The prior `replace('/','~').replace(':','~')` was NON-injective: '/' and
    ':' BOTH mapped to '~', so two distinct scopes differing only by '/'↔':'
    at a position collided into ONE snapshot directory → cross-scope bleed
    (§13.7 clause-2 firewall breach; HAL Council #2917 instinct — root-caused,
    not patched: no-guardrail-evasion). The delta-council §13.5 "bijective
    substitution" injectivity claim was therefore false; this corrects it.

    Reversibility (dir→scope) is required by NO caller (synthesis always
    computes its own scope, then reads its own dir; it never reverses a
    dirname — verified across read_all_instances_in_scope + nextup_mesh), so
    a one-way collision-resistant hash is the correct minimal firewall-by-
    construction primitive: the §13.5 partition proof reduces to "distinct
    scope ⇒ distinct sha256[:32] ⇒ disjoint directory".
    """
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:32]


# --- bounded instance read --------------------------------------------------
# The instance store grows without bound. Measured 2026-09-14 on one live
# scope: 13,069 files / 13,019,913,658 bytes (13 GB). This reader sits on the
# PreToolUse hook path (hooks/nextstep-refresh-hook.py, matcher ".*", declared
# timeout 5s, re-fired every 60s) and reading all of it cost 65.2s, so
# invocations overlapped and never drained — five alive at once, 35-90% CPU
# each, machine load 34.85. A harness timeout bounds the harness's WAIT, never
# the child PROCESS, so every tick leaked a live CPU-burning python.
#
# Usefulness here is strictly recency-ordered: the authoritative entry IS
# max(serialized_at). Reading newest-first and capping costs 1.6s, touches
# 1.59% of the bytes, and always contains the authoritative entry.
#
# GRIP_NEXTUP_MAX_INSTANCES=0 (or negative) restores the unbounded read.
_DEFAULT_MAX_INSTANCE_FILES = 200


def _max_instance_files() -> int:
    """Operator-tunable cap. NEVER raises — the calling hook's documented
    contract is fail-open ABSOLUTE, so a malformed env var must degrade to
    the default rather than take the read down."""
    try:
        return int(os.environ.get("GRIP_NEXTUP_MAX_INSTANCES",
                                  _DEFAULT_MAX_INSTANCE_FILES))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_INSTANCE_FILES


def _safe_mtime(entry) -> float:
    """mtime of a scandir entry, 0.0 if it vanished mid-scan (R4-graceful)."""
    try:
        return entry.stat().st_mtime
    except OSError:
        return 0.0


def _newest_json_files(json_dir: Path) -> list:
    """*.json newest-first, capped at _max_instance_files().

    Used for BOTH substrate directories — the per-project instance store and
    the cross-machine snapshot store. Neither has a reaper, so both grow
    without bound and both need the same cap.

    index.json is excluded BEFORE the cap is applied — it is rewritten on
    every save, so it is almost always the newest file in the directory and
    would otherwise consume a slot that belongs to a real entry.
    """
    try:
        entries = [e for e in os.scandir(json_dir)
                   if e.name.endswith(".json")
                   and e.name != "index.json"
                   and e.is_file()]
    except OSError:
        return []
    entries.sort(key=_safe_mtime, reverse=True)
    cap = _max_instance_files()
    if cap > 0:
        entries = entries[:cap]
    return [Path(e.path) for e in entries]


def _read_instance_entries(grip_dir: Path) -> list:
    """Local instance entries, newest-first + capped. [] if the dir is absent.

    Mirror of _read_snapshot_entries — the two substrate sources are read the
    same way so neither is second-class.
    """
    if not grip_dir.exists():
        return []
    out = []
    for p in _newest_json_files(grip_dir):
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        nu = data.get("next_up")
        out.append({
            "instance_id": data.get("instance_id", p.stem),
            "serialized_at": data.get("serialized_at", ""),
            "next_up": nu if isinstance(nu, dict) else None,
        })
    return out


def read_all_instances_in_scope(project_path: Path) -> list:
    """§4.1 scope-keyed substrate reader — TWO sources, merged.

    Local instances (projects/<scope>/grip/*.json, excluding index.json) plus
    cross-machine snapshots (state/nextup/<scope>/*.json). Both are read
    NEWEST-FIRST and capped — see the bounded-read note above for the
    measurement that forced the cap. Returns list of dicts:
        instance_id, serialized_at, next_up (dict or None).
    R4-graceful: any unreadable entry is skipped.

    NOTE: there is deliberately no early return on a missing grip_dir. The two
    sources live in DIFFERENT directories, so a machine that has never
    serialised an instance locally — a fresh mesh join — still has snapshots
    to merge. The previous `if not grip_dir.exists(): return []` discarded
    them silently. Found by the forward-anchor test written for xAI Grok 4.3's
    falsifier on GRIP#5677; the defect predates this PR.
    """
    if encode_project_path is None:
        return []
    encoded = encode_project_path(project_path)
    grip_dir = CLAUDE_DIR / "projects" / encoded / "grip"
    results = _read_instance_entries(grip_dir)
    results.extend(_read_snapshot_entries(project_path))
    return results


def _read_snapshot_entries(project_path: Path) -> list:
    """Cross-machine snapshot entries for this scope, newest-first + capped.

    Extracted so each function does ONE thing (the KISS gate flagged the
    merged version at 54 lines). Bounded for the same reason as the instance
    read — this directory has no reaper either, so an unbounded glob would
    reintroduce the same defect on another path. LATENT today (state/nextup/
    is absent on this machine), which is when it is cheapest to close. Each
    source caps independently, so the caller returns at most
    2 * _max_instance_files() entries.
    """
    scope = project_scope_key(project_path)
    snap_dir = CLAUDE_DIR / "state" / "nextup" / _enc_scope(scope)
    if not snap_dir.exists():
        return []
    out = []
    for snap in _newest_json_files(snap_dir):
        try:
            data = json.loads(snap.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        nu = data.get("next_up") or {
            k: data[k] for k in ("remaining", "blockers", "notes") if k in data
        }
        out.append({
            "instance_id": snap.stem,
            "serialized_at": data.get("serialized_at", ""),
            "next_up": nu if isinstance(nu, dict) else None,
        })
    return out


def _dedupe_preserve_order(items: list) -> list:
    """Deduplicate while preserving first-seen order."""
    seen: set = set()
    out = []
    for item in items:
        key = item.strip() if isinstance(item, str) else item
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def synthesise_unified_next_up(project_path: Path) -> dict:
    """§5 pure synthesis: N in-scope instances → one unified next_up dict.

    Same scope substrate ⇒ identical dict (R2-analogue — no wall-clock,
    no machine id in output).
    """
    entries = [e for e in read_all_instances_in_scope(project_path) if e["next_up"]]
    if not entries:
        return {}
    authoritative = max(entries, key=lambda e: e["serialized_at"])
    # remaining: newest-wins union (authoritative order first, extras appended)
    sorted_desc = sorted(entries, key=lambda e: e["serialized_at"], reverse=True)
    all_remaining: list = []
    for e in sorted_desc:
        all_remaining.extend(e["next_up"].get("remaining") or [])
    auth_remaining = list(authoritative["next_up"].get("remaining") or [])
    # _dedupe_preserve_order keeps the FIRST occurrence, and auth_remaining
    # leads the union — so the `if r not in auth_remaining` filter this
    # replaced was redundant: anything also present in auth has already been
    # seen by the time the union reaches it. Identical output, O(n) rather
    # than O(n*m) (that filter is a list membership test inside a
    # comprehension, and `all_remaining` grows with the instance count).
    remaining = _dedupe_preserve_order(auth_remaining + all_remaining)
    # blockers: FULL union (safety-conservative — a blocker from any session stands)
    all_blockers: list = []
    for e in entries:
        all_blockers.extend(e["next_up"].get("blockers") or [])
    blockers = _dedupe_preserve_order(all_blockers)
    # notes: authoritative only (free-text merge is lossy — KISS)
    notes = authoritative["next_up"].get("notes") or ""
    return {"remaining": remaining, "blockers": blockers, "notes": notes}


def unified_next_step_lines(project_path: Path) -> list:
    """§4.1 DRY: synthesise → reuse existing next_step_lines renderer."""
    return next_step_lines(synthesise_unified_next_up(project_path))


def nextstep_digest(unified: dict) -> str:
    """SHA256[:16] of canonical-sorted unified dict — idempotency anchor."""
    canonical = json.dumps(unified, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def nextstep_signal(scope: str, digest: str) -> str:
    """§4.1 scope-tagged mesh signal — distinct prefix from holistic-updated:."""
    return f"{_NEXTUP_SIGNAL_PREFIX}{scope}:{digest}"


def parse_nextstep_signal(text: str) -> Optional[tuple]:
    """Extract (scope, digest) from a mesh inbox line, else None."""
    i = text.find(_NEXTUP_SIGNAL_PREFIX)
    if i == -1:
        return None
    rest = text[i + len(_NEXTUP_SIGNAL_PREFIX):]
    # format: scope:digest — scope may contain ':' (e.g. git:host/o/r)
    # digest is the LAST ':'-delimited token (16 hex chars)
    parts = rest.strip().split(":")
    if len(parts) < 2:
        return None
    digest = parts[-1].split()[0].strip()
    scope = ":".join(parts[:-1])
    if not scope or not digest:
        return None
    return (scope, digest)


def should_refresh_nextstep(
    signal_scope: str, signal_digest: str,
    my_scope: str, my_digest: Optional[str],
) -> bool:
    """§4.1 + §13.5 scope-tag checked FIRST (contamination firewall).

    Returns True only when: same scope AND signal digest differs from current.
    Cross-scope is discarded before digest compare — by construction.
    """
    if signal_scope != my_scope:
        return False          # §13.5 step 3: scope-tag mismatch → discard
    return bool(signal_digest) and signal_digest != my_digest


# ---------------------------------------------------------------------------
# Mid-session self-staleness (additive — closes the deferred-YAGNI gap).
#
# should_refresh_nextstep keys on a PEER signal vs my digest, so it never
# fires for a SINGLE long-running session whose OWN next_up evolves mid-flight
# (the hook publishes my fresh digest, then consume_pending sees my own
# just-published signal where signal_digest == my_digest → no refresh).  The
# boot-time '## Next Step' written once by kono_preload therefore goes stale
# across a long session (e.g. an overnight RSI loop) with no re-surface.
#
# self_nextstep_stale is the pure self-case predicate: a running session has
# surfaced-to-itself digest S (None at boot); when the freshly-synthesised
# own digest differs from S, the inherited block is stale and must be
# re-emitted.  Same-scope BY CONSTRUCTION (no scope arg — the caller computes
# exactly one own scope), so the cross-session contamination firewall is
# untouched: this path can only ever re-surface a session's OWN next_up.
# ---------------------------------------------------------------------------


def self_nextstep_stale(current_digest: Optional[str], surfaced_digest: Optional[str]) -> bool:
    """True iff this session's own next_up changed since it last self-surfaced.

    Pure (no I/O): caller supplies the freshly-synthesised own digest and the
    last digest it surfaced to ITSELF (None ⇒ never surfaced this session —
    the warm-start block is the boot floor, so an empty/falsy current digest
    is NOT stale: nothing fresh to show).  Returns True only on a real change.
    """
    if not current_digest:
        return False              # nothing to surface → boot floor stands
    return current_digest != surfaced_digest
