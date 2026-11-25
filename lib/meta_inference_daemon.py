#!/usr/bin/env python3
"""
meta_inference_daemon.py -- Background RSI engine for GRIP (HAL-backed).

Watches for git commits and runs PrincipledScorer via HALLLMAdapter.
Never rate-limited: routes through HAL server → Groq → Gemini → neutral.

Architecture:
    1. Detects new commits via git log
    2. Extracts diff for commit
    3. Runs PrincipledScorer.score_diff_hybrid() via HALLLMAdapter
    4. Records scores to Cube via record_to_cube()
    5. Appends results to cache/llama-scores.jsonl

Modes:
    - Oneshot: evaluate_last_commit() -- call from hooks
    - Polling: run() -- blocking loop for launchd/systemd

State: ~/.claude/state/meta-inference-state.json (tracks last evaluated commit)
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Use centralized state manager (GRASP: Pure Fabrication)
from lib.state_manager import get_state_manager, CLAUDE_DIR

SCORES_LOG = CLAUDE_DIR / "cache" / "llama-scores.jsonl"
DAEMON_STATE = get_state_manager().path("meta-inference-state")

POLL_INTERVAL_SEC = 30
MAX_DIFF_SIZE = 4000  # chars


@dataclass
class DaemonState:
    """Persistent state for the daemon between runs."""
    last_commit_hash: str = ""
    last_run_at: str = ""
    total_evaluations: int = 0
    running: bool = False


class MetaInferenceDaemon:
    """Background daemon that evaluates code quality on git events.

    Lifecycle:
        daemon = MetaInferenceDaemon()
        daemon.evaluate_last_commit()  # Oneshot (for hook integration)
        # OR
        daemon.run()                   # Blocking poll loop (for launchd)
    """

    def __init__(self, state_path: Path = None, scores_path: Path = None):
        self._scorer = None  # Lazy PrincipledScorer
        self._state_path = state_path or DAEMON_STATE
        self._scores_path = scores_path or SCORES_LOG
        self._state = self._load_state()

    def _get_scorer(self):
        """Lazy initialisation of PrincipledScorer backed by HALLLMAdapter."""
        if self._scorer is None:
            from lib.principled_scorer import PrincipledScorer
            from lib.hal_llm_adapter import HALLLMAdapter
            self._scorer = PrincipledScorer(llama_engine=HALLLMAdapter())
        return self._scorer

    def _load_state(self) -> DaemonState:
        """Load daemon state from disk."""
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text())
                known_fields = {f for f in DaemonState.__dataclass_fields__}
                filtered = {k: v for k, v in data.items() if k in known_fields}
                return DaemonState(**filtered)
            except Exception:
                pass
        return DaemonState()

    def _save_state(self):
        """Persist daemon state to disk."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(asdict(self._state), indent=2))

    @staticmethod
    def get_last_commit_hash(repo_path: str = ".") -> str:
        """Get the latest commit hash from git."""
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%H"],
                capture_output=True, text=True, timeout=10,
                cwd=repo_path,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    @staticmethod
    def get_commit_diff(commit_hash: str, repo_path: str = ".") -> str:
        """Get the diff for a specific commit."""
        try:
            result = subprocess.run(
                ["git", "diff", f"{commit_hash}~1..{commit_hash}"],
                capture_output=True, text=True, timeout=30,
                cwd=repo_path,
            )
            diff = result.stdout if result.returncode == 0 else ""
            return diff[:MAX_DIFF_SIZE]
        except Exception:
            return ""

    def _build_score_entry(self, commit_hash: str, evaluation, session_id: str) -> dict:
        """Build JSONL entry and append to scores log. Returns the entry dict."""
        entry = {
            "status": "evaluated",
            "commit": commit_hash[:12],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "composite": round(evaluation.composite_score, 4),
            "scores": {p: round(ps.score, 4) for p, ps in evaluation.scores.items()},
            "session_id": session_id,
        }
        try:
            self._scores_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._scores_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning("Failed to write scores log: %s", e)
        return entry

    def evaluate_commit(self, commit_hash: str, repo_path: str = ".") -> dict:
        """Evaluate a single commit's diff against design principles.

        Args:
            commit_hash: Full or short git commit hash.
            repo_path: Repository path for git operations.

        Returns:
            Dict with evaluation results or skip reason.
        """
        diff = self.get_commit_diff(commit_hash, repo_path)
        if not diff:
            return {"status": "skip", "reason": "empty diff", "commit": commit_hash[:12]}

        session_id = f"hal-{commit_hash[:8]}"
        scorer = self._get_scorer()

        try:
            evaluation = scorer.score_diff_hybrid(diff, path=repo_path, session_id=session_id)
        except Exception as e:
            logger.error("Evaluation failed for %s: %s", commit_hash[:12], e)
            return {"status": "error", "reason": str(e), "commit": commit_hash[:12]}

        try:
            scorer.record_to_cube(evaluation)
        except Exception as e:
            logger.warning("Cube recording failed: %s", e)

        entry = self._build_score_entry(commit_hash, evaluation, session_id)

        self._state.last_commit_hash = commit_hash
        self._state.last_run_at = datetime.now(timezone.utc).isoformat()
        self._state.total_evaluations += 1
        self._save_state()

        return entry

    def evaluate_last_commit(self, repo_path: str = ".") -> dict:
        """Oneshot: evaluate the most recent commit if not already evaluated.

        Returns:
            Dict with evaluation results or skip reason.
        """
        commit_hash = self.get_last_commit_hash(repo_path)
        if not commit_hash:
            return {"status": "skip", "reason": "no commits found"}
        if commit_hash == self._state.last_commit_hash:
            return {"status": "skip", "reason": "already evaluated", "commit": commit_hash[:12]}
        return self.evaluate_commit(commit_hash, repo_path)

    def run(self, repo_path: str = "."):
        """Blocking poll loop. Intended for launchd/systemd.

        Checks for new commits every POLL_INTERVAL_SEC seconds.
        """
        logger.info("Meta-inference daemon starting (poll interval: %ds)", POLL_INTERVAL_SEC)
        self._state.running = True
        self._save_state()

        try:
            while True:
                try:
                    result = self.evaluate_last_commit(repo_path)
                    if result.get("status") == "evaluated":
                        logger.info(
                            "Evaluated %s: composite=%.2f",
                            result["commit"], result["composite"],
                        )
                except Exception as e:
                    logger.warning("Daemon cycle error: %s", e)
                time.sleep(POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            logger.info("Daemon stopped by user")
        finally:
            self._state.running = False
            self._save_state()

    @property
    def state(self) -> DaemonState:
        """Current daemon state."""
        return self._state


# -- CLI -------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "score-last"
    repo = sys.argv[2] if len(sys.argv) > 2 else "."

    daemon = MetaInferenceDaemon()

    if cmd == "run":
        daemon.run(repo)
    elif cmd == "score-last":
        result = daemon.evaluate_last_commit(repo)
        print(json.dumps(result, indent=2))
    elif cmd == "status":
        print(json.dumps(asdict(daemon.state), indent=2))
    else:
        print(f"Usage: {sys.argv[0]} [run|score-last|status] [repo_path]")
        sys.exit(1)
