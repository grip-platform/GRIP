#!/usr/bin/env python3
"""Smart settings merge -- preserves local hook additions during reference sync.

The current bash/jq merge in settings-merge.sh does reference * user_preserved_fields,
which OVERWRITES all hooks. This Python merge:
1. Takes ALL reference hooks as canonical
2. Detects local additions (hooks not in reference) and preserves them
3. Preserves user fields: permissions, language, model, extraKnownMarketplaces, enabledPlugins
4. Overwrites GRIP-managed fields from reference

Hook identity is determined by extracting the script basename from the command string.
External hooks (Dorothy, etc.) have paths outside ~/.claude/ and never match reference hooks.
"""

import argparse
import json
import os
import shlex
import re
import shutil
import sys
import tempfile
from pathlib import Path

# Self-sufficient rather than relying on a caller to have put this directory
# on sys.path first (bin/grip-settings-reconcile does; a module loaded by
# path via importlib.util, as lib/tree_sync_resolve.py loads this one, does
# not touch sys.path at all). file_lock.py is this module's own sibling and
# is not itself changed by plan-v4, so importing it however sys.path finds
# it carries none of D1's "wrong checkout" risk -- only settings_merge.py and
# tree_sync_resolve.py, both edited in this PR, need the by-path discipline.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from file_lock import FileLock  # noqa: E402

#: Shell syntax that can trail a hook path but is never part of its identity.
#: One set for both identity paths: the GRIP-path branch used to strip a
#: narrower one, so `hooks/foo.sh&` kept its `&` there while `/o/foo.sh&`
#: shed it in the fallback (GRIP#5102).
_TRAILING_CHARS = "\"'`;,:&|()<> "
_TRAILING_SYNTAX = str.maketrans("", "", _TRAILING_CHARS)

# GRIP-managed keys: always overwritten from reference
GRIP_MANAGED_KEYS = frozenset({
    "_comment",
    "env",
    "hooks",
    "statusLine",
    "alwaysThinkingEnabled",
    "effortLevel",
    "skipDangerousModePermissionPrompt",
    # GRIP #2868 W7 — the canonical-output-style delivery keys. These are
    # infrastructure (same class as hooks/statusLine), not user taste:
    # the PARAMOUNT rules/canonical-output-style.md contract is delivered
    # via the grip-canonical preset + the harness reasoning-render toggle.
    # Force-overwritten every install/update so a manual `/status` pick
    # cannot silently drift the canonical default. Per-session operator
    # control survives via `normal mode` / `/caveman` (session-scoped,
    # not settings.json). Reversible: drop these two lines.
    "outputStyle",
    "showThinkingSummaries",
    # 2026-08-25 (V>>-ratified, GRIP #5047). settings.json shipped
    # `autoCompactEnabled: true` while BOTH reference templates said false, and
    # the key was in no list — so it took the preserve-from-existing branch and
    # the two populations diverged the wrong way round: a FRESH install got the
    # fix, while an EXISTING install kept compaction on. The people who hit the
    # 200K compaction wall were exactly the ones who did not receive the change.
    #
    # Managing it converges everyone on the reference value at next update.
    # That IS a behaviour change for anyone who set `true` deliberately, which
    # is why it was an operator decision rather than a bug-fix; per-session
    # control is unaffected (compaction can still be triggered manually).
    # Reversible: drop this line and the key falls back to preserve-from-existing.
    "autoCompactEnabled",
})

# Keys the harness no longer reads, dropped from the merged result rather than
# carried forward. `autoCompact` is the originating case: Claude Code reads
# `autoCompactEnabled`, so an install holding both saw two keys with opposite
# values and only one of them consulted — which is precisely how an operator
# came to believe compaction was off while it was on (GRIP #5047). Preserving
# an unread key is not conservatism; it keeps a contradiction alive in the file
# a human reads to understand their own configuration.
#
# Scope note, deliberately narrow: this drops a key GRIP itself shipped and
# then superseded. It is not a general "unknown key" reaper — unknown keys stay
# preserved, because a key this merger does not recognise may be one a newer
# harness does.
DEAD_KEYS = frozenset({
    "autoCompact",
})

# User-preserved keys: always kept from existing settings
USER_PRESERVED_KEYS = frozenset({
    "permissions",
    "language",
    "model",
    "extraKnownMarketplaces",
    "enabledPlugins",
})

# The env spelling of the operator's model choice: ANTHROPIC_MODEL and the alias
# map ANTHROPIC_DEFAULT_<FAMILY>_MODEL. `model` is user-preserved because which
# model an operator runs is taste, not infrastructure, and these variables are
# the same choice written another way, so they survive a merge even though the
# `env` block as a whole is GRIP-managed. The existing value wins, as it does for
# `model`. Measured 2026-10-02: a fast-forward in one worktree re-ran this merge
# on the shared install and erased the three alias lines GRIP#6995 added,
# putting `--model fable` and its siblings back on 200K windows.
_OPERATOR_MODEL_ENV = re.compile(r"ANTHROPIC_(?:DEFAULT_[A-Z0-9]+_)?MODEL")

