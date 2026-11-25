#!/usr/bin/env python3
"""Rules-scope classifier for the #464 subagent-preload split.

Single source of truth (config/rules-scope.json) for which ~/.claude/rules/*.md
are SAFETY-CORE (stay natively loaded -> reach Task-tool subagents) vs EXTENDED
(excluded from native load via settings.claudeMdExcludes and listed as an index in
the MAIN session only by hooks/subagent-preload-main-inject.py) vs MAIN-TIER
(excluded from native load the same way, and delivered to the MAIN session in full
by hooks/main-tier-inject.py, in chunks under the harness's 10,000-character cap;
KERN v2, the operator's ruling of 2026-10-18).

A SAFETY-CORE rule that carries ``paths:`` frontmatter loads natively only when a
matching file is read, written or edited: the file moment. A pinned rule may do that
only when the config's `file_moment` lists it (KERN v2 ruling 2(a), 2026-10-18).

Every rule is classified on purpose: validate() reports a rule in no list, so
one cannot reach main unclassified. Where a computation meets an unlisted rule anyway,
classify() follows the config's safety_core_default, false since 2026-10-01 (it was
true, and two rules nobody classified grew the always-loaded set by 4,180 characters).
Claude Code still loads natively any rule settings.claudeMdExcludes does not name, so
the default never drops a rule from a subagent. See config/rules-scope.json.

CLI:
  rules_scope.py --validate [ROOT]     # exit 0 iff partition and readers are sound (mutation guard)
  rules_scope.py --print-globs         # JSON array of claudeMdExcludes globs (extended, main tier)
  rules_scope.py --check-settings P    # exit 0 iff settings.json@P claudeMdExcludes == globs
  rules_scope.py --sync-settings P     # write the globs into settings.json@P (idempotent)
  rules_scope.py --check-all [ROOT]    # --check-settings over every SETTINGS_FILES entry
  rules_scope.py --sync-all [ROOT]     # --sync-settings over every SETTINGS_FILES entry;
                                       # exit 1 if one is missing (never creates a file)
  rules_scope.py --projection          # byte/token projection of the safety-core subagent load

ROOT defaults to the checkout this file lives in, never the shared tree, so a
sync edits, and a validation judges, the files you are working on
(rules/shared-tree-is-not-canonical.md). Until 2026-10-06 --validate read the
shared tree, and graded a worktree's new readers tags against main's config.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path, PurePosixPath

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
CONFIG_PATH = CLAUDE_DIR / "config" / "rules-scope.json"
RULES_DIR = CLAUDE_DIR / "rules"

# Hard mutation guard: these MUST always be safety-core (loaded into subagents).
# Moving any of them to `extended` is a SECURITY REGRESSION and fails --validate.
# Deliberately a strict SUBSET of safety_core_explicit — the non-negotiable floor.
MUST_BE_SAFETY_CORE = frozenset({
    "bash-safety.md",
    "no-guardrail-evasion.md",
    "untrusted-content-capability-lock.md",
    "no-sensitive-info-in-grip.md",
    "sync-mirror-safety.md",
    "no-stubs.md",
    "commit-standards.md",
    "visual-artifact-leak-prevention.md",
    "verify-canonical-not-claimed.md",
    "prove-it-claims-canonical.md",
    # Added 2026-08-25 (AGORA #5080 round 3). config/rules-scope.json documented
    # these five as DELIBERATELY KEPT in safety-core, and an earlier note claimed
    # the extended-vs-explicit disjointness check enforced that. It did not: that
    # check fires only on an OVERLAP, so removing a rule from safety_core_explicit
    # AND adding it to extended passes every test - verified, 14 passed / 1
    # pre-existing failure, exactly as before. The note asserted a safeguard that
    # did not exist, which is worse than the gap it claimed to close. This is the
    # floor `validate()` actually enforces, so the decision lives here.
    "tests-must-be-able-to-fail.md",          # (b) integrity of code a subagent writes
    "declared-input-must-reach-the-actor.md",  # (b) same - the input-side twin of no-stubs
    "shared-tree-is-not-canonical.md",         # (c) integrity of claims a subagent makes
    "anchor-on-the-artefact-not-the-session.md",  # (c) same - hypothesis ranking
    "provenance-name-the-authors.md",          # governs commit trailers; commit-standards is core
    # Added 2026-09-20. The budget test names this rule in its growth term and
    # tells the reader to move non-safety ones to `extended`. This one is NOT a
    # non-safety one: it is (a) dangerous-action. A subagent has Bash, and running
    # a GRIP module under the system python does not fail -- it writes a FORGEABLE
    # demo seal and exits 0 (rules/never-system-python-for-grip.md). Shedding it
    # to save ~2976 tokens would drop a rule that prevents a subagent silently
    # producing un-signable records, which is the security regression
    # config/rules-scope.json::_conservative_default forbids. Nothing stopped that
    # before this line; the four other rules in that growth term are genuinely
    # extended by _classification_principle, which is what made the list look
    # uniformly safe to shed.
    "never-system-python-for-grip.md",         # (a) dangerous-action a subagent can take
    # Added 2026-10-03, the operator's ruling. This rule merged UNCLASSIFIED
    # (GRIP#6957, 2026-10-01) and test_partition_is_sound went red on main
    # naming it, but no CI workflow ran that test, so nothing on the PR showed
    # it. It is (c): it forbids calling a failure "pre-existing" without
    # evidence, which is the integrity of a claim a subagent makes. Pinned here
    # so that moving it to `extended` still fails --validate even if it is
    # also dropped from safety_core_explicit.
    "surface-and-fix-regression.md",           # (c) integrity of claims a subagent makes
})

# Who a rule is for (KERN v2 phase 1). config/rules-scope.json tags every rule with
# its readers. A subagent holds the capabilities the tools in its definition give it
# (lib/rule_routing.py derives them), and every agent makes claims;
# handles-personal-data comes from the task, not from a tool. The main session alone
# talks to the operator and runs GRIP's loops, merges and memory, so those two tags
# never select a subagent.
SUBAGENT_READERS = frozenset({"makes-claims", "writes-code", "runs-shell", "writes-repo",
                              "sends-outside", "dispatches", "handles-personal-data"})
MAIN_ONLY_READERS = frozenset({"talks-to-operator", "orchestrates"})
READERS = SUBAGENT_READERS | MAIN_ONLY_READERS

# Who each pinned rule must reach, stated here and not read from the config, so a
# tag dropped from config/rules-scope.json fails validate() instead of quietly
# narrowing who receives the rule. Covers every MUST_BE_SAFETY_CORE rule, plus the
# three other rules of the floor KERN v2 keeps: claim integrity (r0) and
# irreversible harm (personal matters, the operator's anonymity).
MUST_REACH = {
    "bash-safety.md": frozenset({"runs-shell"}),
    "no-guardrail-evasion.md": frozenset({"writes-code"}),
    "untrusted-content-capability-lock.md": frozenset({"writes-code", "dispatches"}),
    "no-sensitive-info-in-grip.md": frozenset({"writes-code", "writes-repo"}),
    "sync-mirror-safety.md": frozenset({"writes-repo"}),
    "no-stubs.md": frozenset({"writes-code"}),
    "commit-standards.md": frozenset({"writes-repo"}),
    "visual-artifact-leak-prevention.md": frozenset({"sends-outside", "writes-code"}),
    "verify-canonical-not-claimed.md": frozenset({"makes-claims"}),
    "prove-it-claims-canonical.md": frozenset({"makes-claims"}),
    "tests-must-be-able-to-fail.md": frozenset({"writes-code"}),
    "declared-input-must-reach-the-actor.md": frozenset({"writes-code"}),
    "shared-tree-is-not-canonical.md": frozenset({"makes-claims"}),
    "anchor-on-the-artefact-not-the-session.md": frozenset({"makes-claims"}),
    "provenance-name-the-authors.md": frozenset({"writes-repo"}),
    "never-system-python-for-grip.md": frozenset({"runs-shell"}),
    "surface-and-fix-regression.md": frozenset({"makes-claims"}),
    "r0-critical-thinking.md": frozenset({"makes-claims"}),
    "personal-matters-stay-private.md": frozenset({"handles-personal-data", "writes-repo"}),
    "operator-anonymity.md": frozenset({"handles-personal-data", "writes-repo"}),
}

# The safety-core growth tripwire, in projection tokens (bytes / 3.7). The
# operator raised it from 40,000 on 2026-10-03 (config/rules-scope.json,
# _note_budget_tripwire_2026_10_03). It lives here so lib/lexicon/measure.py and
# tests/test_rules_scope.py read one number: measure.py kept reporting 40,000
# for a week after the suite moved to 72,000.
SAFETY_CORE_TOKEN_TRIPWIRE = 72_000

# The main-session tier (KERN v2, the operator's ruling of 2026-10-18). A rule only
# the main session needs leaves native loading and arrives in full through this hook,
# which settings bind on SessionStart once per chunk, `--chunk 0` to
# `--chunk MAIN_TIER_CHUNKS - 1`. lib/main_tier.py packs the chunks; validate()
# refuses a tier that needs more chunks than this, and delivery_problems() a settings
# file that excludes the tier without running every chunk on every kind of start.
MAIN_TIER_HOOK = "hooks/main-tier-inject.py"
MAIN_TIER_CHUNKS = 6
# KERN v2 ruling 1(a), 2026-10-18: a command-moment rule leaves native loading and is
# TAUGHT by hooks/teach-once-gate.py, which refuses the first matching command in each
# recipient context with the rule's full text as the reason, then lets it pass. The
# gate names the rules it teaches (lib/teach_once.RULES), and `command_moment` in
# config/rules-scope.json may list only those, so no rule can be routed to a gate
# that does not know it. The gate runs for subagents' Bash calls too, so a pinned
# rule keeps its reach there: at the moment it matters rather than at every start.
COMMAND_MOMENT_HOOK = "hooks/teach-once-gate.py"


def taught_rules() -> frozenset[str]:
    """The basenames hooks/teach-once-gate.py teaches, read from lib/teach_once."""
    import teach_once  # imported here: a library module, loaded only when validating
    return frozenset(PurePosixPath(rule).name for rule in teach_once.RULES)


def load_config(path: Path | None = None) -> dict:
    p = path or CONFIG_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def extended_files(cfg: dict | None = None) -> list[str]:
    cfg = cfg or load_config()
    return list(cfg.get("extended", []))


def safety_core_explicit(cfg: dict | None = None) -> list[str]:
    cfg = cfg or load_config()
    return list(cfg.get("safety_core_explicit", []))


def _moment_names(cfg: dict) -> list[str] | None:
    """The config's `file_moment` names, or None when the value is not a list of names."""
    names = cfg.get("file_moment", [])
    if isinstance(names, list) and all(isinstance(n, str) for n in names):
        return names
    return None


