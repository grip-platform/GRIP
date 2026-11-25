"""
Broly Adaptive Router (BAR) - Intelligent prompt classification and routing.

Two-tier classification: heuristic (0-cost) + semantic (embedding-based fallback).
Budget metabolism caps power levels based on weekly spend percentage.
Friction boost auto-escalates when recent wrong_approach events detected.

Architecture:
    User Prompt
        -> Tier 1: Heuristic Classifier (0 cost, <1ms)
        -> Tier 2: Semantic Classifier (embeddings, ~10ms, fallback only)
        -> Friction History Boost (+1/+2 levels)
        -> Budget Metabolism Cap (graceful degradation)
        -> RoutingDecision

Usage:
    from prompt_router import classify_prompt, RoutingDecision
    decision = classify_prompt("security audit the entire codebase")
    # decision.power_level = "cluster", decision.confidence = 0.85

Version: 1.0.0
"""

from file_lock import FileLock
import hashlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure lib/ is on sys.path for sibling imports
_lib_dir = str(Path(__file__).resolve().parent)
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from json_utils import safe_load_json

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

# Recursion guard: prevents unbounded classify_prompt() calls when
# Broly agents spawn sub-agents that trigger BAR again.
# Max depth 2: one for initial classification, one for re-routing in execute().
_BAR_MAX_DEPTH = 2
_BAR_DEPTH_ENV = "_GRIP_BAR_DEPTH"
IMPROVEMENT_LOG = CLAUDE_DIR / "improvement-log.jsonl"
ROUTING_METRICS_FILE = CLAUDE_DIR / "data" / "routing-metrics.jsonl"
CALIBRATED_WEIGHTS_FILE = CLAUDE_DIR / "data" / "bar-weights.json"

# Calibration parameters (H1 from #196)
_CALIBRATION_LEARNING_RATE = 0.1
_CALIBRATION_MIN_OUTCOMES = 5  # Minimum outcomes before calibrating
_CALIBRATION_CONVERGENCE_DELTA = 0.01  # Stop when weight changes < this
_CALIBRATION_MAX_CUMULATIVE_DELTA = 0.5  # H2: Cap per-keyword drift to prevent Goodhart

# Power level ordering (index = numeric level for arithmetic)
LEVEL_ORDER = ["thread", "fork", "cluster", "pipeline", "mesh"]

# Known shell command prefixes for H5 command-string detection.
# Used to suppress false positive routing advisories on Bash tool inputs.
_COMMAND_PREFIXES = frozenset({
    "git", "npm", "npx", "yarn", "pnpm", "python3", "python", "pip", "pip3",
    "rg", "fd", "gh", "docker", "kubectl", "make", "cargo", "brew", "apt",
    "chmod", "mkdir", "rmdir", "ls", "cat", "echo", "export", "source",
    "curl", "wget", "ssh", "scp", "rsync", "tar", "zip", "unzip",
    "pytest", "node", "ruby", "java", "go", "rustc", "swift",
    "bash", "zsh", "sh", "bat", "jq", "sed", "awk", "grep",
    "cp", "mv", "rm", "touch", "find", "wc", "sort", "head", "tail",
    "shellcheck", "black", "ruff", "mypy", "eslint", "prettier",
})


@dataclass
class RoutingDecision:
    """Output of the prompt router."""
    power_level: str = "thread"
    subagent_model: str = "opus"
    reasoning_framework: str = "cot"
    confidence: float = 0.0
    signals: Dict[str, Any] = field(default_factory=dict)


# =========================================================================
# TIER 1: HEURISTIC CLASSIFIER
# =========================================================================

