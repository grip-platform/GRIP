#!/usr/bin/env python3
"""RSI Orchestrator — backward-compatible facade.

All functionality has been decomposed into the rsi/ package.
This module re-exports every public name for backward compatibility.
"""

from rsi import *  # noqa: F401,F403
from rsi import __all__  # noqa: F401 — propagate __all__ for star re-export

if __name__ == "__main__":
    from rsi.cli import cli_main
    cli_main()
