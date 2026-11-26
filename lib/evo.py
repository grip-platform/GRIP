#!/usr/bin/env python3
"""
evo.py -- Fractal Facet Evolution Engine.

/evo IS converge() applied to GRIP self-understanding.

    converge(
        state  = corpus of CC facet analyses,
        criterion = calibrated understanding matches reality,
        improve = deepen analysis with GRIP context,
        max_depth = 7,
    )

Structural Mapping (Gentner SMT, validated Gen 475):
    converge.state     = facet corpus (externally measured by CC)
    converge.criterion = calibration complete: the advocate has seen the state
    converge.improve   = descent into sub-components (mechanical)
    converge.fixed_pt  = true friction density (asymptotic limit)

"Fractal" names the same question asked at each tier, not nested loops.
    Tier 0: Corpus   -- all facets -> calibrated metrics
    Tier 1: Facet    -- one session -> verdict
    Tier 2: Friction -- one event -> true/false-flag classification
    Tier 3: Evidence -- one claim -> validation against GRIP context
    Tier 4: Advocate -- weakly evidenced claims discounted

Only Tier 0 is a converge() loop: evo() calls converge() once. Round 1 of
that loop runs Tiers 1 to 3 as plain calls (analyse_facet, classify_friction,
match_evidence); round 2 runs Tier 4 once, and the loop stops there.
Discount factors EMERGE from evidence density, never hardcoded.

TIM Integration (Gen 79, arXiv:2603.10600):
    Trajectory-Informed Memory adds three capabilities:
    1. Tip Type Classification: strategy | recovery | optimization
    2. Causal Attribution: outcome -> root cause with message refs
    3. Proactive Tip Injection: GUIDELINE blocks before task execution
    See plans/tim-grip-integration-plan.md for architecture.
"""

import json, logging, hashlib, os, sys, time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
FACETS_DIR = CLAUDE_DIR / "usage-data" / "facets"
HOOKS_DIR = CLAUDE_DIR / "hooks"
MODES_DIR = CLAUDE_DIR / "modes" / "definitions"
LEARNING_DIR = CLAUDE_DIR / "learning"
EVO_CONVERGENCE = LEARNING_DIR / "evo-convergence.jsonl"

try:
    if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    from converge import converge
except ImportError:
    def converge(state, criterion, improve, depth=0, max_depth=3,
                 _history=None, _best=None, _scorer=None, _start=None, label=""):
        if _start is None: _start = time.monotonic()
        if _history is None: _history = []
        _history.append((depth, state))
        if criterion(state) or depth >= max_depth:
            return type("R", (), {"best_state": state, "satisfied": criterion(state),
                "depth_reached": depth, "elapsed_sec": time.monotonic() - _start})()
        return converge(improve(state), criterion, improve,
                        depth+1, max_depth, _history, _best, _scorer, _start, label)

# Evidence markers. False-flag rates EMERGE from match density.
GATE_EVIDENCE = {
    "hook", "gate", "block", "permission", "denied", "guardrail",
    "pretooluse", "safety", "restricted", "blocked by",
    # Quality/HITL/substitution gates. Sampled 2026-07-31 across 15 distinct
    # sessions (86 tool errors): ~70% were GRIP's own gates firing as designed,
    # but only the guardrail share matched above — the "[QUALITY] ..." and
    # "[HITL QUALITY GATE] ..." prefixes (~22% of the sample) matched nothing
    # and were therefore scored as real friction. A gate refusing an edit is
    # the system working; counting it as a defect inflates the friction rate
    # that /evo exists to measure honestly.
    "quality", "hitl", "tool substitution", "approach verification",
    "violation", "ratchet",
}
STEERING_EVIDENCE = {
    "user corrected", "user pointed out", "user had to",
    "requiring user", "user interrupted", "user redirected",
    "user clarified", "user provided", "user said",
    "user asked", "user wanted", "user manually",
}
ENVIRONMENT_EVIDENCE = {
    "api error", "500 error", "timeout", "eaddrinuse",
    "lock file", "git lock", "oom", "memory", "context window",
    "rate limit", "quota", "venv", "missing package", "missing module",
}
# GRIP infrastructure operations that CC misclassifies as friction.
# When friction_detail mentions these, the event is more likely a false flag
# (GRIP orchestration working as designed, not a real failure).
GRIP_WORKFLOW_EVIDENCE = {
    "mode", "rsi", "converge", "genome", "skill", "broly",
    "worktree", "compaction", "compact", "sprint", "grip",
    "heartbeat", "/save", "/recall", "/learn", "serialize",
}

# --- TIM: Tip Type Classification (arXiv:2603.10600) ---
# Three tip types from trajectory outcome analysis.
# Strategy: clean success patterns worth replicating.
# Recovery: failure-then-recovery sequences worth remembering.
# Optimization: successful but inefficient patterns worth improving.

class TipType(str, Enum):
    STRATEGY = "strategy"
    RECOVERY = "recovery"
    OPTIMIZATION = "optimization"
    UNCLASSIFIED = "unclassified"


# Friction types that indicate recovery patterns (failure then success).
RECOVERY_FRICTION = {"wrong_approach", "buggy_code", "tool_failure", "tool_error"}
# Friction types that indicate inefficiency (succeeded but wasted effort).
OPTIMIZATION_FRICTION = {"excessive_change", "user_rejected_action", "tool_permission_gate"}


