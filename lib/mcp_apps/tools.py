"""Tool-oriented MCP app generators: smart commit, threat matrix, pair suggestions."""

from .common import _run_git, CLAUDE_DIR


def generate_smart_commit_options(diff_analysis: dict = None) -> dict:
    """Generate grip_options_picker data for commit message suggestions.

    Args:
        diff_analysis: Dict with options list, each having type, subject, body.
                       If None, tries to run git diff --staged and analyze.

    Returns:
        Dict for grip_options_picker: {title, description, options}
    """
    if diff_analysis is None:
        staged = _run_git(["diff", "--staged", "--stat"])
        if staged:
            lines = staged.strip().split("\n")
            file_count = len(lines) - 1 if lines else 0
            summary_line = lines[-1] if lines else ""
            diff_analysis = {
                "options": [
                    {"type": "feat", "subject": f"add changes across {file_count} files",
                     "body": summary_line},
                    {"type": "fix", "subject": f"update {file_count} files",
                     "body": summary_line},
                    {"type": "chore", "subject": f"maintenance across {file_count} files",
                     "body": summary_line},
                ]
            }
        else:
            diff_analysis = {"options": []}

    options = []
    for idx, opt in enumerate(diff_analysis.get("options", [])[:3]):
        commit_type = opt.get("type", "chore")
        subject = opt.get("subject", "update")
        body = opt.get("body", "")

        options.append({
            "id": f"option-{idx}",
            "label": f"{commit_type}: {subject}"[:80],
            "description": body[:120] if body else f"Commit as {commit_type}",
            "recommended": idx == 0,
        })

    return {
        "title": "Smart Commit Options",
        "description": f"{len(options)} suggestion(s) based on staged changes",
        "options": options,
    }


def generate_threat_matrix() -> dict:
    """Generate grip_threat_matrix with auto-detected codebase entry points.

    Scans lib/ for WebSocket handlers, CLI entrypoints, file I/O operations,
    subprocess calls, and eval/exec usage to populate STRIDE entry points.
    """
    lib_dir = CLAUDE_DIR / "lib"
    entry_points = []

    # 1. WebSocket endpoints
    ws_files = []
    for py_file in lib_dir.glob("*.py"):
        try:
            content = py_file.read_text(errors="replace")
            if "websockets" in content or "WebSocket" in content:
                ws_files.append(py_file.stem)
        except OSError:
            continue
    if ws_files:
        entry_points.append({
            "id": "websocket",
            "label": "WebSocket Servers",
            "description": f"Real-time connections in: {', '.join(ws_files[:5])}. Spoofing, tampering, and DoS risks.",
        })

    # 2. CLI entrypoints (files with if __name__ == "__main__")
    cli_files = []
    for py_file in lib_dir.glob("*.py"):
        try:
            content = py_file.read_text(errors="replace")
            if '__name__' in content and '"__main__"' in content:
                cli_files.append(py_file.stem)
        except OSError:
            continue
    if cli_files:
        entry_points.append({
            "id": "cli",
            "label": "CLI Entrypoints",
            "description": f"{len(cli_files)} scripts with __main__ blocks. Input validation and argument injection risks.",
        })

    # 3. Subprocess calls (shell=True is high risk)
    shell_true_files = []
    subprocess_files = []
    for py_file in lib_dir.glob("*.py"):
        try:
            content = py_file.read_text(errors="replace")
            if "subprocess" in content:
                subprocess_files.append(py_file.stem)
                if "shell=True" in content:
                    shell_true_files.append(py_file.stem)
        except OSError:
            continue
    if subprocess_files:
        desc = f"{len(subprocess_files)} files use subprocess"
        if shell_true_files:
            desc += f", {len(shell_true_files)} with shell=True ({', '.join(shell_true_files[:3])})"
        entry_points.append({"id": "subprocess", "label": "Subprocess Execution", "description": desc})

    # 4. File I/O (YAML/JSON loading, config files)
    config_files = []
    for py_file in lib_dir.glob("*.py"):
        try:
            content = py_file.read_text(errors="replace")
            if "yaml.load" in content or "yaml.safe_load" in content or "json.load(" in content:
                config_files.append(py_file.stem)
        except OSError:
            continue
    if config_files:
        entry_points.append({
            "id": "config_parsing",
            "label": "Config/Data Parsing",
            "description": f"{len(config_files)} files parse YAML/JSON. Deserialization and path traversal risks.",
        })

    # 5. eval/exec usage
    eval_files = []
    for py_file in lib_dir.glob("*.py"):
        try:
            content = py_file.read_text(errors="replace")
            if "eval(" in content or "exec(" in content:
                eval_files.append(py_file.stem)
        except OSError:
            continue
    if eval_files:
        entry_points.append({
            "id": "dynamic_eval",
            "label": "Dynamic Code Execution",
            "description": f"eval/exec in: {', '.join(eval_files[:5])}. Code injection risk.",
        })

    # 6. Steering injection (GRIP-specific)
    entry_points.append({
        "id": "steering",
        "label": "Steering Injection Pipeline",
        "description": "File-based context injection via /tmp/grip-injected-*.txt. Tampering and elevation risks.",
    })

    return {"title": "GRIP Security Threat Model", "entryPoints": entry_points}


def generate_pair_suggestions_table(pair_data: dict = None) -> dict:
    """Generate grip_data_table from pair matcher suggestions.

    Args:
        pair_data: Dict with pair suggestions. If None, uses defaults.

    Returns:
        Dict for grip_data_table MCP app.
    """
    if pair_data is None:
        pair_data = {"suggestions": []}

    rows = []
    for i, suggestion in enumerate(pair_data.get("suggestions", [])[:10]):
        rows.append({
            "rank": i + 1,
            "pair": f"{suggestion.get('dev1', '?')} + {suggestion.get('dev2', '?')}",
            "score": f"{suggestion.get('overall_score', 0):.0f}",
            "reason": suggestion.get("top_reason", "complementary skills"),
            "area": suggestion.get("suggested_task_area", "general"),
        })

    return {
        "title": "Pair Programming Suggestions",
        "columns": [
            {"key": "rank", "label": "#", "width": "50px"},
            {"key": "pair", "label": "Suggested Pair", "width": "250px"},
            {"key": "score", "label": "Match Score", "width": "100px", "sortable": True},
            {"key": "reason", "label": "Primary Reason", "width": "200px"},
            {"key": "area", "label": "Task Area", "width": "150px"},
        ],
        "rows": rows,
        "searchable": True,
        "pageSize": 10,
    }
