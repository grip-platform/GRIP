#!/usr/bin/env python3
"""
Branch Management - List and manage GRIP branches.
Part of GRIP v6.0 SRP refactoring.

Responsibilities:
    - List branches in a project
    - Count sibling branches
"""

import json
from pathlib import Path
from typing import List, Dict, Any


def list_branches(project_instances_dir: Path) -> List[Dict[str, Any]]:
    """List all branches in the project."""
    if not project_instances_dir.exists():
        return []

    index_file = project_instances_dir / "index.json"
    if not index_file.exists():
        return []

    with open(index_file, 'r') as f:
        index = json.load(f)

    branches = []
    for name, info in index.get('branches', {}).items():
        branches.append({
            'name': name,
            'latest': info.get('latest'),
            'fork_point': info.get('fork_point'),
            'updated_at': info.get('updated_at')
        })

    return sorted(branches, key=lambda b: (b['name'] != 'main', b['name']))


def count_sibling_branches(instance: Dict[str, Any]) -> int:
    """Count sibling branches for the given instance."""
    lineage = instance.get('lineage', {})
    siblings = lineage.get('siblings', [])
    return len(siblings)
