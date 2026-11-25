#!/usr/bin/env python3
"""PostToolUse hook: Auto-run relevant test after Edit/Write.

Detects the test file corresponding to an edited source file and runs
only that test for fast regression feedback. Skips gracefully when no
test file exists. Output is a single compact status line.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hook_utils import parse_hook_input, respond, extract_tool_info

DEBOUNCE_FILE = Path.home() / ".claude" / ".test-runner-debounce.json"
DEBOUNCE_SECONDS = 5

TESTABLE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs"}

SKIP_DIRS = {"node_modules", "venv", ".venv", "dist", "build", "__pycache__", "target", "vendor"}

TEST_INDICATORS = {"test_", "_test.", ".test.", ".spec.", "_spec."}


def load_debounce() -> dict:
    if DEBOUNCE_FILE.exists():
        try:
            return json.loads(DEBOUNCE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_debounce(state: dict):
    try:
        DEBOUNCE_FILE.write_text(json.dumps(state))
    except OSError:
        pass


def is_test_file(file_path: str) -> bool:
    name = Path(file_path).name.lower()
    return any(indicator in name for indicator in TEST_INDICATORS)


def in_skip_dir(file_path: str) -> bool:
    parts = Path(file_path).parts
    return bool(SKIP_DIRS.intersection(parts))


def find_project_root(file_path: str) -> "Path | None":
    markers = {".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Makefile"}
    current = Path(file_path).resolve().parent
    for _ in range(20):
        if any((current / m).exists() for m in markers):
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def find_test_file(file_path: str, project_root: Path) -> "Path | None":
    source = Path(file_path)
    name = source.stem
    ext = source.suffix

    if ext == ".py":
        candidates = [
            project_root / "tests" / f"test_{name}.py",
            project_root / "test" / f"test_{name}.py",
            source.parent / f"test_{name}.py",
            source.parent / "tests" / f"test_{name}.py",
        ]
    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        base_ext = ".ts" if ext in (".ts", ".tsx") else ".js"
        candidates = [
            source.parent / "__tests__" / f"{name}.test{base_ext}",
            source.parent / f"{name}.test{base_ext}",
            source.parent / f"{name}.spec{base_ext}",
            project_root / "tests" / f"{name}.test{base_ext}",
            project_root / "test" / f"{name}.test{base_ext}",
        ]
        if ext != base_ext:
            candidates += [
                source.parent / "__tests__" / f"{name}.test{ext}",
                source.parent / f"{name}.test{ext}",
            ]
    elif ext == ".go":
        candidates = [source.parent / f"{name}_test.go"]
    elif ext == ".rs":
        candidates = [project_root / "tests" / f"{name}.rs"]
    else:
        return None

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def detect_test_runner(project_root: Path, ext: str) -> "tuple[list[str], str] | None":
    if ext == ".py":
        return ([sys.executable, "-m", "pytest", "-x", "-q", "--tb=line", "--no-header"], "pytest")

    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        pkg_json = project_root / "package.json"
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text())
                deps = {**pkg.get("devDependencies", {}), **pkg.get("dependencies", {})}
                if "vitest" in deps:
                    return (["npx", "vitest", "run", "--reporter=dot"], "vitest")
                elif "jest" in deps:
                    return (["npx", "jest", "--silent", "--no-coverage"], "jest")
            except (json.JSONDecodeError, OSError):
                pass
        return None

    elif ext == ".go":
        return (["go", "test", "-count=1", "-run", "."], "go test")

    elif ext == ".rs":
        return (["cargo", "test", "--quiet"], "cargo test")

    return None


def run_test(test_file: Path, project_root: Path, ext: str) -> str:
    runner = detect_test_runner(project_root, ext)
    if not runner:
        return ""

    cmd_prefix, runner_name = runner

    if ext == ".go":
        rel_dir = test_file.parent.relative_to(project_root)
        cmd = cmd_prefix + [f"./{rel_dir}/..."]
    elif ext == ".rs":
        cmd = cmd_prefix + [test_file.stem]
    else:
        cmd = cmd_prefix + [str(test_file)]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            return f"[TEST PASS] {test_file.name} ({runner_name})"
        else:
            output = (result.stderr or result.stdout).strip()
            lines = [l for l in output.split("\n") if l.strip()]
            summary = lines[-1] if lines else "unknown error"
            if len(summary) > 120:
                summary = summary[:117] + "..."
            return f"[TEST FAIL] {test_file.name}: {summary}"

    except subprocess.TimeoutExpired:
        return f"[TEST TIMEOUT] {test_file.name} (>30s)"
    except FileNotFoundError:
        return f"[TEST SKIP] {runner_name} not found"
    except Exception:
        return ""


def main():
    hook_input = parse_hook_input()
    if not hook_input:
        respond()
        return

    tool_name, tool_input, _ = extract_tool_info(hook_input)

    if tool_name not in ("Write", "Edit"):
        respond()
        return

    file_path = tool_input.get("file_path", "")
    if not file_path:
        respond()
        return

    ext = Path(file_path).suffix.lower()
    if ext not in TESTABLE_EXTENSIONS:
        respond()
        return

    if is_test_file(file_path) or in_skip_dir(file_path):
        respond()
        return

    now = time.time()
    debounce = load_debounce()
    if now - debounce.get(file_path, 0) < DEBOUNCE_SECONDS:
        respond()
        return

    project_root = find_project_root(file_path)
    if not project_root:
        respond()
        return

    test_file = find_test_file(file_path, project_root)
    if not test_file:
        respond()
        return

    debounce[file_path] = now
    save_debounce(debounce)

    status = run_test(test_file, project_root, ext)
    respond(status if status else None)


if __name__ == "__main__":
    main()
