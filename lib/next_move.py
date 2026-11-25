"""next_move — rank what to do next, and derive how, before asking anyone.

The problem this solves is a flood, not a shortage. Measured on 2026-09-01 this
machine carried ~226 live candidates (98 open issues, ~125 hypotheses past their
verification deadline, 3 open PRs). An unranked menu of that is not a question,
it is an abdication; and a menu of three drawn from 226 that does not SAY it was
drawn from 226 is worse, because it reads as complete.

This module is the deterministic floor only. It gathers, diversifies and
tie-breaks; it attaches a derived execution manner; and it reports its own
search scope so the caller can never present a slate as exhaustive. The
judgement — which of three genuinely matters most to the operator right now —
belongs to the model above it (llm-augmentation-pattern: deterministic floor,
LLM on top, never the reverse).

WHAT THIS DOES NOT DO. It does not ask. `AskUserQuestion` is undelegatable
(rules/dispatch-economics.md Gate 1) and lives in the skill, in the main
session. It does not rank by "importance" with invented weights; every signal
here was measured to discriminate before it was used (see diversify).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# Operator-only markers: work no agent at any pool can do, because it needs
# credentials, money, or the operator's signature.
#
# These are ACTION PHRASES matched on word boundaries, not bare nouns matched
# as substrings. The first version used substrings including "account", "bank",
# "legal" and "secret", and misclassified 7 of 8 ordinary titles: an
# `account_manager.py` refactor, the accounting module, an accountability
# table, a databank loader, the legal-ops skill, a payment-webhook FIXTURE and
# a secretstore lint rule were all marked undelegatable and would never have
# been dispatched. Raised by the AGORA council on PR #5226 and measured before
# fixing. lib/hitl_quality_gates.py already learned this lesson -- it matches
# \bOther\b precisely so "another" and "mother" cannot trip it.
#
# The bar for adding a phrase: it must name something the OPERATOR does, and
# it must not appear inside a plausible identifier or file name.
_OPERATOR_ONLY_PATTERNS = (
    # Up to three words may sit between the verb and "account": "create a new
    # GitHub account" was missed when only one was allowed.
    r"\b(create|register|open|set up) (a |an |the )?(\w+ ){0,3}account\b",
    r"\bsign (up|in) (for|to)\b",
    # "sign the CONTRACT", never "sign the commit" -- signing a commit is
    # automated and perfectly delegable.
    r"\bsign the (contract|agreement|nda|document|lease|form|papers)\b",
    r"\benter (the |your |a )?(password|passphrase|api key|token|credential)",
    r"\byour (password|credentials|api key|card)\b",
    r"\bpay (for|the)\b",
    r"\bset up 2fa\b",
    r"\benable 2fa\b",
    r"\bbuy(?!-)\b",          # "buy the domain", never "buy-in from the team"
    r"\bpurchase\b",
    r"\bapprove the (invoice|payment|contract)\b",
    r"\bverify your\b",
)
_OPERATOR_ONLY_RE = re.compile("|".join(_OPERATOR_ONLY_PATTERNS), re.IGNORECASE)

# Manner vocabulary. Each entry is (label, pool, note). The pool is stated
# because Pool A IS the weekly /usage percentage — a manner recommendation that
# hides which pool it spends is hiding the only number that matters.
MANNERS: dict[str, tuple[str, str]] = {
    "inline":     ("A", "main thread, depth 0 — the default"),
    "background": ("A", "Agent(run_in_background) — hides latency, not cost"),
    "fleet":      ("A", "Workflow tool — always Pool A, no pool switch"),
    "pool-b":     ("B", "HAL grip_infer — groq/gemini/deepseek, ~$0"),
    "pool-c":     ("C", "bin/grip-codex — ChatGPT subscription"),
    "pool-d":     ("D", "Kimi Code subscription — bin/grip-kimi (unarmed until keychain grip-kimi-coding exists)"),
    "council":    ("B", "grip-sovereign-council — synchronous, exogenous judge"),
    "overnight":  ("B", "grip-overnight — the only detached mechanism"),
    "fresh":      ("-", "gg++ — BLOCKS a terminal; hand over the command"),
    "you-only":   ("-", "needs the operator personally; never dispatched"),
}


@dataclass
class Candidate:
    """One ranked next move, with everything needed to choose it honestly."""

    id: str
    title: str
    source: str
    score: float
    origin: str = "global"          # "session" | "global"
    evidence: str = ""
    manner: str = "inline"
    manner_reason: str = ""
    delegable: bool = True
    falsifier: str = ""
    overdue_days: float = 0.0

    @property
    def pool(self) -> str:
        return MANNERS.get(self.manner, ("?", ""))[0]


@dataclass
class Slate:
    """A ranked slate PLUS the scope it was drawn from.

    `considered` and `sources` exist so the renderer can always state
    "N of M candidates". A Slate that cannot say what it excluded is the
    failure mode this whole module was built to avoid.
    """

    candidates: list[Candidate] = field(default_factory=list)
    considered: int = 0
    sources: dict[str, int] = field(default_factory=dict)
    modes: dict[str, Any] = field(default_factory=dict)
    degraded: list[str] = field(default_factory=list)

    @property
    def interactive(self) -> bool:
        """True when the operator should be ASKED rather than proceeded past."""
        return not (self.modes.get("afk") or self.modes.get("auto"))

    def scope_line(self) -> str:
        shown = len(self.candidates)
        hidden = max(0, self.considered - shown)
        if not self.considered:
            return "no candidates found"
        parts = ", ".join(f"{n} {s}" for s, n in sorted(
            self.sources.items(), key=lambda kv: -kv[1]))
        return f"{shown} of {self.considered} candidates — {hidden} not shown ({parts})"


# ---------------------------------------------------------------------------
# Mode detection
#
# Three naive implementations of this are silently WRONG, each confirmed live:
#   session_mode.is_active('auto')  -> always False. auto/RSI does not use
#       session_mode at all; session_mode.py RESERVES the name. It arms the
#       GLOBAL yolo marker through a different module.
#   grip-afk status | grep ACTIVE   -> false positive. "INACTIVE" contains
#       "ACTIVE" (measured: count=1 on an inactive session).
#   marker file exists?             -> false positive. 25 stale afk-*-mode.json
#       files are on disk; only status() honours the TTL and reaps them.
# ---------------------------------------------------------------------------

def afk_marker_name(session_id: Optional[str] = None) -> str:
    """Derive the session-scoped afk marker name.

    The env var is CLAUDE_CODE_SESSION_ID, NOT CLAUDE_SESSION_ID — the latter is
    unset in the Bash environment, so keying on it silently falls back to the
    global name and scopes nothing.
    """
    raw = session_id
    if raw is None:
        raw = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or ""
    sid = "".join(c for c in raw.lower() if c.isalnum())[:16]
    return f"afk-{sid}" if sid else "afk"


def detect_modes(session_id: Optional[str] = None) -> dict[str, Any]:
    """Report whether AFK or auto/RSI is engaged, via BOTH required APIs.

    Fail-closed toward ASKING: if a probe raises we report the mode as inactive
    and name the failure in `degraded`. Asking an operator who is away costs a
    delay; silently proceeding past one who is present costs an unwanted action.
    """
    out: dict[str, Any] = {"afk": False, "auto": False, "activator": "", "degraded": []}

    try:
        import session_mode as sm
        out["afk"] = bool(sm.is_active(afk_marker_name(session_id)))
    except Exception as exc:                                    # pragma: no cover
        out["degraded"].append(f"afk probe failed: {type(exc).__name__}")

    try:
        from gates.yolo_mode import is_yolo_active, YOLO_MARKER_PATH
        active, bypass_set = is_yolo_active()
        out["bypass_set"] = bypass_set or ""
        reason = ""
        if active:
            try:
                import json as _json
                reason = str(_json.loads(YOLO_MARKER_PATH.read_text()).get("reason") or "")
            except Exception:
                reason = ""
        out["activator"] = reason[:60]
        # THE YOLO MARKER IS GLOBAL. Measured 2026-09-02: it was armed by ANOTHER
        # session's /afk (a different pid, reason "afk autonomous continuation:
        # recover WhatsApp fleet ...") while the operator was typing to THIS one,
        # and the first shipped version read it as "this session is autonomous"
        # and silently took rank 1 without asking. An afk-originated marker
        # belongs to whichever session's SESSION-SCOPED afk marker is active;
        # if ours is not, it is someone else's and must not mute us. Any other
        # activator (/auto, manual grip-yolo) cannot be scoped from here and is
        # honoured as written -- a documented limitation, not a hidden one.
        afk_originated = reason.lower().startswith("afk autonomous continuation")
        if active and afk_originated and not out["afk"]:
            out["auto"] = False
            out["degraded"].append("global yolo marker is another session's /afk; not muting")
        else:
            out["auto"] = bool(active)
    except Exception as exc:                                    # pragma: no cover
        out["degraded"].append(f"auto probe failed: {type(exc).__name__}")

    return out


# ---------------------------------------------------------------------------
# Candidate gathering
# ---------------------------------------------------------------------------

def _run(args: Sequence[str], cwd: Optional[str] = None, timeout: int = 10) -> str:
    try:
        p = subprocess.run(list(args), cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
        return p.stdout.strip()
    except Exception:
        return ""


def session_candidates(cwd: Optional[str] = None) -> list[Candidate]:
    """Loose ends belonging to THIS working tree, from git state only.

    Deterministic and cheap. These are the continuation candidates the operator
    chose to see first — finishing what is open beats context-switching into a
    98-item backlog.
    """
    out: list[Candidate] = []

    dirty = _run(["git", "status", "--porcelain"], cwd=cwd)
    if dirty:
        n = len([ln for ln in dirty.splitlines() if ln.strip()])
        out.append(Candidate(
            id="session:dirty", title=f"Commit or discard {n} uncommitted change(s)",
            source="session-git", score=72.0, origin="session",
            evidence=f"git status --porcelain -> {n} entries",
            falsifier="falsified if `git status --porcelain` returns empty",
        ))

    # HEAD, not --branches. `--branches --not --remotes` spans EVERY local branch:
    # measured 987 on this machine against 557 stale branches, where the real
    # answer for the line of work in hand was 2. A candidate inflated by three
    # orders of magnitude is not a candidate, it is noise that outranks the truth.
    unpushed = _run(["git", "log", "HEAD", "--not", "--remotes", "--oneline"], cwd=cwd)
    # After a squash-merge the branch's commit SHAs are on no remote forever while
    # their CONTENT is on main -- so "N commits on no remote" fired as rank 1 on
    # every post-merge run (measured: 5 phantom commits, tree identical to
    # origin/main). Only count it if the tree actually differs from upstream.
    tree_differs = True
    try:
        rc = subprocess.run(["git", "diff", "--quiet", "origin/main", "HEAD"],
                            cwd=cwd, capture_output=True, timeout=10).returncode
        tree_differs = rc != 0
    except Exception:
        tree_differs = True          # cannot tell -> keep the candidate
    if unpushed and tree_differs:
        n = len(unpushed.splitlines())
        out.append(Candidate(
            id="session:unpushed", title=f"Push {n} commit(s) that exist on no remote",
            source="session-git", score=88.0, origin="session",
            evidence=f"git log HEAD --not --remotes -> {n} commits",
            falsifier="falsified if `git log HEAD --not --remotes` returns empty",
        ))

    return out


def _overdue_days(item: Any) -> float:
    dl = getattr(item, "deadline", None)
    if not dl:
        return 0.0
    try:
        return max(0.0, (datetime.now(timezone.utc) - dl).total_seconds() / 86400.0)
    except Exception:
        return 0.0


def gather(k: int = 40,
           reader: Optional[Callable[..., Iterable[Any]]] = None,
           cwd: Optional[str] = None) -> tuple[list[Candidate], list[str]]:
    """Session-local candidates first, then the global backlog.

    `reader` is injected rather than imported at the call site because
    holistic_backlog_consumer already exposes exactly this seam
    (consume(..., reader=read_top_k)); matching it keeps one pattern, and makes
    this function testable without touching GitHub or the hypothesis registry.
    """
    degraded: list[str] = []
    out: list[Candidate] = list(session_candidates(cwd=cwd))

    if reader is None:
        try:
            from holistic_backlog import read_top_k as reader   # type: ignore
        except Exception as exc:
            degraded.append(f"global backlog unavailable: {type(exc).__name__}")
            return out, degraded

    try:
        items = list(reader(k=k))
    except Exception as exc:
        degraded.append(f"global backlog read failed: {type(exc).__name__}")
        return out, degraded

    for it in items:
        out.append(Candidate(
            id=str(getattr(it, "id", "")) or f"global:{len(out)}",
            title=str(getattr(it, "title", "")),
            source=str(getattr(it, "source", "unknown")),
            score=float(getattr(it, "score", 0.0)),
            origin="global",
            evidence=f"priority {getattr(it, 'priority', '?')}, effort {getattr(it, 'est_effort', '?')}",
            overdue_days=_overdue_days(it),
        ))
    return out, degraded


# ---------------------------------------------------------------------------
# Diversification — the fix for a measured defect
#
# read_top_k(40) returns 39 hypotheses and 1 github item, and the first twelve
# hold exactly TWO distinct scores (every hypothesis scores 70/3 = 46.67). Past
# position 1 a plain score sort is arbitrary list order wearing a number.
#
# Round-robin by source fixes the flood. Ties break on days-overdue, which is
# the one signal MEASURED to be legitimate-but-weak: across 136 pending
# hypotheses the overdue band is min -77 / median 90 / max 109 days with none
# past 180, so it separates almost nothing and belongs exactly here — as a
# tie-break, never as a primary key. No weight is invented anywhere.
# ---------------------------------------------------------------------------

def diversify(items: Sequence[Candidate],
              per_source: int = 1,
              slate_size: int = 3) -> list[Candidate]:
    """Round-robin the best of each source before taking any source's second."""
    if not items:
        return []

    buckets: dict[str, list[Candidate]] = {}
    for c in items:
        buckets.setdefault(c.source, []).append(c)

    for bucket in buckets.values():
        # Session-local wins ties with global; then score; then overdue days.
        bucket.sort(key=lambda c: (c.origin != "session", -c.score, -c.overdue_days))

    order = sorted(buckets, key=lambda s: (
        buckets[s][0].origin != "session", -buckets[s][0].score))

    picked: list[Candidate] = []
    for rnd in range(per_source):
        for src in order:
            if len(picked) >= slate_size:
                return picked
            if rnd < len(buckets[src]):
                picked.append(buckets[src][rnd])
    return picked[:slate_size]