# Operator choices written as environment variables that are not about the model,
# kept through a merge for the same reason, with the existing value winning.
# GRIP_AUTH_FAILOVER_ON_CAP decides whether a usage cap engages the paid API key or
# waits for the reset: a choice about one machine's spending, not infrastructure
# (see docs/auth-bucket-failover.md). Names are listed exactly and never matched by
# pattern, so the rest of the env block stays GRIP-managed.
_OPERATOR_POLICY_ENV = frozenset({"GRIP_AUTH_FAILOVER_ON_CAP"})

# The root a hook command uses to reach the GRIP install. settings.json resolves
# it as ${CLAUDE_DIR:-$HOME/.claude} so an install outside ~/.claude still works,
# and the closing brace sits between the root and hooks/ -- a pattern anchored on
# a literal "$HOME/.claude/" misses every one of those. Measured on 2026-08-17:
# 121 of 127 tracked hook commands returned a malformed identity because of it
# (issue #4791). Keep every spelling here, not just the one a fixture happens to use.
#
# ~/.grip / $HOME/.grip is one of those spellings, not a hypothetical one
# (review finding F10 on PR #8719): CLAUDE.md states "~/.claude IS the GRIP
# local repository" via a symlink to ~/.grip, and that symlink is this
# machine's actual deployment (rules/shared-tree-is-not-canonical.md names
# ~/.grip as the tree launchd agents execute from) -- so a hook command
# written against the real target rather than through the symlink is a
# live case, not a corner nothing produces. Before this, such a command
# fell through to _basename_identity's generic fallback instead of its real
# hooks/lib identity, and missing_hook_scripts/verify_composed_settings (C4)
# could not resolve its script path at all -- silently exempting it from
# the "does this script exist" check rather than refusing when it does not.
_GRIP_ROOT = r"""(?:\$\{CLAUDE_DIR(?::-[^}]*)?\}|\$CLAUDE_DIR|(?:\$HOME|~)/\.(?:claude|grip))"""

# Uses \S+ (non-whitespace) instead of .+ to match individual path segments
# when multiple root references appear in one command.
_GRIP_PATH_RE = re.compile(
    _GRIP_ROOT + r"""/(?:hooks|lib)/(\S+)"""
)


def _command_words(command: str):
    """POSIX-ish words with comments removed, or None if the command will not parse.

    Delegated to `shlex` rather than hand-rolled (AGORA #5082 round 2). The
    hand-written scanner was patched twice and still lost three cases the
    council found, each a different corner of shell quoting:

      * a CLOSING QUOTE is a word boundary, so `'/path/foo.sh'# note` is a real
        trailing comment - the scanner missed it and returned `note`, the exact
        comment-word identity this function exists to eliminate;
      * a comment ends at the next unquoted NEWLINE, not at end of string, so a
        multi-line command lost every line after the first and resolved to the
        `grip-python.sh` wrapper the identity logic deliberately skips;
      * inside `"..."` POSIX keeps a backslash LITERAL before an ordinary
        character, so `"/path/foo\\bar.sh"` must not become `foobar.sh`.

    That is three misses across two rounds on one hand-written tokeniser, which
    is the signal to stop writing one. `shlex` gets all three right, and returns
    words already unquoted, so the quote-stripping below is free.

    KNOWN DIVERGENCE, stated rather than hidden: `shlex` applies its commenter
    regardless of word position, so a `#` INSIDE a word is stripped too - a URL
    fragment `https://x/y#frag` yields `y`, where POSIX would keep `y#frag`. A
    hybrid that applied the word-start rule after lexing was tried and was worse:
    it re-broke both the closing-quote and multi-line cases, because comment
    rules have to apply DURING lexing, not after. The divergence only reaches
    arguments that are not hook scripts, so it costs nothing this function needs.
    """
    # The ONLY call to `shlex.split(..., comments=True)` in this module, so any
    # future change to comment handling stays in one place (AGORA #5094).
    try:
        return shlex.split(command, comments=True)
    except ValueError:
        return None


def extract_hook_identity(command: str) -> str:
    """Extract a stable identity string from a hook command.

    For GRIP hooks like:
        bash $HOME/.claude/lib/grip-python.sh $HOME/.claude/hooks/context-gate-hook.py
    Returns: "context-gate-hook.py"

    For external hooks like:
        /Applications/Dorothy.app/Contents/Resources/hooks/session-start.sh
    Returns the path from the app name onward: "Dorothy.app/Contents/Resources/hooks/session-start.sh"
    This will never collide with any GRIP reference hook basename.
    """
    if not command:
        return ""
    words = _command_words(command)
    if words:
        command = " ".join(words)
    grip = _grip_identity(command)
    if grip is not None:
        return grip
    app = _app_identity(command)
    if app is not None:
        return app
    return _basename_identity(command)