def file_moment(cfg: dict | None = None) -> frozenset[str]:
    """The rules the operator has ruled may load at the file moment. Each carries ``paths:``
    frontmatter, so Claude Code loads it only when a matching file is read, written or
    edited, and the first write of a new matching file goes out without it
    (docs/measurements/2026-10-06-kern-v2-vendor-probes.md). Listing is the one way a pinned
    rule may be path-scoped (KERN v2 ruling 2(a), 2026-10-18). A malformed value admits
    nothing, and validate() reports it."""
    return frozenset(_moment_names(cfg or load_config()) or ())


def tracked_rules(rules_dir: Path | None = None) -> list[str]:
    """All rules/*.md present on disk (basenames)."""
    d = rules_dir or RULES_DIR
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("*.md"))


def classify(name: str, cfg: dict | None = None) -> str:
    """Return 'main-tier', 'command-moment', 'extended' or 'safety_core' for a rule basename. An unlisted
    rule takes the config's safety_core_default, which an older config without the key
    leaves true. A rule listed both main-tier and extended (validate() refuses that)
    counts as main-tier, the class that delivers its full text."""
    cfg = cfg or load_config()
    if name in set(cfg.get("main_tier") or []):
        return "main-tier"
    if name in set(cfg.get("command_moment") or []):
        return "command-moment"
    if name in set(cfg.get("extended", [])):
        return "extended"
    if name in set(cfg.get("safety_core_explicit", [])) or cfg.get("safety_core_default", True):
        return "safety_core"
    return "extended"


