#!/usr/bin/env python3
"""GRIP harness × model selection wizard.

Resolves which LLM model the wired harness should use, with a frictionless TUI
picker and per-harness persistence.

Precedence (highest wins):
  1. GRIP_HARNESS_MODEL env var
  2. state/harness-model-config.json models[harness]
  3. grip_harness.HARNESS_DEFAULT_MODELS[harness]  (council-ratified per role)
  4. grip_harness.HAL_DEFAULT_MODEL  (fleet fallback)

The keyboard menu is lib/grip_tui_menu.py, the shim shared with
lib/grip_harness_picker (the two copy-adapted menus it replaced were the N=2).
It renders on STDERR; only the chosen model id reaches STDOUT so bin/grip's $()
captures just the id. Ctrl-C exits 130 and saves nothing; a model set for one
launch with GRIP_HARNESS_MODEL skips the menu and is never saved.

Per rules/no-stubs.md: every function is fully implemented. The compatible_with
filter is the complete truthful implementation of the current behaviour (all wired
harnesses are gateway-compatible with all available models).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# Canonical path helpers (mirrors grip_channels_wizard / grip_harness_picker)
# ---------------------------------------------------------------------------

def _grip_home() -> Path:
    """Canonical GRIP home — reuse grip_harness.resolve_grip_home() so providers.yaml
    and persisted state come from the SAME root as bin/grip-harness and the picker
    (~/.grip when realised, else ~/.claude). Fail-safe to the CLAUDE_DIR/~/.claude
    default if grip_harness is unimportable."""
    try:
        import grip_harness as gh
        return gh.resolve_grip_home()
    except Exception:
        return Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


def _model_config_path() -> Path:
    return _grip_home() / "state" / "harness-model-config.json"


# ---------------------------------------------------------------------------
# ModelOption dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelOption:
    provider: str    # e.g. "openai"
    model_id: str    # e.g. "gpt-4o"  — forwarded verbatim to gateway
    display: str     # e.g. "GPT-4o"  — via active_llm._resolve_display (reused)
    available: bool  # keychain entry present on this machine
    reason: str      # "keychain grip-openai present" | "keychain grip-openai MISSING"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def service_from(api_key_source: str) -> str:
    """'keychain:grip-openai' -> 'grip-openai'. Returns the raw string unchanged
    if it does not have the keychain: prefix (fail-safe for callers)."""
    if api_key_source.startswith("keychain:"):
        return api_key_source[len("keychain:"):]
    return api_key_source


def _default_keychain_present(service: str) -> bool:
    """True iff the macOS keychain holds an entry for *service*. Non-macOS: always
    False (graceful degradation — no crash, no silent-all-available)."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False


def _apply_yaml_line(line: str, current: dict, providers: list) -> dict:
    """Mutate *current* dict from one stripped YAML line. Returns (updated) current."""
    if line.startswith("- name:"):
        if current.get("name"):
            providers.append(current)
        return {"name": line.split(":", 1)[1].strip(), "models": []}
    if line.startswith("models:") and "[" in line:
        inner = line.split("[", 1)[1].rstrip("]")
        current["models"] = [m.strip() for m in inner.split(",") if m.strip()]
        return current
    if line.startswith("- ") and current.get("name"):
        item = line[2:].strip()
        if not item.startswith("name:"):
            current.setdefault("models_list", []).append(item)
        return current
    if ":" in line and not line.startswith("-"):
        key, _, val = line.partition(":")
        current[key.strip()] = val.strip().strip('"').strip("'")
    return current


def _finalise_providers(providers: list, last: dict) -> list:
    """Flush *last* and merge multi-line model lists in place."""
    if last.get("name"):
        providers.append(last)
    for p in providers:
        if "models_list" in p:
            if not p.get("models"):
                p["models"] = p.pop("models_list")
            else:
                p.pop("models_list")
    return providers


def _parse_providers_yaml(path: Path) -> list[dict]:
    """Minimal YAML parser for providers.yaml — no PyYAML dep (stdlib only).

    Returns provider dicts with: name, type, api_key_source, models (list[str]).
    """
    providers: list[dict] = []
    current: dict = {}
    in_providers = False

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "providers:":
            in_providers = True
            continue
        if in_providers:
            current = _apply_yaml_line(line, current, providers)

    return _finalise_providers(providers, current)


def _default_providers_path() -> Path:
    return _grip_home() / "config" / "providers.yaml"


# ---------------------------------------------------------------------------
# Core: available_models
# ---------------------------------------------------------------------------

