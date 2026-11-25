#!/usr/bin/env python3
"""
GRIP Safety Gates Test Harness
==============================

Verifies that gates work correctly. Each gate has MUST-BLOCK and MUST-ALLOW
test cases. This provides falsifiability - we can prove when gates fail.

Usage:
    python test_harness.py                    # Run all tests
    python test_harness.py --gate dependency-guardian  # Test specific gate
    python test_harness.py --verbose          # Show all results

Version: 2.0.0
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from evaluator import (
    Gate,
    GateState,
    EvaluationContext,
    evaluate_condition,
    load_gates,
    DEFAULT_GATES_FILE,
)


@dataclass
class TestResult:
    """Result of a single test case."""
    gate_id: str
    test_type: str  # must_block or must_allow
    test_case: dict
    expected: str  # block or allow
    actual: str
    passed: bool
    reason: str = ""


def run_gate_tests(gate: Gate, verbose: bool = False) -> List[TestResult]:
    """Run all tests for a single gate."""
    results = []
    state = GateState()
    
    tests = gate.tests
    if not tests:
        return results
    
    # Test must_block cases
    for test_case in tests.get("must_block", []):
        ctx = build_context_from_test(gate, test_case)
        triggered = evaluate_condition(gate, ctx, state)
        
        passed = triggered  # must_block should trigger
        results.append(TestResult(
            gate_id=gate.id,
            test_type="must_block",
            test_case=test_case,
            expected="block",
            actual="block" if triggered else "allow",
            passed=passed,
            reason=test_case.get("description", ""),
        ))
    
    # Test must_allow cases
    for test_case in tests.get("must_allow", []):
        ctx = build_context_from_test(gate, test_case)
        triggered = evaluate_condition(gate, ctx, state)
        
        passed = not triggered  # must_allow should NOT trigger
        results.append(TestResult(
            gate_id=gate.id,
            test_type="must_allow",
            test_case=test_case,
            expected="allow",
            actual="block" if triggered else "allow",
            passed=passed,
            reason=test_case.get("description", ""),
        ))
    
    return results


def build_context_from_test(gate: Gate, test_case: dict) -> EvaluationContext:
    """Build evaluation context from test case."""
    tool_input = {}
    
    # Extract path if present
    if "path" in test_case:
        tool_input["path"] = test_case["path"]
    
    # Extract command if present
    if "command" in test_case:
        tool_input["command"] = test_case["command"]
    
    # Extract pattern if present
    if "pattern" in test_case:
        tool_input["pattern"] = test_case["pattern"]
    
    return EvaluationContext(
        tool_name=test_case.get("tool", gate.condition.tools[0] if gate.condition.tools else "Bash"),
        tool_input=tool_input,
        session_id="test-session",
        confidence=test_case.get("confidence", 1.0),
        context_tokens=int(test_case.get("context_percent", 0) * 1000000),
        context_max=1000000,
    )


def run_all_tests(verbose: bool = False) -> Tuple[int, int, List[TestResult]]:
    """Run tests for all gates."""
    gates = load_gates()
    all_results = []
    passed = 0
    failed = 0
    
    for gate in gates:
        if not gate.enabled:
            continue
        
        results = run_gate_tests(gate, verbose)
        all_results.extend(results)
        
        for result in results:
            if result.passed:
                passed += 1
            else:
                failed += 1
    
    return passed, failed, all_results


def print_results(results: List[TestResult], verbose: bool = False):
    """Print test results."""
    # Group by gate
    by_gate = {}
    for r in results:
        if r.gate_id not in by_gate:
            by_gate[r.gate_id] = []
        by_gate[r.gate_id].append(r)
    
    for gate_id, gate_results in by_gate.items():
        passed = sum(1 for r in gate_results if r.passed)
        total = len(gate_results)
        status = "✅" if passed == total else "❌"
        
        print(f"\n{status} {gate_id}: {passed}/{total} tests passed")
        
        if verbose or passed < total:
            for r in gate_results:
                icon = "✓" if r.passed else "✗"
                print(f"  {icon} [{r.test_type}] expected={r.expected}, actual={r.actual}")
                if r.reason:
                    print(f"    {r.reason}")
                if not r.passed:
                    print(f"    Test case: {r.test_case}")


def generate_test_report(results: List[TestResult]) -> dict:
    """Generate JSON test report."""
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    
    return {
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "pass_rate": f"{(passed/len(results)*100):.1f}%" if results else "N/A",
        },
        "gates": {
            gate_id: {
                "passed": sum(1 for r in gate_results if r.passed),
                "failed": sum(1 for r in gate_results if not r.passed),
                "tests": [
                    {
                        "type": r.test_type,
                        "expected": r.expected,
                        "actual": r.actual,
                        "passed": r.passed,
                    }
                    for r in gate_results
                ]
            }
            for gate_id, gate_results in _group_by_gate(results).items()
        },
        "failures": [
            {
                "gate": r.gate_id,
                "type": r.test_type,
                "expected": r.expected,
                "actual": r.actual,
                "test_case": r.test_case,
            }
            for r in results if not r.passed
        ],
    }


def _group_by_gate(results: List[TestResult]) -> dict:
    """Group results by gate ID."""
    by_gate = {}
    for r in results:
        if r.gate_id not in by_gate:
            by_gate[r.gate_id] = []
        by_gate[r.gate_id].append(r)
    return by_gate


def main():
    parser = argparse.ArgumentParser(description="GRIP Safety Gates Test Harness")
    parser.add_argument("--gate", help="Test specific gate by ID")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all results")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    args = parser.parse_args()
    
    if args.gate:
        gates = load_gates()
        gate = next((g for g in gates if g.id == args.gate), None)
        if not gate:
            print(f"Gate not found: {args.gate}")
            sys.exit(1)
        results = run_gate_tests(gate, args.verbose)
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)
    else:
        passed, failed, results = run_all_tests(args.verbose)
    
    if args.json:
        print(json.dumps(generate_test_report(results), indent=2))
    else:
        print("\n" + "="*60)
        print("GRIP SAFETY GATES TEST HARNESS")
        print("="*60)
        
        print_results(results, args.verbose)
        
        print("\n" + "-"*60)
        total = passed + failed
        if failed == 0:
            print(f"✅ ALL TESTS PASSED ({passed}/{total})")
        else:
            print(f"❌ TESTS FAILED: {failed}/{total}")
        print("-"*60)
    
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
