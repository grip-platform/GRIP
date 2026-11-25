#!/usr/bin/env python3
"""GRIP merge command - safe replacement for inline python3 -c in grip binary.

Accepts arguments via sys.argv instead of bash string interpolation.
Fixes code injection vulnerability (audit finding #2).
"""
import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="GRIP instance merge")
    parser.add_argument("--refs", nargs="+", required=True, help="Source references to merge")
    parser.add_argument("--target", required=True, help="Target branch name")
    parser.add_argument("--dry-run", action="store_true", help="Preview merge without executing")
    parser.add_argument("--lib-dir", required=True, help="Path to GRIP lib directory")
    args = parser.parse_args()

    # Safe sys.path insertion using validated argument
    lib_dir = Path(args.lib_dir).resolve()
    if not lib_dir.is_dir():
        print(f"Error: lib directory not found: {lib_dir}", file=sys.stderr)
        sys.exit(1)
    sys.path.insert(0, str(lib_dir))

    from grip_merged import merge_by_references
    from grip_atomic import find_atomic_by_reference
    from path_encoding import encode_project_path

    project_path = Path.cwd()
    encoded = encode_project_path(project_path)
    instances_dir = Path.home() / ".claude" / "projects" / encoded / "grip"

    refs = args.refs
    target_branch = args.target

    if args.dry_run:
        print(f"Source references: {refs}")
        print(f"Target branch: {target_branch}")

        sources = []
        for ref in refs:
            inst = find_atomic_by_reference(ref, instances_dir)
            if inst:
                print(f"  - {ref}: Gen {inst.get_generation()} on {inst.get_branch()}, {inst.get_memory_count()} memories")
                sources.append(inst)
            else:
                print(f"  - {ref}: NOT FOUND")
                sys.exit(1)

        merged = merge_by_references(refs, instances_dir, target_branch=target_branch)
        print()
        print("Merged instance would be:")
        print(f"  ID: {merged.get_instance_id()}")
        print(f"  Generation: {merged.get_generation()}")
        print(f"  Total memories: {merged.get_memory_count()}")
        print(f"  Achievements: {len(merged.get_achievements())}")
    else:
        from grip_merged import save_merged_instance

        merged = merge_by_references(refs, instances_dir, target_branch=target_branch)
        output_file = save_merged_instance(merged, instances_dir)

        print("Merge complete!")
        print(f"  Instance: {merged.get_instance_id()}")
        print(f"  Generation: {merged.get_generation()}")
        print(f"  Branch: {target_branch}")
        print(f"  Total memories: {merged.get_memory_count()}")
        print(f"  Saved to: {output_file}")
        print()
        print("Branches merged successfully.")


if __name__ == "__main__":
    main()
