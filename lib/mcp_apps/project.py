"""Project-level MCP app generators: circuit dashboards, dependencies, workflows."""

import json
import os
import glob as glob_mod
import tempfile
from datetime import datetime, timezone, timedelta

from .common import _run_git, _run_gh, CLAUDE_DIR, REPO_SLUG


def generate_project_circuit(metrics: dict = None) -> dict:
    """Generate grip_circuit_dashboard data from project health metrics.

    Args:
        metrics: Dict with keys like prs_merged_7d, issues_open, commits_7d, etc.
                 If None, collects live metrics.

    Returns:
        Dict with structure: {title, description, circuits: [{id, label, status, value, detail}]}
    """
    if metrics is None:
        # Collect minimal metrics
        metrics = {}

        # PR metrics
        prs_json = _run_gh(["pr", "list", "-R", REPO_SLUG, "--state", "open", "--json", "number", "--limit", "50"])
        metrics["prs_open"] = len(json.loads(prs_json)) if prs_json else 0

        merged_json = _run_gh(["pr", "list", "-R", REPO_SLUG, "--state", "merged", "--json", "number,mergedAt", "--limit", "50"])
        if merged_json:
            try:
                merged = json.loads(merged_json)
                since_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
                metrics["prs_merged_7d"] = sum(1 for p in merged if p.get("mergedAt", "") >= since_7d)
            except json.JSONDecodeError:
                metrics["prs_merged_7d"] = 0
        else:
            metrics["prs_merged_7d"] = 0

        # Issue metrics
        issues_json = _run_gh(["issue", "list", "-R", REPO_SLUG, "--state", "open", "--json", "number,labels", "--limit", "100"])
        if issues_json:
            try:
                issues = json.loads(issues_json)
                metrics["issues_open"] = len(issues)
                metrics["bugs_open"] = sum(1 for i in issues if any(l.get("name") == "bug" for l in i.get("labels", [])))
            except json.JSONDecodeError:
                metrics["issues_open"] = 0
                metrics["bugs_open"] = 0
        else:
            metrics["issues_open"] = 0
            metrics["bugs_open"] = 0

        # Commit metrics
        log = _run_git(["log", "--since=7 days ago", "--oneline", "--no-merges"])
        metrics["commits_7d"] = len(log.strip().split("\n")) if log else 0

    def status_for(value, good_threshold, warn_threshold):
        if value >= good_threshold:
            return "nominal"
        if value >= warn_threshold:
            return "degraded"
        return "critical"

    circuits = []

    # PR Flow
    pr_flow_status = status_for(metrics.get("prs_merged_7d", 0), 5, 2)
    circuits.append({
        "id": "pr_flow",
        "label": "PR Flow",
        "status": pr_flow_status,
        "value": f"{metrics.get('prs_merged_7d', 0)} merged / {metrics.get('prs_open', 0)} open",
        "detail": f"{metrics.get('prs_stale', 0)} stale" if "prs_stale" in metrics else "7 day window"
    })

    # Issue Health
    bugs = metrics.get("bugs_open", 0)
    issue_status = "nominal" if bugs < 3 else "degraded" if bugs < 6 else "critical"
    circuits.append({
        "id": "issue_health",
        "label": "Issue Health",
        "status": issue_status,
        "value": f"{metrics.get('issues_open', 0)} open",
        "detail": f"{bugs} bugs"
    })

    # Velocity
    velocity_status = status_for(metrics.get("commits_7d", 0), 20, 10)
    circuits.append({
        "id": "velocity",
        "label": "Velocity",
        "status": velocity_status,
        "value": f"{metrics.get('commits_7d', 0)} commits",
        "detail": "Last 7 days"
    })

    # Feature Ratio (if provided)
    if "feature_ratio" in metrics:
        ratio = metrics["feature_ratio"]
        circuits.append({
            "id": "feature_ratio",
            "label": "Feature Ratio",
            "status": status_for(ratio * 100, 40, 25),
            "value": f"{ratio:.0%} features",
            "detail": f"{metrics.get('feature_count_7d', 0)} features / {metrics.get('infra_count_7d', 0)} infra"
        })

    # Test Health (if provided)
    if "test_count" in metrics:
        test_status = "nominal" if metrics["test_count"] > 1000 else "degraded"
        circuits.append({
            "id": "test_health",
            "label": "Test Suite",
            "status": test_status,
            "value": f"{metrics['test_count']} tests",
            # A dict default of 100 fabricated a perfect result; an absent
            # measurement renders as exactly that (sweep wf_eec3b22e-a92).
            "detail": (f"{metrics['test_pass_rate']:.0f}% pass rate"
                       if "test_pass_rate" in metrics
                       else "pass rate not measured")
        })

    # Overall score (if provided)
    if "overall_score" in metrics:
        overall_status = "nominal" if metrics["overall_score"] >= 80 else "degraded" if metrics["overall_score"] >= 60 else "critical"
        circuits.append({
            "id": "overall",
            "label": "Overall Health",
            "status": overall_status,
            "value": f"{metrics['overall_score']}/100",
            "detail": metrics.get("status", "unknown").upper()
        })

    return {
        "title": "GRIP Project Health",
        "description": f"Overall: {metrics.get('overall_score', 'N/A')}/100",
        "circuits": circuits
    }