# ---------------------------------------------------------------------------
# Manner derivation
# ---------------------------------------------------------------------------

# The question is ACTOR-framed, and that framing is the whole trick. Asked
# topic-framed -- "does this involve credentials or payment?" -- a model marked
# 2 of 8 ordinary titles operator-only, because an accounting module and a
# GPG-signed commit are topically about money and signing. Asked who must ACT,
# the same model got 0 of 7 wrong. Measured 2026-09-01.
_DELEGABLE_Q = (
    "Could a fully automated coding agent complete this task alone? It has full "
    "repository and CLI access, but cannot create accounts on websites, spend "
    "money, or sign legal agreements. Writing code, tests or docs ABOUT billing, "
    "accounts, security or legal topics IS something it can do, and so is pushing, "
    "committing, or running any CLI whose credentials are ALREADY configured -- "
    "that is not a human entering credentials. Answer no only if a HUMAN must "
    "personally act outside the repository."
)


def is_delegable(title: str) -> bool:
    """False when only the operator can do it — credentials, money, signature.

    Reads the title with a cheap model rather than matching words in it. The
    regex below is no longer the answer; it is the DECLARED DEFAULT for when
    inference is unavailable, so behaviour offline is exactly what it was
    before and nothing fails silently.

    Why the change: the regex reached 0 false positives on a ten-title corpus
    only after three rounds of tuning, and each round bought one corpus. The
    substring version it replaced misclassified 7 of 8 ordinary titles. A model
    reading the sentence sees that `account_manager.py` is a filename.

    Still deliberately CONSERVATIVE about saying no: a false "you only" removes
    the candidate from every pool and it never gets done, while a false
    "delegable" is caught by the agent that tries and cannot.
    """
    floor = not _OPERATOR_ONLY_RE.search(title or "")
    if floor:
        # MONOTONE TOWARD THE SAFE VERDICT, and safe here is DELEGABLE.
        # The first shipped version let the model overrule a delegable floor,
        # and on its first live day it marked "Push 5 commit(s)..." and "Verify
        # hypothesis H200" as YOU ONLY -- ordinary work, silently removed from
        # every pool. A false YOU-ONLY never self-corrects; a false delegable is
        # caught by the agent that tries and cannot. So the model is consulted
        # ONLY to rescue a regex hit (the original 7-of-8 defect was regex false
        # positives), and may only ever move NOT-delegable -> delegable. This is
        # rules/llm-augmentation-pattern.md's invariant with the sign set by
        # the harm direction, not by which verdict sounds more cautious.
        return True
    try:
        from infer_classify import classify
        return classify(title, _DELEGABLE_Q, default="no") == "yes"
    except Exception:
        return False


