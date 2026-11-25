"""Re-export shim -- converge_inherit merged into converge_transfer.

All symbols are now in converge_transfer. This shim maintains
backward compatibility for existing import sites.
"""
from converge_transfer import (
    ConvergenceStrategy,
    STRATEGY_STORE,
    record_success,
    find_transferable,
    apply_inheritance,
    inheritance_report,
    _load_strategies,
    _save_strategies,
)

__all__ = [
    "ConvergenceStrategy",
    "STRATEGY_STORE",
    "record_success",
    "find_transferable",
    "apply_inheritance",
    "inheritance_report",
    "_load_strategies",
    "_save_strategies",
]
