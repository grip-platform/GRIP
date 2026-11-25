"""Verbind -- a coined slash verb becomes a binding, through a Koppel round.

The operator types a slash verb GRIP does not know, inside a prompt that says
what they want. Verbind asks them once (a Koppel round) what the verb should
mean, binds it to an existing read-only operation, and applies it. A deferred
verb is never asked about again; a bound verb is applied with no inference.

Contract: ``docs/VERBIND.md``. Formal model: ``formal/VERBIND.lean``.

Modules, one job each:

  gate       the state machine of the contract's section 3 -- pure, every
             effect injected, no environment reads, no harness imports
  catalogue  known verbs, leading-verb parsing, the read-only allowlist
  store      append-only state: bindings, unresolved verbs, chained receipts
  floor      meaning evidence (CF-IOF ranking of the skill catalogue);
             advisory only, it never returns a verdict
  koppel     the AskUserQuestion payload for one Koppel round
  runtime    the production wiring of the gate's dependencies
  cli        the JSON command line behind ``bin/grip-verbind``

The Claude Code adapter is ``hooks/verbind-hook.py``. It is the only module
that knows about Claude Code.

Importing the package puts this checkout's repository root and its ``lib/``
on ``sys.path`` once, so every submodule can reach ``lib.x`` and the bare
``x`` spellings the rest of the tree uses, from THIS checkout.
"""
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
for _path in (_LIB.parent, _LIB):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

VERBIND_VERSION = "1.0"