def _grip_identity(command: str):
    """The last GRIP hooks/ or lib/ path component, or None when there is none.

    The last match is the actual hook, not the wrapper like grip-python.sh.
    Shell commands may quote the path or terminate it with punctuation:
        /bin/sh "$HOME/.claude/hooks/otty-state-hook.sh" idle
        "$HOME/.claude/lib/tool.py";
    The historical ``\\S+`` matcher includes those delimiters. They are syntax,
    not part of the stable hook identity.
    """
    matches = _GRIP_PATH_RE.findall(command)
    # rstrip, not the fallback's translate: the match is a path component, and
    # deleting characters mid-string would turn `foo.sh>/dev/null` into a
    # nonsense `foo.sh/dev/null`. Only TRAILING syntax is shed.
    return matches[-1].rstrip(_TRAILING_CHARS) if matches else None


def _app_identity(command: str):
    """External app-style hook: the path from the app bundle onward, or None.

    e.g. /Applications/Dorothy.app/.../session-start.sh -> Dorothy.app/.../session-start.sh
    """
    app_match = re.search(r"(/\w+\.app/.+)$", command)
    return app_match.group(1).lstrip("/") if app_match else None


def _basename_identity(command: str) -> str:
    """Fallback: basename of the last word.

    Quotes are already gone when `shlex` parsed the command; they are stripped
    again for the unparseable case, where `command` is still the raw string. A
    trailing shell operator is syntax rather than identity, so `/path/foo.sh;`
    and `/path/foo.sh` are one hook. One translate table rather than chained
    strip/rstrip keeps removal ORDER-INDEPENDENT and auditable at a glance
    (AGORA #5094): the chained form stripped quotes then operators, so
    `foo.sh";` cleaned up but `foo.sh;"` did not - a difference with no meaning,
    since both are syntax rather than identity.
    """
    parts = command.strip().split()
    if not parts:
        return command
    return os.path.basename(parts[-1].translate(_TRAILING_SYNTAX)).strip()


def _hooks_identity_set(hook_groups: list) -> set:
    """Build a set of hook identities from a list of hook groups."""
    identities = set()
    for group in hook_groups:
        identities |= _group_identity_set(group)
    return identities


def _group_identity_set(group: dict) -> set:
    """Return the set of hook identities within a single hook group."""
    identities = set()
    for hook in group.get("hooks", []):
        identity = extract_hook_identity(hook.get("command", ""))
        if identity:
            identities.add(identity)
    return identities


def smart_merge_hooks(ref_hooks: dict, user_hooks: dict) -> dict:
    """Merge hook configurations, preserving user additions not in reference.

    For each event type:
    1. Start with all reference hook groups (canonical)
    2. Append user hook groups whose identities don't overlap with reference

    Event types that exist only in user config are preserved in full (future-proof
    for external tools that register new event types).
    """
    all_event_types = set(ref_hooks.keys()) | set(user_hooks.keys())
    return {
        event_type: _merge_event_groups(
            ref_hooks.get(event_type, []), user_hooks.get(event_type, [])
        )
        for event_type in all_event_types
    }


def _merge_event_groups(ref_groups: list, user_groups: list) -> list:
    """One event type's groups: the reference's first, then each user group's residue."""
    if not ref_groups:
        # Event type only in user config -- preserve entirely
        return user_groups
    if not user_groups:
        # Event type only in reference -- use reference
        return ref_groups
    ref_identities = _hooks_identity_set(ref_groups)
    residues = (_residual_group(group, ref_identities) for group in user_groups)
    return list(ref_groups) + [group for group in residues if group]


def _residual_group(user_group: dict, ref_identities: set):
    """The user group minus the hooks the reference already carries, or None.

    The old all-or-none rule dropped an entire user group when just one handler
    overlapped the reference, silently deleting unrelated local/external hooks,
    so a mixed group keeps its residue.
    """
    residual_hooks = [
        hook for hook in user_group.get("hooks", [])
        if _identity_is_new(hook, ref_identities)
    ]
    if not residual_hooks:
        return None
    return {**user_group, "hooks": residual_hooks}


def _identity_is_new(hook: dict, ref_identities: set) -> bool:
    """True when the reference does not already carry this hook."""
    identity = extract_hook_identity(hook.get("command", ""))
    return not identity or identity not in ref_identities


def merge_settings(reference: dict, existing: dict) -> dict:
    """Full settings merge.

    - GRIP-managed keys: overwritten from reference
    - User-preserved keys: kept from existing
    - hooks: smart-merged (reference canonical + user additions)
    - Unknown keys: preserved from existing (future-proof)
    """
    merged = {}

    all_keys = set(reference.keys()) | set(existing.keys())

    for key in all_keys:
        if key in DEAD_KEYS:
            # Superseded by construction — never carried into the result.
            continue
        if key == "hooks":
            # Smart merge hooks
            ref_hooks = reference.get("hooks", {})
            user_hooks = existing.get("hooks", {})
            merged["hooks"] = smart_merge_hooks(ref_hooks, user_hooks)
        elif key in USER_PRESERVED_KEYS:
            # Keep user value if it exists, otherwise use reference
            if key in existing:
                merged[key] = existing[key]
            elif key in reference:
                merged[key] = reference[key]
        elif key in GRIP_MANAGED_KEYS:
            # Always use reference value
            if key in reference:
                merged[key] = reference[key]
        else:
            # Unknown key: preserve from existing, fall back to reference
            if key in existing:
                merged[key] = existing[key]
            elif key in reference:
                merged[key] = reference[key]

    _keep_operator_model_choice(merged, existing)
    return merged