def _rule_globs(names) -> list[str]:
    """claudeMdExcludes globs for rule basenames, scoped under /rules/ and sorted."""
    # SORTED, because the two entry points disagreed otherwise (AGORA #5080
    # round 3): `sync_settings` compares lists order-sensitively while
    # `check_settings` compares sets, so a settings.json in a different order
    # than the `extended` array passed the drift TEST while `--sync-settings`
    # reported "changed" and rewrote every glob. One canonical order makes them
    # agree by construction.
    return sorted({f"**/rules/{name}" for name in names})


def main_tier_globs(cfg: dict | None = None) -> list[str]:
    """claudeMdExcludes globs for the main-session tier alone."""
    cfg = cfg or load_config()
    return _rule_globs(cfg.get("main_tier") or [])


def command_moment_globs(cfg: dict | None = None) -> list[str]:
    """claudeMdExcludes globs for the command-moment rules alone."""
    cfg = cfg or load_config()
    return _rule_globs(cfg.get("command_moment") or [])


def exclude_globs(cfg: dict | None = None) -> list[str]:
    """claudeMdExcludes globs for every rule kept out of native loading: the extended
    set and the main-session tier. Scoped under /rules/ so they can NEVER match a
    top-level CLAUDE.md (user-global or project ./CLAUDE.md)."""
    cfg = cfg or load_config()
    return _rule_globs([*cfg.get("extended", []), *(cfg.get("main_tier") or []),
                        *(cfg.get("command_moment") or [])])


def settings_excludes(cfg: dict | None = None) -> list[str]:
    """The full settings.claudeMdExcludes list: the extended-rule globs plus the
    config's `non_rule_excludes`, sorted. The one definition both entry points use,
    so `--sync-settings` can no longer write a list `--check-settings` rejects: it
    used to write the rule globs alone and drop `**/.grip-worktrees/*/CLAUDE.md`,
    the exclude that stops worktree sessions loading the global CLAUDE.md twice."""
    cfg = cfg or load_config()
    return sorted(set(exclude_globs(cfg)) | set(cfg.get("non_rule_excludes") or []))


