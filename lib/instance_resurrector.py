#!/usr/bin/env python3
"""
Backward compatibility wrapper for resurrection module.
GRIP v6.0 - Redirects to resurrection/ package.

The monolithic instance-resurrector.py has been refactored into:
    - resurrection/loader.py      (instance loading)
    - resurrection/branches.py    (branch management)
    - resurrection/context.py     (context generation)
    - resurrection/orchestrator.py (coordination + CLI)

Original monolith archived at: archive/instance_resurrector_v5_monolith.py
"""

# Re-export main class
from resurrection import InstanceResurrector

# Re-export utility functions for any direct imports
from resurrection.loader import (
    get_project_instance_dir,
    is_merged_instance,
    load_instance,
    find_latest_project_instance,
)
from resurrection.branches import list_branches, count_sibling_branches
from resurrection.orchestrator import main

__all__ = [
    'InstanceResurrector',
    'get_project_instance_dir',
    'is_merged_instance',
    'load_instance',
    'find_latest_project_instance',
    'list_branches',
    'count_sibling_branches',
]

if __name__ == '__main__':
    main()
