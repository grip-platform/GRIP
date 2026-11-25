"""Fast heuristic approximations for GRIP gate checks.

These functions prioritize speed and recall over precision. They run on
every Write/Edit tool call, so they must be lightweight (no AST parsing).
False positives are acceptable for soft gates that warn rather than block.

For precise analysis, use the corresponding library functions:
- detect_nested_loops() -> lib/complexity_analyzer.py max_complexity_class()
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

# W8 (2026-05-05): documented-intrinsic skip — `# INSIGHT[<12-16 hex>]: hook`
# annotations above an offending construct downgrade the gate to a logged
# skip with empirical justification (the hypothesis fingerprint links to
# learning/insights.jsonl + data/converge-fixed-points.jsonl).
_INSIGHT_RE = re.compile(r"#\s*INSIGHT\[([0-9a-f]{12,16})\]:\s*(.+)")


def _line_has_insight_skip(lines: list[str], idx: int) -> tuple[str, str] | None:
    """Return (fingerprint, hook) if line ``idx`` has an insight annotation
    on itself or in the 2 lines above, else None."""
    for offset in (0, 1, 2):
        target_idx = idx - offset
        if target_idx < 0:
            return None
        match = _INSIGHT_RE.search(lines[target_idx])
        if match:
            return match.group(1), match.group(2).strip()
    return None


def _log_gate_bypass(detector: str, fingerprint: str, hook: str, line_idx: int) -> None:
    """Append a structured record to logs/gate-bypass-via-insight.jsonl."""
    log = Path.home() / ".claude" / "logs" / "gate-bypass-via-insight.jsonl"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "detector": detector,
            "fingerprint": fingerprint,
            "hook": hook,
            "line_idx": line_idx,
        }
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass  # Best-effort logging — never block detection.


def detect_nested_loops(content: str) -> bool:
    """Detect nested for/while loops in Python code via indentation analysis.

    This is a fast heuristic approximation — NOT AST-based. It may produce
    false positives on sequential loops at different indentation levels
    (e.g., a loop inside an if followed by another loop). False positives
    are acceptable because Gate Q is a soft warning gate, not a hard block.

    Bounded-loop awareness:
    - Lines annotated with ``# O(1):`` are treated as constant-time.
    - Lines with ``# INSIGHT[<fp>]:`` annotations within 2 lines above
      are treated as documented-intrinsic and emit a logged skip rather
      than firing the gate. The fingerprint links back to
      learning/insights.jsonl (W8 self-documenting code, Rule 18).

    For precise O(n^2) detection, use lib/complexity_analyzer.max_complexity_class().

    Returns True if nested loops are likely present.
    """
    loop_indent = -1
    lines = content.split("\n")
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if re.match(r"(for |while )\S", stripped):
            if loop_indent >= 0 and indent > loop_indent:
                # Existing bounded-loop annotation
                if "# O(1):" in line:
                    continue
                if i > 0 and lines[i - 1].lstrip().startswith("#") and "# O(1):" in lines[i - 1]:
                    continue
                # W8: documented-intrinsic via INSIGHT[<fp>] annotation.
                insight = _line_has_insight_skip(lines, i)
                if insight:
                    fp, hook = insight
                    _log_gate_bypass("BIG-O", fp, hook, i)
                    continue
                return True
            loop_indent = indent
        elif indent <= loop_indent and not stripped.startswith(
            ("else", "elif", "except", "finally")
        ):
            loop_indent = -1
    return False