def _partition_problems(ext: set[str], sce: set[str], main: set[str] = frozenset(),
                        command: set[str] = frozenset()) -> list[str]:
    """The lists are disjoint, and the non-negotiable floor is listed core, never extended
    and never main-tier: both keep a rule from every subagent."""
    problems = []
    if ext & sce:
        problems.append(f"extended & safety_core_explicit overlap: {sorted(ext & sce)}")
    problems += [f"main_tier & {label} overlap: {sorted(main & other)}"
                 for label, other in (("extended", ext), ("safety_core_explicit", sce)) if main & other]
    if MUST_BE_SAFETY_CORE & ext:
        problems.append(
            f"SECURITY REGRESSION: safety rule(s) moved to extended: {sorted(MUST_BE_SAFETY_CORE & ext)}")
    problems += [f"command_moment & {label} overlap: {sorted(command & other)}"
                 for label, other in (("extended", ext), ("safety_core_explicit", sce), ("main_tier", main))
                 if command & other]
    untaught = (MUST_BE_SAFETY_CORE & command) - taught_rules()
    if untaught:
        problems.append(f"SECURITY REGRESSION: safety rule(s) moved to command_moment that "
                        f"{COMMAND_MOMENT_HOOK} does not teach: {sorted(untaught)}")
    if MUST_BE_SAFETY_CORE & main:
        problems.append(
            f"SECURITY REGRESSION: safety rule(s) moved to main_tier: {sorted(MUST_BE_SAFETY_CORE & main)}")
    if MUST_BE_SAFETY_CORE - sce - command:
        problems.append(
            f"MUST_BE_SAFETY_CORE not in safety_core_explicit: {sorted(MUST_BE_SAFETY_CORE - sce - command)}")
    return problems


def _classification_problems(on_disk: set[str], ext: set[str], sce: set[str], runtime: set[str],
                             main: set[str] = frozenset(), command: set[str] = frozenset()) -> list[str]:
    """Every rule on disk is classified on purpose, in exactly one class. An unlisted rule was once
    allowed and defaulted to safety-core, which grew the always-loaded set unseen; it is now reported,
    so the choice is made, and seen, in review (2026-10-01)."""
    problems = []
    for name in sorted(on_disk):
        if name in ext and name in sce:
            problems.append(f"{name}: in BOTH extended and safety_core_explicit")
        elif not (name in ext or name in sce or name in main or name in command or name in runtime):
            problems.append(f"{name}: not classified; list it in safety_core_explicit, extended, "
                            f"main_tier or command_moment of config/rules-scope.json "
                            f"(see _classification_principle)")
    return problems


def _reference_problems(known: set[str], ext: set[str], sce: set[str],
                        main: set[str] = frozenset(), command: set[str] = frozenset()) -> list[str]:
    """The lists name real files, allowing runtime-generated ones that are absent."""
    return ([f"extended lists non-existent, non-runtime file: {name}" for name in sorted(ext - known)]
            + [f"safety_core_explicit lists non-existent file: {name}" for name in sorted(sce - known)]
            + [f"main_tier lists non-existent file: {name}" for name in sorted(main - known)]
            + [f"command_moment lists non-existent file: {name}" for name in sorted(command - known)])


def reader_table(cfg: dict | None = None) -> dict[str, list]:
    """config/rules-scope.json's `readers`, without its `_doc` keys."""
    cfg = cfg or load_config()
    return {k: v for k, v in (cfg.get("readers") or {}).items() if not k.startswith("_")}


def readers(name: str, cfg: dict | None = None) -> frozenset[str]:
    """Who rules/<name> is for; empty when it is untagged, which validate() reports."""
    tags = reader_table(cfg).get(name)
    return frozenset(tags) if isinstance(tags, list) else frozenset()


def _tag_problems(name: str, tags) -> list[str]:
    if not isinstance(tags, list) or not tags:
        return [f"{name}: readers must be a non-empty list of {sorted(READERS)}"]
    unknown = sorted(set(tags) - READERS)
    return [f"{name}: unknown readers {unknown}; known: {sorted(READERS)}"] if unknown else []


def _reader_problems(on_disk: set[str], known: set[str], cfg: dict) -> list[str]:
    """Every rule on disk says who it is for, in the known vocabulary, for a real file."""
    table = reader_table(cfg)
    problems = [f"{name}: no readers; tag who it is for under `readers` in "
                f"config/rules-scope.json" for name in sorted(on_disk - set(table))]
    for name, tags in sorted(table.items()):
        if name not in known:
            problems.append(f"readers names a non-existent, non-runtime file: {name}")
        problems += _tag_problems(name, tags)
    return problems


def _path_scoped(rules_dir: Path, name: str) -> bool:
    """Whether rules/<name> carries ``paths:`` frontmatter, so it loads only on a matching file."""
    from lexicon import surfaces  # imported here: lexicon.surfaces imports this module
    path = rules_dir / name
    return path.is_file() and surfaces.is_path_scoped(surfaces.load_text(path)[0])


