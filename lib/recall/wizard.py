"""Recall onboarding/config wizard — terminal Q&A with injectable IO.

Progressive disclosure, one question at a time, numbered choices with the
locked defaults preselected on Enter (skills/creating-wizards discipline:
each question teaches AND collects). Persists atomically to
``<store_dir or $CLAUDE_DIR/state/recall>/config.json`` and is idempotent —
re-running loads the existing config as the new defaults.

Store keys deliberately mirror ``schema.Artefact.source`` values
(idr | context | receipt | file-history) so the index track can consume the
config without a mapping layer.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from lib.claude_dir import get_claude_dir

CONFIG_VERSION = 1
GATE_ACTIONS = (
    ("hitl_forensic", "error state + surface to operator + signed RED node"),
    ("annotate", "let the claim pass, annotated with the contradicting evidence"),
    ("hard_block", "refuse the output outright (autonomous contexts)"),
)
FRESHNESS_MODES = (
    ("incremental_verify_on_read", "append events, verify index hash on read"),
    ("rebuild_on_read", "rebuild the materialised view on every read"),
)
COVERAGE_MEDIATED = "mediated_only"
STORE_KEYS = ("idr", "context", "receipt", "file-history")
_STORE_LABELS = {
    "idr": "IDR decision chain",
    "context": "context chain (what GRIP believed)",
    "receipt": "receipts",
    "file-history": "file history",
}


def _defaults() -> dict:
    return {
        "version": CONFIG_VERSION,
        "set_at": None,
        "gate_action": GATE_ACTIONS[0][0],
        "coverage": COVERAGE_MEDIATED,
        "freshness": FRESHNESS_MODES[0][0],
        "stores": {key: True for key in STORE_KEYS},
        "aliases": {},
    }


def _config_path(store_dir=None) -> Path:
    base = Path(store_dir) if store_dir else get_claude_dir() / "state" / "recall"
    return base / "config.json"


def load_config(store_dir=None) -> dict:
    """Existing config merged over the locked defaults; defaults when absent."""
    cfg = _defaults()
    path = _config_path(store_dir)
    if not path.exists():
        return cfg
    saved = json.loads(path.read_text(encoding="utf-8"))
    for key, value in saved.items():
        if key == "stores":
            cfg["stores"].update(value or {})
        elif key == "aliases":
            cfg["aliases"] = dict(value or {})
        else:
            cfg[key] = value
    return cfg


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_config(cfg: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _ask_choice(input_fn, print_fn, question, options, default) -> str:
    """Numbered single-pick; Enter keeps the default; invalid input re-asks."""
    print_fn("")
    print_fn(question)
    for n, (value, label) in enumerate(options, 1):
        mark = "  (default — press Enter)" if value == default else ""
        print_fn(f"  [{n}] {value} — {label}{mark}")
    values = [value for value, _ in options]
    while True:
        raw = input_fn("> ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return values[int(raw) - 1]
        if raw in values:
            return raw
        print_fn(f"  Please answer 1-{len(options)}, or press Enter for the default.")


def _ask_yesno(input_fn, print_fn, question, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    print_fn(f"{question} [{hint}]")
    while True:
        raw = input_fn("> ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print_fn("  Please answer y or n, or press Enter for the default.")


def _ask_stores(input_fn, print_fn, current: dict) -> dict:
    all_on = all(current.get(key, True) for key in STORE_KEYS)
    choice = _ask_choice(
        input_fn,
        print_fn,
        "1. Which moat stores should recall index?",
        (
            ("all", "idr, context chain, receipts, file-history"),
            ("pick", "choose stores one by one"),
        ),
        "all" if all_on else "pick",
    )
    if choice == "all":
        return {key: True for key in STORE_KEYS}
    return {
        key: _ask_yesno(
            input_fn, print_fn,
            f"   Index {_STORE_LABELS[key]}?", bool(current.get(key, True)),
        )
        for key in STORE_KEYS
    }


def _parse_alias_line(raw: str):
    """'subject=alias1,alias2' -> (subject, [aliases]) or None when malformed."""
    subject, sep, rest = raw.partition("=")
    aliases = [a.strip() for a in rest.split(",") if a.strip()]
    if not sep or not subject.strip() or not aliases:
        return None
    return subject.strip(), aliases


def _ask_aliases(input_fn, print_fn, existing: dict) -> dict:
    aliases = dict(existing)
    print_fn("")
    print_fn("5. Extra alias pairs — teach recall your names for things.")
    print_fn("   Format: subject=alias1,alias2   (empty line to finish)")
    if aliases:
        print_fn(f"   Keeping {len(aliases)} existing pair(s): "
                 + ", ".join(sorted(aliases)))
    while True:
        raw = input_fn("alias> ").strip()
        if not raw:
            return aliases
        parsed = _parse_alias_line(raw)
        if parsed is None:
            print_fn("   Format: subject=alias1,alias2 — try again.")
            continue
        subject, names = parsed
        aliases[subject] = names
        print_fn(f"   ✓ {subject} → {', '.join(names)}")


def run_wizard(input_fn=input, print_fn=print, store_dir=None) -> dict:
    """Run the Q&A, persist atomically, return the saved config dict."""
    cfg = load_config(store_dir)
    print_fn("GRIP recall setup — the moat, answering before you rebuild")
    print_fn("─" * 58)
    cfg["stores"] = _ask_stores(input_fn, print_fn, cfg["stores"])
    cfg["gate_action"] = _ask_choice(
        input_fn, print_fn,
        "2. When the gate catches a false 'it does not exist' claim (DENY)?",
        GATE_ACTIONS, cfg["gate_action"],
    )
    cfg["coverage"] = _ask_choice(
        input_fn, print_fn,
        "3. Coverage policy — recall only vouches for surfaces it mediates\n"
        "   (the honest boundary; unmediated writes are honestly out of scope).",
        ((COVERAGE_MEDIATED, "count mediated surfaces only"),),
        cfg["coverage"],
    )
    cfg["freshness"] = _ask_choice(
        input_fn, print_fn,
        "4. Freshness — how current must the index be at read time?",
        FRESHNESS_MODES, cfg["freshness"],
    )
    cfg["aliases"] = _ask_aliases(input_fn, print_fn, cfg["aliases"])
    cfg["set_at"] = _utc_now()
    path = _config_path(store_dir)
    _write_config(cfg, path)
    print_fn("")
    print_fn(f"✓ Saved {path}")
    return cfg