def classify_tip_type(facet: dict) -> TipType:
    """Classify a session facet into a TIM tip type based on outcome and friction.

    Strategy: clean success (no/low friction, good outcome).
    Recovery: had failures but ultimately succeeded.
    Optimization: succeeded but with inefficiency friction.
    """
    outcome = facet.get("outcome", "")
    succeeded = outcome in ("fully_achieved", "mostly_achieved")
    friction = facet.get("friction_counts", {})
    total_friction = sum(
        int(v) if isinstance(v, (int, float)) else 0
        for v in friction.values()
    )

    if not succeeded:
        return TipType.UNCLASSIFIED

    # Check for recovery pattern: had failure-type friction but succeeded
    has_recovery = any(
        _normalise_friction_type(ft) in RECOVERY_FRICTION
        for ft in friction
        if (int(friction[ft]) if isinstance(friction[ft], (int, float)) else 0) > 0
    )
    if has_recovery and total_friction >= 3:
        return TipType.RECOVERY

    # Check for optimisation pattern: had inefficiency friction but succeeded
    has_optimisation = any(
        _normalise_friction_type(ft) in OPTIMIZATION_FRICTION
        for ft in friction
        if (int(friction[ft]) if isinstance(friction[ft], (int, float)) else 0) > 0
    )
    if has_optimisation:
        return TipType.OPTIMIZATION

    # Clean success with low friction
    if total_friction <= 2:
        return TipType.STRATEGY

    return TipType.UNCLASSIFIED


# --- TIM: Causal Attribution (arXiv:2603.10600) ---
# Traces friction backward to root-cause decisions.
# Attribution must reference specific message numbers (anti-Goodhart).

@dataclass
class CausalAttribution:
    """Attribution chain from outcome to root cause."""
    friction_type: str
    immediate_cause: str
    root_cause: str
    contributing_factors: list = field(default_factory=list)
    message_refs: list = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "friction_type": self.friction_type,
            "immediate_cause": self.immediate_cause,
            "root_cause": self.root_cause,
            "contributing_factors": self.contributing_factors,
            "message_refs": self.message_refs,
            "confidence": self.confidence,
        }


def attribute_causes(friction_type: str, detail: str, evidence: list) -> CausalAttribution:
    """Derive causal attribution from friction evidence.

    Uses evidence markers to infer the causal chain without an LLM call.
    For full LLM-based attribution, use attribute_causes_llm() (future).
    """
    immediate = detail[:200] if detail else "unknown"
    root = immediate  # Default: immediate IS root when no deeper signal

    # Infer root cause from evidence categories
    gate_ev = [e for e in evidence if e.startswith("gate:")]
    steering_ev = [e for e in evidence if e.startswith("steering:")]
    env_ev = [e for e in evidence if e.startswith("env:")]
    grip_ev = [e for e in evidence if e.startswith("grip_workflow:")]

    contributing = []
    if gate_ev:
        root = f"safety gate triggered: {gate_ev[0].split(':',1)[1]}"
        contributing.append("hook/gate enforcement")
    if steering_ev:
        root = f"agent misaligned: {steering_ev[0].split(':',1)[1]}"
        contributing.append("user had to correct course")
    if env_ev:
        root = f"environment issue: {env_ev[0].split(':',1)[1]}"
        contributing.append("external system constraint")
    if grip_ev:
        root = f"GRIP workflow overhead: {grip_ev[0].split(':',1)[1]}"
        contributing.append("infrastructure operation")

    # Confidence: more evidence categories = higher confidence
    categories_matched = sum(1 for e in [gate_ev, steering_ev, env_ev, grip_ev] if e)
    conf = min(0.9, 0.3 + categories_matched * 0.15)

    return CausalAttribution(
        friction_type=friction_type,
        immediate_cause=immediate,
        root_cause=root,
        contributing_factors=contributing,
        message_refs=[],  # Populated by LLM analysis in future
        confidence=conf,
    )


@dataclass
class EvoState:
    """State of the /evo converge() loop; ``tier`` is 0 before round 1 and 4 after it.

    ``challenged`` turns True when the devil's advocate (Tier 4) has run, which
    it does once per analysis; the loop stops on that state (GRIP#9533).
    """
    tier: int
    data: Any
    context: dict = field(default_factory=dict)
    confidence: float = 0.0
    evidence: list = field(default_factory=list)
    verdicts: list = field(default_factory=list)
    raw_friction_density: float = 0.0
    calibrated_friction_density: float = 0.0
    false_flag_rate: float = 0.0
    friction_analysis: dict = field(default_factory=dict)
    genome_proposals: list = field(default_factory=list)
    cube_updates: dict = field(default_factory=dict)
    # TIM extensions
    tip_types: dict = field(default_factory=dict)  # {TipType.value: count}
    attributions: list = field(default_factory=list)  # [CausalAttribution]
    challenged: bool = False


def load_grip_context() -> dict:
    """Load GRIP self-knowledge that CC classifier lacks."""
    hooks = set()
    if HOOKS_DIR.exists():
        for f in HOOKS_DIR.iterdir():
            if f.suffix in (".py", ".sh"):
                hooks.add(f.stem.lower().replace("-", " ").replace("_", " "))
    modes = set()
    if MODES_DIR.exists():
        for f in MODES_DIR.glob("*-mode.yaml"):
            modes.add(f.stem.replace("-mode", ""))
    return {
        "hooks": hooks,
        "modes": modes,
        "genesis": load_genesis_evidence(),
        "converge_stats": _load_converge_stats(),
    }