def _path_globs(rules_dir: Path, name: str) -> list[str]:
    """The non-empty globs rules/<name>'s ``paths:`` frontmatter declares, read as YAML; a
    single string counts as one. ``paths: []``, a null value or only empty strings declare
    none, although the key alone still reads as path-scoped (Codex on GRIP#8526)."""
    import yaml  # here, not at the top: only validate() needs it, and a hook imports this module
    from lexicon import surfaces  # imported here: lexicon.surfaces imports this module
    try:
        meta = yaml.safe_load(surfaces.frontmatter(surfaces.load_text(rules_dir / name)[0]))
    except yaml.YAMLError:
        return []
    paths = meta.get("paths") if isinstance(meta, dict) else None
    paths = [paths] if isinstance(paths, str) else paths
    return [p for p in paths if isinstance(p, str) and p.strip()] if isinstance(paths, list) else []


def _reach_problems(ext: set[str], cfg: dict, rules_dir: Path,
                    main: set[str] = frozenset()) -> list[str]:
    """A pinned rule keeps its pinned readers, and a route that reaches them: an `extended`
    or `main_tier` rule reaches no subagent, and a path-scoped one only an agent touching a
    matching file (Codex on GRIP#7687). So a pinned rule must load at every start unless
    `file_moment` lists it, which records the operator's ruling that the file moment is
    enough for it (KERN v2 ruling 2(a), 2026-10-18)."""
    problems = []
    moment = file_moment(cfg)
    for name, need in sorted(MUST_REACH.items()):
        missing = need - readers(name, cfg)
        if missing:
            problems.append(f"SAFETY REACH: {name} lost readers {sorted(missing)} "
                            "(lib/rules_scope.MUST_REACH)")
        if need & SUBAGENT_READERS and name in ext | main:
            route = "main_tier" if name in main else "extended"
            problems.append(f"SAFETY REACH: {name} must reach subagents holding "
                            f"{sorted(need & SUBAGENT_READERS)}, and `{route}` reaches none")
        if _path_scoped(rules_dir, name) and name not in moment:
            problems.append(f"SAFETY REACH: {name} loads only when a matching file is touched, "
                            "and a pinned rule must reach its readers at every start unless "
                            "config/rules-scope.json `file_moment` lists it")
    return problems


def _file_moment_problems(cfg: dict, rules_dir: Path) -> list[str]:
    """Every rule `file_moment` lists is a real rule whose ``paths:`` frontmatter names at
    least one glob and which `extended` does not exclude, so the list and the files cannot
    drift. A listed rule that
    lost its frontmatter would load at every start again; one moved to `extended` would not
    load even on a matching file, because claudeMdExcludes keeps it out (Codex on GRIP#8526).
    Either way the config would still say it waits for a matching file."""
    names = _moment_names(cfg)
    if names is None:
        return [f"FILE MOMENT: file_moment must be a list of rule file names, "
                f"not {cfg.get('file_moment')!r}"]
    ext = set(cfg.get("extended", []))
    problems = []
    for name in sorted(set(names)):
        if not (rules_dir / name).is_file():
            problems.append(f"FILE MOMENT: file_moment names a non-existent rule: {name}")
            continue
        if not _path_scoped(rules_dir, name):
            problems.append(f"FILE MOMENT: {name} is listed in file_moment but carries no "
                            "paths: frontmatter, so it loads at every start; give it paths: "
                            "or take it off the list")
        elif not _path_globs(rules_dir, name):
            problems.append(f"FILE MOMENT: {name} is listed in file_moment but its paths: "
                            "names no glob, so no file is named to load it; give it at least "
                            "one glob or take it off the list")
        if name in ext:
            problems.append(f"FILE MOMENT: {name} is listed in file_moment but classified "
                            "extended, so claudeMdExcludes keeps it out even when a matching "
                            "file is touched; list it in safety_core_explicit or take it off "
                            "the list")
    return problems


def _main_tier_fit(main: set[str], cfg: dict, rules_dir: Path) -> list[str]:
    """The tier packs into no more chunks than settings bind: a chunk past the last bound
    hook would carry its rules to no one."""
    if not main:
        return []
    import main_tier  # imported here: main_tier imports lexicon.surfaces, which imports this module
    needed = len(main_tier.chunks(cfg, rules_dir))
    if needed <= MAIN_TIER_CHUNKS:
        return []
    return [f"MAIN TIER: it needs {needed} SessionStart chunks of at most "
            f"{main_tier.CHUNK_LIMIT:,} characters, and settings bind {MAIN_TIER_CHUNKS} "
            f"({MAIN_TIER_HOOK} --chunk 0 to --chunk {MAIN_TIER_CHUNKS - 1}); bind more or move "
            "a rule out, or the rules in the extra chunks reach no one"]