# Pool D — Moonshot's Kimi Code subscription. A sealed non-Anthropic sovereign
# council (seal e49c00c214c500be, IDR leaf precog-1788805766-c58539b1; verify
# with `bin/grip-sovereign-council --verify e49c00c214c500be`) ruled it a pool
# of its own, armed ONLY when this keychain entry exists. GRIP holds no such
# credential today, so the manner below is unreachable until the operator adds
# one: a MANNERS row nothing derives is dead code, and a derivation that fires
# without a credential is a false promise. Both are forbidden, hence the gate.
POOL_D_KEYCHAIN_SERVICE = "grip-kimi-coding"

# Titles that name the Kimi Code product itself. Word-boundaried on purpose:
# substring matching is a recorded defect class in this module (see
# _OPERATOR_ONLY_PATTERNS). The bare model ids (`k3`) are deliberately absent —
# `\bk3\b` would also match a spreadsheet tab or a cluster name.
_KIMI_CODE_RE = re.compile(
    r"\bkimi[ -]?cod(?:e|ing)\b|\bkimi-for-coding(?:-highspeed)?\b|api\.kimi\.com/coding",
    re.IGNORECASE,
)


def pool_d_armed(**seams: Any) -> bool:
    """True only when a Kimi Code credential is resolvable. Never its value.

    Delegates to lib/provider_secret.resolve_provider_key, the one shim every
    provider secret goes through: on macOS that is
    `security find-generic-password -s grip-kimi-coding -w`; on any OS the env
    var KIMI_CODING_API_KEY answers first (Linux/CI has no keychain and the
    /proc scan stays opt-in). The resolved key is compared to None and dropped
    — it is not returned, logged or printed. Any error reads as unarmed,
    because the harm direction is asymmetric: a false "armed" recommends a
    pool that cannot be billed, while a false "unarmed" costs one manner label.
    *seams* are the resolver's injectable seams (env, platform,
    keychain_reader), so tests never touch a real keychain.
    """
    try:
        from provider_secret import resolve_provider_key
        return resolve_provider_key(POOL_D_KEYCHAIN_SERVICE, **seams) is not None
    except Exception:
        return False