# Weighted keyword signals per power level.
# Each entry: (keyword, weight). Score = sum(weight for matching keywords).
HEURISTIC_SIGNALS: Dict[str, List[Tuple[str, float]]] = {
    "mesh": [
        ("full codebase", 0.9),
        ("security audit", 0.8),
        ("entire project", 0.8),
        ("architecture review", 0.8),
        ("full refactor", 0.7),
        ("rewrite", 0.7),
        ("comprehensive analysis", 0.7),
        ("all files", 0.6),
        ("system-wide", 0.6),
        ("cross-cutting", 0.6),
        ("end-to-end", 0.5),
    ],
    "pipeline": [
        ("multi-step", 0.7),
        ("build and deploy", 0.7),
        ("research and implement", 0.6),
        ("test suite", 0.6),
        ("migration", 0.6),
        ("workflow", 0.5),
        ("pipeline", 0.5),
        ("sequential", 0.5),
        ("phased", 0.5),
    ],
    "cluster": [
        ("compare approaches", 0.7),
        ("trade-off", 0.6),
        ("design system", 0.6),
        ("api design", 0.6),
        ("multiple components", 0.5),
        ("parallel", 0.5),
        ("swarm", 0.5),
        ("team", 0.5),
        ("council", 0.5),
    ],
    "fork": [
        ("implement", 0.4),
        ("add feature", 0.4),
        ("create", 0.4),
        ("build", 0.3),
        ("develop", 0.3),
        ("integrate", 0.3),
        ("refactor", 0.3),
        ("extract", 0.3),
    ],
    "thread": [
        ("fix", 0.3),
        ("quick", 0.4),
        ("simple", 0.4),
        ("typo", 0.5),
        ("rename", 0.4),
        ("update", 0.3),
        ("tweak", 0.4),
        ("small", 0.4),
        ("minor", 0.4),
    ],
}


def _load_calibrated_weights() -> Dict[str, Dict[str, float]]:
    """Load calibrated keyword weight adjustments from data/bar-weights.json.

    Returns a dict mapping level -> keyword -> weight_delta.
    Empty dict if no calibrated weights exist.
    """
    data = safe_load_json(CALIBRATED_WEIGHTS_FILE)
    if data is None:
        return {}
    return data.get("weight_deltas", {})


def _get_effective_signals() -> Dict[str, List[Tuple[str, float]]]:
    """Return HEURISTIC_SIGNALS with calibrated weight adjustments applied.

    Merges static weights with learned deltas from bar-weights.json.
    """
    deltas = _load_calibrated_weights()
    if not deltas:
        return HEURISTIC_SIGNALS

    effective: Dict[str, List[Tuple[str, float]]] = {}
    for level, signals in HEURISTIC_SIGNALS.items():
        level_deltas = deltas.get(level, {})
        adjusted = []
        for keyword, weight in signals:
            delta = level_deltas.get(keyword, 0.0)
            new_weight = max(0.01, weight + delta)  # Floor at 0.01 to prevent negatives
            adjusted.append((keyword, round(new_weight, 4)))
        effective[level] = adjusted
    return effective


def _heuristic_classify(text: str) -> Tuple[str, float, Dict[str, float]]:
    """
    Tier 1: Fast keyword-based classification.

    Uses calibrated weights when available (H1 from #196).

    Returns:
        (level, confidence, per_level_scores)
    """
    text_lower = text.lower()
    scores: Dict[str, float] = {}
    signals = _get_effective_signals()

    for level, level_signals in signals.items():
        score = sum(weight for keyword, weight in level_signals if keyword in text_lower)
        scores[level] = score

    # Additional structural signals
    word_count = len(text.split())
    has_code_block = "```" in text

    # Long prompts suggest complexity
    if word_count > 200:
        for level in ("cluster", "pipeline", "mesh"):
            scores[level] = scores.get(level, 0) + 0.3
    elif word_count > 100:
        for level in ("fork", "cluster"):
            scores[level] = scores.get(level, 0) + 0.2

    # Code blocks suggest at least fork-level work
    if has_code_block:
        scores["fork"] = scores.get("fork", 0) + 0.2

    # Question detection (questions tend to be simpler)
    if text.strip().endswith("?") and word_count < 30:
        scores["thread"] = scores.get("thread", 0) + 0.3

    # Find the highest-scoring level
    best_level = "thread"
    best_score = 0.0

    for level in LEVEL_ORDER:
        s = scores.get(level, 0)
        if s > best_score:
            best_score = s
            best_level = level

    # Confidence: normalize score (cap at 1.0).
    # A score of 1.5+ gives high confidence.
    confidence = min(best_score / 1.5, 1.0) if best_score > 0 else 0.1

    return best_level, confidence, scores


# =========================================================================
# TIER 2: SEMANTIC CLASSIFIER (FALLBACK)
# =========================================================================

