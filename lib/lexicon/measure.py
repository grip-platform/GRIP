"""Per-file and per-tier figures, each reported against the budget it answers to.

A budget is a CEILING, which ``--check`` fails on; a TARGET, a goal that is
reported but never fails the command; or a RATCHET, a ceiling that only comes
down. Ratchets are declared in config/rules-scope.json (``ratchet``). After a
change that saves characters, :func:`lower_ratchet` lowers each one to today's
figure; no tool ever raises one, because a raise is a decision a reviewer should
see with its reason. A figure over its ratchet is a warning on the pull request,
never a failure (KERN v2, HITL lock 3, decision 3).

Every character figure is in UTF-16 code units, Claude Code's own unit; the
core-rule figure reuses lib/rules_scope.projection so it can never disagree with
the budget test it mirrors (tests/test_rules_scope.py::test_projection_under_budget).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import surfaces
from .tokencount import CountError, Counter
from .surfaces import rules_scope

INJECTOR_REL = "hooks/subagent-preload-main-inject.py"
MAIN_TIER_REL = rules_scope.MAIN_TIER_HOOK
SCOPE_REL = "config/rules-scope.json"

# KERN v2, ruling 6 (2026-10-18): the main session's whole instruction load is
# reported beside the figure Claude Code warns about, because moving a rule out of
# native loading changes only the second.
DELIVERED_NOTE = ("Routing a rule out of native loading removes it from Claude Code's count, "
                  "not from the main session's context.")


@dataclass(frozen=True)
class Budget:
    metric: str
    limit: int
    unit: str
    level: str  # "ceiling" fails --check; "target" and "ratchet" are reported only
    why: str
    strict: bool = False  # True: the figure must stay UNDER the limit, not merely at it


BUDGETS = (
    Budget("always_loaded_chars", 120_000, "chars", "target",
           "Claude Code's warning bar on a 200K-context session"),
    Budget("always_loaded_chars", 150_000, "chars", "ceiling",
           "Claude Code warns at startup above this on a 1M-context session"),
    # Strict because tests/test_rules_scope.py asserts `< 72_000`: at exactly the
    # tripwire the suite is red, so this report must not call it within (Codex on
    # GRIP#7662).
    Budget("core_rule_tokens", rules_scope.SAFETY_CORE_TOKEN_TRIPWIRE, "bytes/3.7", "ceiling",
           "must stay under the safety-core tripwire in tests/test_rules_scope.py", strict=True),
    Budget("injected_chars", 10_000, "chars", "target",
           "hook output over 10,000 chars reaches the model as a 2KB preview (GRIP#6446)"),
    Budget("lexicon_chars", 3_000, "chars", "ceiling",
           "the LEXICON admission rule"),
    Budget("agents_bytes", 30_720, "bytes", "target",
           "AGENTS.md goal, 2 KiB under Codex's cap"),
    Budget("agents_bytes", 32_768, "bytes", "ceiling",
           "Codex project_doc_max_bytes: anything past it is dropped silently"),
)

RATCHET_WHY = f"the ratchet in {SCOPE_REL}: lowered with each saving, never raised by a tool"

LABELS = {
    "always_loaded_chars": "always-loaded characters",
    "core_rule_tokens": "safety-core rule tokens",
    "injected_chars": "SessionStart index characters",
    "main_tier_chars": "main-session tier characters",
    "delivered_chars": "characters delivered to the main session",
    "lexicon_chars": "LEXICON characters",
    "agents_bytes": "AGENTS.md bytes",
}


@dataclass
class Report:
    root: str
    files: list[dict] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)
    tokens: dict[str, dict] = field(default_factory=dict)
    defaulted: list[dict] = field(default_factory=list)
    budgets: list[dict] = field(default_factory=list)
    encrypted: list[str] = field(default_factory=list)  # ciphertext here: not counted

    @property
    def ceilings_breached(self) -> list[dict]:
        return [b for b in self.budgets if b["level"] == "ceiling" and not b["ok"]]

    @property
    def ratchets(self) -> list[dict]:
        return [b for b in self.budgets if b["level"] == "ratchet"]


def _row(surface: surfaces.Surface) -> dict:
    return {"rel": surface.rel, "tier": surface.tier,
            "chars": surface.chars, "bytes": surface.nbytes}


def _hook_chars(root: Path, rel: str, *args: str) -> int:
    """UTF-16 length of the additionalContext a SessionStart hook under ``root`` prints.

    The hook is run, not re-implemented, so the figure follows it through every
    change. An absent hook contributes nothing; one that exits non-zero is a
    measurement failure, never a zero.
    """
    hook = root / rel
    if not hook.is_file():
        return 0
    proc = subprocess.run([sys.executable, str(hook), *args], input='{"source":"startup"}',
                          capture_output=True, text=True, timeout=60,
                          env={**os.environ, "CLAUDE_DIR": str(root)})
    if proc.returncode != 0:
        raise RuntimeError(f"{rel} exited {proc.returncode}: {proc.stderr[:200]}")
    if not proc.stdout.strip():
        return 0
    context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    return surfaces.utf16_len(context)


def injected_chars(root: Path) -> int:
    """UTF-16 length of what the SessionStart index hook actually delivers for ``root``
    (it emitted whole extended rules until GRIP#6446 made it an index)."""
    return _hook_chars(root, INJECTOR_REL)


def main_tier_chars(root: Path) -> int:
    """UTF-16 length of every main-tier chunk the main session is handed for ``root``:
    the main-tier hook run once for each chunk settings bind."""
    return sum(_hook_chars(root, MAIN_TIER_REL, "--chunk", str(index))
               for index in range(rules_scope.MAIN_TIER_CHUNKS))


def _metrics(root: Path) -> dict[str, int]:
    loaded = surfaces.always_loaded(root)
    lexicon = surfaces.lexicon(root)
    agents = surfaces.agents(root)
    projection = rules_scope.projection(surfaces.scope_config(root), root / "rules")
    loaded_chars = sum(s.chars for s in loaded)
    index_chars, tier_chars = injected_chars(root), main_tier_chars(root)
    return {
        "always_loaded_files": len(loaded),
        "always_loaded_chars": loaded_chars,
        "core_rule_tokens": projection["safety_core_tokens_approx"],
        "injected_chars": index_chars,
        "main_tier_chars": tier_chars,
        # What the main session holds: Claude Code's count plus every SessionStart value.
        "delivered_chars": loaded_chars + index_chars + tier_chars,
        "lexicon_chars": lexicon.chars if lexicon else 0,
        "agents_bytes": agents.nbytes if agents else 0,
    }


def _token_totals(loaded: list[surfaces.Surface], counters: list[Counter]) -> dict[str, dict]:
    out = {}
    for counter in counters:
        try:
            total = sum(counter.count(s.text) for s in loaded)
            out[counter.id] = {"kind": counter.kind, "always_loaded": total}
        except CountError as exc:
            out[counter.id] = {"kind": counter.kind, "error": str(exc)}
    return out


def _defaulted(root: Path) -> list[dict]:
    """Core rules that are core only because nothing classified them.

    These are the cheapest cut available: moving one to ``extended`` is a
    config change, but it is the operator's call, because a rule that governs
    a subagent's actions must stay where subagents can read it.
    """
    cfg = surfaces.scope_config(root)
    explicit = set(cfg.get("safety_core_explicit", [])) | set(rules_scope.MUST_BE_SAFETY_CORE)
    return [_row(s) for s in surfaces.rules(root)
            if s.tier in ("core-rule", "path-scoped") and s.rel.split("/")[-1] not in explicit]


def ratchet_limits(cfg: dict) -> dict[str, int]:
    """The ratchets a rules-scope config declares, keyed by metric.

    A name that is not a budgeted metric is refused rather than skipped: a
    misspelt ratchet would otherwise watch nothing and report nothing.
    """
    known = {b.metric for b in BUDGETS}
    table = cfg.get("ratchet") or {}
    if not isinstance(table, dict):
        raise ValueError(f"{SCOPE_REL} ratchet must be an object of figures, not {table!r}")
    declared = {k: v for k, v in table.items() if not k.startswith("_")}
    unknown = sorted(set(declared) - known)
    if unknown:
        raise ValueError(f"{SCOPE_REL} ratchet names no budgeted figure: {unknown}; "
                         f"the budgeted figures are {sorted(known)}")
    bad = {k: v for k, v in declared.items() if isinstance(v, bool) or not isinstance(v, int)}
    if bad:
        raise ValueError(f"{SCOPE_REL} ratchet values must be whole numbers: {bad}")
    return dict(declared)


def _ratchet_budgets(cfg: dict) -> tuple[Budget, ...]:
    units = {b.metric: b.unit for b in BUDGETS}
    return tuple(Budget(metric, limit, units[metric], "ratchet", RATCHET_WHY)
                 for metric, limit in ratchet_limits(cfg).items())


def _within(value: int, budget: Budget) -> bool:
    return value < budget.limit if budget.strict else value <= budget.limit


def _verdicts(metrics: dict[str, int], budgets: tuple[Budget, ...]) -> list[dict]:
    return [{"metric": b.metric, "value": metrics[b.metric], "limit": b.limit, "unit": b.unit,
             "level": b.level, "ok": _within(metrics[b.metric], b), "why": b.why}
            for b in budgets]


def measure(root: Path, counters: list[Counter]) -> Report:
    loaded = surfaces.always_loaded(root)
    extras = [s for s in (surfaces.lexicon(root), surfaces.agents(root)) if s]
    metrics = _metrics(root)
    budgets = BUDGETS + _ratchet_budgets(surfaces.scope_config(root))
    return Report(root=str(root),
                  files=[_row(s) for s in loaded + extras],
                  metrics=metrics,
                  tokens=_token_totals(loaded, counters),
                  defaulted=_defaulted(root),
                  budgets=_verdicts(metrics, budgets),
                  encrypted=surfaces.encrypted(root))


def _ratchet_row(metric: str, limit: int, value: int) -> dict:
    action = "lowered" if value < limit else ("unchanged" if value == limit else "over")
    return {"metric": metric, "limit": limit, "value": value, "action": action}


def lower_ratchet(root: Path) -> list[dict]:
    """Lower every ratchet in config/rules-scope.json to today's figure; never raise one.

    Returns one row per ratchet: its metric, its limit before the call, today's
    value and the action taken (lowered, unchanged or over). The config is
    rewritten only when a ratchet moved, in the same json.dumps form the file is
    kept in, so every other line stays byte-identical.
    """
    path = root / SCOPE_REL
    cfg = rules_scope.load_config(path)
    metrics = _metrics(root)
    rows = [_ratchet_row(metric, limit, metrics[metric])
            for metric, limit in ratchet_limits(cfg).items()]
    lowered = {r["metric"]: r["value"] for r in rows if r["action"] == "lowered"}
    if lowered:
        cfg["ratchet"].update(lowered)
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return rows


def ratchet_line(row: dict) -> str:
    """One plain line describing what :func:`lower_ratchet` did to one ratchet."""
    label = LABELS.get(row["metric"], row["metric"])
    if row["action"] == "lowered":
        return f"{label}: ratchet lowered from {row['limit']:,} to {row['value']:,}"
    if row["action"] == "unchanged":
        return f"{label}: {row['value']:,}, at the ratchet"
    return (f"{label}: {row['value']:,} is {row['value'] - row['limit']:,} over the ratchet of "
            f"{row['limit']:,}; left as it is, because a tool never raises a ratchet")


def ratchet_message(budget: dict) -> tuple[str, str]:
    """(state, sentence) for one ratchet verdict; state is over, under or at."""
    label = LABELS.get(budget["metric"], budget["metric"])
    value, limit = budget["value"], budget["limit"]
    if value > limit:
        return "over", (f"The {label} add up to {value:,} with this change, {value - limit:,} over "
                        f"the ratchet of {limit:,} in {SCOPE_REL}. Cut the growth, or raise the "
                        f"ratchet by hand with the reason in the pull request.")
    if value < limit:
        return "under", (f"The {label} add up to {value:,}, {limit - value:,} under the ratchet of "
                         f"{limit:,}. Lower it: run bin/grip-lexicon ratchet and commit {SCOPE_REL}.")
    return "at", f"The {label} add up to {value:,}, exactly at the ratchet."


def _escape(message: str) -> str:
    """Escape a GitHub workflow-command message so it stays on one line."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


ANNOTATION = {"over": ("warning", "Instruction files over the ratchet"),
              "under": ("notice", "The ratchet can come down")}


def annotations(report: Report) -> list[str]:
    """GitHub workflow commands for each ratchet that is not met exactly.

    Only ratchets annotate. A ceiling breached on main is the same on every
    pull request and is stated in the job summary; a ratchet tells a pull
    request what IT changed, which is the line its author can act on.
    """
    out = []
    for budget in report.ratchets:
        state, message = ratchet_message(budget)
        if state in ANNOTATION:
            level, title = ANNOTATION[state]
            out.append(f"::{level} title={title}::{_escape(message)}")
    return out


def delivered_sentence(m: dict) -> str:
    """What the main session holds in all, and why that differs from Claude Code's count."""
    return (f"The main session holds {m['delivered_chars']:,} characters of instructions: "
            f"{m['always_loaded_chars']:,} always loaded, {m['injected_chars']:,} in the "
            f"SessionStart index and {m['main_tier_chars']:,} in the main-session tier. "
            + DELIVERED_NOTE)


def render(report: Report) -> str:
    """A plain-text report: totals, token counts, budgets, defaulted rules."""
    m = report.metrics
    lines = [f"KERN measure — {report.root}",
             f"  always-loaded files: {m['always_loaded_files']}  "
             f"chars: {m['always_loaded_chars']:,}",
             "  " + delivered_sentence(m)]
    if report.encrypted:
        lines.append(f"  WARNING: {len(report.encrypted)} file(s) are git-crypt ciphertext in "
                     f"this checkout and count as empty: {', '.join(report.encrypted)}")
    for cid, t in report.tokens.items():
        figure = f"{t['always_loaded']:,} tokens" if "always_loaded" in t else f"unavailable ({t['error']})"
        lines.append(f"  {cid} [{t['kind']}]: {figure}")
    lines.append("  budgets:")
    for b in report.budgets:
        mark = "ok  " if b["ok"] else ("FAIL" if b["level"] == "ceiling" else "over")
        lines.append(f"    {mark} {b['metric']:<20} {b['value']:>9,} / {b['limit']:>9,} "
                     f"{b['unit']:<10} {b['level']:<8} {b['why']}")
    if report.defaulted:
        lines.append(f"  core only by default ({len(report.defaulted)} rules, "
                     f"{sum(r['chars'] for r in report.defaulted):,} chars):")
        lines += [f"    {r['chars']:>7,}  {r['rel']}" for r in
                  sorted(report.defaulted, key=lambda r: -r["chars"])]
    return "\n".join(lines)


_MD_STATE = {"over": "**Over the ratchet.**", "under": "**Under the ratchet.**",
             "at": "**At the ratchet.**"}


def _md_budget_row(b: dict) -> str:
    status = "within" if b["ok"] else "over"
    label = LABELS.get(b["metric"], b["metric"])
    return (f"| {label} | {b['value']:,} | {b['limit']:,} {b['unit']} | {b['level']} "
            f"| {status} | {b['why']} |")


def _md_ratchet(report: Report) -> list[str]:
    if not report.ratchets:
        return [f"No ratchet is declared in `{SCOPE_REL}`."]
    return [f"{_MD_STATE[state]} {message}"
            for state, message in map(ratchet_message, report.ratchets)]


def _md_largest(report: Report, limit: int = 10) -> list[str]:
    loaded = [r for r in report.files if r["tier"] in ("claude-md", "core-rule")]
    largest = sorted(loaded, key=lambda r: -r["chars"])[:limit]
    return (["", f"<details><summary>The {len(largest)} largest of the "
                 f"{report.metrics['always_loaded_files']} always-loaded files</summary>", "",
             "| File | Characters |", "|---|---:|"]
            + [f"| `{r['rel']}` | {r['chars']:,} |" for r in largest]
            + ["", "</details>"])


def render_markdown(report: Report) -> str:
    """The pull-request job summary: the budgets, the ratchet and the largest files."""
    m = report.metrics
    lines = ["### Instruction-file size (KERN)", "",
             f"Claude Code loads {m['always_loaded_files']} instruction files into every "
             f"session at startup, {m['always_loaded_chars']:,} characters in all, and warns "
             "when they pass the ceiling below. This report runs on every pull request and "
             "never blocks a merge.", "",
             delivered_sentence(m), "",
             "| Figure | Value | Limit | Kind | Status | Why |",
             "|---|---:|---:|---|---|---|"]
    lines += [_md_budget_row(b) for b in report.budgets]
    lines += ["", *_md_ratchet(report), *_md_largest(report)]
    if report.encrypted:
        lines += ["", f"{len(report.encrypted)} file(s) are git-crypt ciphertext in this "
                      "checkout and count as empty: "
                      + ", ".join(f"`{rel}`" for rel in report.encrypted)]
    return "\n".join(lines) + "\n"