def _session_breakers() -> list:
    """Context-usage and steering-pipeline circuit breakers."""
    log_1h = _run_git(["log", "--since=1 hour ago", "--oneline", "--no-merges"])
    msg_count = len(log_1h.strip().split("\n")) if log_1h.strip() else 0
    # Heuristic: >40 commits/hr suggests heavy context, >25 is moderate
    ctx_state = "open" if msg_count > 40 else "half-open" if msg_count > 25 else "closed"

    tmp_dir = tempfile.gettempdir()
    injection_files = glob_mod.glob(os.path.join(tmp_dir, "grip-injected-*.txt"))
    active_injections = sum(1 for f in injection_files if os.path.getsize(f) > 0)
    steer_state = "closed" if active_injections == 0 else "half-open" if active_injections == 1 else "open"
    return [
        {
            "id": "context_usage",
            "label": "Context Usage",
            "state": ctx_state,
            "triggerCount": msg_count,
            "lastTrigger": datetime.now(timezone.utc).isoformat(),
        },
        {
            "id": "steering_pipeline",
            "label": "Steering Pipeline",
            "state": steer_state,
            "triggerCount": active_injections,
        },
    ]


_ERROR_FIELDS = ("context", "event", "level", "error", "status")


def _is_error_event(line: str) -> bool:
    """True only when a SEMANTIC field marks an error (AGORA #4774) — a
    substring hit in a filename, a description, or a structured value's repr
    must not count (the real producer writes plain strings)."""
    try:
        entry = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(entry, dict):
        return False
    return any(isinstance(v := entry.get(k), str) and "error" in v.lower()
               for k in _ERROR_FIELDS)


def _scan_metrics_ledger(metrics_path) -> tuple:
    """(tool_calls, file_reads, git_ops, error_events) from metrics.jsonl.

    error_events counts entries whose SEMANTIC fields carry an error marker
    (e.g. the auto-learn bridge's context=bash_execution_error) — a real
    producer, so errorCount is a measurement, not a constant.
    """
    tool_calls, file_reads, git_ops, error_events = 0, 0, 0, 0
    if not metrics_path.exists():
        return tool_calls, file_reads, git_ops, error_events
    try:
        with open(metrics_path) as f:
            for line in f:
                if _is_error_event(line):
                    error_events += 1
                if '"tool_call"' in line:
                    tool_calls += 1
                elif '"file_read"' in line:
                    file_reads += 1
                elif '"git_op"' in line:
                    git_ops += 1
    except (OSError, json.JSONDecodeError):
        pass
    return tool_calls, file_reads, git_ops, error_events


def _recent_checkpoints() -> list:
    """Last 5 commits as rollback checkpoints."""
    checkpoints = []
    log_recent = _run_git(["log", "--oneline", "--no-merges", "-5", "--format=%H %aI %s"])
    if log_recent:
        for line in log_recent.strip().split("\n"):
            parts = line.split(" ", 2)
            if len(parts) >= 3:
                checkpoints.append({
                    "id": parts[0][:8],
                    "label": parts[2][:60],
                    "timestamp": parts[1],
                })
    return checkpoints


def generate_session_circuit() -> dict:
    """Generate grip_circuit_dashboard for real-time session health monitoring.

    Shows context usage circuit breaker, steering pipeline status,
    recent git commits as rollback checkpoints, and rate limit gauges.
    """
    tool_calls, file_reads, git_ops, error_events = _scan_metrics_ledger(
        CLAUDE_DIR / "metrics.jsonl")
    rate_limits = [
        {"id": "tool_calls", "label": "Tool Calls", "consumed": tool_calls, "limit": 500, "unit": "ops/session"},
        {"id": "file_reads", "label": "File Reads", "consumed": file_reads, "limit": 200, "unit": "reads/session"},
        {"id": "git_ops", "label": "Git Operations", "consumed": git_ops, "limit": 100, "unit": "ops/session"},
    ]

    commits_today = _run_git(["log", "--since=today", "--oneline", "--no-merges"])
    op_count = len(commits_today.strip().split("\n")) if commits_today.strip() else 0

    return {
        "title": "GRIP Session Health",
        "circuitBreakers": _session_breakers(),
        "rateLimits": rate_limits,
        "checkpoints": _recent_checkpoints(),
        "operationCount": op_count,
        # Measured from the ledger — the old literal 0 sat beside genuinely
        # measured tallies and read as a count that was never made.
        "errorCount": error_events,
    }


