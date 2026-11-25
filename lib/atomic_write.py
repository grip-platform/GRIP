#!/usr/bin/env python3
"""
Atomic write pattern for crash-resistant file operations.
Adapted from Cortex by Tomas Hjertefolger (MIT License).
https://github.com/hjertefolger/cortex
"""

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path | str, content: bytes | str) -> None:
    """
    Write content atomically using temp-file + rename pattern.

    Crash between write and rename = temp file orphaned, target intact.

    Args:
        path: Target file path
        content: Content to write (str or bytes)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in same directory (ensures same filesystem)
    fd, temp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp"
    )

    try:
        if isinstance(content, str):
            content = content.encode('utf-8')
        os.write(fd, content)
        os.fsync(fd)  # Ensure written to disk
        os.close(fd)

        # Atomic replace (cross-platform; os.rename fails on Windows if target exists)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_path)  # Clean up temp file
        except OSError:
            pass
        raise


def atomic_write_json(path: Path | str, data: dict) -> None:
    """Write JSON atomically with pretty formatting."""
    import json
    content = json.dumps(data, indent=2, ensure_ascii=False)
    atomic_write(path, content)


if __name__ == "__main__":
    # Quick test
    import sys
    if len(sys.argv) > 1:
        test_path = Path(sys.argv[1])
        atomic_write(test_path, "Test content written atomically")
        print(f"Wrote to {test_path}")
