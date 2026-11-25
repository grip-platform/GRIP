"""A2A Task Lifecycle Adapter — maps A2A protocol to GRIP converge() kernel."""

from __future__ import annotations
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, Generator
from pathlib import Path

logger = logging.getLogger(__name__)

class TaskState(Enum):
    """A2A task states."""
    CREATED = "created"
    RUNNING = "running"
    STREAMING = "streaming"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"

@dataclass
class A2ATask:
    """An A2A task that wraps a converge() execution."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: TaskState = TaskState.CREATED
    input_data: Any = None
    result: Any = None
    events: list = field(default_factory=list)
    created_at: str = ""
    completed_at: str = ""
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

class CancelledError(Exception):
    """Raised when a task is cancelled mid-execution."""
    pass

class A2AAdapter:
    """Maps A2A task lifecycle to GRIP's converge() kernel.

    Mapping:
        A2A Phase      -> converge() Phase
        Task.create    -> converge(state, criterion) init
        Task.execute   -> improve(state) at each depth
        Task.stream    -> depth progress events from history
        Task.complete  -> criterion met or max_depth reached
        Task.cancel    -> circuit breaker / external halt
    """

    def __init__(self):
        self._tasks: dict[str, A2ATask] = {}
        self._cancel_flags: dict[str, bool] = {}

    def create_task(self, input_data: Any, metadata: Optional[dict] = None) -> A2ATask:
        """A2A Task.create — initialise a new task."""
        task = A2ATask(input_data=input_data, metadata=metadata or {})
        self._tasks[task.id] = task
        self._cancel_flags[task.id] = False
        task.events.append({"type": "created", "timestamp": task.created_at})
        return task

    def execute_task(
        self,
        task_id: str,
        criterion: Callable,
        improve: Callable,
        max_depth: int = 5,
        label: Optional[str] = None,
    ) -> A2ATask:
        """A2A Task.execute — run converge() and return completed task."""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if task.state not in (TaskState.CREATED,):
            raise ValueError(f"Task {task_id} in state {task.state}, cannot execute")

        task.state = TaskState.RUNNING
        task.events.append({"type": "started", "timestamp": _now()})

        try:
            # Import converge at runtime to avoid circular imports
            import sys
            root = str(Path(__file__).resolve().parent.parent)
            if root not in sys.path:
                sys.path.insert(0, root)
            from lib.converge import converge

            # Wrap improve to check cancel flag and emit events
            original_improve = improve
            def cancellable_improve(state):
                if self._cancel_flags.get(task_id, False):
                    raise CancelledError(f"Task {task_id} cancelled")
                result = original_improve(state)
                task.events.append({
                    "type": "progress",
                    "timestamp": _now(),
                    "depth": len([e for e in task.events if e["type"] == "progress"]),
                })
                return result

            result = converge(
                state=task.input_data,
                criterion=criterion,
                improve=cancellable_improve,
                max_depth=max_depth,
                label=label or f"a2a-{task_id[:8]}",
            )

            task.result = {
                "best_state": result.best_state,
                "final_state": result.final_state,
                "satisfied": result.satisfied,
                "iterations": result.iterations,
                "depth_reached": result.depth_reached,
                "elapsed_sec": result.elapsed_sec,
            }
            task.state = TaskState.COMPLETED
            task.completed_at = _now()
            task.events.append({"type": "completed", "timestamp": task.completed_at, "satisfied": result.satisfied})

        except CancelledError:
            task.state = TaskState.CANCELLED
            task.completed_at = _now()
            task.events.append({"type": "cancelled", "timestamp": task.completed_at})
        except Exception as e:
            task.state = TaskState.FAILED
            task.error = str(e)
            task.completed_at = _now()
            task.events.append({"type": "failed", "timestamp": task.completed_at, "error": str(e)})

        return task

    def stream_events(self, task_id: str) -> Generator[dict, None, None]:
        """A2A Task.stream — yield progress events."""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        yield from task.events

    def cancel_task(self, task_id: str) -> A2ATask:
        """A2A Task.cancel — signal cancellation via flag."""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        self._cancel_flags[task_id] = True
        return task

    def get_task(self, task_id: str) -> Optional[A2ATask]:
        """Get task by ID."""
        return self._tasks.get(task_id)

    def to_a2a_response(self, task: A2ATask) -> dict:
        """Serialise task to A2A-compliant JSON response."""
        return {
            "id": task.id,
            "state": task.state.value,
            "input": task.input_data,
            "result": task.result,
            "events": task.events,
            "created_at": task.created_at,
            "completed_at": task.completed_at,
            "error": task.error,
            "metadata": task.metadata,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
