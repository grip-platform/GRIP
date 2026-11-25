"""Harness for Claude Code --json-schema structured pipeline output.

Wraps `claude -p` invocations with `--output-format json` and optional
schema validation so GRIP pipelines receive typed, validated dicts instead
of raw text.

Usage:
    from lib.harness_json_schema import run_structured, validate_output

    result = run_structured(
        prompt="List the top 3 risks as JSON",
        schema={"type": "object", "required": ["risks"]},
    )
    if result["ok"]:
        data = result["data"]   # parsed + validated dict
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

CLAUDE_DIR = Path(__file__).resolve().parent.parent

_MODEL_FALLBACK = "claude-sonnet-4-6"


def _default_model() -> str:
    """Sonnet-tier id from HAL's canonical catalogue (fail-safe literal).

    Mirrors bin/agora-demo::_default_model() — resolves via the merged
    lib.hal_model_resolver shim, degrading to a current literal when the
    catalogue / shim is unavailable. Never raises (a missing HAL clone
    must not break this CLI).
    """
    if str(CLAUDE_DIR) not in sys.path:
        sys.path.insert(0, str(CLAUDE_DIR))
    try:
        from lib.hal_model_resolver import resolve
        return resolve("anthropic/claude-sonnet-", _MODEL_FALLBACK)
    except Exception:
        return _MODEL_FALLBACK


def _build_cmd(prompt: str, model: str, extra_flags: list[str]) -> list[str]:
    """Assemble the claude CLI command for structured JSON output."""
    return [
        "claude", "--print",
        "--output-format", "json",
        "-m", model,
        *extra_flags,
        prompt,
    ]


def _validate_against_schema(data: Any, schema: dict) -> list[str]:
    """Minimal schema validation: check required keys for object schemas.

    Returns a list of error strings (empty = valid).
    Intentionally KISS — full jsonschema is an optional dependency.
    """
    errors: list[str] = []
    if schema.get("type") == "object":
        for key in schema.get("required", []):
            if not isinstance(data, dict) or key not in data:
                errors.append(f"missing required key: {key!r}")
    return errors


def validate_output(data: Any, schema: dict) -> dict:
    """Validate parsed JSON data against a schema dict.

    Returns:
        {"valid": bool, "errors": list[str]}
    """
    errors = _validate_against_schema(data, schema)
    return {"valid": len(errors) == 0, "errors": errors}


def run_structured(
    prompt: str,
    schema: dict | None = None,
    model: str | None = None,
    timeout: int = 120,
    extra_flags: list[str] | None = None,
) -> dict:
    """Run a claude -p pipeline and return schema-validated structured output.

    Args:
        prompt:      The prompt to send to Claude.
        schema:      Optional JSON Schema dict for output validation.
        model:       Claude model to use. None (default) resolves the
                     Sonnet-tier id from HAL's canonical catalogue.
        timeout:     subprocess timeout in seconds.
        extra_flags: Additional CLI flags passed verbatim.

    Returns:
        {
          "ok":     bool,       # True if run succeeded and schema passed
          "data":   dict|None,  # parsed JSON on success, None on failure
          "raw":    str,        # raw stdout
          "errors": list[str],  # parse or schema errors
        }
    """
    if model is None:
        model = _default_model()
    cmd = _build_cmd(prompt, model, extra_flags or [])
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return {"ok": False, "data": None, "raw": "", "errors": ["claude CLI not found"]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "data": None, "raw": "", "errors": [f"timed out after {timeout}s"]}

    raw = proc.stdout.strip()
    if proc.returncode != 0:
        return {"ok": False, "data": None, "raw": raw, "errors": [f"exit {proc.returncode}: {proc.stderr[:200]}"]}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "data": None, "raw": raw, "errors": [f"JSON parse error: {exc}"]}

    if schema:
        validation = validate_output(data, schema)
        if not validation["valid"]:
            return {"ok": False, "data": data, "raw": raw, "errors": validation["errors"]}

    return {"ok": True, "data": data, "raw": raw, "errors": []}


# ── CLI ────────────────────────────────────────────────────────────────

def _cli() -> None:
    """Minimal CLI: harness_json_schema.py <prompt> [--schema '{...}']"""
    import argparse
    parser = argparse.ArgumentParser(description="Run claude -p with --json-schema output")
    parser.add_argument("prompt", help="Prompt to send to Claude")
    parser.add_argument("--schema", default=None, help="JSON Schema string for validation")
    parser.add_argument("--model", default=None,
                        help="Model name (default: HAL-catalogue Sonnet tier)")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout in seconds")
    args = parser.parse_args()

    # Resolve per-invocation in the handler (AGORA PR #2970 fix): single
    # resolution path identical to run_structured(model=None) — no
    # parser-build-time side effect, consistent CLI vs programmatic timing.
    model = args.model if args.model is not None else _default_model()
    schema = json.loads(args.schema) if args.schema else None
    result = run_structured(args.prompt, schema=schema, model=model, timeout=args.timeout)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    _cli()
