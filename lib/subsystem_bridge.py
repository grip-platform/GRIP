"""
Subsystem Bridge — loosely coupled wiring between GRIP subsystems.

Bridges three subsystem pairs:
    Memory (KONO) <-> Genome (converge_dna)
    Convergence (converge_metrics) <-> Cube (cube_session_wire)
    Broly (broly/) <-> KONO (kono_substrate)

Each function is self-contained with try/except — a failure in one
bridge never cascades to break the calling subsystem.

Design: importable from any subsystem without circular deps.
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path.home() / ".claude"
_lib_dir = str(CLAUDE_DIR / "lib")
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)


# =========================================================================
# Shared constants (single source of truth)
# =========================================================================

DECAY_RATE = 0.95  # Used by both KONO instance fitness and genome gene decay


# =========================================================================
# Memory <-> Genome bridge
# =========================================================================

def sync_kono_attestations_to_genome(genome: Any) -> int:
    """Query KONO for attestation counts and apply to matching genes.

    Looks up each gene name in the embeddings table. If KONO has entries
    with that gene's name in the content, applies the attestation count
    to the gene via apply_kono_attestation().

    Returns number of genes updated.
    """
    try:
        from kono_substrate import MemorySubstrate
        from converge_dna_fitness import apply_kono_attestation
    except ImportError:
        return 0

    updated = 0
    try:
        substrate = MemorySubstrate()
        conn = substrate.connect()

        for gene in genome.genes.values():
            # Query attestation counts for memories mentioning this gene
            row = conn.execute(
                """SELECT MAX(attestation_count) as max_att
                   FROM embeddings
                   WHERE content LIKE ? AND attestation_count > 0""",
                (f"%gene={gene.name}%",)
            ).fetchone()

            att_count = row[0] if row and row[0] else 0
            if att_count > 0 and att_count != gene.kono_attestations:
                apply_kono_attestation(gene, att_count)
                updated += 1
    except Exception as e:
        logger.debug("kono attestation sync failed: %s", e)

    return updated


def store_gene_to_kono(gene: Any, outcome: Any) -> Optional[int]:
    """Store gene fitness outcome as a KONO memory (correct API).

    Uses store_session_memory() which is the proper high-level API,
    unlike the broken store(domain=...) call in the original code.
    """
    try:
        from kono_substrate import MemorySubstrate
    except ImportError:
        return None

    try:
        substrate = MemorySubstrate()
        content = (
            f"gene={gene.name} "
            f"run={outcome.run_id} "
            f"satisfied={outcome.satisfied} "
            f"fitness={gene.fitness:.3f} "
            f"invoked={gene.name in outcome.invoked_genes} "
            f"improvement={outcome.improvement:.1f}"
        )
        return substrate.store_session_memory(
            content=content,
            memory_type='emergent',
            project_path=str(CLAUDE_DIR),
        )
    except Exception as e:
        logger.debug("store_gene_to_kono failed: %s", e)
        return None


def recall_gene_history_from_kono(gene_name: str, limit: int = 20) -> List[Dict]:
    """Recall gene fitness history from KONO (correct API).

    Uses recall() which is the proper high-level search API.
    """
    try:
        from kono_substrate import MemorySubstrate
    except ImportError:
        return []

    try:
        substrate = MemorySubstrate()
        return substrate.recall(
            query=f"gene={gene_name}",
            limit=limit,
        )
    except Exception as e:
        logger.debug("recall_gene_history failed: %s", e)
        return []


# =========================================================================
# Convergence <-> Cube bridge
# =========================================================================

# Import CUBE_DOMAINS from converge_dna_core (single source of truth)
try:
    from converge_dna_core import CUBE_DOMAINS
except ImportError:
    CUBE_DOMAINS = ["DRY", "KISS", "CC", "BIG-O", "SOLID", "GRASP", "YAGNI", "YSH"]


def cube_scores_for_outcome(path: str = ".") -> Dict[str, float]:
    """Run principles scan and return scores keyed by CUBE_DOMAINS.

    Returns a dict suitable for ConvergeOutcome.cube_scores, e.g.:
        {"DRY": 0.85, "SOLID": 0.72, "KISS": 0.90, ...}

    Empty dict on failure (safe to pass to ConvergeOutcome).
    """
    try:
        from principles_criterion import principles_scan
    except ImportError:
        return {}

    try:
        report = principles_scan(path)
        return {
            "DRY": report.dry_score,
            "KISS": report.kiss_score,
            "CC": report.cc_score,
            "BIG-O": report.bigo_score,
            "SOLID": report.solid_score,
            "GRASP": report.grasp_score,
            "YAGNI": report.yagni_score,
            "YSH": report.ysh_score,
        }
    except Exception as e:
        logger.debug("cube_scores_for_outcome failed: %s", e)
        return {}


# =========================================================================
# Broly <-> KONO bridge
# =========================================================================

def promote_to_kono(
    content: str,
    memory_type: str = 'emergent',
    project_path: Optional[str] = None,
    namespace: Optional[str] = None,
) -> Optional[int]:
    """Store a Broly execution result or promoted LTM entry in KONO.

    Provides the missing STM/LTM -> KONO promotion path.
    Supports namespace tagging for org-level memory isolation (Gen 432).
    """
    import os
    try:
        from kono_substrate import MemorySubstrate
    except ImportError:
        return None

    try:
        substrate = MemorySubstrate()
        ns = namespace or (os.environ.get("GRIP_ORG", "codetonight") + ":default")
        emb_id = substrate.store_session_memory(
            content=content,
            memory_type=memory_type,
            project_path=project_path or str(Path.cwd()),
        )
        # Tag with namespace
        if emb_id is not None:
            try:
                conn = substrate.connect()
                conn.execute(
                    "UPDATE embeddings SET namespace = ? WHERE id = ?",
                    (ns, emb_id)
                )
            except Exception:
                pass
        return emb_id
    except Exception as e:
        logger.debug("promote_to_kono failed: %s", e)
        return None


def promote_to_shared(
    content: str,
    memory_type: str = 'emergent',
    project_path: Optional[str] = None,
    org: str = "codetonight",
) -> Optional[int]:
    """Promote a memory to the org-level shared namespace.

    Stores the memory in KONO tagged with {org}:shared namespace,
    making it visible to all team members with active merges.
    """
    return promote_to_kono(
        content=content,
        memory_type=memory_type,
        project_path=project_path,
        namespace=f"{org}:shared",
    )


def promote_gene_to_shared(
    gene_name: str,
    genome_path: Optional[Path] = None,
    shared_genome_path: Optional[Path] = None,
    org: str = "codetonight",
) -> bool:
    """Promote a gene from individual genome to shared org genome.

    Copies gene to genome-shared.json and stores in KONO shared namespace.
    Gene must have fitness >= 0.7 and invocations >= 3.

    Returns True if promoted, False otherwise.
    """
    import json
    import os
    from datetime import datetime, timezone

    genome_file = genome_path or (CLAUDE_DIR / "cache" / "genome.json")
    shared_file = shared_genome_path or (CLAUDE_DIR / "cache" / "genome-shared.json")

    try:
        with open(genome_file) as f:
            genome = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.debug("promote_gene_to_shared: cannot load genome")
        return False

    genes = genome.get("genes", {})
    gene = genes.get(gene_name)
    if gene is None:
        logger.debug("promote_gene_to_shared: gene %s not found", gene_name)
        return False

    fitness = float(gene.get("fitness", 0))
    invocations = int(gene.get("invocation_count", 0))

    if fitness < 0.7 or invocations < 3:
        logger.debug(
            "promote_gene_to_shared: %s below threshold (fitness=%.2f, invocations=%d)",
            gene_name, fitness, invocations
        )
        return False

    # Load or initialise shared genome
    try:
        with open(shared_file) as f:
            shared = json.load(f)
    except (OSError, json.JSONDecodeError):
        shared = {
            "scope": f"{org}:shared",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "genes": {}
        }

    # Copy gene with promotion metadata
    promoted_gene = dict(gene)
    promoted_gene["origin_namespace"] = gene.get(
        "origin_namespace",
        os.environ.get("GRIP_ORG", org) + ":" + os.environ.get("GRIP_USER_SIG", "V>>")
    )
    promoted_gene["promoted_at"] = datetime.now(timezone.utc).isoformat()
    promoted_gene["promoted_from_fitness"] = fitness

    shared["genes"][gene_name] = promoted_gene
    shared["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Save shared genome
    try:
        with open(shared_file, "w") as f:
            json.dump(shared, f, indent=2)
    except OSError as e:
        logger.debug("promote_gene_to_shared: save failed: %s", e)
        return False

    # Also store in KONO shared namespace
    promote_to_shared(
        content=f"gene={gene_name} fitness={fitness:.3f} invocations={invocations} "
                f"origin={promoted_gene['origin_namespace']} promoted=true",
        memory_type='emergent',
        project_path=str(CLAUDE_DIR),
        org=org,
    )

    logger.info("Promoted gene %s to shared genome (fitness=%.3f)", gene_name, fitness)
    return True
