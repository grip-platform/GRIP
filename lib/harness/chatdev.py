"""ChatDevHarness — OpenBMB ChatDev (1.0) as a Harness leaf.

ChatDev 1.0 ships NO console script; the scriptable entrypoint is
`python3 <checkout>/run.py --task <prompt> --name <name> --config
<config> --org <org> [--model <MODEL>]` inside a ChatDev checkout, so
the constructor takes the checkout directory and as_argv renders an
absolute run.py path.

Provenance (verified 2026-07-17, confidence HIGH — argparse quoted
from source):
- https://github.com/OpenBMB/ChatDev — README legacy-1.0 invocations
  (`python3 run.py --task ... --config ...`).
- https://raw.githubusercontent.com/OpenBMB/ChatDev/chatdev1.0/run.py
  — argparse: --config "Default"; --org "DefaultOrganization";
  --task; --name "Gomoku"; --model in {GPT_3_5_TURBO, GPT_4,
  GPT_4_TURBO, GPT_4O, GPT_4O_MINI} default GPT_3_5_TURBO; --path ""
  (incremental mode).

Honest limits: ChatDev 2.0 (current main) replaced run.py with a
server + Python SDK (server_main.py / runtime.sdk.run_workflow) and is
NOT wrapped here — this adapter targets the 1.0 run.py the caller's
checkout provides. The run WRITES generated software into WareHouse/
inside that checkout; no read-only mode exists. --model is omitted by
default so the checkout's own default applies; widening to a specific
ModelType name is an explicit constructor choice. available() only
reflects python3 on PATH, not checkout presence.
"""
from __future__ import annotations

from pathlib import Path

from .base import Task, register
from .cli import CLIHarness


@register
class ChatDevHarness(CLIHarness):
    """ChatDev 1.0 run.py invocation rendered from a caller checkout."""

    KIND = "chatdev"
    EXECUTABLE = "python3"

    def __init__(self, chatdev_dir: Path | str, name: str = "GripTask",
                 config: str = "Default",
                 org: str = "DefaultOrganization", model: str = "",
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self._chatdev_dir = Path(chatdev_dir)
        self._name = name
        self._config = config
        self._org = org
        self._model = model

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE, str(self._chatdev_dir / "run.py"),
                "--task", task.prompt, "--name", self._name,
                "--config", self._config, "--org", self._org]
        if self._model:
            argv += ["--model", self._model]
        return argv
