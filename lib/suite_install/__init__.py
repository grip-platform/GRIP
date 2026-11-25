"""GRIP Suite installer core: one engine behind the install doors and the setup page.

The suite installs GRIP, HAL, HAPPI and GRASP with one command per operating system. Two
thin doors (a shell script for macOS and Linux, a PowerShell script for Windows) find or
install Python 3.11+ and Git, then hand over to this package, so every step and every
proof is written once and shared by the installer, the setup page and the terminal mode.

- steps.py: a step counts only when its proof passes; the end state is proved again.
- computer.py: the first step, "check your computer".
- products.py: GRIP, HAL, HAPPI and GRASP, each installed by its own installer and
  proved by its own entry point.
- cli.py, run as `python3 -m lib.suite_install [--dry-run]` from the checkout's root:
  prints each step as it goes, then one closing line, and exits 0 only when complete.
"""
