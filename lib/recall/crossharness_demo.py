"""Cross-harness "same brain" proof for the recall substrate (T3, task #505).

Runs the SAME recall query through independent execution legs and compares
the pinned ``result_hash`` (subject + artefacts only — schema.py), proving
GRIP's memory is harness-independent: identical bytes in, identical
provenance out, whichever harness asks.

Legs:

1. in_process        — lib.recall.index.query_subject (lazy import)
2. cli_subprocess    — python3 bin/grip-recall query ... --json
3. module_subprocess — python3 -c "<import lib.recall.index; print hash>"
4. opencode          — OPTIONAL, only when grip-opencode is installed;
                       otherwise honestly labelled "skipped: ..." (coverage
                       is claimed only where mediated — the locked F3
                       boundary, plans/grip-recall-substrate.ultraprompt.md)

Errors are captured as strings (never escaping exceptions); an errored
deterministic leg counts as DIVERGED, a skipped leg is excluded.

Usage::

    python3 -m lib.recall.crossharness_demo --subject "AOD Arnold" --store DIR
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

__all__ = ["run_demo", "identical_verdict", "render_table"]

_ROOT = Path(__file__).resolve().parents[2]
_RECALL_BIN = _ROOT / "bin" / "grip-recall"
_TIMEOUT_S = 30
_HEX64 = re.compile(r"\b[0-9a-f]{64}\b")
_SKIP_PREFIX = "skipped:"

_MODULE_SNIPPET = (
    "import sys\n"
    "from lib.recall.index import query_subject\n"
    "print(query_subject(sys.argv[1], store=sys.argv[2]).result_hash)\n"
)


# --------------------------------------------------------------- subprocess
def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_ROOT)
    return env


def _run(cmd) -> tuple:
    """Run a leg subprocess; return (stdout, error_string_or_None)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(_ROOT),
            env=_env(),
            timeout=_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return "", "error: timed out after {}s".format(_TIMEOUT_S)
    except OSError as exc:
        return "", "error: {}".format(exc)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return "", "error: exit {}: {}".format(proc.returncode, detail[-1][:160] if detail else "")
    return proc.stdout or "", None


def _extract_hash(result) -> str:
    value = getattr(result, "result_hash", None)
    if value is None and isinstance(result, dict):
        value = result.get("result_hash")
    return value or "error: query returned no result_hash"


# --------------------------------------------------------------------- legs
def _leg_in_process(subject: str, store: str) -> str:
    try:
        from lib.recall.index import query_subject
    except ImportError:
        return "error: lib.recall.index not importable (engine track, T1)"
    try:
        return _extract_hash(query_subject(subject, store=store))
    except Exception as exc:  # error-as-string contract: nothing escapes
        return "error: {}".format(exc)


def _leg_cli(subject: str, store: str) -> str:
    cmd = [
        sys.executable,
        str(_RECALL_BIN),
        "query",
        "--subject",
        subject,
        "--store",
        store,
        "--json",
    ]
    stdout, err = _run(cmd)
    if err:
        return err
    try:
        import json

        return _extract_hash(json.loads(stdout))
    except ValueError as exc:
        return "error: grip-recall emitted non-JSON output ({})".format(exc)


def _leg_module(subject: str, store: str) -> str:
    stdout, err = _run([sys.executable, "-c", _MODULE_SNIPPET, subject, store])
    if err:
        return err
    line = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    if not _HEX64.fullmatch(line):
        return "error: module leg printed no result_hash ({!r})".format(line[:64])
    return line


def _leg_opencode(subject: str, store: str) -> str:
    binary = shutil.which("grip-opencode")
    if not binary:
        return "skipped: grip-opencode not installed"
    recall_cmd = shlex.join(
        [str(_RECALL_BIN), "query", "--subject", subject, "--store", store, "--json"]
    )
    prompt = (
        "Run this exact shell command and reply with ONLY the result_hash "
        "value from its JSON output, nothing else: " + recall_cmd
    )
    stdout, err = _run([binary, "run", prompt])
    if err:
        return "skipped: opencode leg failed ({})".format(err[:120])
    match = _HEX64.search(stdout)
    if not match:
        return "skipped: opencode output carried no result_hash"
    return match.group(0)


# ------------------------------------------------------------------ verdict
def identical_verdict(legs: dict) -> bool:
    """True iff every non-skipped leg produced the same value.

    Skipped legs are excluded; an errored deterministic leg breaks identity
    (a real signal, not an excuse). Zero non-skipped legs is False — claiming
    IDENTICAL with no evidence would be the coverage illusion.
    """
    values = [str(v) for v in legs.values() if not str(v).startswith(_SKIP_PREFIX)]
    return len(values) > 0 and len(set(values)) == 1


def run_demo(subject: str, store_dir) -> dict:
    """Run all legs for one subject against one store; compare result hashes."""
    store = str(store_dir)
    legs = {
        "in_process": _leg_in_process(subject, store),
        "cli_subprocess": _leg_cli(subject, store),
        "module_subprocess": _leg_module(subject, store),
        "opencode": _leg_opencode(subject, store),
    }
    return {"legs": legs, "identical": identical_verdict(legs)}


# -------------------------------------------------------------------- table
def _shown(value: str) -> str:
    value = str(value)
    return value[:12] if _HEX64.fullmatch(value) else value


def render_table(subject: str, demo: dict) -> str:
    lines = [
        "GRIP recall — cross-harness same-brain proof",
        "subject: {}".format(subject),
        "",
        "{:<20} result".format("leg"),
    ]
    lines.extend(
        "{:<20} {}".format(leg, _shown(value)) for leg, value in demo["legs"].items()
    )
    lines.append("")
    lines.append("verdict: {}".format("IDENTICAL" if demo["identical"] else "DIVERGED"))
    return "\n".join(lines)


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m lib.recall.crossharness_demo",
        description="Prove the recall result is identical across harness legs.",
    )
    parser.add_argument("--subject", required=True, help="subject to recall")
    parser.add_argument("--store", required=True, help="recall store directory")
    args = parser.parse_args(argv)
    demo = run_demo(args.subject, args.store)
    print(render_table(args.subject, demo))
    return 0 if demo["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
