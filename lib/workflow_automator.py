"""GRIP Workflow Automator -- chain multi-step operations."""

import json
import os
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable
from enum import Enum

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))


class StepType(Enum):
    PYTHON_CALL = "python_call"
    SHELL_COMMAND = "shell_command"
    FILE_CHECK = "file_check"
    CONDITION = "condition"


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class WorkflowStep:
    name: str
    step_type: StepType
    command: str  # shell cmd, python expression, or file path
    depends_on: List[str] = field(default_factory=list)
    timeout: int = 60
    description: str = ""
    on_failure: str = "stop"  # stop, continue, skip_dependents


@dataclass
class StepResult:
    step_name: str
    status: StepStatus
    output: str = ""
    error: str = ""
    duration: float = 0.0


@dataclass
class Workflow:
    name: str
    description: str
    steps: List[WorkflowStep]
    tags: List[str] = field(default_factory=list)


@dataclass
class WorkflowResult:
    workflow_name: str
    steps_completed: int
    steps_failed: int
    steps_skipped: int
    step_results: List[StepResult]
    total_duration: float
    success: bool


class CycleError(Exception):
    """Raised when a dependency cycle is detected."""
    pass


def resolve_execution_order(steps: List[WorkflowStep]) -> List[WorkflowStep]:
    """Topological sort of steps based on depends_on. Detect cycles."""
    step_map = {s.name: s for s in steps}
    visited: Dict[str, str] = {}  # name -> "visiting" | "done"
    order: List[WorkflowStep] = []

    def visit(name: str) -> None:
        if name in visited:
            if visited[name] == "visiting":
                raise CycleError(f"Dependency cycle detected involving '{name}'")
            return  # already done
        if name not in step_map:
            raise ValueError(f"Unknown dependency '{name}'")
        visited[name] = "visiting"
        for dep in step_map[name].depends_on:
            visit(dep)
        visited[name] = "done"
        order.append(step_map[name])

    for step in steps:
        visit(step.name)

    return order


def _apply_template(command: str, context: Dict[str, Any]) -> str:
    """Replace {prev.output} and {steps.<name>.output} in command."""
    result = command
    if "prev" in context and context["prev"] is not None:
        result = result.replace("{prev.output}", context["prev"].output or "")
    for name, step_result in context.get("steps", {}).items():
        result = result.replace(
            f"{{steps.{name}.output}}", step_result.output or ""
        )
    return result


def execute_step(
    step: WorkflowStep, context: Dict[str, Any], dry_run: bool = False
) -> StepResult:
    """Execute a single workflow step."""
    start = time.monotonic()

    if dry_run:
        return StepResult(
            step_name=step.name,
            status=StepStatus.PASSED,
            output=f"[dry-run] Would execute: {step.command}",
            duration=0.0,
        )

    command = _apply_template(step.command, context)

    try:
        if step.step_type == StepType.SHELL_COMMAND:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=step.timeout,
            )
            duration = time.monotonic() - start
            if proc.returncode == 0:
                return StepResult(
                    step_name=step.name,
                    status=StepStatus.PASSED,
                    output=proc.stdout.strip(),
                    duration=duration,
                )
            else:
                return StepResult(
                    step_name=step.name,
                    status=StepStatus.FAILED,
                    output=proc.stdout.strip(),
                    error=proc.stderr.strip(),
                    duration=duration,
                )

        elif step.step_type == StepType.FILE_CHECK:
            path = Path(command)
            duration = time.monotonic() - start
            if path.exists():
                return StepResult(
                    step_name=step.name,
                    status=StepStatus.PASSED,
                    output=f"File exists: {path}",
                    duration=duration,
                )
            else:
                return StepResult(
                    step_name=step.name,
                    status=StepStatus.FAILED,
                    error=f"File not found: {path}",
                    duration=duration,
                )

        elif step.step_type == StepType.CONDITION:
            namespace: Dict[str, Any] = {"context": context, "os": os, "Path": Path}
            result = eval(command, namespace)  # noqa: S307
            duration = time.monotonic() - start
            if result:
                return StepResult(
                    step_name=step.name,
                    status=StepStatus.PASSED,
                    output=str(result),
                    duration=duration,
                )
            else:
                return StepResult(
                    step_name=step.name,
                    status=StepStatus.FAILED,
                    error=f"Condition evaluated to falsy: {result}",
                    duration=duration,
                )

        elif step.step_type == StepType.PYTHON_CALL:
            namespace: Dict[str, Any] = {
                "context": context,
                "os": os,
                "Path": Path,
                "json": json,
                "subprocess": subprocess,
                "__result__": None,
            }
            exec(command, namespace)  # noqa: S102
            duration = time.monotonic() - start
            output = namespace.get("__result__", "")
            return StepResult(
                step_name=step.name,
                status=StepStatus.PASSED,
                output=str(output) if output is not None else "",
                duration=duration,
            )

        else:
            duration = time.monotonic() - start
            return StepResult(
                step_name=step.name,
                status=StepStatus.FAILED,
                error=f"Unknown step type: {step.step_type}",
                duration=duration,
            )

    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return StepResult(
            step_name=step.name,
            status=StepStatus.FAILED,
            error=f"Timeout after {step.timeout}s",
            duration=duration,
        )
    except Exception as e:
        duration = time.monotonic() - start
        return StepResult(
            step_name=step.name,
            status=StepStatus.FAILED,
            error=str(e),
            duration=duration,
        )


