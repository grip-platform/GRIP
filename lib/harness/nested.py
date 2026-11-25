"""Harness compositions — the harness-in-harness layer.

Three shapes, all Harness subtypes (polymorphism: a composition is
substitutable anywhere a leaf is, so compositions nest inside
compositions without isinstance branches):

- OrchestratorHarness(planner, executor): the orchestrator/executor
  split — plan with one runtime, execute with another, inspect before
  accepting.
- HostedHarness(host, guest): TRUE nesting — the guest's command line
  runs INSIDE the host runtime's sandbox/tool loop. Codex-in-Claude,
  Claude-in-Codex, Gemini-in-OpenCode: symmetric by construction, since
  any CLI-invocable harness can be the guest of any harness whose
  capability surface includes command execution.
- FallbackHarness(rungs): first-ok-wins ladder (the HAL sentinel shape
  at harness granularity; rate limits fail over, never block).

Cycle safety: composites expose _members(); Harness.run walks the
composition graph before executing and raises on any cycle.
"""
from __future__ import annotations

import shlex
from typing import Sequence

from .base import Harness, HarnessResult, Task, register
from .cli import CLIHarness

PLAN_PROMPT = (
    "Produce a concise, numbered implementation plan (no code) for the "
    "task below. Cover edge cases and the verification step.\n\n"
    "TASK:\n{prompt}"
)
EXEC_PROMPT = (
    "Execute the task below. A reviewed plan is attached — follow it "
    "unless it conflicts with reality.\n\nTASK:\n{prompt}\n\nPLAN:\n{plan}"
)
HOST_PROMPT = (
    "Run exactly this command and return only its stdout, nothing else:\n"
    "{command}"
)


@register
class OrchestratorHarness(Harness):
    """Plan with `planner`, execute with `executor` (both any Harness).

    A failed plan degrades gracefully to direct execution of the raw
    task; overall ok tracks the executor. Both sub-results are preserved
    so the caller can inspect the executor's work before accepting it.
    """

    KIND = "orchestrator"

    def __init__(self, planner: Harness, executor: Harness,
                 **kwargs) -> None:
        self.planner = planner
        self.executor = executor
        super().__init__(**kwargs)

    def _members(self) -> tuple[Harness, ...]:
        return (self.planner, self.executor)

    def _execute(self, task: Task) -> HarnessResult:
        plan = self.planner.run(Task(
            id=f"{task.id}.plan",
            prompt=PLAN_PROMPT.format(prompt=task.prompt),
            timeout_s=task.timeout_s))
        exec_prompt = (EXEC_PROMPT.format(prompt=task.prompt,
                                          plan=plan.content)
                       if plan.ok else task.prompt)
        done = self.executor.run(Task(id=f"{task.id}.exec",
                                      prompt=exec_prompt,
                                      timeout_s=task.timeout_s))
        if done.ok:
            return HarnessResult(id=task.id, kind=self.kind,
                                 harness_id=self.harness_id, ok=True,
                                 content=done.content,
                                 sub_results=(plan, done))
        return HarnessResult(id=task.id, kind=self.kind,
                             harness_id=self.harness_id, ok=False,
                             error=done.error, sub_results=(plan, done))


@register
class HostedHarness(Harness):
    """Run `guest` INSIDE `host`'s runtime — the literal harness-in-harness.

    The guest must be CLI-invocable (constructor precondition of this
    composite — an ISP boundary, not a strengthened Harness precondition).
    The host must have command execution in its capability surface; that
    widening is the caller's explicit choice (e.g.
    ClaudeCodeHarness(tools=("Bash",))), never implicit here.
    """

    KIND = "hosted"

    def __init__(self, host: Harness, guest: CLIHarness, **kwargs) -> None:
        if not isinstance(guest, CLIHarness):
            raise TypeError(
                "HostedHarness guest must be CLI-invocable (CLIHarness)")
        self.host = host
        self.guest = guest
        super().__init__(**kwargs)

    def _members(self) -> tuple[Harness, ...]:
        return (self.host, self.guest)

    def _execute(self, task: Task) -> HarnessResult:
        command = shlex.join(self.guest.as_argv(task))
        hosted = self.host.run(Task(
            id=f"{task.id}.host",
            prompt=HOST_PROMPT.format(command=command),
            timeout_s=task.timeout_s))
        if hosted.ok:
            return HarnessResult(id=task.id, kind=self.kind,
                                 harness_id=self.harness_id, ok=True,
                                 content=hosted.content,
                                 sub_results=(hosted,))
        return HarnessResult(id=task.id, kind=self.kind,
                             harness_id=self.harness_id, ok=False,
                             error=hosted.error, sub_results=(hosted,))


@register
class FallbackHarness(Harness):
    """Try each rung in order; first ok wins; all failures are preserved."""

    KIND = "fallback"

    def __init__(self, rungs: Sequence[Harness], **kwargs) -> None:
        if not rungs:
            raise ValueError("FallbackHarness requires at least one rung")
        self.rungs = tuple(rungs)
        super().__init__(**kwargs)

    def _members(self) -> tuple[Harness, ...]:
        return self.rungs

    def _execute(self, task: Task) -> HarnessResult:
        attempts: list[HarnessResult] = []
        for i, rung in enumerate(self.rungs):
            attempt = rung.run(Task(id=f"{task.id}.r{i}",
                                    prompt=task.prompt,
                                    timeout_s=task.timeout_s))
            attempts.append(attempt)
            if attempt.ok:
                return HarnessResult(id=task.id, kind=self.kind,
                                     harness_id=self.harness_id, ok=True,
                                     content=attempt.content,
                                     sub_results=tuple(attempts))
        errors = " | ".join(f"{a.kind}: {a.error}" for a in attempts)
        return HarnessResult(
            id=task.id, kind=self.kind, harness_id=self.harness_id,
            ok=False,
            error=f"all {len(attempts)} rungs failed — {errors}",
            sub_results=tuple(attempts))