def _semantic_classify(
    text: str,
    h_level: str,
    h_confidence: float,
) -> Tuple[str, float, Dict[str, Any]]:
    """
    Tier 2: Embedding-based classification using GRIP's semantic pipeline.

    Only called when Tier 1 confidence < 0.6.
    Gracefully falls back to heuristic result on any error.

    Returns:
        (level, confidence, signals_dict)
    """
    signals: Dict[str, Any] = {"tier": "semantic"}

    try:
        from embeddings.classify import get_full_classification
        from embeddings.engine import get_engine

        # get_engine() is the factory for the live backend. Instantiating the
        # EmbeddingEngine base directly raises NotImplementedError, which this
        # function catches and folds into fallback_reason -- so the tier looked
        # healthy while never once reaching an embedding. See engine.py:157.
        engine = get_engine()
        result = get_full_classification(engine, text)

        novelty = result.get("novelty_score", 0.5)
        suggested_agent = result.get("suggested_agent")
        agent_name = ""
        if suggested_agent and isinstance(suggested_agent, dict):
            agent_name = suggested_agent.get("name", "").lower()

        signals["novelty"] = novelty
        signals["agent_match"] = agent_name
        signals["intent"] = result.get("intent", [])

        # Novelty-based level adjustment
        level_idx = LEVEL_ORDER.index(h_level)

        if novelty > 0.7:
            level_idx = min(level_idx + 2, len(LEVEL_ORDER) - 1)
            signals["novelty_boost"] = 2
        elif novelty > 0.5:
            level_idx = min(level_idx + 1, len(LEVEL_ORDER) - 1)
            signals["novelty_boost"] = 1
        else:
            signals["novelty_boost"] = 0

        # Agent match boost
        if agent_name in ("broly", "sprint-loop"):
            level_idx = min(level_idx + 2, len(LEVEL_ORDER) - 1)
            signals["agent_boost"] = 2
        elif agent_name in ("researcher", "architect", "plan"):
            level_idx = min(level_idx + 1, len(LEVEL_ORDER) - 1)
            signals["agent_boost"] = 1
        else:
            signals["agent_boost"] = 0

        semantic_level = LEVEL_ORDER[level_idx]

        # Blended confidence: 40% heuristic + 60% semantic
        semantic_conf = min(novelty + 0.3, 1.0)
        blended = 0.4 * h_confidence + 0.6 * semantic_conf
        signals["blended_formula"] = "0.4*heuristic + 0.6*semantic"

        return semantic_level, blended, signals

    except ImportError:
        logger.debug("Semantic classifier unavailable (embeddings not installed)")
        signals["fallback_reason"] = "import_error"
        return h_level, h_confidence, signals
    except Exception as e:
        logger.debug("Semantic classifier failed: %s", e)
        signals["fallback_reason"] = str(e)
        return h_level, h_confidence, signals


# =========================================================================
# FRICTION BOOST
# =========================================================================