def pipe_results(step_results: List[StepResult], next_step: WorkflowStep) -> str:
    """Pass previous step output as input to next step via template substitution."""
    if not step_results:
        return next_step.command
    context: Dict[str, Any] = {
        "prev": step_results[-1],
        "steps": {r.step_name: r for r in step_results},
    }
    return _apply_template(next_step.command, context)


def execute_workflow(
    workflow: Workflow, dry_run: bool = False, verbose: bool = False
) -> WorkflowResult:
    """Execute steps in dependency order."""
    start = time.monotonic()
    ordered = resolve_execution_order(workflow.steps)

    results: List[StepResult] = []
    result_map: Dict[str, StepResult] = {}
    failed_ancestors: set = set()
    stopped = False

    for step in ordered:
        # Check if any dependency failed with skip_dependents or stop
        should_skip = False
        for dep in step.depends_on:
            if dep in failed_ancestors:
                should_skip = True
                break
            dep_result = result_map.get(dep)
            if dep_result and dep_result.status == StepStatus.FAILED:
                dep_step = next(
                    (s for s in workflow.steps if s.name == dep), None
                )
                if dep_step and dep_step.on_failure in ("stop", "skip_dependents"):
                    should_skip = True
                    failed_ancestors.add(dep)
                    break

        if stopped or should_skip:
            sr = StepResult(
                step_name=step.name,
                status=StepStatus.SKIPPED,
                output="Skipped due to dependency failure",
                duration=0.0,
            )
            results.append(sr)
            result_map[step.name] = sr
            failed_ancestors.add(step.name)
            continue

        context: Dict[str, Any] = {
            "prev": results[-1] if results else None,
            "steps": result_map,
        }

        sr = execute_step(step, context, dry_run)
        results.append(sr)
        result_map[step.name] = sr

        if sr.status == StepStatus.FAILED:
            if step.on_failure == "stop":
                stopped = True
            elif step.on_failure == "skip_dependents":
                failed_ancestors.add(step.name)
            # "continue" does nothing special

    total_duration = time.monotonic() - start
    completed = sum(1 for r in results if r.status == StepStatus.PASSED)
    failed = sum(1 for r in results if r.status == StepStatus.FAILED)
    skipped = sum(1 for r in results if r.status == StepStatus.SKIPPED)

    return WorkflowResult(
        workflow_name=workflow.name,
        steps_completed=completed,
        steps_failed=failed,
        steps_skipped=skipped,
        step_results=results,
        total_duration=total_duration,
        success=failed == 0,
    )