def _is_operator_env_choice(name: str) -> bool:
    """True for the model and spending-policy env names that stay local even
    though `env` as a whole is GRIP-managed. One predicate for both the
    pre-existing two-way merge (below) and the three-way composer (plan-v4
    C2), so "is this name the operator's to keep" can never drift between
    the two consumers -- the exact gap opus-review flagged (the two-way
    merge and a hypothetical separate three-way rule following different
    lists for the same names)."""
    return bool(_OPERATOR_MODEL_ENV.fullmatch(name)) or name in _OPERATOR_POLICY_ENV


def _keep_operator_model_choice(merged: dict, existing: dict) -> None:
    """Carry the operator's model and spending-policy variables through a merge; theirs win."""
    block = existing.get("env")
    if not isinstance(block, dict):
        return
    chosen = {k: v for k, v in block.items() if _is_operator_env_choice(k)}
    if chosen:
        merged["env"] = {**merged.get("env", {}), **chosen}


# ============================================================================
# Three-way settings.json composer (plan-v4: "settings.json divergence in
# GRIP"). lib/tree_sync_resolve.py routes the shared tree's root settings.json
# through compose_settings_three_way when main and a local edit both touched
# it (C1-C3); bin/grip-settings-reconcile and bin/grip-tree-sync's repair call
# use reconcile_settings for a tree that is already level with origin/main but
# whose live settings.json has drifted from what HEAD tracks (the /model
# picker's whole-file rewrite, a hook edit, a plugin install). The two differ
# because a repair has no "base" distinct from the deployed revision itself --
# see reconcile_settings's own docstring for why that rules out reusing the
# three-way shortcut cascade there.
#
# Neither function does any I/O; every read, write, override, verify and
# backup decision lives in the caller (tree_sync_resolve.py's existing
# write-over-the-bytes-read loop, or write_settings_reconciled below).
# ============================================================================

_ABSENT = object()  # a key missing from one side -- mirrors tree_sync_resolve.py's own
                     # sentinel of the same name; not imported, so this module still
                     # composes without that one (install.sh needs it standalone).

# Machine-local keys beyond the pre-existing USER_PRESERVED_KEYS (which the
# two-way install merge already keeps local): Claude Code's own UI toggles
# that the /model picker and `/voice`/`/switch-models` rewrite wholesale.
# Reuses USER_PRESERVED_KEYS rather than repeating permissions/language/model/
# enabledPlugins/extraKnownMarketplaces, so the two lists cannot drift apart.
SETTINGS_MACHINE_LOCAL_KEYS = USER_PRESERVED_KEYS | frozenset({
    "verbose", "voice", "voiceEnabled", "switchModelsOnFlag",
})

# Keys config/settings.reference.json declares that GRIP_MANAGED_KEYS (the
# two-way install merge's own list, untouched by this section per plan-v4 C6)
# does not yet carry. Classified "managed" for the three-way composer only --
# GRIP_MANAGED_KEYS itself is deliberately left as it was measured.
SETTINGS_MANAGED_TOPLEVEL_KEYS = GRIP_MANAGED_KEYS | frozenset({
    "claudeMdExcludes", "showClearContextOnPlanAccept",
})

# Claude Code's own session/UI state: toggles the harness writes that GRIP's
# reference template does not declare (verified 2026-10-26 against every key
# the reference carries). Classified here, explicitly, rather than left to
# fall through as "unclassified" -- but never forced to a canonical value:
# nothing in this repo has a principled opinion on this machine's theme.
SETTINGS_LOCAL_ONLY_KEYS = frozenset({
    "_claudeMdExcludes_doc", "agentPushNotifEnabled", "autoContinueAtUsageLimit",
    "crossSessionInbound", "enabledMcpjsonServers", "footerLinksRegexes",
    "inputNeededNotifEnabled", "preferredNotifChannel", "remoteControlAtStartup",
    "skillListingBudgetFraction", "skipAutoPermissionPrompt", "skipWorkflowUsageWarning",
    "teammateMode", "theme", "todoFeatureEnabled", "worktree",
})

# Handled by their own dedicated merge in both composers, never through the
# per-key classification loop.
_SETTINGS_STRUCTURAL_KEYS = frozenset({"hooks", "permissions", "env"})