def _check_friction_boost(text: str) -> int:
    """
    Check recent friction events and return level boost (0, 1, or 2).

    Reads improvement-log.jsonl for friction_encountered events in last 3 hours.
    """
    if not IMPROVEMENT_LOG.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
    friction_count = 0

    try:
        with open(IMPROVEMENT_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("trigger") != "friction_encountered":
                    continue

                ts_str = entry.get("timestamp", "")
                try:
                    # Handle both UTC and offset-aware timestamps
                    if "+" in ts_str or ts_str.endswith("Z"):
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    else:
                        ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
                    if ts >= cutoff:
                        friction_count += 1
                except (ValueError, TypeError):
                    continue
    except Exception:
        return 0

    if friction_count >= 3:
        return 2
    if friction_count >= 1:
        return 1
    return 0


# Budget metabolism removed — Anthropic generous usage model (1M context, 2x off-peak).


# =========================================================================
# FRAMEWORK SELECTION
# =========================================================================

def _select_framework(level: str, text: str) -> str:
    """
    Select reasoning framework based on level and task text.

    Delegates to ReasoningRouter.select_framework() when available,
    falls back to level-based defaults.
    """
    try:
        from broly.power_levels import PowerLevel, resolve_power_level
        from broly.reasoning import ReasoningRouter

        pl = resolve_power_level(level)
        router = ReasoningRouter(power_level=pl)
        return router.select_framework(text)
    except Exception:
        # Fallback: level-based defaults
        defaults = {
            "thread": "cot",
            "fork": "cot",
            "cluster": "tot",
            "pipeline": "react",
            "mesh": "reflexion",
        }
        return defaults.get(level, "cot")


# =========================================================================
# METRICS LOGGING
# =========================================================================

def _prompt_hash(text: str) -> str:
    """SHA-256 truncated hash for prompt deduplication."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _append_jsonl_atomic(filepath: Path, entry: dict) -> None:
    """Append a JSON entry to a JSONL file with file locking.

    Uses FileLock with timeout for safe concurrent writes (H3 fix from #196).
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry) + "\n"
    with FileLock(filepath, timeout=5):
        with open(filepath, "a") as f:
            f.write(line)
            f.flush()


def _log_routing_decision(
    decision: RoutingDecision,
    text: str,
    session_id: Optional[str] = None,
) -> None:
    """Append routing decision to metrics JSONL (file-locked)."""
    try:
        entry = {
            "date": datetime.now(timezone.utc).isoformat(),
            "type": "decision",
            "prompt_hash": _prompt_hash(text),
            "power_level": decision.power_level,
            "subagent_model": decision.subagent_model,
            "reasoning_framework": decision.reasoning_framework,
            "confidence": round(decision.confidence, 4),
            "tier": decision.signals.get("tier", "heuristic"),
            "budget_capped": False,
            "friction_boosted": decision.signals.get("friction_boost", 0) > 0,
        }
        if session_id:
            entry["session_id"] = session_id
        _append_jsonl_atomic(ROUTING_METRICS_FILE, entry)
    except Exception as e:
        logger.debug("Failed to log routing decision: %s", e)


def log_routing_outcome(
    prompt_hash: str,
    outcome: str,
    actual_level_needed: Optional[str] = None,
) -> None:
    """
    Log outcome for a routing decision (called by improvement-analyzer).

    Uses file-locked atomic append (H3 fix from #196).

    Args:
        prompt_hash: Hash from original decision record.
        outcome: "correct", "under_routed", "over_routed", "wrong_approach".
        actual_level_needed: The level that would have been correct.
    """
    try:
        entry = {
            "date": datetime.now(timezone.utc).isoformat(),
            "type": "outcome",
            "prompt_hash": prompt_hash,
            "outcome": outcome,
        }
        if actual_level_needed:
            entry["actual_level_needed"] = actual_level_needed
        _append_jsonl_atomic(ROUTING_METRICS_FILE, entry)
    except Exception as e:
        logger.debug("Failed to log routing outcome: %s", e)


# =========================================================================
# WEIGHT CALIBRATION (H1 from #196)
# =========================================================================

def _load_metrics() -> tuple[Dict[str, dict], List[dict]] | None:
    """Load decisions and outcomes from routing-metrics.jsonl."""
    if not ROUTING_METRICS_FILE.exists():
        return None
    decisions: Dict[str, dict] = {}
    outcomes: List[dict] = []
    try:
        with open(ROUTING_METRICS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "decision":
                    ph = entry.get("prompt_hash", "")
                    if ph:
                        decisions[ph] = entry
                elif entry.get("type") == "outcome":
                    outcomes.append(entry)
    except OSError:
        return None
    return decisions, outcomes


def _resolve_direction(outcome: str, predicted_idx: int, actual: str | None) -> int | None:
    """Determine calibration direction from outcome type. Returns None to skip."""
    if outcome == "under_routed":
        return 1
    if outcome == "over_routed":
        return -1
    if outcome == "wrong_approach" and actual:
        actual_idx = LEVEL_ORDER.index(actual)
        return 1 if actual_idx > predicted_idx else -1
    return None


def _apply_adjustments(outcomes, decisions, existing_deltas):
    """Calculate weight adjustments from outcome records."""
    adjustments_count = 0
    max_delta = 0.0
    lr = _CALIBRATION_LEARNING_RATE

    for outcome_rec in outcomes:
        ph = outcome_rec.get("prompt_hash", "")
        outcome = outcome_rec.get("outcome", "")
        actual = outcome_rec.get("actual_level_needed")

        if not ph or outcome == "correct":
            continue
        decision = decisions.get(ph)
        if not decision:
            continue

        predicted = decision.get("power_level", "thread")
        if predicted not in LEVEL_ORDER:
            continue
        if actual and actual not in LEVEL_ORDER:
            continue

        predicted_idx = LEVEL_ORDER.index(predicted)
        direction = _resolve_direction(outcome, predicted_idx, actual)
        if direction is None:
            continue

        for level in LEVEL_ORDER:
            if level not in existing_deltas:
                existing_deltas[level] = {}
            for keyword, _weight in HEURISTIC_SIGNALS.get(level, []):
                if level == predicted and direction < 0:
                    existing_deltas[level][keyword] = existing_deltas[level].get(keyword, 0.0) - lr * 0.5
                    adjustments_count += 1
                elif actual and level == actual and direction > 0:
                    existing_deltas[level][keyword] = existing_deltas[level].get(keyword, 0.0) + lr * 0.5
                    adjustments_count += 1

        max_delta = max(max_delta, abs(lr * direction))

    return adjustments_count, max_delta


def _cap_deltas(existing_deltas) -> int:
    """Goodhart protection: cap cumulative deltas. Returns count of capped values."""
    capped = 0
    for _level, kw_deltas in existing_deltas.items():
        for keyword, delta in list(kw_deltas.items()):
            if abs(delta) > _CALIBRATION_MAX_CUMULATIVE_DELTA:
                kw_deltas[keyword] = _CALIBRATION_MAX_CUMULATIVE_DELTA if delta > 0 else -_CALIBRATION_MAX_CUMULATIVE_DELTA
                capped += 1
    return capped


def _compute_accuracy(outcomes, decisions) -> float:
    """Fraction of matched outcomes that were correct."""
    total = correct = 0
    for rec in outcomes:
        if rec.get("prompt_hash", "") in decisions:
            total += 1
            if rec.get("outcome") == "correct":
                correct += 1
    return round(correct / total, 4) if total > 0 else 0.0


def calibrate_weights() -> Dict[str, Any]:
    """Calibrate heuristic keyword weights from routing outcome data."""
    loaded = _load_metrics()
    if loaded is None:
        return {"outcomes_processed": 0, "error": "no metrics file"}

    decisions, outcomes = loaded
    if len(outcomes) < _CALIBRATION_MIN_OUTCOMES:
        return {
            "outcomes_processed": len(outcomes),
            "error": f"need {_CALIBRATION_MIN_OUTCOMES}+ outcomes, have {len(outcomes)}",
        }

    existing_data = safe_load_json(CALIBRATED_WEIGHTS_FILE)
    existing_deltas = (existing_data or {}).get("weight_deltas", {})

    adjustments_count, max_delta = _apply_adjustments(outcomes, decisions, existing_deltas)
    capped_count = _cap_deltas(existing_deltas)
    accuracy_rate = _compute_accuracy(outcomes, decisions)

    converged = max_delta < _CALIBRATION_CONVERGENCE_DELTA and adjustments_count > 0
    calibration_data = {
        "version": 1,
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
        "outcomes_processed": len(outcomes),
        "adjustments_count": adjustments_count,
        "max_delta": round(max_delta, 6),
        "converged": converged,
        "accuracy_rate": accuracy_rate,
        "deltas_capped": capped_count,
        "weight_deltas": existing_deltas,
    }

    try:
        CALIBRATED_WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CALIBRATED_WEIGHTS_FILE.write_text(json.dumps(calibration_data, indent=2) + "\n")
    except OSError as e:
        return {"outcomes_processed": len(outcomes), "error": f"write failed: {e}"}

    return {
        "outcomes_processed": len(outcomes),
        "adjustments_count": adjustments_count,
        "max_delta": round(max_delta, 6),
        "converged": converged,
        "accuracy_rate": accuracy_rate,
        "deltas_capped": capped_count,
    }


# =========================================================================
# MAIN ENTRY POINT
# =========================================================================

def classify_prompt(
    text: str,
    context: Optional[Dict[str, Any]] = None,
) -> RoutingDecision:
    """
    Classify a prompt and return a RoutingDecision.

    Two-tier classification with friction boost and budget metabolism.

    Args:
        text: The prompt/task text to classify.
        context: Optional dict with:
            - mode_config: Dict from mode YAML broly section
            - session_id: Current session identifier

    Returns:
        RoutingDecision with power_level, model, framework, confidence, signals.
    """
    context = context or {}
    signals: Dict[str, Any] = {}

    # Recursion guard: bail to THREAD if we're too deep
    current_depth = int(os.environ.get(_BAR_DEPTH_ENV, "0"))
    if current_depth >= _BAR_MAX_DEPTH:
        return RoutingDecision(
            power_level="thread",
            subagent_model="opus",
            reasoning_framework="cot",
            confidence=0.0,
            signals={"tier": "recursion_guard", "depth": current_depth},
        )

    # Increment depth for nested calls
    os.environ[_BAR_DEPTH_ENV] = str(current_depth + 1)
    try:
        return _classify_prompt_inner(text, context)
    finally:
        # Restore depth on exit
        os.environ[_BAR_DEPTH_ENV] = str(current_depth)


def _classify_prompt_inner(
    text: str,
    context: Dict[str, Any],
) -> RoutingDecision:
    """Inner classification logic (guarded by recursion depth in classify_prompt)."""
    signals: Dict[str, Any] = {}

    # Skip empty-text classification (C3 fix: _auto_detect_level calls with "")
    if not text.strip():
        return RoutingDecision(
            power_level="thread",
            subagent_model="opus",
            reasoning_framework="cot",
            confidence=0.0,
            signals={"tier": "empty_input", "skipped": True},
        )

    # Step 1: Heuristic classify
    h_level, h_confidence, h_scores = _heuristic_classify(text)
    signals["tier"] = "heuristic"
    signals["heuristic_scores"] = {k: round(v, 3) for k, v in h_scores.items() if v > 0}
    signals["heuristic_level"] = h_level
    signals["heuristic_confidence"] = round(h_confidence, 4)

    level = h_level
    confidence = h_confidence

    # Step 2: Semantic fallback (only when heuristic is uncertain)
    if h_confidence < 0.6 and text.strip():
        s_level, s_confidence, s_signals = _semantic_classify(text, h_level, h_confidence)
        signals["tier"] = "semantic"
        signals["semantic"] = s_signals
        level = s_level
        confidence = s_confidence

    # Step 3: Friction boost
    friction_boost = _check_friction_boost(text)
    if friction_boost > 0:
        level_idx = LEVEL_ORDER.index(level)
        level_idx = min(level_idx + friction_boost, len(LEVEL_ORDER) - 1)
        level = LEVEL_ORDER[level_idx]
        signals["friction_boost"] = friction_boost

    # Step 4: Model selection (budget caps removed — Anthropic generous usage model)
    model = "opus"

    # Step 5: Framework selection
    framework = _select_framework(level, text)

    # Step 6: Mode config floor (minimum level from mode)
    mode_config = context.get("mode_config")
    if mode_config and isinstance(mode_config, dict):
        default_level = mode_config.get("default_level", "")
        if default_level and default_level in LEVEL_ORDER:
            mode_idx = LEVEL_ORDER.index(default_level)
            current_idx = LEVEL_ORDER.index(level)
            if mode_idx > current_idx:
                level = default_level
                signals["mode_floor"] = default_level

    # Build decision
    decision = RoutingDecision(
        power_level=level,
        subagent_model=model,
        reasoning_framework=framework,
        confidence=round(confidence, 4),
        signals=signals,
    )

    # Step 8: Log metrics
    _log_routing_decision(decision, text, context.get("session_id"))

    return decision


def _apply_model_downgrade(model: str, spend_pct: float) -> str:
    """Downgrade model based on budget pressure."""
    if spend_pct >= 90:
        return "haiku"
    if spend_pct >= 80 and model == "opus":
        return "haiku"
    if spend_pct >= 70 and model == "opus":
        return "sonnet"
    return model


# =========================================================================
# COMMAND STRING DETECTION (H5 fix from #196)
# =========================================================================

def _is_likely_command(text: str) -> bool:
    """Detect if text is likely a shell command rather than a natural language prompt.

    Prevents false positive routing advisories when Bash tool inputs contain
    keywords like 'build', 'test suite', 'migration' that match heuristic signals.
    """
    text = text.strip()
    if not text:
        return False

    # First word (strip path prefix for /usr/bin/python3 etc.)
    first_word = text.split()[0].rsplit("/", 1)[-1]
    if first_word in _COMMAND_PREFIXES:
        return True

    # Shell operators strongly indicate command strings
    shell_indicators = (" | ", " && ", " || ", " 2>&1", " >> ", " > /", " < /")
    if any(ind in text for ind in shell_indicators):
        return True

    # High ratio of flags (words starting with -)
    words = text.split()
    if len(words) >= 3:
        flag_count = sum(1 for w in words if w.startswith("-"))
        if flag_count / len(words) > 0.3:
            return True

    return False


# =========================================================================
# HOOK ADVISORY (for Gate R)
# =========================================================================

def get_routing_advisory(tool_name: str, tool_input: str) -> Optional[str]:
    """
    Generate a routing advisory for the context-gate hook.

    Only fires for significant tool calls (Bash > 30 chars, Write, Edit, Task).
    Returns advisory string or None.
    """
    # Skip trivial tools
    if tool_name in ("Read", "Glob", "Grep", "AskUserQuestion",
                     "TaskCreate", "TaskUpdate", "TaskList", "TaskGet"):
        return None

    # For Bash, only consider substantial commands and skip command strings (H5)
    if tool_name == "Bash":
        command = tool_input if isinstance(tool_input, str) else str(tool_input)
        if len(command) < 30:
            return None
        if _is_likely_command(command):
            return None

    # Classify the tool input
    classify_text = tool_input if isinstance(tool_input, str) else json.dumps(tool_input)
    if len(classify_text) < 10:
        return None

    # H5: Skip command-like inputs from non-Bash tools too (e.g. Task tool commands)
    if _is_likely_command(classify_text):
        return None

    try:
        h_level, h_confidence, _ = _heuristic_classify(classify_text)

        # Only advise when recommendation is above THREAD with decent confidence
        if h_level != "thread" and h_confidence >= 0.5:
            pct = int(h_confidence * 100)
            return (
                f"[BAR] Routing: {h_level.upper()} recommended "
                f"(confidence: {pct}%, framework: {_select_framework(h_level, classify_text)}). "
                f"Consider `/broly {h_level}` for this task."
            )
    except Exception:
        pass

    return None


# =========================================================================
# CLI
# =========================================================================

def _cli():
    """CLI entry point for BAR operations."""
    import sys
    if len(sys.argv) < 2:
        print("Usage: prompt_router.py <command>")
        print("Commands: classify <text>, calibrate, weights, status")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "classify":
        text = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        result = classify_prompt(text)
        print(json.dumps({
            "power_level": result.power_level,
            "model": result.subagent_model,
            "framework": result.reasoning_framework,
            "confidence": result.confidence,
            "signals": result.signals,
        }, indent=2))

    elif cmd == "calibrate":
        result = calibrate_weights()
        print(json.dumps(result, indent=2))
        if result.get("converged"):
            print("\nWeights have converged (delta < 0.01). No further calibration needed.")

    elif cmd == "weights":
        deltas = _load_calibrated_weights()
        if not deltas:
            print("No calibrated weights. Run 'calibrate' first.")
        else:
            print(json.dumps(deltas, indent=2))

    elif cmd == "status":
        data = safe_load_json(CALIBRATED_WEIGHTS_FILE)
        if data is not None:
            print(f"Calibrated: {data.get('calibrated_at', 'unknown')}")
            print(f"Outcomes:   {data.get('outcomes_processed', 0)}")
            print(f"Adjustments: {data.get('adjustments_count', 0)}")
            print(f"Max delta:  {data.get('max_delta', 0)}")
            print(f"Converged:  {data.get('converged', False)}")
            total_deltas = sum(
                len(kw_deltas) for kw_deltas in data.get("weight_deltas", {}).values()
            )
            print(f"Adjusted keywords: {total_deltas}")
        else:
            print("No calibration data. Run 'calibrate' first.")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    _cli()