def get_builtin_workflows() -> List[Workflow]:
    """Return built-in workflow templates."""
    health_check = Workflow(
        name="health-check",
        description="Run GRIP health checks and report findings",
        steps=[
            WorkflowStep(
                name="check-genome",
                step_type=StepType.FILE_CHECK,
                command=str(CLAUDE_DIR / "data" / "genome.json"),
                description="Verify genome file exists",
            ),
            WorkflowStep(
                name="check-metrics",
                step_type=StepType.FILE_CHECK,
                command=str(CLAUDE_DIR / "data" / "metrics.json"),
                description="Verify metrics file exists",
                depends_on=["check-genome"],
            ),
            WorkflowStep(
                name="report",
                step_type=StepType.PYTHON_CALL,
                command="__result__ = 'Health check complete'",
                description="Generate health report",
                depends_on=["check-genome", "check-metrics"],
            ),
        ],
        tags=["health", "diagnostic"],
    )

    test_suite = Workflow(
        name="test-suite",
        description="Discover and run all tests",
        steps=[
            WorkflowStep(
                name="discover",
                step_type=StepType.SHELL_COMMAND,
                command="find tests/ -name 'test_*.py' | wc -l",
                description="Count test files",
            ),
            WorkflowStep(
                name="run-tests",
                step_type=StepType.SHELL_COMMAND,
                command="python3 -m pytest tests/ --tb=short -q 2>&1 | tail -5",
                description="Run test suite",
                depends_on=["discover"],
                timeout=300,
            ),
            WorkflowStep(
                name="summarize",
                step_type=StepType.PYTHON_CALL,
                command="__result__ = f'Tests output: {context[\"prev\"].output}'",
                description="Summarize test results",
                depends_on=["run-tests"],
            ),
        ],
        tags=["test", "quality"],
    )

    save_state = Workflow(
        name="save-state",
        description="Save GRIP state: genome, metrics, memory",
        steps=[
            WorkflowStep(
                name="save-genome",
                step_type=StepType.FILE_CHECK,
                command=str(CLAUDE_DIR / "data" / "genome.json"),
                description="Verify genome exists",
            ),
            WorkflowStep(
                name="save-memory",
                step_type=StepType.FILE_CHECK,
                command=str(CLAUDE_DIR / "projects"),
                description="Verify memory directory exists",
            ),
            WorkflowStep(
                name="confirm",
                step_type=StepType.PYTHON_CALL,
                command="__result__ = 'State verified'",
                description="Confirm state saved",
                depends_on=["save-genome", "save-memory"],
            ),
        ],
        tags=["state", "backup"],
    )

    pre_commit = Workflow(
        name="pre-commit",
        description="Run linting, tests, then commit",
        steps=[
            WorkflowStep(
                name="lint",
                step_type=StepType.SHELL_COMMAND,
                command="python3 -m py_compile lib/workflow_automator.py && echo 'Lint OK'",
                description="Lint source files",
                on_failure="stop",
            ),
            WorkflowStep(
                name="test",
                step_type=StepType.SHELL_COMMAND,
                command="python3 -m pytest tests/ -q --tb=line 2>&1 | tail -3",
                description="Run tests",
                depends_on=["lint"],
                timeout=120,
                on_failure="stop",
            ),
            WorkflowStep(
                name="stage",
                step_type=StepType.SHELL_COMMAND,
                command="git status --porcelain | head -10",
                description="Show files to stage",
                depends_on=["test"],
            ),
        ],
        tags=["git", "quality"],
    )

    return [health_check, test_suite, save_state, pre_commit]


def list_workflows(include_builtins: bool = True) -> List[Dict[str, Any]]:
    """List available workflows with descriptions."""
    workflows: List[Dict[str, Any]] = []
    if include_builtins:
        for wf in get_builtin_workflows():
            workflows.append({
                "name": wf.name,
                "description": wf.description,
                "steps": len(wf.steps),
                "tags": wf.tags,
                "builtin": True,
            })
    return workflows


