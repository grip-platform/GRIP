#!/usr/bin/env python3
"""
Message filtering patterns for GRIP serialization and compression.

Filters out Claude Code internal metadata that should never appear in:
- Semantic context compression (resurrection.md)
- GRIP instance serialization (*.json)
- Session-start output ([SEMANTIC-CONTEXT])

Gen 233: Exit Command Semantic Leak Fix
"""

import re
from typing import List

# Command wrapper patterns to exclude (Claude Code internal metadata)
# These XML-like tags wrap slash commands and their output
COMMAND_WRAPPER_PATTERNS: List[str] = [
    r'<command-name>',
    r'</command-name>',
    r'<command-message>',
    r'</command-message>',
    r'<command-args>',
    r'</command-args>',
    r'<local-command-stdout>',
    r'</local-command-stdout>',
    r'<local-command-stderr>',
    r'</local-command-stderr>',
    r'<command-output>',
    r'</command-output>',
]


def is_command_wrapper(content: str) -> bool:
    """Check if content is Claude Code command wrapper metadata.

    Args:
        content: Message content to check

    Returns:
        True if content contains command wrapper tags, False otherwise
    """
    if not content:
        return False
    return any(re.search(pattern, content) for pattern in COMMAND_WRAPPER_PATTERNS)