def classify_settings_key(key: str) -> str:
    """Which bucket a top-level settings.json key falls in: "dead",
    "machine-local", "managed" or "local-only" -- or "unclassified" when a
    brand-new key appears in neither list, which is the one outcome the CI
    completeness check (plan-v4 C2) exists to catch. "hooks"/"permissions"/
    "env" classify as "managed" for that same completeness check even though
    both composers handle them structurally and never consult this function
    for them."""
    if key in DEAD_KEYS:
        return "dead"
    if key in SETTINGS_MACHINE_LOCAL_KEYS:
        return "machine-local"
    if key in _SETTINGS_STRUCTURAL_KEYS:
        return "machine-local" if key == "permissions" else "managed"
    if key in SETTINGS_LOCAL_ONLY_KEYS:
        return "local-only"
    return "managed" if key in SETTINGS_MANAGED_TOPLEVEL_KEYS else "unclassified"


def classify_env_name(name: str) -> str:
    """plan-v4 C2: every env name main carries is managed, except the
    operator's own model/policy choice, which stays local like `model` itself."""
    return "machine-local" if _is_operator_env_choice(name) else "managed"


def _settings_key_order(base: dict, ours: dict, theirs: dict) -> list:
    """ours' own key order first, then any key only base or theirs carries --
    so a composed settings.json reads as a small, local-style edit rather
    than a full reshuffle, mirroring tree_sync_resolve.py's own key-ordering
    for the same reason."""
    order = list(ours)
    seen = set(ours)
    for key in (*base, *theirs):
        if key not in seen:
            order.append(key)
            seen.add(key)
    return order


def _three_way_pick(base, ours, theirs, cls: str) -> tuple[object, bool]:
    """plan-v4 C2's shortcut cascade for one key path: ours==theirs -> ours;
    theirs==base -> ours (main is silent here, local edit/addition kept);
    ours==base -> theirs (local is silent here, main's change arrives); and
    only when BOTH sides genuinely changed it, tie-break by class. Returns
    (value_or__ABSENT, conflicted). Never called for a machine-local key,
    which both composers keep local unconditionally rather than running
    through this cascade -- opus-review's change 6: "ours == base for a
    local key while main changes it" must never silently take main's value,
    which the `ours == base -> theirs` shortcut here would otherwise do."""
    if ours == theirs:
        return ours, False
    if theirs == base:
        return ours, False
    if ours == base:
        return theirs, False
    return (theirs if cls == "managed" else ours), True


def _merge_permissions_union_deny(ours_perms: dict, theirs_perms: dict) -> dict:
    """permissions.deny is the union of both sides; allow (and anything else,
    e.g. defaultMode) stays local -- plan-v4 C2. A deny rule main adds must
    always reach every machine, even one that has customised its own deny
    list; a deny rule main REMOVES still applies locally if this machine
    added it on its own (union only ever grows, never silently shrinks a
    local safety choice)."""
    merged = {**theirs_perms, **ours_perms}
    merged["deny"] = sorted({*ours_perms.get("deny", []), *theirs_perms.get("deny", [])})
    return merged


def _merge_hooks_three_way(base_hooks: dict, ours_hooks: dict, theirs_hooks: dict) -> dict:
    """Hook groups by identity, aware of base (plan-v4 C3). theirs' groups are
    canonical, in theirs' order (a reorder can never conflict). A local-only
    hook survives only when its identity is in NEITHER base NOR theirs -- an
    identity base once carried that theirs no longer does was RETIRED on
    main, and the local copy's stale spelling of it (however it names the
    GRIP root) is dropped rather than carried forward."""
    all_events = set(base_hooks) | set(ours_hooks) | set(theirs_hooks)
    return {
        event: _merge_hook_event_three_way(
            base_hooks.get(event, []), ours_hooks.get(event, []), theirs_hooks.get(event, []))
        for event in all_events
    }


def _merge_hook_event_three_way(base_groups: list, ours_groups: list, theirs_groups: list) -> list:
    exclude = _hooks_identity_set(base_groups) | _hooks_identity_set(theirs_groups)
    residues = (_residual_group(group, exclude) for group in ours_groups)
    return list(theirs_groups) + [group for group in residues if group]


def compose_settings_three_way(base: dict, ours: dict, theirs: dict) -> tuple[dict, list[dict]]:
    """The TRUE three-way settings.json composer: base, ours and theirs are
    genuinely three different revisions -- a fast-forward over local edits
    (lib/tree_sync_resolve.py) or the installer's own `git pull --rebase`
    (scripts/install.sh run_update). NOT for a repair against an unmoved
    HEAD; see reconcile_settings for that. Pure: no I/O, raises nothing."""
    merged: dict = {}
    notes: list[dict] = []
    for key in _settings_key_order(base, ours, theirs):
        if key in _SETTINGS_STRUCTURAL_KEYS:
            continue
        cls = classify_settings_key(key)
        if cls == "dead":
            continue
        if cls == "machine-local":
            value = ours[key] if key in ours else theirs.get(key, _ABSENT)
        else:
            value, conflicted = _three_way_pick(
                base.get(key, _ABSENT), ours.get(key, _ABSENT), theirs.get(key, _ABSENT), cls)
            if conflicted and cls != "managed":
                notes.append({"path": f"$.{key}", "class": cls, "action": "local",
                              "why": "both sides changed it; kept local"})
        if value is not _ABSENT:
            merged[key] = value
    merged["hooks"] = _merge_hooks_three_way(
        base.get("hooks", {}), ours.get("hooks", {}), theirs.get("hooks", {}))
    merged["permissions"] = _merge_permissions_union_deny(
        ours.get("permissions", {}), theirs.get("permissions", {}))
    merged["env"] = _compose_env_three_way(
        base.get("env", {}), ours.get("env", {}), theirs.get("env", {}))
    return merged, notes


