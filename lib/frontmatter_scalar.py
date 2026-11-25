"""Decode one value from a simple ``key: value`` frontmatter line.

``lib/hal/memory_engine.py`` writes free-text values (``name``, ``description``)
as JSON strings (GRIP#6096), so a colon or quote inside them cannot break the
header. Its two readers, ``MemoryEngine`` and ``lib/hal_memory_indexer.py``,
decode them here (GRIP#7408, GRIP#7409).

This sits directly under ``lib/`` on purpose. Importing anything under
``lib/hal`` runs that package's ``__init__``, which loads the provider registry,
and the indexer deliberately avoids that.
"""

from __future__ import annotations

import json


def unquote(val: str) -> str:
    """Decode a JSON double-quoted value; return any other value as written.

    A value that starts and ends with a double quote and parses as JSON can
    only be a JSON string, so a successful parse always returns ``str``. One
    that does not parse, such as a final quote that is itself escaped, is
    returned unchanged rather than half-decoded.
    """
    if len(val) >= 2 and val[0] == val[-1] == '"':
        try:
            return json.loads(val)
        except ValueError:
            return val
    return val