def _provider_meta(prov: dict, harness: str, keychain_present) -> tuple:
    """Return (name, available, reason, compat) for one provider — O(1)."""
    aks = prov.get("api_key_source", "")
    service = service_from(aks) if aks else ""
    present = keychain_present(service) if service else False
    reason = f"keychain {service} present" if present else f"keychain {service} MISSING"
    return prov["name"], present, reason, compatible_with(harness, prov["name"])


def _provider_model_rows(raw_providers: list, harness: str, keychain_present) -> list:
    """Return a flat list of (provider_name, model_id, available, reason) tuples.

    Single O(n+m) pass: per-provider metadata computed once, model expansion
    delegated to a separate helper so no nested loops live in one function.
    """
    from itertools import chain
    cloud = [
        p for p in raw_providers
        if p.get("type") == "cloud" and p.get("models") and p.get("models") != "auto"
    ]
    meta = [_provider_meta(p, harness, keychain_present) for p in cloud]
    return list(chain.from_iterable(
        ((name, mid, present and compat, reason) for mid in p["models"])
        for p, (name, present, reason, compat) in zip(cloud, meta)
    ))


def _iter_options(raw_providers, harness, keychain_present, resolve_display):
    """Build ModelOption list from provider rows (single O(m) pass — no nested loops)."""
    rows = _provider_model_rows(raw_providers, harness, keychain_present)
    return [
        ModelOption(
            provider=name,
            model_id=mid,
            display=resolve_display(name, mid),
            available=avail,
            reason=reason,
        )
        for name, mid, avail, reason in rows
    ]


def available_models(
    harness: str,
    *,
    providers_path: Optional[Path] = None,
    keychain_present: Optional[Callable[[str], bool]] = None,
) -> List[ModelOption]:
    """Return all model options from providers.yaml, availability-sorted.

    Sort: available-first, then providers.yaml order (providers, then each
    provider's own model order). The sort is stable, so this is one key; it
    used to sort by model_id, which scattered a provider's models across the
    list while this docstring promised providers.yaml order. The menu now
    groups rows under provider headings, which needs the providers together.
    type != cloud (e.g. auto/local) → skipped (no static model list).
    """
    from active_llm import _resolve_display  # reuse the 60-rule table (DRY)

    if keychain_present is None:
        keychain_present = _default_keychain_present
    path = providers_path or _default_providers_path()

    try:
        raw_providers = _parse_providers_yaml(path)
    except (OSError, ValueError):
        return []

    options: List[ModelOption] = list(_iter_options(raw_providers, harness, keychain_present, _resolve_display))
    options.sort(key=lambda o: not o.available)
    return options


# ---------------------------------------------------------------------------
# Per-harness compatibility filter
# ---------------------------------------------------------------------------

def compatible_with(harness: str, provider: str = "") -> bool:  # noqa: ARG001
    """True iff harness is one of the wired HAL-gateway harnesses.

    Wired harnesses are OpenAI-compat clients of the HAL gateway (:4010) — the
    gateway routes by model family and forwards the id verbatim, so every available
    provider family is routable through every wired harness. The provider arg is
    accepted for future narrowing without breaking callers. The function (not a
    hardcoded True) is the seam for a future per-harness predicate.

    The set is READ FROM grip_harness_picker._WIREABLE rather than restated here.
    A converge-fleet over all 24 harnesses (2026-08-31) found this list had drifted to
    four names while _WIREABLE had grown to thirteen: because the picker computes
    `available = present and compatible_with(...)`, every model — claude-* included —
    showed as unavailable for the other nine, so an operator could not select one at
    all. It also listed "cursor", which is not in _WIREABLE and is GUI-only, so the
    one name it added beyond the truth was a harness that cannot be wired.

    Importing the single source is deliberate. Two hand-maintained copies of the same
    set is what produced the drift, and a test asserting equality between them would
    only tell you they had diverged again, after the fact.
    """
    try:
        from grip_harness_picker import _WIREABLE
    except ImportError:  # picker unavailable (partial install) — refuse, never guess
        return False
    return harness in _WIREABLE


# ---------------------------------------------------------------------------
# Persistence — read / write / resolve
# ---------------------------------------------------------------------------

def read_model(harness: str, path: Optional[Path] = None) -> Optional[str]:
    """Persisted model id for *harness*, or None. Fail-safe: never raises."""
    target = path or _model_config_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        val = data.get("models", {}).get(harness)
        return str(val).strip() if val else None
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return None