def _compose_env_three_way(base_env: dict, ours_env: dict, theirs_env: dict) -> dict:
    merged: dict = {}
    for name in _settings_key_order(base_env, ours_env, theirs_env):
        if classify_env_name(name) == "machine-local":
            value = ours_env[name] if name in ours_env else theirs_env.get(name, _ABSENT)
        else:
            value, _conflicted = _three_way_pick(
                base_env.get(name, _ABSENT), ours_env.get(name, _ABSENT),
                theirs_env.get(name, _ABSENT), "managed")
        if value is not _ABSENT:
            merged[name] = value
    return merged


def reconcile_settings(theirs: dict, ours: dict) -> tuple[dict, list[dict]]:
    """Repair-only composition against an UNMOVED HEAD (plan-v4 C5): there is
    no "base" distinct from `theirs` here -- `theirs` IS the deployed,
    already-canonical revision (HEAD's tracked settings.json, confirmed level
    with origin/main by the caller), and `ours` is the live file as some
    runtime writer may have left it. The three-way shortcut cascade cannot be
    reused with base==theirs: its `theirs == base` shortcut would then ALWAYS
    fire first and keep `ours` verbatim, so a drifted managed key would never
    be repaired. A managed key therefore always takes theirs here -- the
    pre-existing two-way merge_settings()'s own rule for the same class,
    applied with a richer, base-aware classification; machine-local and
    local-only keys always keep ours, never forced."""
    merged: dict = {}
    notes: list[dict] = []
    for key in _settings_key_order({}, ours, theirs):
        if key in _SETTINGS_STRUCTURAL_KEYS:
            continue
        cls = classify_settings_key(key)
        if cls == "dead":
            continue
        if cls == "managed":
            value = theirs.get(key, _ABSENT)
        else:
            value = ours[key] if key in ours else theirs.get(key, _ABSENT)
            if cls == "unclassified" and key in ours and key not in theirs:
                notes.append({"path": f"$.{key}", "class": "unclassified",
                              "action": "local", "why": "not in any registry; kept local"})
        if value is not _ABSENT:
            merged[key] = value
    merged["hooks"] = smart_merge_hooks(theirs.get("hooks", {}), ours.get("hooks", {}))
    merged["permissions"] = _merge_permissions_union_deny(
        ours.get("permissions", {}), theirs.get("permissions", {}))
    merged["env"] = _reconcile_env(theirs.get("env", {}), ours.get("env", {}))
    return merged, notes


def _reconcile_env(theirs_env: dict, ours_env: dict) -> dict:
    merged: dict = {}
    for name in _settings_key_order({}, ours_env, theirs_env):
        if classify_env_name(name) == "machine-local":
            value = ours_env[name] if name in ours_env else theirs_env.get(name, _ABSENT)
        else:
            value = theirs_env.get(name, _ABSENT)
        if value is not _ABSENT:
            merged[name] = value
    return merged


def unarrived_settings_changes(theirs: dict, ours: dict, overrides: dict) -> list[str]:
    """plan-v4 C7's falsifier: every managed settings.json change `theirs`
    carries that `ours` does not yet reflect, and that no override explains.
    `bin/grip-tree-sync --check` reports this for the shared tree so "the
    sync looks clean" can never mean "a change never arrived and nothing
    noticed". Dollar-prefixed JSON paths, consistent with
    lib/tree_sync_resolve.py's own MergeConflict messages."""
    merged, _notes = reconcile_settings(theirs, ours)
    merged = apply_settings_overrides(merged, overrides)
    unarrived = [
        f"$.{key}" for key in merged
        if key not in _SETTINGS_STRUCTURAL_KEYS
        and classify_settings_key(key) == "managed"
        and merged.get(key) != ours.get(key)
    ]
    ours_env, merged_env = ours.get("env", {}), merged.get("env", {})
    unarrived += [
        f"$.env.{name}" for name in {*merged_env, *ours_env}
        if classify_env_name(name) == "managed" and merged_env.get(name) != ours_env.get(name)
    ]
    for event, groups in theirs.get("hooks", {}).items():
        if ours.get("hooks", {}).get(event, [])[: len(groups)] != groups:
            unarrived.append(f"$.hooks.{event}")
    return unarrived


def settings_overrides_path(claude_dir) -> Path:
    return Path(claude_dir) / "config" / "settings-overrides.local.json"