def _main_tier_problems(main: set[str], cfg: dict, rules_dir: Path) -> list[str]:
    """A main-tier rule is for the main session alone, because no subagent receives it,
    and the whole tier fits the chunks settings bind."""
    problems = [f"MAIN TIER: {name} is for readers no main-tier rule may have: "
                f"{sorted(readers(name, cfg) - MAIN_ONLY_READERS)}; a main-tier rule reaches no "
                f"subagent, so its readers must be among {sorted(MAIN_ONLY_READERS)}"
                for name in sorted(main) if readers(name, cfg) - MAIN_ONLY_READERS]
    return problems + _main_tier_fit(main, cfg, rules_dir)


def _command_moment_problems(command: set[str]) -> list[str]:
    """A command-moment rule must be one the gate teaches, or it would reach no one."""
    return [f"COMMAND MOMENT: {name} is not a rule {COMMAND_MOMENT_HOOK} teaches "
            f"(lib/teach_once.RULES); it would reach no one" for name in sorted(command - taught_rules())]


def validate(cfg: dict | None = None, rules_dir: Path | None = None) -> list[str]:
    """Return a list of problems (empty == sound): the safe partition, every rule classified on
    purpose and tagged with its readers, pinned rules keeping their reach (path-scoped only
    when `file_moment` lists them, and every listed rule path-scoped), a main tier that only
    the main session needs and that fits its chunks, list entries naming real files, and
    exclude globs scoped under /rules/."""
    cfg = cfg or load_config()
    ext = set(cfg.get("extended", []))
    sce = set(cfg.get("safety_core_explicit", []))
    main = set(cfg.get("main_tier") or [])
    command = set(cfg.get("command_moment") or [])
    runtime = set(cfg.get("runtime_generated", []))
    rules_dir = rules_dir or RULES_DIR
    on_disk = set(tracked_rules(rules_dir))
    # Exclude globs never target a bare CLAUDE.md outside /rules/.
    unscoped = [f"exclude glob not scoped under /rules/: {g}" for g in exclude_globs(cfg) if "/rules/" not in g]
    return (_partition_problems(ext, sce, main, command)
            + _classification_problems(on_disk, ext, sce, runtime, main, command)
            + _reference_problems(on_disk | runtime, ext, sce, main, command) + unscoped
            + _reader_problems(on_disk, on_disk | runtime, cfg) + _reach_problems(ext, cfg, rules_dir, main)
            + _file_moment_problems(cfg, rules_dir) + _main_tier_problems(main, cfg, rules_dir)
            + _command_moment_problems(command))


def _settings_excludes(settings_path: Path) -> list[str]:
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    return list(data.get("claudeMdExcludes", []))


def check_settings(settings_path: Path, cfg: dict | None = None) -> list[str]:
    """Return problems if settings.claudeMdExcludes does not contain exactly the
    generated exclude globs (drift guard)."""
    # Globs that are deliberately NOT rules/*.md are declared in config
    # (`non_rule_excludes`) rather than hardcoded, so the drift guard still fires
    # on anything outside the named set.
    want = set(settings_excludes(cfg))
    have = set(_settings_excludes(settings_path))
    problems: list[str] = []
    missing = want - have
    extra = have - want
    if missing:
        problems.append(f"settings.claudeMdExcludes MISSING globs: {sorted(missing)}")
    if extra:
        problems.append(f"settings.claudeMdExcludes has UNEXPECTED globs: {sorted(extra)}")
    return problems


def _dumps_like(original: str, data: dict) -> str:
    """``data`` as JSON in the escaping ``original`` was written with.

    settings.json escapes non-ASCII characters and the reference pair keeps them
    as they are, so one fixed choice would rewrite every non-ASCII character in
    one of the files on each sync.
    """
    escaped = json.dumps(json.loads(original), indent=2) + "\n" == original
    return json.dumps(data, indent=2, ensure_ascii=escaped) + "\n"


def sync_settings(settings_path: Path, cfg: dict | None = None) -> bool:
    """Write the exclude globs into a settings file (idempotent). Returns True if changed."""
    text = settings_path.read_text(encoding="utf-8")
    data = json.loads(text)
    want = settings_excludes(cfg)
    if data.get("claudeMdExcludes") == want:
        return False
    data["claudeMdExcludes"] = want
    settings_path.write_text(_dumps_like(text, data), encoding="utf-8")
    return True


# Every settings file that must carry the excludes. settings.json is what an
# existing install loads. The reference pair is what a FRESH install is built
# from: lib/settings_merge.py copies config/settings.reference.json when no
# settings.json exists yet, and lib/hooks_registry.py renders that file from its
# base. Until KERN v2 phase 0 the pair carried no excludes, so a fresh install
# loaded every rule natively, extended ones included (measured 2026-10-06).
SETTINGS_FILES = ("settings.json", "config/settings.reference.base.json",
                  "config/settings.reference.json")


# The settings files Claude Code actually runs with: settings.json, and the
# reference a fresh install copies into place. The base is only an input that
# lib/hooks_registry.py renders hooks into, so it carries no hooks to check.
INSTALLED_SETTINGS = ("settings.json", "config/settings.reference.json")