def _load_converge_stats() -> dict:
    """Load converge run statistics for evo calibration context (Loop 2).

    Returns summary stats that help evo distinguish friction in successful
    converge runs (self-corrected) from friction in stalled runs.
    """
    metrics_file = CLAUDE_DIR / "learning" / "converge-metrics.jsonl"
    if not metrics_file.exists():
        return {}
    try:
        lines = metrics_file.read_text().strip().splitlines()
        if not lines:
            return {}
        total = len(lines)
        satisfied = sum(1 for l in lines if '"satisfied": true' in l or '"satisfied":true' in l)
        return {
            "total_runs": total,
            "satisfied_rate": round(satisfied / max(total, 1), 3),
            "recent_runs": min(total, 20),
        }
    except Exception:
        return {}


_genesis_evidence_cache: Optional[Dict[str, Set[str]]] = None


def _parse_genesis_skill(skill_file: Path) -> Optional[Tuple[str, Set[str]]]:
    """Extract (friction_type, keywords) from a single genesis evo skill."""
    import yaml
    try:
        text = skill_file.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return None
        end = text.index("---", 3)
        fm = yaml.safe_load(text[3:end])
        if not isinstance(fm, dict):
            return None
        lineage = fm.get("lineage", "")
        if not isinstance(lineage, str) or not lineage.startswith("genesis:evo:"):
            return None
        friction_type = fm.get("friction_type", "")
        keywords = fm.get("evidence_keywords", [])
        if friction_type and keywords:
            return friction_type, {kw.lower() for kw in keywords if isinstance(kw, str)}
    except Exception:
        pass
    return None


def load_genesis_evidence() -> Dict[str, Set[str]]:
    """Read genesis micro-skills and extract evidence_keywords for dynamic enrichment.

    Scans skills/ for SKILL.md files with lineage starting with 'genesis:evo:'.
    Cached per process — genesis skills don't change mid-run.

    Returns: {friction_type: {keyword1, keyword2, ...}}
    """
    global _genesis_evidence_cache
    if _genesis_evidence_cache is not None:
        return _genesis_evidence_cache

    result: Dict[str, Set[str]] = {}
    skills_dir = CLAUDE_DIR / "skills"
    if not skills_dir.exists():
        _genesis_evidence_cache = result
        return result

    for skill_dir in skills_dir.iterdir():
        skill_file = skill_dir / "SKILL.md"
        if not skill_dir.is_dir() or not skill_file.exists():
            continue
        parsed = _parse_genesis_skill(skill_file)
        if parsed:
            ftype, kws = parsed
            result.setdefault(ftype, set()).update(kws)

    _genesis_evidence_cache = result
    return result


def match_evidence(detail: str, evidence_set: set, label: str) -> tuple:
    """Tier 3: match detail against evidence. Weight emerges from density.

    Plain substring scan. The keyword sets are stems written for it ("block"
    must match "blocked", "guardrail" must match "[GUARDRAIL:"). The token
    intersection that replaced it in 3f05d30a0 kept punctuation attached and
    dropped those matches. The built-in sets hold under twenty keywords each.
    """
    dl = detail.lower()
    matches = [kw for kw in evidence_set if kw in dl]
    if not matches:
        return 0.0, []
    return min(0.4, len(matches) * 0.12), [f"{label}:{m}" for m in matches[:3]]


def classify_friction(ftype: str, count: int, detail: str, ctx: dict) -> dict:
    """Tier 2: classify one friction event in a single pass of evidence matching."""
    ev = []
    ff = 0.0
    w, e = match_evidence(detail, GATE_EVIDENCE, "gate")
    ff += w; ev.extend(e)
    for h in ctx.get("hooks", set()):
        if h in detail.lower():
            ff += 0.25; ev.append(f"known_hook:{h}"); break
    w, e = match_evidence(detail, STEERING_EVIDENCE, "steering")
    ff += w; ev.extend(e)
    w, e = match_evidence(detail, ENVIRONMENT_EVIDENCE, "env")
    ff += w; ev.extend(e)
    w, e = match_evidence(detail, GRIP_WORKFLOW_EVIDENCE, "grip_workflow")
    ff += w; ev.extend(e)
    # Dynamic evidence from genesis micro-skills (Loop 1)
    genesis = ctx.get("genesis", {})
    if genesis:
        genesis_kws = genesis.get(ftype, set()) | genesis.get(
            _normalise_friction_type(ftype), set()
        )
        if genesis_kws:
            w, e = match_evidence(detail, genesis_kws, "genesis")
            ff += w; ev.extend(e)
    # Converge satisfaction context (Loop 2): high satisfaction rate means
    # friction events were likely self-corrected, not permanent failures
    converge_stats = ctx.get("converge_stats", {})
    sat_rate = converge_stats.get("satisfied_rate", 0)
    if sat_rate > 0.6 and ftype in ("wrong_approach", "buggy_code", "excessive_change"):
        bonus = round(min(0.1, sat_rate * 0.1), 3)
        ff += bonus
        ev.append(f"converge_self_corrected:{sat_rate:.0%}")
    ff = min(0.95, max(0.0, ff))
    length_penalty = 0.15 if len(detail) < 60 else 0.0
    return {"type": ftype, "raw": count, "true": round(count * (1-ff), 3),
            "ff_rate": round(ff, 3), "evidence": ev,
            "confidence": min(0.95, max(0.1, 0.3 + len(ev) * 0.12 - length_penalty))}


