#!/usr/bin/env python3
"""
poll_ralph_with_fallback -- Polls Ralph Wiggum PR review with timeout and retry.

Issue #136. RSI loops fail 10-20% of the time when Ralph times out.
This module provides reliable Ralph polling with exponential backoff
and gh workflow dispatch retry.

Usage:
    from lib.ralph_poller import poll_ralph_with_fallback

    score = poll_ralph_with_fallback(pr_number=211, timeout_seconds=300)
    if score is None:
        logger.warning("Ralph unavailable after retries")
    else:
        logger.info("Ralph score: %d", score)
"""
import json
import logging
import re
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

REPO = "grip-platform/GRIP"
RALPH_WORKFLOW = "ralph-wiggum.yml"
RALPH_LOGIN = "ralph-wiggum[bot]"
DEFAULT_TIMEOUT = 300        # 5 minutes
DEFAULT_POLL_INTERVAL = 15   # check every 15 seconds
MAX_DISPATCH_RETRIES = 2     # retry dispatch up to 2 times


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run a subprocess, return (returncode, stdout, stderr).

    Raises:
        RuntimeError: If gh CLI is not found or command fails unexpectedly.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        raise RuntimeError(
            f"gh CLI not found -- install GitHub CLI: {cmd}"
        ) from None
    except subprocess.TimeoutExpired:
        return 1, "", f"subprocess timeout after {timeout}s"
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"gh command failed (exit {e.returncode}): {e.stderr}"
        ) from e


def _get_latest_ralph_comment(pr_number: int) -> Optional[int]:
    """Return the composite score from Ralph's latest comment, or None.

    Filters comments by Ralph's login to handle multi-comment scenarios
    where Ralph is not the last commenter.
    """
    code, out, _ = _run([
        "gh", "pr", "view", str(pr_number),
        "--repo", REPO,
        "--json", "comments",
    ])
    if code != 0 or not out:
        return None

    try:
        data = json.loads(out)
    except (json.JSONDecodeError, ValueError):
        logger.debug("Failed to parse comments JSON for PR #%d", pr_number)
        return None

    comments = data.get("comments", [])
    # Walk backwards to find the latest Ralph comment
    for comment in reversed(comments):
        author = comment.get("author", {}).get("login", "")
        if author == RALPH_LOGIN:
            body = comment.get("body", "")
            match = re.search(
                r"(?:Composite Score|Score):\s*(\d+)/100", body
            )
            if match:
                return int(match.group(1))

    return None


def _dispatch_ralph(pr_number: int) -> bool:
    """Trigger Ralph workflow via gh workflow run. Returns True on success."""
    code, _, err = _run([
        "gh", "workflow", "run", RALPH_WORKFLOW,
        "--repo", REPO,
        "-f", f"pr_number={pr_number}",
    ])
    return code == 0


def poll_ralph_with_fallback(
    pr_number: int,
    timeout_seconds: int = DEFAULT_TIMEOUT,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
    dispatch_retries: int = MAX_DISPATCH_RETRIES,
    verbose: bool = False,
    _clock=None,
) -> Optional[int]:
    """Poll Ralph for a PR score, retrying dispatch on timeout.

    Args:
        pr_number: GitHub PR number to poll (must be positive).
        timeout_seconds: Total seconds to wait per dispatch attempt (must be positive).
        poll_interval: Seconds between score checks (must be positive).
        dispatch_retries: Max number of workflow re-dispatches on timeout (must be >= 0).
        verbose: Enable debug-level logging output.
        _clock: Injectable time function for testing. Defaults to time.time.

    Returns:
        Composite score (0-100) from Ralph's latest comment, or None if
        all retries exhausted without a score.

    Raises:
        ValueError: If any numeric parameter is out of valid range.
    """
    if pr_number <= 0:
        raise ValueError(f"pr_number must be positive, got {pr_number}")
    if timeout_seconds <= 0:
        raise ValueError(f"timeout_seconds must be positive, got {timeout_seconds}")
    if poll_interval <= 0:
        raise ValueError(f"poll_interval must be positive, got {poll_interval}")
    if dispatch_retries < 0:
        raise ValueError(f"dispatch_retries must be non-negative, got {dispatch_retries}")

    clock = _clock or time.time

    for attempt in range(dispatch_retries + 1):
        if attempt > 0:
            logger.info(
                "Timeout on attempt %d, re-dispatching Ralph...", attempt
            )
            if not _dispatch_ralph(pr_number):
                logger.warning(
                    "Failed to dispatch Ralph on attempt %d", attempt + 1
                )
                continue
            # Brief wait for workflow to start
            time.sleep(10)

        deadline = clock() + timeout_seconds
        logger.info(
            "Polling PR #%d (attempt %d/%d, timeout=%ds)",
            pr_number, attempt + 1, dispatch_retries + 1, timeout_seconds,
        )

        while clock() < deadline:
            score = _get_latest_ralph_comment(pr_number)
            if score is not None:
                logger.info("Got Ralph score: %d/100", score)
                return score

            remaining = int(deadline - clock())
            logger.debug("No score yet, %ds remaining...", remaining)
            time.sleep(poll_interval)

        logger.info("Timeout reached on attempt %d", attempt + 1)

    logger.warning("All retries exhausted -- Ralph unavailable")
    return None


def main() -> int:
    """Entry point for ralph_poller CLI.

    Exit codes:
        0: Ralph score >= 80 (success)
        1: Ralph score < 80 (below threshold)
        2: Polling timed out without a Ralph response
        3: Input validation or runtime error
    """
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="[ralph_poller] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Poll Ralph Wiggum with retry")
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--retries", type=int, default=MAX_DISPATCH_RETRIES)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger(__name__).setLevel(logging.DEBUG)

    try:
        score = poll_ralph_with_fallback(
            pr_number=args.pr_number,
            timeout_seconds=args.timeout,
            poll_interval=args.poll_interval,
            dispatch_retries=args.retries,
            verbose=args.verbose,
        )
    except ValueError as e:
        logger.error("Validation error: %s", e)
        return 3
    except RuntimeError as e:
        logger.error("Runtime error: %s", e)
        return 3

    if score is None:
        logger.warning("TIMEOUT: Ralph did not respond")
        return 2

    logger.info("SCORE: %d", score)
    return 0 if score >= 80 else 1


if __name__ == "__main__":
    raise SystemExit(main())
