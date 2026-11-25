"""DevikaHarness — stition.ai Devika as a Harness leaf.

Devika is a SERVER + UI application, not a CLI:
`python3 <checkout>/devika.py` starts a Flask-SocketIO backend that
listens on ALL network interfaces at port 1337 — that bind is the
UPSTREAM entrypoint's own hardcoded default (`socketio.run(app,
debug=False, port=1337, ...)`), unauthenticated, and this adapter
neither adds nor can remove it (worth firewalling if a caller ever
runs a live checkout). The UI is a separate bun app on port 3001 and
tasks are driven through the UI/socket API. The constructor takes the
checkout directory; as_argv renders exactly that server launch.

Provenance (verified 2026-07-17, confidence HIGH — devika.py source
read: no argparse anywhere in the entrypoint):
- https://github.com/stitionai/devika — README: `python devika.py`
  then the bun UI on http://127.0.0.1:3001.
- https://raw.githubusercontent.com/stitionai/devika/main/devika.py

Honest limits: devika.py accepts NO CLI arguments, so task.prompt
CANNOT ride argv and this adapter refuses to fake a flag for it —
no non-interactive one-shot mode exists. With the default subprocess
runner a dispatch blocks on the foreground server and ends at
task.timeout_s as an honest TimeoutExpired failure; a useful dispatch
requires an injected runner that supervises the server (launch, probe
readiness, return output). available() only reflects python3 on PATH,
not checkout presence.
"""
from __future__ import annotations

from pathlib import Path

from .base import Task, register
from .cli import CLIHarness


@register
class DevikaHarness(CLIHarness):
    """Devika server launch (no CLI task mode exists — see module doc)."""

    KIND = "devika"
    EXECUTABLE = "python3"

    def __init__(self, devika_dir: Path | str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._devika_dir = Path(devika_dir)

    def as_argv(self, task: Task) -> list[str]:
        return [self.EXECUTABLE, str(self._devika_dir / "devika.py")]
