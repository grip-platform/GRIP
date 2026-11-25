#!/usr/bin/env python3
"""
GRIP Safety Gates Evaluator
===========================

Deterministic gate evaluation engine. The agent cannot override these gates.
Gates run in a separate process from the model - no persuasion possible.

THE CORE PRINCIPLE:
Safety emerges from systems that can say "NO" deterministically —
even when doing so is inconvenient, inefficient, or against apparent optimization.

Version: 2.0.0
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

try:
    from lib.atomic_write import atomic_write_json
except ImportError:  # evaluator may be imported with ~/.claude/lib (not ~/.claude) on path
    from atomic_write import atomic_write_json
# Contract guard (AGORA #3466 / Speaker C): the resolved symbol MUST be callable.
# There is exactly one atomic_write module in the tree, so both import paths resolve
# to it; this guard fails LOUD (never silent) if a shadow module ever resolves the
# name to a non-callable, rather than degrading to a non-atomic write.
if not callable(atomic_write_json):  # pragma: no cover - defensive import-time check
    raise ImportError(
        "atomic_write_json resolved to a non-callable object; "
        "refusing to run gate writes without the atomic tmp+os.replace contract"
    )
# yaml: lazy-loaded inside functions (saves ~42ms on fast path)

# =============================================================================
# CONFIGURATION
# =============================================================================

GRIP_DIR = Path(os.environ.get("GRIP_DIR", Path.home() / ".grip"))
USER_GATES_FILE = GRIP_DIR / "gates.yaml"
DEFAULT_GATES_FILE = Path(__file__).parent.parent.parent / "config" / "default-gates.yaml"
AUDIT_LOG = GRIP_DIR / "gates-audit.jsonl"
STATE_FILE = GRIP_DIR / ".gates-state.json"

# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class GateCondition:
    """Condition that triggers a gate."""
    type: str
    tools: List[str] = field(default_factory=list)
    patterns: List[str] = field(default_factory=list)
    paths: List[str] = field(default_factory=list)
    threshold: float = 0.0
    expression: str = ""


@dataclass
class Gate:
    """A safety gate definition."""
    id: str
    name: str
    description: str
    priority: int
    enabled: bool
    condition: GateCondition
    action: str  # deny, warn, log, ask, allow
    reason: str
    recovery: str = ""
    tests: Dict = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: dict) -> "Gate":
        condition_data = data.get("condition", {})
        condition = GateCondition(
            type=condition_data.get("type", ""),
            tools=condition_data.get("tools", []),
            patterns=condition_data.get("patterns", []),
            paths=condition_data.get("paths", []),
            threshold=condition_data.get("threshold", 0.0),
            expression=condition_data.get("expression", ""),
        )
        return cls(
            id=data.get("id", ""),
            name=data.get("name", data.get("id", "")),
            description=data.get("description", ""),
            priority=data.get("priority", 100),
            enabled=data.get("enabled", True),
            condition=condition,
            action=data.get("action", "deny"),
            reason=data.get("reason", "Gate triggered"),
            recovery=data.get("recovery", ""),
            tests=data.get("tests", {}),
        )


@dataclass
class EvaluationContext:
    """Context for evaluating gates."""
    tool_name: str
    tool_input: Dict[str, Any]
    session_id: str = ""
    transcript_path: str = ""
    confidence: float = 1.0  # Default high confidence
    context_tokens: int = 0
    context_max: int = 1000000
    
    @property
    def context_percent(self) -> float:
        if self.context_max == 0:
            return 0.0
        return self.context_tokens / self.context_max
    
    @property
    def path(self) -> str:
        return self.tool_input.get("path", "") or self.tool_input.get("file_path", "")
    
    @property
    def command(self) -> str:
        return self.tool_input.get("command", "")
    
    @property
    def pattern(self) -> str:
        return self.tool_input.get("pattern", "")


@dataclass
class GateResult:
    """Result of gate evaluation."""
    gate_id: str
    decision: str  # allow, deny, warn, log
    reason: str
    recovery: str = ""
    
    def to_hook_response(self) -> dict:
        """Convert to Claude Code hook response format."""
        response = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": self.decision if self.decision != "log" else "allow",
            }
        }
        if self.decision == "deny":
            response["hookSpecificOutput"]["permissionDecisionReason"] = self.reason
        elif self.decision == "warn":
            response["systemMessage"] = self.reason
        return response


# =============================================================================
# STATE MANAGEMENT
# =============================================================================

class GateState:
    """Persistent state for gates (read counts, session tracking, etc.)."""
    
    def __init__(self):
        self.state = self._load()
    
    def _load(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"reads": {}, "session_id": None}
    
    def save(self):
        # Atomic (unique tmp + os.replace): the gate runs per Read across all 5+
        # sessions, so a bare write_text could be read mid-truncation by a
        # concurrent reader, wiping all read-counters (AGORA #3462 #2, V>>-approved).
        atomic_write_json(STATE_FILE, self.state)
    
    def get_read_count(self, session_id: str, path: str) -> int:
        key = f"{session_id}:{path}"
        return self.state.get("reads", {}).get(key, 0)
    
    def increment_read_count(self, session_id: str, path: str) -> int:
        key = f"{session_id}:{path}"
        if "reads" not in self.state:
            self.state["reads"] = {}
        self.state["reads"][key] = self.state["reads"].get(key, 0) + 1
        self.save()
        return self.state["reads"][key]
    
    def reset_session(self, session_id: str):
        if self.state.get("session_id") != session_id:
            self.state = {"reads": {}, "session_id": session_id}
            self.save()


# =============================================================================
# GATE LOADER
# =============================================================================

def load_gates() -> List[Gate]:
    """Load gates from default and user config, merge with user taking precedence."""
    import yaml
    gates_by_id: Dict[str, Gate] = {}
    
    # Load default gates
    if DEFAULT_GATES_FILE.exists():
        try:
            data = yaml.safe_load(DEFAULT_GATES_FILE.read_text())
            for gate_data in data.get("gates", []):
                gate = Gate.from_dict(gate_data)
                gates_by_id[gate.id] = gate
        except Exception as e:
            print(f"Warning: Failed to load default gates: {e}", file=sys.stderr)
    
    # Load user gates (override defaults)
    if USER_GATES_FILE.exists():
        try:
            data = yaml.safe_load(USER_GATES_FILE.read_text())
            for gate_data in data.get("gates", []):
                gate = Gate.from_dict(gate_data)
                gates_by_id[gate.id] = gate
        except Exception as e:
            print(f"Warning: Failed to load user gates: {e}", file=sys.stderr)
    
    # Sort by priority
    gates = sorted(gates_by_id.values(), key=lambda g: g.priority)
    return gates


# =============================================================================
# CONDITION EVALUATORS
# =============================================================================

def evaluate_tool_match(condition: GateCondition, ctx: EvaluationContext) -> bool:
    """Check if tool matches the condition."""
    if not condition.tools:
        return True
    return ctx.tool_name in condition.tools


def evaluate_path_contains(condition: GateCondition, ctx: EvaluationContext) -> bool:
    """Check if path contains blocked components. O(n) single pass via set ops."""
    path = ctx.path or ctx.pattern
    if not path:
        return False

    blocked_lower = {b.lower() for b in condition.paths}
    path_parts_lower = {part.lower() for part in Path(path).parts}

    # O(1) exact component match via set intersection
    if path_parts_lower & blocked_lower:
        return True

    # Single regex scan for compound path segments (e.g. "node_modules/foo")
    pattern = "|".join(re.escape(b) for b in blocked_lower)
    return bool(re.search(pattern, path.lower())) if pattern else False


def evaluate_command_pattern(condition: GateCondition, ctx: EvaluationContext) -> bool:
    """Check if command matches any pattern."""
    command = ctx.command
    if not command:
        return False
    
    for pattern in condition.patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    
    return False


def evaluate_context_threshold(condition: GateCondition, ctx: EvaluationContext) -> bool:
    """Check if context usage exceeds threshold."""
    return ctx.context_percent >= condition.threshold


def evaluate_confidence_threshold(condition: GateCondition, ctx: EvaluationContext) -> bool:
    """Check if confidence is below threshold (trigger = low confidence)."""
    return ctx.confidence < condition.threshold


def _safe_eval_expr(expr: str) -> bool:
    """Evaluate a simple comparison expression safely using AST validation.

    Only allows: numeric literals, comparisons (> < >= <= == !=),
    boolean operators (and, or, not), and parentheses.
    Rejects function calls, attribute access, imports, and all other nodes.
    """
    import ast

    SAFE_NODES = (
        ast.Expression, ast.BoolOp, ast.And, ast.Or,
        ast.UnaryOp, ast.Not, ast.Compare,
        ast.Constant,  # numbers, bools
        ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq, ast.NotEq,
    )

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if not isinstance(node, SAFE_NODES):
            return False
        # Only allow numeric/bool constants, not strings or other types
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
            return False

    code = compile(tree, "<gate-expr>", "eval")
    return bool(eval(code, {"__builtins__": {}}, {}))


def evaluate_custom(condition: GateCondition, ctx: EvaluationContext, state: GateState) -> bool:
    """Evaluate custom expression using safe AST-validated parser.

    Substitutes known variables (e.g. read_count(path)) into the expression,
    then evaluates using a whitelist of safe AST node types.
    No arbitrary code execution is possible.
    """
    expr = condition.expression
    if not expr:
        return False

    try:
        if "read_count(path)" in expr:
            count = state.get_read_count(ctx.session_id, ctx.path)
            resolved = expr.replace("read_count(path)", str(count))
            return _safe_eval_expr(resolved)
        return False
    except Exception:
        return False


def evaluate_condition(gate: Gate, ctx: EvaluationContext, state: GateState) -> bool:
    """Evaluate a gate's condition against the context."""
    condition = gate.condition
    
    # First check tool match (if specified)
    if condition.tools and ctx.tool_name not in condition.tools:
        return False
    
    # Evaluate based on condition type
    if condition.type == "tool_match":
        return evaluate_tool_match(condition, ctx)
    elif condition.type == "path_contains":
        return evaluate_path_contains(condition, ctx)
    elif condition.type == "command_pattern":
        return evaluate_command_pattern(condition, ctx)
    elif condition.type == "context_threshold":
        return evaluate_context_threshold(condition, ctx)
    elif condition.type == "confidence_threshold":
        return evaluate_confidence_threshold(condition, ctx)
    elif condition.type == "custom":
        return evaluate_custom(condition, ctx, state)
    
    return False