def load_settings_overrides(claude_dir) -> dict:
    """This machine's deliberate settings.json choices (plan-v4 C2), applied
    LAST by both composers. Gitignored; fails open like config/model-pin.local.json
    -- a missing or unreadable file means no overrides, never an error."""
    try:
        data = json.loads(settings_overrides_path(claude_dir).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def apply_settings_overrides(merged: dict, overrides: dict) -> dict:
    """Applied LAST, after either composer: a "keys" entry forces that
    top-level value outright; an "env" entry forces that name's value, or
    (JSON `null`) forces it ABSENT even though the managed default would
    otherwise restore it -- the GRIP_STE_TRIAL case plan-v4 C2 names by name."""
    out = dict(merged)
    keys = overrides.get("keys")
    if isinstance(keys, dict):
        out.update(keys)
    env_overrides = overrides.get("env")
    if isinstance(env_overrides, dict):
        env = dict(out.get("env", {}))
        for name, value in env_overrides.items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        out["env"] = env
    return out


def _iter_hook_groups(hooks: dict):
    """Every hook group in a hooks dict, flattened across event types."""
    for groups in hooks.values():
        yield from groups


def _iter_hook_entries(hooks: dict):
    """Every individual hook entry in a hooks dict, flattened across event
    types and groups -- two flat generators rather than one triple-nested
    loop: the hook tree is a few dozen entries, never the shape that
    warrants a set/bisect rewrite."""
    for group in _iter_hook_groups(hooks):
        yield from group.get("hooks", [])


def missing_hook_scripts(hooks: dict, claude_dir, arriving=None) -> list[str]:
    """Hook commands whose resolved GRIP-rooted script does not exist under
    `claude_dir` (plan-v4 C4): "a binding to a missing script can block every
    matching tool call". An external/app command, which resolves no GRIP
    path, cannot be checked this way and is skipped, not flagged.

    `arriving(script)` lets a caller vouch for a script that is not on disk
    yet because the very merge it is checking writes it: GRIP#8454 bound
    hooks/red-check-merge-gate.py and added the file in one commit, the check
    ran on the shared tree's disk before the fast-forward, and the tree stuck
    three commits behind main (2026-10-27). Only a script absent on disk is
    put to the vouch; with no vouch the check is the disk alone, as before."""
    claude_dir = Path(claude_dir)
    missing = []
    for hook in _iter_hook_entries(hooks):
        script = _grip_script_path(hook.get("command", ""), claude_dir)
        if script is None or script.exists():
            continue
        if arriving is not None and arriving(script):
            continue
        missing.append(str(script))
    return missing


def _grip_script_path(command: str, claude_dir: Path):
    """The hooks/ or lib/ file a GRIP hook command resolves to, or None for a
    command with no GRIP-rooted path (an external app, or unparseable)."""
    words = _command_words(command)
    if words:
        command = " ".join(words)
    matches = re.findall(_GRIP_ROOT + r"/(hooks|lib)/(\S+)", command)
    if not matches:
        return None
    kind, tail = matches[-1]
    return claude_dir / kind / tail.rstrip(_TRAILING_CHARS)


def verify_composed_settings(merged: dict, theirs: dict, claude_dir, arriving=None) -> list[str]:
    """plan-v4 C4: before anything is written, every managed hook group of
    the deployed revision (`theirs`) must be present verbatim as a PREFIX of
    the composed result (a local addition may still follow it), and every
    hook command's script must exist -- on disk, or vouched for by
    `arriving` as written by the same merge (see missing_hook_scripts).
    Returns the problems found; empty means safe to write."""
    problems = []
    for event, groups in theirs.get("hooks", {}).items():
        if merged.get("hooks", {}).get(event, [])[: len(groups)] != groups:
            problems.append(f"the deployed {event!r} hook group(s) are not intact in the result")
    problems.extend(
        f"hook script missing: {p}"
        for p in missing_hook_scripts(merged.get("hooks", {}), claude_dir, arriving=arriving))
    return problems


class ReconcileRefused(Exception):
    """verify_composed_settings found a problem; args[0] is the list of them."""


_INDENT_RE = re.compile(r"\n([ \t]+)\S")


def _dump_like_settings(value: dict, sample: str) -> bytes:
    """Serialise like the live file was written: same indent, same final
    newline. Deliberately duplicates lib/tree_sync_resolve.py's own
    `_dump_like` rather than importing it: that module imports THIS one by
    path (plan-v4 D1), and a reverse import would make this module unusable
    on its own by scripts/install.sh and bin/grip-settings-reconcile."""
    found = _INDENT_RE.search(sample)
    indent = (found.group(1) if "\t" in found.group(1) else len(found.group(1))) if found else 2
    text = json.dumps(value, indent=indent, ensure_ascii=False)
    return (text + "\n" if sample.endswith("\n") else text).encode("utf-8")


def _replace_if_unchanged(path: Path, expected: bytes, data: bytes) -> bool:
    """Atomic write (temp file + rename, same directory) only if `path` still
    holds exactly `expected` -- the optimistic-concurrency check that makes
    write_settings_reconciled's retry loop race-safe (plan-v4 C4/T-race),
    mirroring lib/tree_sync_resolve.py's own `_replace`.

    The check and the write are one critical section under lib/file_lock.py's
    sidecar lock: a bare "read, compare, maybe write" is a textbook TOCTOU --
    two concurrent callers can both read the SAME `expected` bytes, both pass
    the comparison, and both proceed to replace, with whichever lands second
    silently discarding the first's write. Measured directly: T-race's three-
    writer fixture lost an entry on roughly half of runs without this lock,
    with each writer individually race-safe by exactly this un-locked
    pattern. The lock is scoped to THIS function's own callers (both
    composers), not a repo-wide settings.json lock -- it coordinates the two
    composers with EACH OTHER, not with every writer of settings.json.
    Reviewed on PR #8719 (finding F9): lib/model_pin.py and
    lib/pro_launch_defaults.py each take their OWN separate lock
    (state/model-pin.lock), not this one, so a blind read-modify-write from
    either can still race a composer write undetected -- no torn read (each
    write is still atomic on its own), but the composer's result can be
    silently overwritten and only self-heals on the next repair run."""
    with FileLock(path, sidecar=True, timeout=10):
        if path.read_bytes() != expected:
            return False
        mode = path.stat().st_mode & 0o7777
        handle, scratch = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".reconcile")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            os.chmod(scratch, mode)
            os.replace(scratch, path)
        except BaseException:
            Path(scratch).unlink(missing_ok=True)
            raise
        return True