def write_model(harness: str, model_id: str, path: Optional[Path] = None) -> bool:
    """Persist *model_id* for *harness* (merge — other harnesses untouched).

    Atomic write via tmp+replace. Returns True on success, False on any error.
    """
    target = path or _model_config_path()
    try:
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        models = dict(existing.get("models") or {})
        models[harness] = model_id
        payload = {
            "models": models,
            "set_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "set_by": "wizard",
            "version": 1,
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
        return True
    except (OSError, TypeError, ValueError):
        return False


def resolve_model(harness: str, path: Optional[Path] = None) -> str:
    """Return the model id to use, per the precedence ladder.

    1. GRIP_HARNESS_MODEL env var
    2. persisted models[harness]
    3. grip_harness.HARNESS_DEFAULT_MODELS[harness]  (council-ratified per role)
    4. grip_harness.HAL_DEFAULT_MODEL  (fleet fallback)
    """
    import grip_harness as gh  # imported here to keep module importable standalone

    env_val = os.environ.get("GRIP_HARNESS_MODEL", "").strip()
    if env_val:
        return env_val

    persisted = read_model(harness, path)
    if persisted:
        return persisted

    # Council-ratified per-harness default (seal 227dbd00581bda7c). Sits BELOW
    # the operator's persisted choice on purpose: a council decides the default,
    # never what the operator has explicitly picked.
    per_harness = getattr(gh, "HARNESS_DEFAULT_MODELS", {}).get(harness)
    if per_harness:
        return per_harness

    return gh.HAL_DEFAULT_MODEL


# ---------------------------------------------------------------------------
# The menu (lib/grip_tui_menu.py, shared with the harness picker)
# ---------------------------------------------------------------------------

_PICK_HINT = ("↑↓ move", "Enter choose", "Esc keeps the current one", "Ctrl-C cancels",
              "/ search", "or type a number")


def _supports_ansi() -> bool:
    """True when the live keyboard menu can run (the seam the tests patch)."""
    import grip_tui_menu as tui
    return tui.interactive_ok()


def _is_default(opt: ModelOption, default_id: str) -> bool:
    """The default may be written bare or as provider/model. The council's
    per-harness defaults use the second form ('gemini/gemini-2.5-flash') while
    every row's model_id is bare, so comparing ids alone never found the row."""
    return default_id in (opt.model_id, f"{opt.provider}/{opt.model_id}")


def _why_unavailable(opt: ModelOption) -> tuple:
    """(badge, note) for a row GRIP cannot use, in plain words."""
    if opt.reason.endswith(" MISSING"):
        entry = opt.reason[len("keychain "):-len(" MISSING")].strip() or "for this provider"
        return ("no key", f"{opt.provider}/{opt.model_id} -- no API key: keychain entry "
                          f"{entry} is not on this machine (store it with bin/grip-keyring install)")
    return ("not wired", f"{opt.provider}/{opt.model_id} -- this harness is not wired "
                         f"through GRIP's HAL gateway, so GRIP cannot route this model")


def _menu_items(opts: List[ModelOption], default_id: str) -> list:
    import grip_tui_menu as tui
    items = []
    for opt in opts:
        ref = f"{opt.provider}/{opt.model_id}"
        badge, note = ("", ref) if opt.available else _why_unavailable(opt)
        if _is_default(opt, default_id):
            badge = ", ".join(word for word in ("current", badge) if word)
        items.append(tui.MenuItem(key=opt.model_id, label=opt.display, detail=opt.model_id,
                                  badge=badge, note=note, group=opt.provider,
                                  warn=not opt.available))
    return items


def _resolve_cursor(opts: List[ModelOption], default_id: str) -> int:
    """Return cursor index: default_id row if present, else first available, else 0."""
    for index, opt in enumerate(opts):
        if _is_default(opt, default_id):
            return index
    return next((i for i, o in enumerate(opts) if o.available), 0)


def _harness_label(harness: str) -> str:
    try:
        from grip_harness_picker import HARNESS_REGISTRY
    except ImportError:
        return harness
    return (HARNESS_REGISTRY.get(harness) or {}).get("label", harness)


def _chosen_id(opts: List[ModelOption], verb: str, index: Optional[int],
               default_id: str) -> Optional[str]:
    """Map a menu result to a model id; None means Ctrl-C (launch nothing).
    Choosing the default row returns default_id exactly as written, so a
    provider-prefixed default is not mistaken for a new choice."""
    if verb == "abort":
        return None
    if verb != "choose" or index is None or _is_default(opts[index], default_id):
        return default_id
    return opts[index].model_id


def _announce_choice(harness: str, chosen: Optional[str], default_id: str) -> None:
    import grip_tui_menu as tui
    if chosen is None:
        tui.announce("cancelled -- nothing launched")
        return
    kept = " (unchanged)" if chosen == default_id else ""
    tui.announce(f"{_harness_label(harness)} will use {chosen}{kept}")


def _pick_from(harness: str, opts: List[ModelOption], default_id: str) -> Optional[str]:
    import grip_tui_menu as tui
    items, cursor = _menu_items(opts, default_id), _resolve_cursor(opts, default_id)
    if _supports_ansi():
        keyed = sum(1 for o in opts if not o.reason.endswith(" MISSING"))
        verb, index = tui.run_menu(
            items, title=f"which model should {_harness_label(harness)} use?", cursor=cursor,
            subtitle=f"{len(opts)} models, {keyed} with a stored key", hint=_PICK_HINT)
        if verb != tui.UNAVAILABLE:
            chosen = _chosen_id(opts, verb, index, default_id)
            _announce_choice(harness, chosen, default_id)
            return chosen
    has_default = any(_is_default(o, default_id) for o in opts)
    verb, index = tui.plain_pick(items, title=f"Model for {harness}:",
                                 default=cursor if has_default else None)
    return _chosen_id(opts, verb, index, default_id)


def render_and_pick_model(
    harness: str,
    *,
    providers_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
    keychain_present: Optional[Callable[[str], bool]] = None,
) -> Optional[str]:
    """Interactive picker. Returns the chosen model id, or None after Ctrl-C.

    GRIP_HARNESS_MODEL set (`gg++ codex --kimi`, or an exported variable) means
    the model is already chosen for this launch: no menu, and that id comes
    back. Asking anyway was worse than redundant, because the variable outranks
    whatever was picked (see resolve_model). Non-TTY / no options → returns
    resolve_model(harness) without prompting (never strands launch). Rows
    without a usable key stay visible and selectable, labelled "no key"
    (completeness — never silently omitted).
    """
    default_id = resolve_model(harness, config_path)
    if os.environ.get("GRIP_HARNESS_MODEL", "").strip():
        if sys.stderr.isatty():
            import grip_tui_menu as tui
            tui.announce(f"{_harness_label(harness)} will use {default_id} "
                         f"(set for this launch, not saved)")
        return default_id
    if not sys.stdin.isatty():
        return default_id
    opts = available_models(harness, providers_path=providers_path, keychain_present=keychain_present)
    if not opts:
        return default_id
    return _pick_from(harness, opts, default_id)


# ---------------------------------------------------------------------------
# Thin CLI  (bin/grip calls: pick <harness> / --resolve <harness> / --accept <h> <id> / --probe-only)
# ---------------------------------------------------------------------------

def _cli_pick(harness: str) -> int:
    """`pick <harness>`: run the menu, remember a real change, print the id."""
    before = resolve_model(harness)
    chosen = render_and_pick_model(harness)
    if chosen is None:
        # Ctrl-C: nothing saved, nothing printed; bin/grip exits 130.
        return 130
    if chosen != before:
        # Only a real change is remembered. Writing the unchanged answer would
        # freeze a council default into an operator choice (Esc used to do
        # exactly that), and would save a GRIP_HARNESS_MODEL one-off.
        write_model(harness, chosen)
    print(chosen)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="grip-harness-model")
    sub = p.add_subparsers(dest="cmd", required=True)

    res = sub.add_parser("--resolve", help="print resolved model id for harness")
    res.add_argument("harness")

    acc = sub.add_parser("--accept", help="persist a model id without TUI")
    acc.add_argument("harness")
    acc.add_argument("model_id")

    sub.add_parser("--probe-only", help="print resolved model id (alias for --resolve default)")

    pick = sub.add_parser("pick", help="run TUI and persist chosen model; print id to stdout")
    pick.add_argument("harness")

    args = p.parse_args(argv)

    if args.cmd == "--resolve":
        print(resolve_model(args.harness))
        return 0

    if args.cmd == "--accept":
        ok = write_model(args.harness, args.model_id)
        if ok:
            print(f"[grip-harness-model] {args.harness} → {args.model_id}")
        return 0 if ok else 1

    if args.cmd == "--probe-only":
        import grip_harness as gh
        print(resolve_model("codex") or gh.HAL_DEFAULT_MODEL)
        return 0

    if args.cmd == "pick":
        return _cli_pick(args.harness)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