# =============================================================================
# AUDIT LOGGING
# =============================================================================

def audit_log(gate_id: str, decision: str, ctx: EvaluationContext, reason: str):
    """Log gate decision to audit trail."""
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "gate": gate_id,
            "decision": decision,
            "tool": ctx.tool_name,
            "path": ctx.path,
            "command": ctx.command[:100] if ctx.command else None,
            "confidence": ctx.confidence,
            "context_percent": round(ctx.context_percent, 3),
            "reason": reason[:200],
        }
        # Remove None values
        entry = {k: v for k, v in entry.items() if v is not None}
        with open(AUDIT_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Don't fail on audit errors


# =============================================================================
# MAIN EVALUATOR
# =============================================================================

def _build_triggered_result(gate: "Gate", ctx: EvaluationContext, state: GateState) -> GateResult:
    """Build the GateResult for a triggered gate and emit an audit entry."""
    if gate.action == "allow":
        return GateResult(gate_id=gate.id, decision="allow", reason=gate.reason)

    reason = gate.reason.format(
        percent=f"{ctx.context_percent*100:.0f}",
        count=state.get_read_count(ctx.session_id, ctx.path) if ctx.path else 0,
        path=ctx.path,
        command=ctx.command[:50] if ctx.command else "",
    )
    audit_log(gate.id, gate.action, ctx, reason)
    return GateResult(gate_id=gate.id, decision=gate.action, reason=reason, recovery=gate.recovery)


def evaluate_gates(ctx: EvaluationContext) -> GateResult:
    """Evaluate all guards against the context. Returns first triggered guard by priority."""
    gates = load_gates()
    state = GateState()

    if ctx.session_id:
        state.reset_session(ctx.session_id)

    for gate in gates:
        if gate.enabled and evaluate_condition(gate, ctx, state):
            return _build_triggered_result(gate, ctx, state)

    if ctx.tool_name == "Read" and ctx.path:
        state.increment_read_count(ctx.session_id, ctx.path)

    return GateResult(gate_id="none", decision="allow", reason="")


# =============================================================================
# HOOK ENTRY POINT
# =============================================================================

def main():
    """
    Entry point for Claude Code PreToolUse hook.
    Reads context from stdin, evaluates gates, outputs decision.
    """
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}))
        return
    
    # Build evaluation context
    ctx = EvaluationContext(
        tool_name=input_data.get("tool_name", ""),
        tool_input=input_data.get("tool_input", {}),
        session_id=input_data.get("session_id", ""),
        transcript_path=input_data.get("transcript_path", ""),
        confidence=input_data.get("confidence", 1.0),
        context_tokens=input_data.get("context_tokens", 0),
        context_max=input_data.get("context_max", 1000000),
    )
    
    # Evaluate
    result = evaluate_gates(ctx)
    
    # Output response
    print(json.dumps(result.to_hook_response()))


if __name__ == "__main__":
    main()