def derive_manner(c: Candidate) -> tuple[str, str]:
    """Recommend HOW to execute, with the reason stated.

    The *pattern* half defers to lib/prompt_routing.assess(), which is already
    PARAMOUNT and wired; this only maps its verdict onto the pool/harness/council
    vocabulary that routing does not model. Never raises — routing is fail-safe
    by contract and this must be too.
    """
    if not c.delegable:
        return "you-only", "needs credentials, payment, or your own judgement"

    # Regex first, credential check second: the keychain is a subprocess and
    # must not run for every candidate in a forty-item slate. _KIMI_CODE_RE is
    # word-boundaried on purpose (substring matching is a recorded defect class
    # in this file, and a bare "kimi" also names the METERED Pool B models).
    if _KIMI_CODE_RE.search(c.title or "") and pool_d_armed():
        return "pool-d", "names Kimi Code — spend its own subscription window (Pool D), not Pool A"

    pattern = ""
    try:
        from prompt_routing import assess
        a = assess(c.title)
        pattern = "" if getattr(a, "exempt", False) else (a.recommended_pattern or "")
    except Exception as exc:
        # Name the failure rather than silently degrading to the default: a
        # manner chosen because routing was unavailable should not look
        # identical to one routing actually recommended.
        return "inline", f"routing unavailable ({type(exc).__name__}) — defaulting to depth 0"

    if pattern == "FullMesh":
        return "council", "high complexity — wants an exogenous judge, Pool B, $0"
    if pattern == "Sprint":
        return "fleet", "multi-slice work — converge-fleet (Pool A, weekly-limited)"
    if c.source == "hypothesis":
        return "pool-b", "read-and-verify, no judgement call — offload at ~$0"
    if pattern == "Focused":
        return "background", "self-contained; hides latency (still Pool A cost)"
    return "inline", "small and known — depth 0, no dispatch"