def _verdict_from_ff_rate(ffr: float) -> str:
    """Map false-flag rate to verdict category."""
    if ffr > 0.6: return "intentional_gate"
    if ffr > 0.3: return "mixed"
    if ffr > 0.1: return "grip_steering"
    return "true_friction"


# CUBE scoring: runs principles_scan() ONCE per /evo invocation (codebase snapshot)
_CUBE_SCORES_CACHE: dict = {}
# Fingerprints whose scan already came back empty this process (slow-scan timeout
# or a genuinely empty result). Memoised so a single /evo invocation — which calls
# _get_cube_scores once per facet — does not re-run the multi-minute scan 50+ times
# when it degrades to {} (see CUBE_SCAN_TIMEOUT_S). Empty results are still NOT
# written to disk (fail-open), only held in-process.
_CUBE_EMPTY_FPS: set = set()


_CUBE_CACHE_PATH = CLAUDE_DIR / "state" / "evo" / "cube-cache.json"
_CUBE_CACHE_TTL_S = 86_400  # 24h — re-scan even on an unchanged HEAD after a day
# Hard ceiling for the CUBE source scan. principles_scan() runs DedupScanner +
# YAGNIScanner + AST analysis over ~1222 lib/*.py and can take minutes on a cold
# cache; the 8 scores are secondary ("{} on failure"), so a slow scan must not
# block the primary /evo facet calibration (which blew the heartbeat's 120s cap).
CUBE_SCAN_TIMEOUT_S = 30


def _codebase_fingerprint() -> str:
    """Cheap, stable key for the CUBE cache: a hash of the SCORED tree (lib/).

    Keyed on lib/**/*.py (relative path + mtime + size) rather than the git HEAD
    sha, so a PR that only touches docs/skills/tests/CHANGELOG no longer
    invalidates the CUBE cache and forces a multi-minute re-scan of ~1222
    lib/*.py on the next /evo (which blew the heartbeat's 120s cap). Falls back
    to the HEAD sha, then 'nogit', when lib/ is missing or unreadable.
    """
    lib_dir = CLAUDE_DIR / "lib"
    if lib_dir.is_dir():
        try:
            h = hashlib.sha256()
            for p in sorted(lib_dir.rglob("*.py")):
                st = p.stat()
                rel = p.relative_to(lib_dir)
                h.update(f"lib/{rel}:{st.st_mtime_ns}:{st.st_size}".encode("utf-8"))
            return "lib-" + h.hexdigest()[:40]
        except Exception as e:
            logger.debug("lib/ fingerprint failed: %s", e)
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", str(CLAUDE_DIR), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "nogit"


def _cube_cache_read(fp: str) -> Optional[dict]:
    """Cached CUBE scores iff the disk cache matches fingerprint fp and is still
    fresh (< TTL); else None. Never raises — a bad read just forces a scan.

    Note: a dict of all-zero scores is still a non-empty (truthy) dict, so valid
    zero-valued scores ARE served from cache, not re-scanned (AGORA #4073 §2).
    """
    try:
        if _CUBE_CACHE_PATH.exists():
            cached = json.loads(_CUBE_CACHE_PATH.read_text(encoding="utf-8"))
            fresh = (time.time() - float(cached.get("ts", 0))) < _CUBE_CACHE_TTL_S
            if cached.get("key") == fp and fresh and cached.get("scores"):
                return cached["scores"]
    except Exception as e:
        logger.debug("cube cache read failed: %s", e)
    return None


def _cube_cache_write(fp: str, scores: dict) -> None:
    """Persist CUBE scores to the disk cache keyed on fp. Never raises."""
    try:
        _CUBE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CUBE_CACHE_PATH.write_text(
            json.dumps({"key": fp, "ts": time.time(), "scores": scores}),
            encoding="utf-8",
        )
    except Exception as e:
        logger.debug("cube cache write failed: %s", e)


def _cube_scan_root() -> Path:
    """Directory the CUBE scan scores: the install's lib/ if present, else the
    whole install. A misconfigured checkout without a lib/ dir must not silently
    scan nothing (AGORA #4073 §1)."""
    lib_dir = CLAUDE_DIR / "lib"
    return lib_dir if lib_dir.is_dir() else CLAUDE_DIR