# The hook that tells the main session the extended rules exist. An excluded rule
# reaches the main session only through it, and no subagent at all.
INDEX_HOOK = "hooks/subagent-preload-main-inject.py"

# Every way a session starts. The exclusions apply to all of them, so the index
# must too: a group matching only "clear" would leave a normal startup with the
# exclusions and no index (Codex on GRIP#7684).
SESSION_SOURCES = ("startup", "resume", "clear", "compact")


def _covers(matcher, source: str) -> bool:
    """Whether a SessionStart matcher selects ``source``. Read strictly (a full match),
    so a matcher this cannot read counts as covering nothing rather than everything."""
    if matcher in (None, "", "*"):
        return True
    try:
        return re.fullmatch(str(matcher), source) is not None
    except re.error:
        return False


def _matchers(data: dict, runs) -> list:
    """The matcher of each SessionStart group with a hook whose command ``runs`` accepts."""
    return [group.get("matcher") for group in (data.get("hooks") or {}).get("SessionStart", [])
            if any(runs(str(h.get("command", ""))) for h in group.get("hooks", []))]


# A main-tier chunk's argument in a hook command: `--chunk 3` or `--chunk=3`.
_CHUNK_ARG = re.compile(r"--chunk[ =]([0-9]+)\b")


def _runs_chunk(command: str, index: int) -> bool:
    """Whether a hook command runs the main-tier hook for chunk ``index``."""
    return MAIN_TIER_HOOK in command and index in {int(k) for k in _CHUNK_ARG.findall(command)}


def _start_problems(count: int, kind: str, hook: str, matchers: list) -> list[str]:
    """One problem when ``count`` rules are excluded and ``hook`` misses a kind of start."""
    missing = [s for s in SESSION_SOURCES if not any(_covers(m, s) for m in matchers)]
    if not count or not missing:
        return []
    where = (f"runs {hook} only under SessionStart matchers {matchers}, not for {missing}"
             if matchers else f"binds no SessionStart hook running {hook}")
    return [f"excludes {count} {kind} from native loading but {where}, "
            "so those rules reach no one in such a session"]


def _bash_gate_problems(count: int, data: dict) -> list[str]:
    """One problem when ``count`` command-moment rules are excluded and no PreToolUse group
    whose matcher covers Bash runs the teach-once gate: nothing would teach them."""
    groups = (data.get("hooks") or {}).get("PreToolUse", [])
    bound = [g.get("matcher") for g in groups
             if any(COMMAND_MOMENT_HOOK in str(h.get("command", "")) for h in g.get("hooks", []))]
    if not count or any(_covers(m, "Bash") for m in bound):
        return []
    return [f"excludes {count} command-moment rules from native loading but binds no PreToolUse "
            f"group running {COMMAND_MOMENT_HOOK} for Bash, so nothing would teach them"]


def delivery_problems(settings_path: Path, cfg: dict | None = None) -> list[str]:
    """Problems if a settings file keeps rules out of native loading without running, on
    every kind of start, the SessionStart hook that hands them to the main session: the
    index hook for extended rules, and the main-tier hook for every one of its chunks.
    Either gap leaves those rules reaching no one.

    KERN v2 phase 0 (GRIP#7675) put the excludes into the fresh-install reference,
    whose install profile did not bind the index hook: on a fresh install the
    extended rules stopped loading and nothing listed them. Before that change a
    fresh install loaded every rule natively (measured 2026-10-06). A missing chunk
    binding fails the same way, for the rules packed into that chunk.
    """
    cfg = cfg or load_config()
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    excludes = set(data.get("claudeMdExcludes") or [])
    extended = len(excludes & set(_rule_globs(cfg.get("extended", []))))
    main = len(excludes & set(main_tier_globs(cfg)))
    problems = _start_problems(extended, "extended rules", INDEX_HOOK,
                               _matchers(data, lambda command: INDEX_HOOK in command))
    for index in range(MAIN_TIER_CHUNKS):
        problems += _start_problems(main, "main-tier rules", f"{MAIN_TIER_HOOK} --chunk {index}",
                                    _matchers(data, lambda command, i=index: _runs_chunk(command, i)))
    problems += _bash_gate_problems(len(excludes & set(command_moment_globs(cfg))), data)
    return problems


def check_settings_files(root: Path, cfg: dict | None = None) -> list[str]:
    """check_settings over every SETTINGS_FILES entry under ``root``, plus delivery_problems
    for the files Claude Code runs with; a missing file is a problem."""
    problems = []
    for rel in SETTINGS_FILES:
        path = root / rel
        found = check_settings(path, cfg) if path.is_file() else ["file not found"]
        if path.is_file() and rel in INSTALLED_SETTINGS:
            found += delivery_problems(path, cfg)
        problems += [f"{rel}: {p}" for p in found]
    return problems


