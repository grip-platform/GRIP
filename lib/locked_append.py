"""Locked JSONL append utility — thin shim over file_lock.py.

All locking logic is now in file_lock.py (canonical implementation).
This module re-exports for backwards compatibility.

Usage unchanged:
    from locked_append import locked_append, LockTimeoutError
    locked_append("/path/to/file.jsonl", '{"key": "value"}')
"""

from file_lock import locked_append, LockTimeoutError

__all__ = ["locked_append", "LockTimeoutError"]
