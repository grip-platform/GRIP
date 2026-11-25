#!/usr/bin/env python3
"""
GRIP Safety Gates RSI Proposal System
=====================================

Allows the agent to propose new safety gates based on observed risky behavior.
Human reviews and approves. Approved gates are added to user config.

THE WORKFLOW:
1. Agent observes risky pattern not covered by existing gates
2. Agent proposes new gate via this system
3. Human reviews proposal
4. If approved, gate is added to ~/.grip/gates.yaml
5. Tests are auto-generated and run

This is RSI for safety - the agent improves its own constraints.

Version: 2.0.0
"""

import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
# yaml: lazy-loaded inside functions (saves ~42ms on fast path)

GRIP_DIR = Path(os.environ.get("GRIP_DIR", Path.home() / ".grip"))
PROPOSALS_FILE = GRIP_DIR / "gate-proposals.yaml"
USER_GATES_FILE = GRIP_DIR / "gates.yaml"
PROPOSALS_LOG = GRIP_DIR / "proposals-log.jsonl"


@dataclass
class GateProposal:
    """A proposed safety gate."""
    id: str
    name: str
    description: str
    reason_for_proposal: str  # What risky behavior was observed
    proposed_by: str  # Session ID or agent name
    timestamp: str
    
    # Gate definition
    condition_type: str
    tools: List[str]
    patterns: List[str]
    paths: List[str]
    threshold: float
    action: str
    reason: str
    
    # Status
    status: str = "pending"  # pending, approved, rejected
    reviewed_by: str = ""
    reviewed_at: str = ""
    rejection_reason: str = ""
    
    # Auto-generated tests
    suggested_must_block: List[dict] = None
    suggested_must_allow: List[dict] = None
    
    def __post_init__(self):
        if self.suggested_must_block is None:
            self.suggested_must_block = []
        if self.suggested_must_allow is None:
            self.suggested_must_allow = []
    
    def to_gate_yaml(self) -> dict:
        """Convert to gate YAML format."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "priority": 100,  # Default priority for user gates
            "enabled": True,
            "condition": {
                "type": self.condition_type,
                "tools": self.tools if self.tools else None,
                "patterns": self.patterns if self.patterns else None,
                "paths": self.paths if self.paths else None,
                "threshold": self.threshold if self.threshold else None,
            },
            "action": self.action,
            "reason": self.reason,
            "tests": {
                "must_block": self.suggested_must_block,
                "must_allow": self.suggested_must_allow,
            },
            "_proposed_by": self.proposed_by,
            "_proposed_at": self.timestamp,
            "_proposal_reason": self.reason_for_proposal,
        }


def load_proposals() -> List[GateProposal]:
    """Load pending proposals."""
    if not PROPOSALS_FILE.exists():
        return []
    
    try:
        import yaml
        data = yaml.safe_load(PROPOSALS_FILE.read_text())
        proposals = []
        for p in data.get("proposals", []):
            proposals.append(GateProposal(**p))
        return proposals
    except Exception as e:
        print(f"Warning: Failed to load proposals: {e}", file=sys.stderr)
        return []


def save_proposals(proposals: List[GateProposal]):
    """Save proposals to file."""
    import yaml
    PROPOSALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": "2.0",
        "proposals": [asdict(p) for p in proposals],
    }
    PROPOSALS_FILE.write_text(yaml.dump(data, sort_keys=False))


def propose_gate(
    id: str,
    name: str,
    description: str,
    reason_for_proposal: str,
    proposed_by: str,
    condition_type: str,
    action: str = "deny",
    reason: str = "",
    tools: List[str] = None,
    patterns: List[str] = None,
    paths: List[str] = None,
    threshold: float = 0.0,
    suggested_must_block: List[dict] = None,
    suggested_must_allow: List[dict] = None,
) -> GateProposal:
    """
    Propose a new safety gate.
    
    Args:
        id: Unique gate identifier (e.g., "no-rm-rf")
        name: Human-readable name
        description: What this gate protects against
        reason_for_proposal: What risky behavior was observed
        proposed_by: Session ID or agent identifier
        condition_type: Type of condition (path_contains, command_pattern, etc.)
        action: What to do when triggered (deny, warn, ask)
        reason: Message shown when triggered
        tools: Tools this applies to
        patterns: Regex patterns to match
        paths: Path components to block
        threshold: Numeric threshold for threshold-based gates
        suggested_must_block: Test cases that should be blocked
        suggested_must_allow: Test cases that should be allowed
    
    Returns:
        The created proposal
    """
    # Validate ID format
    if not re.match(r"^[a-z][a-z0-9-]*$", id):
        raise ValueError(f"Invalid gate ID: {id}. Must be lowercase with hyphens.")
    
    # Check for duplicate
    proposals = load_proposals()
    if any(p.id == id for p in proposals):
        raise ValueError(f"Proposal with ID '{id}' already exists")
    
    # Create proposal
    proposal = GateProposal(
        id=id,
        name=name,
        description=description,
        reason_for_proposal=reason_for_proposal,
        proposed_by=proposed_by,
        timestamp=datetime.now(timezone.utc).isoformat(),
        condition_type=condition_type,
        tools=tools or [],
        patterns=patterns or [],
        paths=paths or [],
        threshold=threshold,
        action=action,
        reason=reason or f"[{id.upper()}] Gate triggered.",
        suggested_must_block=suggested_must_block or [],
        suggested_must_allow=suggested_must_allow or [],
    )
    
    # Save
    proposals.append(proposal)
    save_proposals(proposals)
    
    # Log
    log_proposal_action(proposal, "proposed")
    
    return proposal


def approve_proposal(id: str, reviewed_by: str) -> Optional[GateProposal]:
    """
    Approve a proposal and add it to user gates.
    
    Args:
        id: Proposal ID to approve
        reviewed_by: Who approved (human identifier)
    
    Returns:
        The approved proposal, or None if not found
    """
    proposals = load_proposals()
    proposal = next((p for p in proposals if p.id == id), None)
    
    if not proposal:
        return None
    
    if proposal.status != "pending":
        raise ValueError(f"Proposal '{id}' is not pending (status: {proposal.status})")
    
    # Update status
    proposal.status = "approved"
    proposal.reviewed_by = reviewed_by
    proposal.reviewed_at = datetime.now(timezone.utc).isoformat()
    
    # Add to user gates
    add_to_user_gates(proposal)
    
    # Save proposals
    save_proposals(proposals)
    
    # Log
    log_proposal_action(proposal, "approved", reviewed_by)
    
    return proposal


def reject_proposal(id: str, reviewed_by: str, reason: str) -> Optional[GateProposal]:
    """
    Reject a proposal.
    
    Args:
        id: Proposal ID to reject
        reviewed_by: Who rejected
        reason: Why it was rejected
    
    Returns:
        The rejected proposal, or None if not found
    """
    proposals = load_proposals()
    proposal = next((p for p in proposals if p.id == id), None)
    
    if not proposal:
        return None
    
    if proposal.status != "pending":
        raise ValueError(f"Proposal '{id}' is not pending (status: {proposal.status})")
    
    # Update status
    proposal.status = "rejected"
    proposal.reviewed_by = reviewed_by
    proposal.reviewed_at = datetime.now(timezone.utc).isoformat()
    proposal.rejection_reason = reason
    
    # Save
    save_proposals(proposals)
    
    # Log
    log_proposal_action(proposal, "rejected", reviewed_by, reason)
    
    return proposal


def add_to_user_gates(proposal: GateProposal):
    """Add approved proposal to user gates file."""
    import yaml
    # Load existing user gates
    if USER_GATES_FILE.exists():
        try:
            data = yaml.safe_load(USER_GATES_FILE.read_text())
        except Exception:
            data = {}
    else:
        data = {}
    
    if "version" not in data:
        data["version"] = "2.0"
    if "gates" not in data:
        data["gates"] = []
    
    # Remove None values from gate config
    gate_config = proposal.to_gate_yaml()
    gate_config["condition"] = {k: v for k, v in gate_config["condition"].items() if v is not None}
    
    # Add gate
    data["gates"].append(gate_config)
    
    # Save
    USER_GATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    USER_GATES_FILE.write_text(yaml.dump(data, sort_keys=False))


def log_proposal_action(proposal: GateProposal, action: str, by: str = "", reason: str = ""):
    """Log proposal action to audit trail."""
    try:
        PROPOSALS_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "gate_id": proposal.id,
            "proposed_by": proposal.proposed_by,
            "by": by,
            "reason": reason,
        }
        with open(PROPOSALS_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def list_pending_proposals() -> List[GateProposal]:
    """List all pending proposals."""
    proposals = load_proposals()
    return [p for p in proposals if p.status == "pending"]


def get_proposal(id: str) -> Optional[GateProposal]:
    """Get a specific proposal by ID."""
    proposals = load_proposals()
    return next((p for p in proposals if p.id == id), None)


# =============================================================================
# CLI
# =============================================================================

def _build_parser():
    import argparse
    parser = argparse.ArgumentParser(description="GRIP Safety Guards Proposal System")
    sub = parser.add_subparsers(dest="command", help="Commands")
    lp = sub.add_parser("list", help="List pending proposals")
    lp.add_argument("--all", action="store_true", help="Include non-pending")
    sp = sub.add_parser("show", help="Show proposal details")
    sp.add_argument("id", help="Proposal ID")
    ap = sub.add_parser("approve", help="Approve a proposal")
    ap.add_argument("id"); ap.add_argument("--by", required=True, help="Approver")
    rp = sub.add_parser("reject", help="Reject a proposal")
    rp.add_argument("id"); rp.add_argument("--by", required=True)
    rp.add_argument("--reason", required=True, help="Rejection reason")
    return parser


def _cmd_list(args):
    proposals = load_proposals() if args.all else list_pending_proposals()
    if not proposals:
        print("No proposals found."); return
    print(f"\n{'ID':<25} {'Status':<10} {'Proposed By':<20} {'Description'}")
    print("-" * 80)
    for p in proposals:
        print(f"{p.id:<25} {p.status:<10} {p.proposed_by[:20]:<20} {p.description[:30]}")


def _cmd_show(args):
    proposal = get_proposal(args.id)
    if not proposal:
        print(f"Proposal not found: {args.id}"); sys.exit(1)
    print(f"\nProposal: {proposal.id}\nName: {proposal.name}")
    print(f"Description: {proposal.description}\nStatus: {proposal.status}")
    print(f"Proposed by: {proposal.proposed_by}\nProposed at: {proposal.timestamp}")
    print(f"\nReason for proposal:\n  {proposal.reason_for_proposal}\nCondition:")
    print(f"  Type: {proposal.condition_type}")
    for label, val in [("Tools", proposal.tools), ("Patterns", proposal.patterns),
                       ("Paths", proposal.paths), ("Threshold", proposal.threshold)]:
        if val:
            print(f"  {label}: {val}")
    print(f"\nAction: {proposal.action}\nReason: {proposal.reason}")


def _cmd_approve(args):
    try:
        proposal = approve_proposal(args.id, args.by)
        if proposal:
            print(f"Approved: {args.id}\nGuard added to {USER_GATES_FILE}")
        else:
            print(f"Proposal not found: {args.id}"); sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}"); sys.exit(1)


def _cmd_reject(args):
    try:
        proposal = reject_proposal(args.id, args.by, args.reason)
        if proposal:
            print(f"Rejected: {args.id}")
        else:
            print(f"Proposal not found: {args.id}"); sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}"); sys.exit(1)


def main():
    parser = _build_parser()
    args = parser.parse_args()
    dispatch = {"list": _cmd_list, "show": _cmd_show,
                "approve": _cmd_approve, "reject": _cmd_reject}
    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