def missing_settings_files(root: Path) -> list[str]:
    """The SETTINGS_FILES entries absent under ``root``."""
    return [rel for rel in SETTINGS_FILES if not (root / rel).is_file()]


def sync_settings_files(root: Path, cfg: dict | None = None) -> list[Path]:
    """sync_settings over every SETTINGS_FILES entry present under ``root``; returns those
    changed. It never creates a missing file: callers report missing_settings_files."""
    present = [root / rel for rel in SETTINGS_FILES if (root / rel).is_file()]
    return [path for path in present if sync_settings(path, cfg)]


def tokens_for_bytes(n: int) -> int:
    """Rough chars/token estimate, exposed so callers share ONE estimator.

    `projection` used to inline this ratio, so any caller wanting a per-file
    figure had to duplicate the constant — and a duplicated estimator drifts,
    which would make a failure message's sizes and ordering disagree with the
    `safety_core_tokens_approx` the budget is actually measured in (AGORA #5080
    round 2, suggestion 2).
    """
    return round(n / 3.7)


def projection(cfg: dict | None = None, rules_dir: Path | None = None) -> dict:
    """Static projection of the safety-core rules load (what a subagent WOULD carry).
    A main-tier rule is excluded from native loading like an extended one, so no
    subagent carries it; it is counted on its own, and the shed figure stays the
    extended set's, the axis tests/test_rules_scope.py floors."""
    cfg = cfg or load_config()
    d = rules_dir or RULES_DIR
    ext = set(cfg.get("extended", []))
    main = set(cfg.get("main_tier") or [])
    core_bytes = ext_bytes = main_bytes = 0
    for p in sorted(d.glob("*.md")):
        n = p.stat().st_size
        if p.name in main:
            main_bytes += n
        elif p.name in ext:
            ext_bytes += n
        else:
            core_bytes += n
    tok = tokens_for_bytes
    return {
        "safety_core_bytes": core_bytes,
        "safety_core_tokens_approx": tok(core_bytes),
        "extended_bytes": ext_bytes,
        "extended_tokens_approx": tok(ext_bytes),
        "main_tier_bytes": main_bytes,
        "main_tier_tokens_approx": tok(main_bytes),
        "shed_tokens_approx": tok(ext_bytes),
    }


def _sync_all(root: Path, cfg: dict) -> int:
    """Sync every settings file present; a missing one fails the command rather
    than passing as in sync (Codex on GRIP#7675)."""
    for path in sync_settings_files(root, cfg):
        print(f"changed {path}")
    missing = missing_settings_files(root)
    for rel in missing:
        print(f"  - {rel}: file not found; --sync-all updates files and never creates one",
              file=sys.stderr)
    if not missing:
        print("every settings file is in sync")
    return 1 if missing else 0


def _check_all(root: Path, cfg: dict) -> int:
    problems = check_settings_files(root, cfg)
    for p in problems:
        print("  -", p, file=sys.stderr)
    if not problems:
        print("every settings file's claudeMdExcludes is in sync")
    return 1 if problems else 0


def _root_arg(argv: list[str], flag: str) -> Path:
    """The ROOT after ``flag``, defaulting to the checkout this file lives in."""
    rest = argv[argv.index(flag) + 1:]
    return Path(rest[0]) if rest and not rest[0].startswith("--") else _LIB.parent


def _all_settings(argv: list[str]) -> int:
    """--check-all / --sync-all [ROOT]: every settings file, against ROOT's own config."""
    flag = "--check-all" if "--check-all" in argv else "--sync-all"
    root = _root_arg(argv, flag)
    cfg = load_config(root / "config" / "rules-scope.json")
    return _sync_all(root, cfg) if flag == "--sync-all" else _check_all(root, cfg)


def main(argv: list[str]) -> int:
    if "--validate" in argv:
        root = _root_arg(argv, "--validate")
        probs = validate(load_config(root / "config" / "rules-scope.json"), root / "rules")
        if probs:
            print("INVALID rules-scope partition:", file=sys.stderr)
            for p in probs:
                print("  -", p, file=sys.stderr)
            return 1
        print("rules-scope partition OK")
        return 0
    if "--print-globs" in argv:
        print(json.dumps(exclude_globs(), indent=2))
        return 0
    if "--check-settings" in argv:
        sp = Path(argv[argv.index("--check-settings") + 1])
        probs = check_settings(sp)
        if probs:
            for p in probs:
                print("  -", p, file=sys.stderr)
            return 1
        print("settings.claudeMdExcludes in sync")
        return 0
    if "--sync-settings" in argv:
        sp = Path(argv[argv.index("--sync-settings") + 1])
        changed = sync_settings(sp)
        print("changed" if changed else "already in sync")
        return 0
    if "--check-all" in argv or "--sync-all" in argv:
        return _all_settings(argv)
    if "--projection" in argv:
        print(json.dumps(projection(), indent=2))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
