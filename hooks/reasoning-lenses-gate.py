#!/usr/bin/env python3
"""Retired on 2026-11-01. This file does nothing and exits 0.

A running session can still hold this path in its Stop hooks. With the file gone,
lib/grip-python.sh would exit 2, and on a Stop hook that sends Claude back to work
with a hook it cannot repair. Claude Code's file watcher normally drops the binding,
so this file only guards the tail. GRIP#8905 deletes it on or after 2026-11-08.

See docs/measurements/2026-11-01-reasoning-lenses-retired.md.
"""
import sys

sys.exit(0)
