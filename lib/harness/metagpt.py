"""MetaGPTHarness — MetaGPT multi-agent software company as a leaf.

Non-interactive one-shot form: `metagpt "<idea>" --n-round N
--investment X [--project-name NAME] [--run-tests]`.

Provenance (verified 2026-07-17, confidence HIGH — flag names quoted
from source):
- https://github.com/geekan/MetaGPT — README one-shot:
  `metagpt "Create a 2048 game"` writes a repo under ./workspace.
- https://github.com/geekan/MetaGPT (metagpt/software_company.py @
  main): typer options — idea argument; --investment float 3.0;
  --n-round int 5; --code-review bool True; --run-tests bool False;
  --implement bool True; --project-name str "".

Capability floor: MetaGPT ALWAYS writes a generated repository (under
./workspace of the process cwd) — no read-only mode exists; that write
is the tool's purpose, stated here rather than hidden. The floor this
adapter holds by default: --run-tests is OMITTED (upstream default
False), so QA never EXECUTES generated code unless the caller widens
with run_tests=True. Only positively-verified flag spellings are ever
emitted — typer-derived --no-* negative forms are not confirmed in
source and are never used.
"""
from __future__ import annotations

from .base import Task, register
from .cli import CLIHarness


@register
class MetaGPTHarness(CLIHarness):
    """MetaGPT one-shot software-company run, test-execution off."""

    KIND = "metagpt"
    EXECUTABLE = "metagpt"

    def __init__(self, n_round: int = 5, investment: float = 3.0,
                 project_name: str = "", run_tests: bool = False,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self._n_round = n_round
        self._investment = investment
        self._project_name = project_name
        self._run_tests = run_tests

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE, task.prompt,
                "--n-round", str(self._n_round),
                "--investment", str(self._investment)]
        if self._project_name:
            argv += ["--project-name", self._project_name]
        if self._run_tests:
            argv.append("--run-tests")
        return argv