def _cube_scan_source() -> dict:
    """Scan GRIP SOURCE (lib/) for the 8 principle scores; {} on failure/timeout.

    The scan AST-analyses ~1222 lib/*.py and can take minutes on a cold cache.
    CUBE scores are secondary (the primary /evo value is facet calibration, and
    these are documented "{} on failure"), so a slow scan must not block /evo —
    it is bounded to CUBE_SCAN_TIMEOUT_S and degrades to {} when it overruns.

    Runs in a SUBPROCESS, not a thread: principles_scan() is CPU-bound (AST +
    regex over the whole lib/), and a daemon thread that overruns its join()
    cannot be stopped — it keeps holding the GIL and starves the rest of /evo
    for minutes (measured 2026-10-10). A subprocess can actually be SIGKILLed,
    so the scan is truly bounded and the parent process stays responsive.
    """
    import subprocess
    scan_root = str(_cube_scan_root())
    # argv[1] = lib/ dir for the import path; argv[2] = the dir to score.
    script = (
        "import sys, json\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from subsystem_bridge import cube_scores_for_outcome\n"
        "print(json.dumps(cube_scores_for_outcome(sys.argv[2])))\n"
    )
    try:
        proc = subprocess.Popen(
            [sys.executable or "python3", "-c", script,
             str(CLAUDE_DIR / "lib"), scan_root],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        try:
            out, _ = proc.communicate(timeout=CUBE_SCAN_TIMEOUT_S)
            if proc.returncode == 0 and out:
                return json.loads(out.decode("utf-8"))
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate()
            except Exception:
                pass
            proc.wait()  # reap the zombie even if communicate() was interrupted
            logger.debug("cube_scores_for_outcome timed out after %ss", CUBE_SCAN_TIMEOUT_S)
    except Exception as e:
        logger.debug("cube_scores_for_outcome failed: %s", e)
    return {}


def _get_cube_scores() -> dict:
    """Return the 8 CUBE domain scores for GRIP's SOURCE (lib/), cached (task #521).

    The previous scan walked the whole ~/.grip install (3121 .py) and AST-analysed
    3000 files on EVERY /evo invocation (~16 min, no result). Now: scan lib/ only
    (tests/ + apps/ + data/ are not the scored codebase), memoised in-process (L0)
    and on disk keyed on a lib/-content fingerprint with a 24h TTL (L1) — so repeat
    runs on unchanged code are instant.

    A scan that degrades to {} (slow-scan timeout, or a genuinely empty result) is
    NOT written to disk (fail-open), but IS memoised in-process per fingerprint so
    the 50+ per-facet callers in one /evo do not re-run the multi-minute scan each
    time.
    """
    if _CUBE_SCORES_CACHE:
        return _CUBE_SCORES_CACHE
    fp = _codebase_fingerprint()
    cached = _cube_cache_read(fp)
    if cached:
        _CUBE_SCORES_CACHE.update(cached)
        return _CUBE_SCORES_CACHE
    if fp in _CUBE_EMPTY_FPS:
        return {}
    scores = _cube_scan_source()
    if scores:
        _CUBE_SCORES_CACHE.update(scores)
        _cube_cache_write(fp, scores)
        return _CUBE_SCORES_CACHE
    _CUBE_EMPTY_FPS.add(fp)
    return {}


def _extract_cube(facet: dict) -> dict:
    """Return CUBE scores for this facet from the codebase-level scan.

    All facets share the same CUBE scores since they reflect the current
    codebase state, not individual session history. The scan is run once
    and cached via _get_cube_scores().
    """
    return dict(_get_cube_scores())


def analyse_facet(facet: dict, ctx: dict) -> dict:
    """Tier 1: one session's verdict, from one classify_friction call per friction type."""
    sid = facet.get("session_id", "?")[:12]
    raw = facet.get("friction_counts", {})
    detail = facet.get("friction_detail", "")
    tip_type = classify_tip_type(facet)
    if not raw:
        return {"session_id": sid, "verdict": "clean", "raw_total": 0,
                "calibrated_total": 0.0, "ff_rate": 0.0, "classifications": [],
                "cube": {}, "tip_type": tip_type.value}
    cls_list = []
    rt, ct = 0, 0.0
    for ft, cnt in raw.items():
        cnt = int(cnt) if isinstance(cnt, (int, float)) else 0
        rt += cnt
        c = classify_friction(ft, cnt, detail, ctx)
        cls_list.append(c); ct += c["true"]
    ffr = round(1 - (ct / max(rt, 1)), 3)
    return {"session_id": sid, "verdict": _verdict_from_ff_rate(ffr), "raw_total": rt,
            "calibrated_total": round(ct, 3), "ff_rate": ffr,
            "classifications": cls_list, "cube": _extract_cube(facet),
            "tip_type": tip_type.value}


def devils_advocate(state: EvoState) -> EvoState:
    """Tier 4: challenge high ff_rate with low evidence, once per analysis.

    A state the advocate has already seen comes back unchanged. A second pass
    would cut the same weakly evidenced rates by 0.6 again, so the discount
    would depend on how many rounds ran rather than on the evidence. Every
    other field is copied, so the TIM tip types and attributions survive.
    """
    if state.challenged:
        return state
    discounted = {}
    notes = []
    for ft, a in state.friction_analysis.items():  # O(1): bounded by friction types (~5)
        a2 = dict(a)
        if a["ff_rate"] > 0.3 and len(a.get("evidence", [])) < 2:
            a2["ff_rate"] = round(a["ff_rate"] * 0.6, 3)
            a2["challenged"] = True
            notes.append(f"{ft}: {a['ff_rate']:.0%}->{a2['ff_rate']:.0%} (weak ev)")
        discounted[ft] = a2
    raw_t = sum(a["raw"] for a in discounted.values())
    cal_t = sum(a["raw"] * (1 - a["ff_rate"]) for a in discounted.values())
    return replace(
        state, challenged=True, friction_analysis=discounted,
        confidence=min(0.95, state.confidence + 0.1),
        evidence=state.evidence + notes,
        calibrated_friction_density=round(cal_t / max(len(state.verdicts), 1), 3),
        false_flag_rate=round(1 - (cal_t / max(raw_t, 1)), 3) if raw_t else 0)


def _normalise_friction_type(ft: str) -> str:
    """Normalise friction type names (deduplicate plurals like tool_failures -> tool_failure)."""
    ft = ft.strip().lower()
    # Only strip trailing 's' from compound underscore types (safe: tool_failures, not: analysis)
    if "_" in ft and ft.endswith("s") and not ft.endswith(("ss", "sis")):
        ft = ft[:-1]
    return ft


def _aggregate_friction(verdicts: list) -> dict:
    """Aggregate per-type friction stats across all verdicts.

    Flattens classifications via chain to avoid nested loop (O(total_classifications)).
    """
    from itertools import chain
    tr, tc, te = {}, {}, {}
    all_cls = chain.from_iterable(v.get("classifications", []) for v in verdicts)
    for c in all_cls:
        ft = _normalise_friction_type(c["type"])
        tr[ft] = tr.get(ft, 0) + c["raw"]
        tc[ft] = tc.get(ft, 0) + c["true"]
        te.setdefault(ft, []).extend(c.get("evidence", []))
    return {ft: {"raw": tr[ft], "true": round(tc.get(ft, tr[ft]), 1),
                 "ff_rate": round(1 - (tc.get(ft, tr[ft]) / max(tr[ft], 1)), 3),
                 "evidence": list(set(te.get(ft, [])))[:10]}
            for ft in tr}


def _aggregate_cube(verdicts: list) -> dict:
    """Average CUBE scores across all verdicts.

    Flattens cube entries via chain to avoid nested loop (O(total_entries)).
    """
    from itertools import chain
    ca: dict[str, list] = {}
    all_entries = chain.from_iterable(v.get("cube", {}).items() for v in verdicts)
    for d, s in all_entries:
        ca.setdefault(d, []).append(s)
    return {d: round(sum(s)/len(s), 3) for d, s in ca.items()}


def _generate_proposals(fa: dict) -> list:
    """Generate genome proposals from friction types with high FF rates.

    Includes friction_type so propose_micro_skill() can template
    evidence_keywords frontmatter for the genesis feedback loop.
    """
    return [{"name": f"evo-{ft.replace('_','-')}-calibrator",
             "source": f"evo:facet:{ft}",
             "friction_type": ft,
             "behavior": f"'{ft}' ff={a['ff_rate']*100:.0f}% ({a['true']:.0f}/{a['raw']})"}
            for ft, a in fa.items()
            if a["ff_rate"] > 0.3 and a["raw"] >= 10]


def _aggregate_tip_types(verdicts: list) -> dict:
    """Count tip type distribution across verdicts."""
    counts = {}
    for v in verdicts:
        tt = v.get("tip_type", "unclassified")
        counts[tt] = counts.get(tt, 0) + 1
    return counts


def _build_attributions(fa: dict) -> list:
    """Build causal attributions for friction types with significant true friction."""
    attrs = []
    for ft, a in fa.items():
        if a["true"] < 3:
            continue
        attr = attribute_causes(ft, f"{ft}: {a['true']:.0f} true events", a.get("evidence", []))
        attrs.append(attr)
    return attrs


def evo_improve(state: EvoState) -> EvoState:
    """The loop's improve step: from Tier 0 it runs Tiers 1 to 3 as calls; from Tier 4, the advocate."""
    if state.tier == 0:
        ctx = state.context or load_grip_context()
        verdicts = [analyse_facet(f, ctx) for f in state.data]
        total = len(verdicts)
        fa = _aggregate_friction(verdicts)
        cube = _aggregate_cube(verdicts)
        tip_types = _aggregate_tip_types(verdicts)
        attributions = _build_attributions(fa)
        rs = sum(v["raw_total"] for v in verdicts)
        cs = sum(v["calibrated_total"] for v in verdicts)
        return EvoState(tier=4, data=state.data, context=ctx,
            confidence=min(0.85, 0.4 + total * 0.001),
            evidence=[f"analysed {total} facets"], verdicts=verdicts,
            raw_friction_density=round(rs / max(total, 1), 3),
            calibrated_friction_density=round(cs / max(total, 1), 3),
            false_flag_rate=round(1-(cs/max(rs,1)),3) if rs else 0,
            friction_analysis=fa, genome_proposals=_generate_proposals(fa),
            cube_updates=cube, tip_types=tip_types, attributions=attributions)
    elif state.tier == 4:
        return devils_advocate(state)
    return state


def evo(facets_dir=None, max_depth=7):
    """converge(corpus, calibrated, evo_improve, max_depth)."""
    if facets_dir is None: facets_dir = FACETS_DIR
    facets_dir = Path(facets_dir)
    facets = []
    for f in sorted(facets_dir.glob("*.json")):
        try: facets.append(json.loads(f.read_text()))
        except: continue
    if not facets:
        print("No facet files.", file=sys.stderr); return None
    # Stop on the first state the advocate has seen. The old stop rule,
    # confidence >= 0.8, held after round 1 from 400 facets, so the advocate
    # never ran; below 300 facets it never held, so the advocate ran again
    # each round. Confidence is reported, not a stop rule (GRIP#9533).
    result = converge(
        state=EvoState(tier=0, data=facets),
        criterion=lambda s: s.challenged,
        improve=evo_improve, max_depth=max_depth,
        _scorer=lambda s: s.confidence, label="evo")
    return result.best_state


def feed_learning(state: EvoState) -> int:
    """Feed calibrated insights into learning pipeline with TIM tip types."""
    ifile = LEARNING_DIR / "insights.jsonl"
    ifile.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if ifile.exists():
        for line in ifile.read_text(errors="replace").strip().split("\n"):
            if line.strip():
                try: existing.add(json.loads(line).get("id", ""))
                except: pass
    now = datetime.now(timezone.utc).isoformat()
    # Build attribution lookup for enrichment
    attr_map = {a.friction_type: a for a in state.attributions}
    n = 0
    with open(ifile, "a") as f:
        for ft, a in state.friction_analysis.items():  # O(1): bounded by friction types (~5)
            if a["raw"] < 5: continue
            iid = hashlib.sha256(f"evo-{ft}-{now}".encode()).hexdigest()[:12]
            if iid in existing: continue
            # Determine tip type for this friction category
            tip_type = TipType.UNCLASSIFIED.value
            if ft in RECOVERY_FRICTION:
                tip_type = TipType.RECOVERY.value
            elif ft in OPTIMIZATION_FRICTION:
                tip_type = TipType.OPTIMIZATION.value
            elif a["ff_rate"] > 0.5:
                tip_type = TipType.STRATEGY.value
            # Build insight record with TIM extensions
            record = {
                "id": iid,
                "text": f"/evo: '{ft}' ff={a['ff_rate']*100:.0f}% ({a['true']:.0f}/{a['raw']}). Ev: {', '.join(a.get('evidence',[])[:5])}",
                "context": "evo_facet_calibration",
                "tags": ["evo", ft, f"tip:{tip_type}"],
                "created_at": now,
                "learning_score": min(0.9, 0.5 + a["ff_rate"]),
                "source": "evo",
                "tip_type": tip_type,
            }
            # Add causal attribution if available
            attr = attr_map.get(ft)
            if attr:
                record["attribution"] = attr.to_dict()
            f.write(json.dumps(record) + "\n")
            n += 1
    return n


def record_convergence(state: EvoState) -> None:
    EVO_CONVERGENCE.parent.mkdir(parents=True, exist_ok=True)
    with open(EVO_CONVERGENCE, "a") as f:
        f.write(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(),
            "raw": state.raw_friction_density, "calibrated": state.calibrated_friction_density,
            "ff_rate": state.false_flag_rate, "confidence": state.confidence,
            "facets": len(state.verdicts),
            "types": {k: {"raw": v["raw"], "true": v["true"], "ff": v["ff_rate"]}
                       for k, v in state.friction_analysis.items()},
            "proposals": len(state.genome_proposals), "cube": state.cube_updates,
            "tip_types": state.tip_types,
            "attributions": [a.to_dict() for a in state.attributions],
        }) + "\n")