def format_workflow_viz(workflow: Workflow) -> str:
    """ASCII visualization of workflow steps and dependencies."""
    lines = [f"{workflow.name} ({len(workflow.steps)} steps)"]

    step_names = [s.name for s in workflow.steps]
    step_index = {s.name: i + 1 for i, s in enumerate(workflow.steps)}

    # Build adjacency: who depends on whom
    dependents: Dict[str, List[str]] = {s.name: [] for s in workflow.steps}
    has_deps: set = set()
    for s in workflow.steps:
        for dep in s.depends_on:
            if dep in dependents:
                dependents[dep].append(s.name)
                has_deps.add(s.name)

    # Find roots (no dependencies)
    roots = [s.name for s in workflow.steps if not s.depends_on]

    # BFS to build chains
    visited: set = set()
    chains: List[List[str]] = []

    def build_chain(start: str) -> None:
        chain = [start]
        visited.add(start)
        current = start
        while dependents[current]:
            # Follow the first unvisited dependent
            next_step = None
            for dep in dependents[current]:
                if dep not in visited:
                    next_step = dep
                    break
            if next_step is None:
                break
            chain.append(next_step)
            visited.add(next_step)
            current = next_step
        chains.append(chain)
        # Check for branches
        for step_name in list(chain):
            for dep in dependents[step_name]:
                if dep not in visited:
                    build_chain(dep)

    for root in roots:
        if root not in visited:
            build_chain(root)

    # Format chains
    for chain in chains:
        parts = []
        for name in chain:
            idx = step_index[name]
            parts.append(f"[{idx}] {name}")
        chain_str = " --> ".join(parts)
        lines.append(f"  {chain_str}")

    # Show branching
    for step in workflow.steps:
        deps = dependents[step.name]
        if len(deps) > 1:
            idx = step_index[step.name]
            branch_parts = []
            for dep in deps:
                dep_idx = step_index[dep]
                branch_parts.append(f"[{dep_idx}] {dep}")
            branch_note = f"  [{idx}] {step.name} branches to: {', '.join(branch_parts)}"
            if branch_note not in lines:
                lines.append(branch_note)

    # Show on_failure annotations
    for step in workflow.steps:
        if step.on_failure != "stop":
            idx = step_index[step.name]
            lines.append(f"  [{idx}] {step.name} (on_failure={step.on_failure})")

    return "\n".join(lines)


def format_result(result: WorkflowResult, verbose: bool = False) -> str:
    """ASCII result report."""
    status = "PASSED" if result.success else "FAILED"
    total = len(result.step_results)
    lines = [
        f"Workflow: {result.workflow_name}",
        f"Status: {status} ({result.steps_completed}/{total} steps)",
        f"Duration: {result.total_duration:.1f}s",
        "",
    ]

    for sr in result.step_results:
        if sr.status == StepStatus.PASSED:
            icon = "[v]"
        elif sr.status == StepStatus.FAILED:
            icon = "[x]"
        elif sr.status == StepStatus.SKIPPED:
            icon = "[-]"
        else:
            icon = "[?]"

        line = f"  {icon} {sr.step_name:<25} {sr.duration:.1f}s"
        lines.append(line)

        if verbose and sr.output:
            for out_line in sr.output.split("\n"):
                lines.append(f"      {out_line}")
        if verbose and sr.error:
            for err_line in sr.error.split("\n"):
                lines.append(f"      ERROR: {err_line}")

    return "\n".join(lines)


def _find_builtin(name: str) -> Optional[Workflow]:
    """Find a built-in workflow by name."""
    for wf in get_builtin_workflows():
        if wf.name == name:
            return wf
    return None


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="GRIP Workflow Automator")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run a workflow")
    run_p.add_argument("workflow", help="Workflow name")
    run_p.add_argument("--dry-run", action="store_true", help="Validate without executing")
    run_p.add_argument("--verbose", action="store_true", help="Show step output")

    list_p = sub.add_parser("list", help="List available workflows")

    show_p = sub.add_parser("show", help="Show workflow visualization")
    show_p.add_argument("workflow", help="Workflow name")

    args = parser.parse_args()

    if args.command == "list":
        workflows = list_workflows()
        for wf in workflows:
            builtin = " (built-in)" if wf.get("builtin") else ""
            print(f"  {wf['name']:<20} {wf['description']}{builtin}")

    elif args.command == "show":
        wf = _find_builtin(args.workflow)
        if wf is None:
            print(f"Workflow '{args.workflow}' not found")
            return
        print(format_workflow_viz(wf))

    elif args.command == "run":
        wf = _find_builtin(args.workflow)
        if wf is None:
            print(f"Workflow '{args.workflow}' not found")
            return
        result = execute_workflow(wf, dry_run=args.dry_run, verbose=args.verbose)
        print(format_result(result, verbose=args.verbose))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