def falsifier_for(c: Candidate) -> str:
    """A predicate-shaped falsifier, because decision_record REJECTS vibes.

    lib/decision_record.py refuses any delegated Decision whose
    falsification_criteria is not predicate-shaped, so a candidate carrying
    "seems important" cannot be recorded at all. Each branch below names a
    command or artefact a third party could check.
    """
    if c.falsifier:
        return c.falsifier
    if c.source == "hypothesis":
        return f"falsified if the hypothesis registry shows {c.id} already resolved"
    if c.source == "github":
        return f"falsified if `gh issue view` shows {c.id} closed"
    return f"falsified if re-reading the {c.source} source no longer lists this item"


# ---------------------------------------------------------------------------

def slate(k: int = 3,
          reader: Optional[Callable[..., Iterable[Any]]] = None,
          cwd: Optional[str] = None,
          session_id: Optional[str] = None,
          pool: int = 40) -> Slate:
    """Gather -> diversify -> attach manner. Returns the slate AND its scope."""
    modes = detect_modes(session_id)
    items, degraded = gather(k=pool, reader=reader, cwd=cwd)
    degraded = list(degraded) + list(modes.get("degraded", []))

    sources: dict[str, int] = {}
    for c in items:
        sources[c.source] = sources.get(c.source, 0) + 1

    picked = diversify(items, per_source=max(1, k), slate_size=k)
    for c in picked:
        c.delegable = is_delegable(c.title)
        c.manner, c.manner_reason = derive_manner(c)
        c.falsifier = falsifier_for(c)

    return Slate(candidates=picked, considered=len(items), sources=sources,
                 modes=modes, degraded=degraded)


