"""MCP Apps Data Generator for GRIP.

Thin facade that re-exports all generators from the mcp_apps package.
The implementation has been decomposed into lib/mcp_apps/ for maintainability.

Usage:
    python3 lib/mcp_app_generator.py sprint_kanban --source github
    python3 lib/mcp_app_generator.py project_circuit --source metrics
    python3 lib/mcp_app_generator.py backlog_table --source backlog
"""

from mcp_apps import *  # noqa: F401, F403
from mcp_apps import main

if __name__ == "__main__":
    main()