def persist_proposals(state: EvoState) -> int:
    """Persist genome proposals to learning/pending/evo-proposals.json."""
    pending_dir = LEARNING_DIR / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    out_path = pending_dir / "evo-proposals.json"

    now = datetime.now(timezone.utc).isoformat()
    proposals = []
    for p in state.genome_proposals:
        proposals.append({
            "name": p["name"],
            "source": p["source"],
            "behavior": p["behavior"],
            "friction_type": p["source"].split(":")[-1] if ":" in p["source"] else "",
            "ff_rate": state.false_flag_rate,
            "raw_count": len(state.verdicts),
            "generated_at": now,
        })

    out_path.write_text(json.dumps(proposals, indent=2) + "\n")
    print(f"  Proposals persisted: {out_path} ({len(proposals)} items)")
    return len(proposals)


def print_report(state: EvoState) -> None:
    print(f"\n{'='*64}")
    print(f"  /evo -- Fractal Facet Evolution Report")
    print(f"{'='*64}\n")
    print(f"  Facets:     {len(state.verdicts)}")
    print(f"  Raw FD:     {state.raw_friction_density}")
    print(f"  Cal FD:     {state.calibrated_friction_density}")
    print(f"  FF rate:    {state.false_flag_rate*100:.1f}%")
    print(f"  Confidence: {state.confidence:.2f}")
    print(f"\n  {'Type':<28} {'Raw':>5} {'True':>7} {'FF%':>7}")
    print(f"  {'~'*28} {'~'*5} {'~'*7} {'~'*7}")
    for ft, a in sorted(state.friction_analysis.items(), key=lambda x: -x[1]["raw"]):  # O(1): bounded ~5 types
        print(f"  {ft:<28} {a['raw']:>5} {a['true']:>7.1f} {a['ff_rate']*100:>6.1f}%")
    vt = {}
    for v in state.verdicts:
        k = v.get("verdict", "?"); vt[k] = vt.get(k, 0) + 1
    t = len(state.verdicts)
    print(f"\n  Verdicts:")
    for k, c in sorted(vt.items(), key=lambda x: -x[1]):
        print(f"    {k:<20} {c:>4} ({c/t*100:.1f}%)")
    if state.genome_proposals:
        print(f"\n  Genome proposals ({len(state.genome_proposals)}):")
        for p in state.genome_proposals: print(f"    {p['name']}")
    if state.tip_types:
        print(f"\n  TIM Tip Types:")
        for tt, cnt in sorted(state.tip_types.items(), key=lambda x: -x[1]):
            print(f"    {tt:<20} {cnt:>4}")
    if state.attributions:
        print(f"\n  Causal Attributions ({len(state.attributions)}):")
        for a in state.attributions[:5]:
            print(f"    {a.friction_type}: {a.root_cause[:60]}")
    if state.cube_updates:
        print(f"\n  CUBE Principle Scores ({len(state.cube_updates)} domains):")
        print(f"  {'Domain':<10} {'Score':>7}")
        print(f"  {'~'*10} {'~'*7}")
        for d, s in sorted(state.cube_updates.items()):
            bar = "#" * int(s * 20) + "." * (20 - int(s * 20))
            print(f"  {d:<10} {s:>6.1%} [{bar}]")
    if state.evidence:
        print(f"\n  Notes: {'; '.join(state.evidence[:5])}")
    _print_yolo_usage_tile()
    print(f"\n{'='*64}\n")


