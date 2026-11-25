#!/usr/bin/env python3
"""
Instance Loader - Loading and finding serialized instances.
Part of GRIP v6.0 SRP refactoring.

Responsibilities:
    - Load instances from project/global storage
    - Find latest instance (with branch support)
    - Partial ID matching
"""

import json
import sys
from pathlib import Path
from typing import Optional, Dict, Any, TYPE_CHECKING

CLAUDE_DIR = Path.home() / ".claude"
INSTANCES_DIR = CLAUDE_DIR / "instances"
PROJECTS_DIR = CLAUDE_DIR / "projects"

# Import unified path encoding
if str(Path(__file__).resolve().parent.parent) not in sys.path:  # own lib (GRIP#9857)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from path_encoding import encode_project_path
except ImportError:
    def encode_project_path(path):
        return str(path).replace('/', '-').replace('.', '-')


def get_project_instance_dir(project_path: Path) -> Path:
    """Get per-project instance storage directory."""
    encoded = encode_project_path(project_path)
    return PROJECTS_DIR / encoded / "grip"


def is_merged_instance(instance: Dict[str, Any]) -> bool:
    """Check if an instance is a merge of multiple branches."""
    if instance.get('merge_type') == 'confluence':
        return True
    if instance.get('lineage', {}).get('is_merge'):
        return True
    if instance.get('instance_id', '').startswith('merge-'):
        return True
    return False


def load_instance(
    instance_id: str,
    project_instances_dir: Path,
    global_instances_dir: Path = INSTANCES_DIR
) -> Dict[str, Any]:
    """Load a serialized instance from project or global storage."""
    for instances_dir in [project_instances_dir, global_instances_dir]:
        if not instances_dir.exists():
            continue

        instance_file = instances_dir / f"{instance_id}.json"
        if instance_file.exists():
            with open(instance_file, 'r') as f:
                return json.load(f)

        # Try partial match
        for f in instances_dir.glob("*.json"):
            if f.name == "index.json":
                continue
            if f.stem.startswith(instance_id):
                with open(f, 'r') as file:
                    return json.load(file)

    raise ValueError(f"Instance {instance_id} not found")


def load_instance_from_dir(instances_dir: Path, instance_id: str) -> Dict[str, Any]:
    """Load instance from a specific directory."""
    instance_file = instances_dir / f"{instance_id}.json"
    if instance_file.exists():
        with open(instance_file, 'r') as f:
            return json.load(f)

    # Try partial match
    for f in instances_dir.glob("*.json"):
        if f.name == "index.json":
            continue
        if f.stem.startswith(instance_id):
            with open(f, 'r') as file:
                return json.load(file)

    raise ValueError(f"Instance {instance_id} not found in {instances_dir}")


def find_latest_on_branch(
    project_instances_dir: Path,
    index: Dict[str, Any],
    branch: str
) -> Optional[Dict[str, Any]]:
    """Find the latest instance on a specific branch."""
    # Check branches metadata first (faster)
    branches = index.get('branches', {})
    if branch in branches:
        latest_id = branches[branch].get('latest_instance_id')
        if latest_id:
            try:
                return load_instance_from_dir(project_instances_dir, latest_id)
            except ValueError:
                pass

    # Search instances for branch
    instances = index.get('instances', [])
    branch_instances = [
        inst for inst in instances
        if inst.get('lineage', {}).get('branch', 'main') == branch
    ]

    if not branch_instances:
        return None

    # Sort by serialized_at and return latest
    branch_instances.sort(key=lambda x: x.get('serialized_at', ''), reverse=True)

    for inst in branch_instances:
        try:
            return load_instance_from_dir(project_instances_dir, inst['instance_id'])
        except ValueError:
            continue

    return None


def find_latest_any_branch(
    project_instances_dir: Path,
    index: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Find the latest instance across all branches."""
    instances = index.get('instances', [])
    if not instances:
        return None

    # Sort all instances by timestamp
    instances.sort(key=lambda x: x.get('serialized_at', ''), reverse=True)

    for inst in instances:
        try:
            return load_instance_from_dir(project_instances_dir, inst['instance_id'])
        except ValueError:
            continue

    return None


def fallback_find_latest(project_instances_dir: Path) -> Optional[Dict[str, Any]]:
    """Fallback: find most recent JSON file by mtime."""
    json_files = [
        f for f in project_instances_dir.glob("*.json")
        if f.name != "index.json"
    ]
    if json_files:
        latest_file = max(json_files, key=lambda f: f.stat().st_mtime)
        with open(latest_file, 'r') as f:
            return json.load(f)
    return None


def find_latest_project_instance(
    project_instances_dir: Path,
    branch: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Find the most recent instance for project, optionally on specific branch.
    
    STRICT PER-PROJECT ISOLATION: Only checks project-specific storage.
    Does NOT fall back to global instances to prevent stale data leakage.
    """
    if not project_instances_dir.exists():
        return None

    index_file = project_instances_dir / "index.json"
    if not index_file.exists():
        return fallback_find_latest(project_instances_dir)

    with open(index_file, 'r') as f:
        index = json.load(f)

    # If specific branch requested, find latest on that branch
    if branch:
        return find_latest_on_branch(project_instances_dir, index, branch)

    # Default: prefer main branch, then fall back to any
    main_instance = find_latest_on_branch(project_instances_dir, index, "main")
    if main_instance:
        return main_instance

    # No main branch - find any branch's latest
    return find_latest_any_branch(project_instances_dir, index)
