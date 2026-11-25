"""
GRIP Resurrection Engine
Modular instance resurrection for GRIP v6.0

Modules:
    - loader: Instance loading and finding
    - branches: Branch management
    - context: Context generation (primers, conversation, verification)
    - orchestrator: Full resurrection coordination

Usage:
    from resurrection import InstanceResurrector
    resurrector = InstanceResurrector()
    context = resurrector.generate_resurrection_context(instance_id)
"""

from .orchestrator import InstanceResurrector

__all__ = ['InstanceResurrector']