def _print_yolo_usage_tile() -> None:
    """Render the Yolo Usage observation tile below the main evo report.

    Best-effort: yolo_metrics is stdlib-only and the log may be absent on
    fresh installs, so failures are swallowed rather than breaking /evo.
    """
    try:
        from yolo_metrics import load, render_evo_tile
    except ImportError:
        return
    try:
        metrics = load()
    except OSError:
        return
    print()
    for line in render_evo_tile(metrics).splitlines():
        print(f"  {line}")


def _is_inline_dispatch() -> bool:
    """Detect if /evo was launched inline (Bash tool) vs Agent-isolated.

    Agent-dispatched runs set GRIP_EVO_AGENT=1 (or CLAUDE_AGENT_CONTEXT
    by the harness). Anything else is an inline invocation that pollutes
    the main session JSONL with 80+ lines of report output — see
    rules/evo-dispatch-protocol.md (PARAMOUNT, 3rd-recurrence fix).
    """
    if os.environ.get("GRIP_EVO_AGENT", "").strip() == "1":
        return False
    if os.environ.get("CLAUDE_AGENT_CONTEXT", "").strip():
        return False
    return True


def _run_quiet_summary(state) -> None:
    """Emit 2-line summary for Agent-dispatch contexts."""
    print(
        f"/evo: {len(state.verdicts)} facets, "
        f"FF={state.false_flag_rate*100:.1f}%, "
        f"calibrated={state.calibrated_friction_density:.3f}"
    )


