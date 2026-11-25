"""Shared constants, imports, and utilities for MCP app generators."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.subprocess_utils import run_git as _run_git, run_gh as _run_gh

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
REPO_SLUG = os.environ.get("GRIP_REPO", "grip-platform/GRIP")
