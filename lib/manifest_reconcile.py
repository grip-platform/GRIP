#!/usr/bin/env python3
"""
Live-state manifest for GRIP — records branch, last PR SHA, deploy state, pending waves.
Written at each milestone; on startup reconcile vs actual git/deploy reality, flag drift.
WS3-W2 of issue #3023. Extends, does NOT rebuild shipped substrate (C5).
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
MANIFEST_PATH = CLAUDE_DIR / "state" / "manifest.json"

REQUIRED_KEYS = {"branch", "last_pr_sha", "deploy_state", "pending_waves", "updated_at"}


def _git(args, cwd=None):
    cwd = cwd or CLAUDE_DIR
    try:
        return subprocess.run(
            ["git"] + args, capture_output=True, text=True, cwd=cwd, timeout=10
        )
    except subprocess.TimeoutExpired:
        return None


def read_manifest():
    if not MANIFEST_PATH.exists():
        return None
    try:
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def write_manifest(data):
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def snapshot():
    """Capture current git state into a manifest snapshot."""
    head = _git(["rev-parse", "HEAD"])
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    main = _git(["rev-parse", "origin/main"])

    # find open PRs for the current branch
    pr_sha = None
    pr_branch = None
    try:
        r = subprocess.run(
            ["gh", "api", "repos/grip-platform/GRIP/pulls?state=open&per_page=20",
             "--jq", '.[] | {sha: .head.sha, branch: .head.ref, number: .number}'],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0 and r.stdout.strip():
            for line in r.stdout.strip().splitlines():
                parsed = json.loads(line)
                if parsed.get("branch") == (branch.stdout.strip() if branch else None):
                    pr_sha = parsed["sha"]
                    pr_branch = parsed["branch"]
    except Exception:
        pass

    return {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "branch": branch.stdout.strip() if branch and branch.returncode == 0 else "detached",
        "head_sha": head.stdout.strip() if head and head.returncode == 0 else "unknown",
        "origin_main_sha": main.stdout.strip() if main and main.returncode == 0 else "unknown",
        "last_pr_sha": pr_sha,
        "last_pr_branch": pr_branch,
        "deploy_state": "unknown",
        "pending_waves": [],
        "dirty": _git(["status", "--porcelain"]) and bool(
            _git(["status", "--porcelain"]).stdout.strip()
        ),
    }


def reconcile():
    """Read manifest, compare to git reality, log drift."""
    manifest = read_manifest()
    if not manifest:
        return {"drift": False, "reason": "no_manifest", "healthy": True}

    current = snapshot()
    drifts = []
    for key in ("branch", "head_sha", "origin_main_sha"):
        stored = manifest.get(key)
        actual = current.get(key)
        if stored and actual and stored != actual:
            drifts.append(f"{key}: stored={stored} actual={actual}")

    if drifts:
        subprocess.run(
            ["logger", "-t", "grip-manifest", f"MANIFEST DRIFT: {'; '.join(drifts)}"],
            timeout=5,
        )
        return {"drift": True, "reasons": drifts, "healthy": False}

    return {"drift": False, "reason": "in_sync", "healthy": True}


def update(**overrides):
    """Write a new manifest snapshot, merged with overrides."""
    s = snapshot()
    s.update(overrides)
    write_manifest(s)
    return s


def main():
    import argparse
    parser = argparse.ArgumentParser(description="GRIP live-state manifest")
    parser.add_argument("action", nargs="?", default="status",
                        choices=["status", "update", "reconcile", "snapshot"])
    parser.add_argument("--deploy-state", help="Deploy state string")
    parser.add_argument("--pending-waves", nargs="*", default=[], help="Pending wave descriptions")
    args = parser.parse_args()

    if args.action == "snapshot":
        s = snapshot()
        json.dump(s, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif args.action == "update":
        overrides = {}
        if args.deploy_state:
            overrides["deploy_state"] = args.deploy_state
        if args.pending_waves:
            overrides["pending_waves"] = args.pending_waves
        s = update(**overrides)
        json.dump({"written": True, "manifest": s}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif args.action == "reconcile":
        result = reconcile()
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        sys.exit(0 if result["healthy"] else 1)
    elif args.action == "status":
        manifest = read_manifest()
        if manifest:
            json.dump(manifest, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            json.dump({"healthy": True, "reason": "no_manifest"}, sys.stdout)
            sys.stdout.write("\n")


if __name__ == "__main__":
    main()
