"""DEPRECATION SHIM: lib.mirror has been renamed to lib.grip_anywhere.

The product was renamed to avoid collision with another GRIP MIRROR.
This module re-exports the public API with a DeprecationWarning so
existing imports continue working through one release cycle.
"""
import warnings
warnings.warn(
    "lib.mirror is deprecated; import from lib.grip_anywhere instead. "
    "This shim will be removed after 2026-08-01.",
    DeprecationWarning,
    stacklevel=2,
)
from lib.grip_anywhere import *  # noqa: F401,F403
# Legacy SYMBOL names — keep `from lib.mirror import Mirror / MirrorState` working.
# (GripAnywhere/GripAnywhereState are the canonical names; these are aliases.)
from lib.grip_anywhere import GripAnywhere as Mirror  # noqa: F401
from lib.grip_anywhere import GripAnywhereState as MirrorState  # noqa: F401