def build_questions(s: Slate) -> list[dict]:
    """Build the AskUserQuestion payload: action and manner, ONE batched call.

    Two questions, not two calls — the live PreToolUse deny hook
    (hooks/hitl-quality-hook.py) permits up to 4 questions and up to 4 options
    each, with headers capped at 12 characters and question text that must end
    in "?". Everything here is shaped to pass that gate rather than to discover
    it at runtime; the caller should still run collect_violations() first,
    because a denied call costs the operator a turn.

    The ceiling is also WHY the slate is three. A ranked list longer than four
    cannot be a single AskUserQuestion at all.
    """
    if not s.candidates:
        return []

    opts = []
    for c in s.candidates[:3]:
        tag = "YOU ONLY" if not c.delegable else c.manner
        opts.append({
            "label": c.title[:60],
            "description": f"[{c.origin}/{c.source}] via {tag} — {c.manner_reason}"[:180],
        })
    opts.append({
        "label": "None of these",
        "description": f"Not on the slate — {s.scope_line()}. Say what you want instead.",
    })

    # The manner question must be about WHICHEVER move is picked, never about
    # candidate 1. An earlier version named candidates[0].manner as "the
    # recommendation": pick the third option and it cheerfully recommended
    # `inline` for a YOU-ONLY item, i.e. dispatching work that needs the
    # operator's credentials. Found by the AGORA council on PR #5226 and
    # reproduced before being fixed. Each candidate carries its own manner in
    # its Q1 description; this question only offers a global OVERRIDE.
    manner_opts = [{
        "label": "Use each move's own",
        "description": "Run whichever move I pick the way it was matched to, as shown above.",
    }]
    for alt in ("inline", "pool-b", "council"):
        if len(manner_opts) < 3:
            pool, note = MANNERS[alt]
            manner_opts.append({
                "label": f"Force {alt}",
                "description": f"Override: {note}. Costs pool {pool}.",
            })
    manner_opts.append({"label": "Explain first",
                        "description": "Show the routing reasoning before I choose."})

    return [
        {"question": "Which move should I take next?",
         "header": "Next move", "multiSelect": False, "options": opts},
        # The '?' must be TERMINAL, not merely present. The live gate checks
        # rstrip().endswith("?"), so "How? Recommended: inline." is denied --
        # caught by test_questions_pass_the_live_quality_gate before it ever
        # cost the operator a turn.
        {"question": "Run it the way I matched it, or override that?",
         "header": "Manner", "multiSelect": False, "options": manner_opts},
    ]


def render(s: Slate) -> str:
    """Plain-text slate. The scope line is not optional."""
    if not s.candidates:
        return f"NEXT MOVE — nothing found ({s.scope_line()})"
    lines = [f"NEXT MOVE  ({s.scope_line()})", ""]
    for i, c in enumerate(s.candidates, 1):
        tag = "YOU ONLY" if not c.delegable else f"{c.manner} · pool {c.pool}"
        lines.append(f"{i}. {c.title}")
        lines.append(f"   [{c.origin}/{c.source}]  ->  {tag}  ({c.manner_reason})")
        if c.evidence:
            lines.append(f"   evidence: {c.evidence}")
    if s.degraded:
        lines.append("")
        lines.append("DEGRADED: " + "; ".join(s.degraded))
    if not s.interactive:
        lines.append("")
        lines.append("AFK/auto engaged — taking rank 1 without asking.")
    return "\n".join(lines)


if __name__ == "__main__":                                      # pragma: no cover
    print(render(slate(k=3)))
