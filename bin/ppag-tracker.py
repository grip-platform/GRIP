#!/usr/bin/env python3
"""ppag-tracker — backward-compat shim. Delegates to showcase-server with SHOWCASE_PROJECT=ppag.

History: this was the original PPAG-hardcoded daemon. It now defers to
`lib/showcase/server.py` which routes all projects via path namespacing.
See ~/.claude/plans/enchanted-spinning-emerson.md.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SHOWCASE_PORT", "3900")
sys.path.insert(0, str(Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))))

from lib.showcase.server import main  # noqa: E402

if __name__ == "__main__":
    main()
