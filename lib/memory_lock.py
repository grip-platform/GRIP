"""Locked file operations for MEMORY.md — thin shim over file_lock.py.

All locking logic is now in file_lock.py (canonical implementation).
This module re-exports for backwards compatibility.

Usage unchanged:
    from memory_lock import locked_write, locked_append
    locked_write(path, content)
    locked_append(path, entry)
"""

import sys
from pathlib import Path

# Ensure lib/ is on the path for sibling imports
_lib_dir = str(Path(__file__).resolve().parent)
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from file_lock import locked_write, locked_append

__all__ = ["locked_write", "locked_append"]