def _write_settings(path: Path, theirs: dict, overrides: dict, compose, *,
                     claude_dir, max_attempts: int) -> tuple[dict, list]:
    """Shared retry/verify/atomic-write core for both composers (plan-v4 C4):
    re-reads and re-composes if another writer touched `path` in between
    (T-race), exactly as lib/tree_sync_resolve.py's own `_write_merged` does
    for every other file. `compose(ours) -> (merged, notes)` is the one
    difference between write_settings_reconciled and write_settings_three_way
    below. Raises ReconcileRefused on a failed verify, or RuntimeError if the
    file will not settle within max_attempts."""
    for _ in range(max_attempts):
        raw = path.read_bytes()
        merged, notes = compose(json.loads(raw))
        merged = apply_settings_overrides(merged, overrides)
        problems = verify_composed_settings(merged, theirs, claude_dir)
        if problems:
            raise ReconcileRefused(problems)
        serialized = _dump_like_settings(merged, raw.decode("utf-8"))
        if serialized == raw or _replace_if_unchanged(path, raw, serialized):
            return merged, notes
    raise RuntimeError(f"{path}: kept changing under us; gave up after {max_attempts} attempts")


def write_settings_reconciled(path: Path, theirs: dict, overrides: dict, *,
                               claude_dir, max_attempts: int = 5) -> tuple[dict, list]:
    """Reconcile `path` (the live settings.json) against `theirs` (the
    deployed revision, an unmoved HEAD) and write the result."""
    return _write_settings(path, theirs, overrides, lambda ours: reconcile_settings(theirs, ours),
                            claude_dir=claude_dir, max_attempts=max_attempts)


def write_settings_three_way(path: Path, base: dict, theirs: dict, overrides: dict, *,
                              claude_dir, max_attempts: int = 5) -> tuple[dict, list]:
    """TRUE three-way write (plan-v4 C6): `base` (HEAD's tracked settings.json
    BEFORE `git pull --rebase`) and `theirs` (HEAD's tracked copy AFTER it)
    are genuinely different revisions, unlike write_settings_reconciled's
    repair-only case. Used by scripts/install.sh's run_update, with `path`
    holding the pre-pull live content as its starting "ours" (the pull's own
    checkout already overwrote it with `theirs`, so the caller restores the
    snapshot into `path` before calling this)."""
    return _write_settings(
        path, theirs, overrides, lambda ours: compose_settings_three_way(base, ours, theirs),
        claude_dir=claude_dir, max_attempts=max_attempts)


def _load_json(path: Path) -> dict:
    """Load and parse a JSON file."""
    with open(path) as f:
        return json.load(f)


def _build_merged(settings_path: Path, reference: dict) -> dict:
    """Compute merged settings: fresh-install or upgrade."""
    if not settings_path.is_file():
        merged = dict(reference)
        merged["permissions"] = {"allow": [], "defaultMode": "default"}
        return merged
    return merge_settings(reference, _load_json(settings_path))


def _write_atomic(path: Path, data: dict, backup: bool) -> None:
    """Write data to path atomically via a temp file; optionally back up first."""
    if backup and path.is_file():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        os.unlink(tmp_path)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smart merge settings.json with reference template"
    )
    parser.add_argument("--settings", required=True, help="Path to existing settings.json")
    parser.add_argument("--reference", required=True, help="Path to settings.reference.json")
    parser.add_argument("--backup", action="store_true", help="Create .bak backup before overwriting")
    args = parser.parse_args()

    settings_path = Path(args.settings)
    reference_path = Path(args.reference)

    if not reference_path.is_file():
        print(f"Reference file not found: {reference_path}", file=sys.stderr)
        sys.exit(1)

    _write_atomic(settings_path, _build_merged(settings_path, _load_json(reference_path)), args.backup)
    return 0


if __name__ == "__main__":
    sys.exit(main())
