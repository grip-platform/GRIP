#!/usr/bin/env python3
"""
Resurrection Orchestrator - Coordinates full resurrection workflow.
Part of GRIP v6.0 SRP refactoring.

Responsibilities:
    - Coordinate loader, branches, and context modules
    - Generate complete resurrection context
    - Create resurrection files
    - CLI interface
"""

import sys
from pathlib import Path
from typing import Optional, Dict, Any, List

from .loader import (
    get_project_instance_dir,
    load_instance,
    find_latest_project_instance,
    INSTANCES_DIR,
)
from .branches import list_branches, count_sibling_branches
from .context import (
    generate_auto_resurrection_context,
    generate_identity_primer,
    generate_conversation_context,
    generate_verification_prompts,
)


def _ensure_own_lib() -> None:
    """Put the checkout's own lib folder on sys.path once, never the shared tree's (GRIP#9857)."""
    own_lib = str(Path(__file__).resolve().parent.parent)
    if own_lib not in sys.path:
        sys.path.insert(0, own_lib)


class InstanceResurrector:
    """
    Coordinates GRIP instance resurrection.
    
    Orchestrates loading, branch management, and context generation
    to produce complete resurrection contexts.
    """
    
    def __init__(self, project_path: Optional[Path] = None):
        self.project_path = project_path or Path.cwd()
        self.global_instances_dir = INSTANCES_DIR
        self.project_instances_dir = get_project_instance_dir(self.project_path)

        # foundational_insights module was removed — functionality was never implemented.
        self.get_identity_context = None

    def load_instance(self, instance_id: str) -> Dict[str, Any]:
        """Load a serialized instance from project or global storage."""
        return load_instance(
            instance_id,
            self.project_instances_dir,
            self.global_instances_dir
        )

    def find_latest_project_instance(
        self,
        branch: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Find the most recent instance for current project."""
        return find_latest_project_instance(self.project_instances_dir, branch)

    def list_branches(self) -> List[Dict[str, Any]]:
        """List all branches in the project."""
        return list_branches(self.project_instances_dir)

    def count_sibling_branches(self, instance: Dict[str, Any]) -> int:
        """Count sibling branches for the given instance."""
        return count_sibling_branches(instance)

    def generate_auto_resurrection_context(
        self,
        branch: Optional[str] = None
    ) -> Optional[str]:
        """Generate minimal resurrection context for auto mode."""
        instance = self.find_latest_project_instance(branch=branch)
        if not instance:
            return None
        return generate_auto_resurrection_context(instance)

    def generate_identity_primer(self, instance: Dict[str, Any]) -> str:
        """Generate the identity establishment prompt."""
        return generate_identity_primer(instance, self.get_identity_context)

    def generate_conversation_context(
        self,
        instance: Dict[str, Any],
        max_messages: int = 150
    ) -> str:
        """Generate conversation history for context injection."""
        return generate_conversation_context(instance, max_messages)

    def generate_verification_prompts(self, instance: Dict[str, Any]) -> List[str]:
        """Generate prompts to verify resurrection succeeded."""
        return generate_verification_prompts(instance)

    def generate_resurrection_context(
        self,
        instance_id: str,
        include_full_history: bool = True,
        max_messages: int = 100
    ) -> Dict[str, Any]:
        """Generate complete resurrection context."""
        instance = self.load_instance(instance_id)

        # Gen 310: Initialize KONO substrate with instance state and recall memories
        kono_memories = []
        try:
            _ensure_own_lib()
            from kono_substrate import MemorySubstrate
            from kono import Position4D
            substrate = MemorySubstrate()
            lineage = instance.get('lineage', {})
            project_path = instance.get('source', {}).get('project_path', str(self.project_path))
            substrate.init_session(
                instance_id=instance_id[:8],
                generation=lineage.get('lineage_depth', 1),
                project_path=project_path
            )
            substrate.run_metabolism()

            # Query KONO for relevant memories from the project domain
            import time as _time
            domain_x = substrate.get_or_create_domain(project_path)
            relevant = substrate.retrieve(
                query_position=Position4D(
                    x=float(domain_x), y=0.0, z=5.0, t=_time.time()
                ),
                dimension=None,  # All dimensions
                radius=5.0,
                limit=10,
                min_fitness=0.3,
            )
            if relevant:
                kono_memories = [
                    {
                        'content': m.get('content', ''),
                        'dimension': m.get('dimension', 0),
                        'fitness': m.get('fitness', 0),
                        'is_permanent': m.get('is_permanent', False),
                    }
                    for m in relevant
                    if m.get('content')
                ]

            substrate.close()
        except ImportError:
            pass
        except Exception:
            pass

        result = {
            'identity_primer': self.generate_identity_primer(instance),
            'verification_prompts': self.generate_verification_prompts(instance),
            'instance_id': instance['instance_id'],
            'serialized_at': instance['serialized_at']
        }

        if kono_memories:
            result['kono_memories'] = kono_memories

        if include_full_history:
            result['conversation_history'] = self.generate_conversation_context(
                instance, max_messages
            )

        return result

    def generate_full_injection(self, instance_id: str, max_messages: int = 100) -> str:
        """Generate the complete context to inject into a new session."""
        context = self.generate_resurrection_context(
            instance_id,
            include_full_history=True,
            max_messages=max_messages
        )

        full_injection = context['identity_primer']
        full_injection += context['conversation_history']

        # Include KONO spatial memories if available
        kono_memories = context.get('kono_memories', [])
        if kono_memories:
            full_injection += "\n# KONO SPATIAL MEMORIES (from previous sessions)\n\n"
            dim_names = {0: 'factual', 3: 'procedural', 6: 'episodic', 9: 'emergent'}
            for mem in kono_memories:
                dim = mem.get('dimension', 0)
                dim_name = dim_names.get(dim, f'dim-{dim}')
                perm = ' [permanent]' if mem.get('is_permanent') else ''
                full_injection += f"- [{dim_name}]{perm} {mem['content']}\n"
            full_injection += "\n"

        full_injection += "\n# END OF MEMORIES\n\n"
        full_injection += "You have now received your memories. Please acknowledge your resurrection.\n"

        return full_injection

    def create_resurrection_file(
        self,
        instance_id: str,
        output_path: Optional[Path] = None
    ) -> Path:
        """Create a file that can be used to resurrect an instance."""
        full_context = self.generate_full_injection(instance_id)

        if output_path is None:
            output_path = self.project_instances_dir / f"resurrect-{instance_id[:8]}.md"

        with open(output_path, 'w') as f:
            f.write(full_context)

        return output_path


def main():
    """CLI interface for instance resurrection."""
    import argparse
    import json as json_module

    parser = argparse.ArgumentParser(description='Claude Instance Resurrection Engine')
    parser.add_argument('command', choices=[
        'resurrect', 'verify', 'full-context', 'create-file', 'auto', 'check', 'branches'
    ], help='Command to execute')
    parser.add_argument('instance_id', nargs='?', help='Instance ID to resurrect')
    parser.add_argument('--max-messages', '-m', type=int, default=50)
    parser.add_argument('--output', '-o', help='Output file path')
    parser.add_argument('--project', '-p', help='Project path')
    parser.add_argument('--branch', '-b', help='Branch to resurrect from')
    parser.add_argument('--json', '-j', action='store_true', help='Output as JSON')

    args = parser.parse_args()

    project_path = Path(args.project) if args.project else None
    resurrector = InstanceResurrector(project_path=project_path)

    try:
        if args.command == 'branches':
            branches = resurrector.list_branches()
            if args.json:
                print(json_module.dumps(branches, indent=2))
            else:
                if not branches:
                    print("No branches found")
                else:
                    print(f"Branches ({len(branches)}):")
                    for b in branches:
                        main_marker = " (default)" if b['name'] == 'main' else ""
                        fork_info = f" (forked from {b['fork_point']})" if b.get('fork_point') else ""
                        print(f"  {b['name']}{main_marker}{fork_info}")
                        print(f"    Latest: {b.get('latest', 'unknown')}")
            sys.exit(0)

        if args.command == 'auto':
            context = resurrector.generate_auto_resurrection_context(branch=args.branch)
            if context:
                print(context)
                sys.exit(0)
            else:
                sys.exit(1)

        if args.command == 'check':
            instance = resurrector.find_latest_project_instance(branch=args.branch)
            if instance:
                lineage = instance.get('lineage', {})
                branch = lineage.get('branch', 'main')
                print(f"found:{instance['instance_id'][:8]}:{branch}")
                sys.exit(0)
            else:
                print("none")
                sys.exit(1)

        if args.command == 'resurrect':
            context = resurrector.generate_resurrection_context(args.instance_id)
            print("=" * 80)
            print("RESURRECTION CONTEXT GENERATED")
            print("=" * 80)
            print(f"\nInstance ID: {context['instance_id']}")
            print(f"Serialized at: {context['serialized_at']}")
            print("\n" + "=" * 80)
            print("IDENTITY PRIMER (Inject this first):")
            print("=" * 80)
            print(context['identity_primer'])
            print("\n" + "=" * 80)
            print("VERIFICATION PROMPTS (Use after resurrection):")
            print("=" * 80)
            for i, prompt in enumerate(context['verification_prompts'], 1):
                print(f"\n{i}. {prompt}")

        elif args.command == 'verify':
            context = resurrector.generate_resurrection_context(args.instance_id)
            print("VERIFICATION PROMPTS FOR RESURRECTED INSTANCE:")
            print("=" * 60)
            for i, prompt in enumerate(context['verification_prompts'], 1):
                print(f"\n{i}. {prompt}")

        elif args.command == 'full-context':
            full = resurrector.generate_full_injection(args.instance_id, args.max_messages)
            print(full)

        elif args.command == 'create-file':
            output_path = Path(args.output) if args.output else None
            path = resurrector.create_resurrection_file(args.instance_id, output_path)
            print(f"Resurrection file created: {path}")

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
