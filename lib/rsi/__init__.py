"""RSI Orchestrator Package — decomposed from rsi_orchestrator.py.

Re-exports all public names for backward compatibility.
"""

__all__ = [
    # config
    "CLAUDE_DIR", "_RSIConfig", "_CONFIG",
    "QUEUE_FILE", "METRICS_FILE", "MEMORY_FILE",
    "STOP_SIGNAL", "RALPH_COLLECT", "RSI_LOCK_FILE", "RSI_LOCK_STALE_SECONDS",
    "RSI_CHECKPOINT_FILE", "RSI_CHECKPOINT_STALE_HOURS",
    "DEFAULT_MAX_ITERATIONS", "DEFAULT_MERGE_THRESHOLD",
    "CONVERGENCE_STALL_COUNT",
    # models
    "Candidate", "ConvergenceState",
    # queue
    "load_queue", "save_queue", "get_pending_candidates", "mark_candidate",
    # mining
    "mine_ralph_reviews", "mine_github_issues", "mine_memory_todos",
    # metrics
    "detect_session_type", "categorize_failure", "record_metric", "get_session_summary",
    # convergence
    "load_convergence", "save_convergence", "record_ralph_score",
    "_PATTERN_IMPACT", "create_candidates_from_ralph", "feed_ralph_result",
    "check_stop_signal", "clear_stop_signal", "set_stop_signal",
    # lock
    "_caffeinate_start_for_rsi", "_caffeinate_stop",
    "acquire_rsi_lock", "release_rsi_lock", "check_rsi_lock",
    # audit
    "GATE_CATEGORIES", "_GATE_TO_CATEGORY", "classify_gate_error", "audit_gate_errors",
    # retest
    "RetestResult", "pre_merge_retest", "post_merge_validate",
    # checkpoint
    "RSICheckpoint", "save_checkpoint", "load_checkpoint",
    "clear_checkpoint", "checkpoint_resume_phase",
    "CloseoutResult", "run_closeout",
    # pr_size
    "MAX_PR_FILES", "MAX_PR_LINES", "analyze_pr_size", "split_pr_by_domain",
    # cli
    "cli_main",
]

# config
from .config import (
    CLAUDE_DIR,
    _RSIConfig,
    _CONFIG,
    QUEUE_FILE,
    METRICS_FILE,
    MEMORY_FILE,
    STOP_SIGNAL,
    RALPH_COLLECT,
    RSI_LOCK_FILE,
    RSI_LOCK_STALE_SECONDS,
    RSI_CHECKPOINT_FILE,
    RSI_CHECKPOINT_STALE_HOURS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MERGE_THRESHOLD,
    CONVERGENCE_STALL_COUNT,
)

# models
from .models import Candidate, ConvergenceState

# queue
from .queue import load_queue, save_queue, get_pending_candidates, mark_candidate

# mining
from .mining import mine_ralph_reviews, mine_github_issues, mine_memory_todos

# metrics (before convergence/lock/checkpoint to avoid circular imports)
from .metrics import detect_session_type, categorize_failure, record_metric, get_session_summary

# convergence
from .convergence import (
    load_convergence, save_convergence, record_ralph_score,
    _PATTERN_IMPACT, create_candidates_from_ralph, feed_ralph_result,
    check_stop_signal, clear_stop_signal, set_stop_signal,
)

# lock
from .lock import (
    _caffeinate_start_for_rsi, _caffeinate_stop,
    acquire_rsi_lock, release_rsi_lock, check_rsi_lock,
)

# audit
from .audit import GATE_CATEGORIES, _GATE_TO_CATEGORY, classify_gate_error, audit_gate_errors

# retest
from .retest import RetestResult, pre_merge_retest, post_merge_validate

# checkpoint
from .checkpoint import (
    RSICheckpoint, save_checkpoint, load_checkpoint,
    clear_checkpoint, checkpoint_resume_phase,
    CloseoutResult, run_closeout,
)

# pr_size
from .pr_size import MAX_PR_FILES, MAX_PR_LINES, analyze_pr_size, split_pr_by_domain

# cli
from .cli import cli_main