def generate_module_dependency_map() -> dict:
    """Generate grip_concept_map showing Python module import dependencies.

    Scans lib/*.py for import statements and builds a dependency graph
    showing which modules import which others.
    """
    lib_dir = CLAUDE_DIR / "lib"
    nodes = []
    edges = []
    seen_modules = set()

    # Scan all Python files in lib/
    py_files = sorted(lib_dir.glob("*.py"))

    for py_file in py_files:
        mod_name = py_file.stem
        if mod_name.startswith("_") and mod_name != "_compat":
            continue
        seen_modules.add(mod_name)

    # Parse imports from each module
    import_map = {}
    for py_file in py_files:
        mod_name = py_file.stem
        if mod_name not in seen_modules:
            continue
        imports = set()
        try:
            with open(py_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("from ") and " import " in line:
                        target = line.split("from ", 1)[1].split(" import")[0].strip().replace("-", "_")
                        if target in seen_modules:
                            imports.add(target)
                    elif line.startswith("import "):
                        target = line.split("import ", 1)[1].split(",")[0].split(" as ")[0].strip().replace("-", "_")
                        if target in seen_modules:
                            imports.add(target)
        except OSError:
            continue
        import_map[mod_name] = imports

    # Build nodes -- categorize by import count (hub vs leaf)
    import_counts = {}
    for mod, deps in import_map.items():
        for dep in deps:
            import_counts[dep] = import_counts.get(dep, 0) + 1

    for mod_name in seen_modules:
        incoming = import_counts.get(mod_name, 0)
        outgoing = len(import_map.get(mod_name, set()))
        if incoming >= 5:
            node_type = "hub"
        elif outgoing == 0 and incoming == 0:
            node_type = "orphan"
        else:
            node_type = "module"
        nodes.append({"id": mod_name, "label": mod_name.replace("_", "-"), "type": node_type})

    # Build edges
    for mod_name, deps in import_map.items():
        for dep in deps:
            edges.append({"from": mod_name, "to": dep, "label": "imports", "style": "solid"})

    return {"title": "GRIP Module Dependencies", "nodes": nodes, "edges": edges}


def generate_dependency_circuit(report_data: dict = None) -> dict:
    """Generate grip_circuit_dashboard from dependency health report.

    Args:
        report_data: Dict with health_score, outdated_count, vulnerable_count, etc.
                     If None, runs dependency_health against current directory.

    Returns:
        Dict for grip_circuit_dashboard: {title, circuitBreakers, rateLimits}
    """
    if report_data is None:
        try:
            from dependency_health import analyze_dependencies, format_report_json
            import json as _json
            report = analyze_dependencies(".")
            report_data = _json.loads(format_report_json(report))
        except Exception as exc:  # noqa: BLE001 — ANY failure is UNAVAILABLE
            # The old fallback substituted a hard-coded perfect-health
            # dashboard (health 100, vulns closed/0) for any failure — an
            # affirmative verdict tracing to no measurement (wf_eec3b22e-a92).
            return _dependency_unavailable_dashboard(exc)

    return _dependency_dashboard(report_data)


def _dependency_unavailable_dashboard(exc: Exception) -> dict:
    """Honest UNAVAILABLE state — never a fabricated clean scan."""
    return {
        "title": "Dependency Health Dashboard — UNAVAILABLE",
        "circuitBreakers": [{
            "id": "analyzer",
            "label": f"Dependency analyzer did not run ({type(exc).__name__})",
            "state": "open",
            "triggerCount": 1,
        }],
        "rateLimits": [],
        "operationCount": 0,
        "errorCount": 1,
    }


def _dependency_breakers(vuln_count: int, outdated_count: int,
                         unavailable: list) -> list:
    """Breakers that never report 'closed' for an audit that did not run."""
    vuln_audits_missing = [a for a in unavailable if str(a).endswith("audit")]
    if vuln_count > 0:
        vuln_state, vuln_label = "open", "Vulnerabilities"
    elif vuln_audits_missing:
        vuln_state, vuln_label = "half-open", "Vulnerabilities (unaudited)"
    else:
        vuln_state, vuln_label = "closed", "Vulnerabilities"
    breakers = [
        {"id": "vulns", "label": vuln_label, "state": vuln_state, "triggerCount": vuln_count},
        {"id": "outdated", "label": "Outdated Packages",
         "state": "half-open" if outdated_count > 5 else "closed",
         "triggerCount": outdated_count},
    ]
    if unavailable:
        breakers.append({
            "id": "unaudited",
            "label": f"Unaudited: {', '.join(unavailable)} did not run",
            "state": "half-open",
            "triggerCount": len(unavailable),
        })
    return breakers


def _dependency_dashboard(report_data: dict) -> dict:
    """Render the dashboard from a real report (health_score may be None —
    nothing measured — per dependency_health's honesty contract)."""
    health = report_data.get("health_score")
    vuln_count = report_data.get("vulnerable_count", 0)
    outdated_count = report_data.get("outdated_count", 0)
    total = report_data.get("total_packages", 0)
    unavailable = report_data.get("audits_unavailable", []) or []

    health_gauge = (
        {"id": "health", "label": "Health Score", "consumed": round(100 - health),
         "limit": 100, "unit": "risk pts"}
        if health is not None else
        {"id": "health", "label": "Health Score (n/a — nothing measured)",
         "consumed": 0, "limit": 100, "unit": "risk pts"}
    )
    return {
        "title": "Dependency Health Dashboard",
        "circuitBreakers": _dependency_breakers(vuln_count, outdated_count, unavailable),
        "rateLimits": [
            health_gauge,
            {
                "id": "coverage",
                "label": "Packages Scanned",
                "consumed": total,
                "limit": max(total, 100),
                "unit": "packages",
            },
        ],
        "operationCount": total,
        "errorCount": vuln_count,
    }


def generate_workflow_health_circuit() -> dict:
    """Generate circuit dashboard showing health of all Copilot workflows.

    Runs the workflow validator and presents results as a circuit dashboard
    with green/yellow/red indicators per workflow.
    """
    import yaml

    workflows_dir = CLAUDE_DIR / "data" / "aide" / "workflows"
    if not workflows_dir.exists():
        return {"title": "Workflow Health", "circuits": [], "summary": "No workflows found"}

    valid_types = {"fetch", "analyze", "decide", "notify", "escalate", "learn",
                   "create_issue", "sprint_loop", "rsi_loop", "record_outcome"}

    circuits = []
    total = 0
    healthy = 0

    for fp in sorted(workflows_dir.glob("*.yaml")):
        total += 1
        try:
            with open(fp) as f:
                wf = yaml.safe_load(f)
        except Exception:
            circuits.append({
                "name": fp.stem,
                "status": "red",
                "value": "YAML error",
                "label": "Parse failure",
            })
            continue

        if not isinstance(wf, dict):
            circuits.append({"name": fp.stem, "status": "red", "value": "Invalid", "label": "Not a dict"})
            continue

        workflow_block = wf.get("workflow", {})
        steps = workflow_block.get("steps", []) if isinstance(workflow_block, dict) else []
        step_count = len(steps)
        issues = []

        step_ids = set()
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            sid = step.get("name", step.get("id", ""))
            if sid:
                step_ids.add(sid)

            st = step.get("type", "")
            if st and st not in valid_types:
                issues.append(f"unknown type '{st}'")

            if st == "decide":
                params = step.get("params", {}) if isinstance(step.get("params"), dict) else {}
                if not (step.get("what") or step.get("description") or params.get("what") or params.get("description")):
                    issues.append(f"{sid}: missing what/description")

            for ref in ([step.get("input")] if isinstance(step.get("input"), str) else step.get("input", [])):
                if ref and ref not in step_ids:
                    issues.append(f"{sid}: bad ref '{ref}'")

        if not issues:
            healthy += 1
            status = "green"
            label = f"{step_count} steps"
        elif len(issues) <= 2:
            status = "yellow"
            label = "; ".join(issues[:2])
        else:
            status = "red"
            label = f"{len(issues)} issues"

        name = wf.get("name", wf.get("id", fp.stem))
        schedule = wf.get("schedule", {})
        sched_type = schedule.get("type", "unknown") if isinstance(schedule, dict) else "unknown"

        circuits.append({
            "name": name[:30],
            "status": status,
            "value": f"{step_count} steps",
            "label": label,
            "metadata": {"schedule": sched_type, "file": fp.name},
        })

    return {
        "title": f"Workflow Health Dashboard ({healthy}/{total} healthy)",
        "description": "Copilot workflow YAML validation status",
        "circuits": circuits,
        "summary": {
            "total": total,
            "healthy": healthy,
            "warnings": sum(1 for c in circuits if c["status"] == "yellow"),
            "errors": sum(1 for c in circuits if c["status"] == "red"),
            "health_pct": round(healthy / max(total, 1) * 100, 1),
        },
    }