def run(max_depth=7, quiet=False):
    """Run /evo. quiet=True returns 2-line summary (Agent-dispatch friendly).

    Per rules/evo-dispatch-protocol.md, /evo MUST be Agent-dispatched.
    When invoked inline (no GRIP_EVO_AGENT=1), prints a single-line
    redirect notice on stderr so the rule violation is visible without
    blocking the run (preserves backwards compatibility).
    """
    if _is_inline_dispatch():
        print(
            "[EVO-DISPATCH] inline invocation detected — should be Agent-dispatched. "
            "See rules/evo-dispatch-protocol.md. Continuing.",
            file=sys.stderr,
        )
    state = evo(max_depth=max_depth)
    if not state:
        print("No data."); return None
    if quiet:
        _run_quiet_summary(state)
    else:
        print("\n--- /evo: Fractal Facet Evolution ---\n")
        print_report(state)
    n = feed_learning(state)
    if not quiet:
        print(f"  Learning: {n} new insights.")
    record_convergence(state)
    if not quiet:
        print(f"  Convergence recorded.")
    if state.genome_proposals:
        if not quiet:
            print(f"  {len(state.genome_proposals)} genome proposals ready.")
        persist_proposals(state)
    stamp = CLAUDE_DIR / "state" / "rsi" / "evo-last-run"  # state_paths.RSI_EVO_LAST_RUN
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(str(time.time()))
    if not quiet:
        print("\n--- /evo complete ---\n")
    return state


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--quiet"]
    quiet_flag = "--quiet" in sys.argv[1:]
    cmd = args[0] if args else "run"
    depth = int(args[1]) if len(args) > 1 else 7
    if cmd == "run": run(depth, quiet=quiet_flag)
    elif cmd == "json":
        s = evo(max_depth=depth)
        if s: print(json.dumps({"facets": len(s.verdicts), "raw": s.raw_friction_density,
            "cal": s.calibrated_friction_density, "ff": s.false_flag_rate,
            "types": {k: {"raw":v["raw"],"true":v["true"],"ff":v["ff_rate"]}
                       for k,v in s.friction_analysis.items()},
            "proposals": s.genome_proposals, "cube": s.cube_updates}, indent=2))
        else: print(json.dumps({"facets": 0, "raw": 0.0, "cal": 0.0, "ff": 0.0,
            "types": {}, "proposals": [], "cube": {}, "status": "no_facet_files"}, indent=2))
    elif cmd == "trajectory":
        if EVO_CONVERGENCE.exists():
            for l in EVO_CONVERGENCE.read_text().strip().split("\n"):
                if l.strip():
                    try:
                        r = json.loads(l)
                        raw = r.get('raw', 0.0)
                        cal = r.get('calibrated', 0.0)
                        ff = r.get('ff_rate', 0.0)
                        print(f"  {r.get('timestamp', '?')[:10]}  raw={raw:.3f}  cal={cal:.3f}  ff={ff*100:.1f}%")
                    except (json.JSONDecodeError, KeyError):
                        continue
        else: print("No trajectory. Run /evo first.")
    else: print("Usage: python3 evo.py [run|json|trajectory] [depth]"); sys.exit(1)
