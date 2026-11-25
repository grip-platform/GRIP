#!/usr/bin/env python3
"""grip_launch_args — classify `gg++` arguments into a launch plan.

The old `gogrip` took ONE positional and treated it as the context-token
budget, so `gg++ opencode` became `grip fresh latest opencode` and the word was
swallowed as a number. This classifies every argument on its own instead:

    all digits            -> context-token budget
    --<family>            -> model family (--kimi, --qwen, --claude ...)
    a word after a family -> that family's tier or reasoning effort
    --direct              -> vendor endpoint instead of HAL (claude/codex only)
    --list / --help       -> informational, launches nothing
    any other -flag       -> loud error
    anything else         -> harness name, validated against the picker

A LOOP, not a shift. A shift-based reading passes the second positional
through unchecked and recreates the exact silent-swallow bug this replaces:
`gg++ codex opencode` would emit `latest opencode`. Here a second harness name
or a second budget is a loud rc=2, and nothing launches.

Validation uses the picker's `detect_installed` (registry AND on PATH), so an
unknown name and a known-but-not-installed one both fail here, before any
worktree is created or any config is written. That matters: bin/grip's own
missing-binary path returns 0 and falls through to Claude Code, so validating
downstream would mean asking for codex and silently getting Claude.

A family is refused up front when the named harness cannot carry it: a harness
outside the picker's wireable set (dsh, gemini) takes no model from GRIP at
launch, and Claude Code takes one only for a family HAL serves on /v1/messages
(kimi today). Without that refusal GRIP_HARNESS_MODEL is exported and read by
nothing — the operator asks for one model and silently gets another.

stdout protocol (read by the gogrip shell function and bin/gg++, never eval'd):
    LAUNCH <harness|-> <tokens> <model|-> <effort|-> <direct|->
Informational modes print human text and emit no LAUNCH line.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

DEFAULT_TOKENS = "12000"

USAGE = """gg++ — start a GRIP session, in any installed harness.

  gg++                      Claude Code, 12000 tokens of context
  gg++ 8000                 Claude Code, 8000 tokens
  gg++ codex                Codex, wired to HAL, in an isolated worktree
  gg++ opencode 8000        harness and budget, in either order
  gg++ codex --kimi         Codex on the best Kimi model
  gg++ opencode --qwen max  ... on the qwen 'max' tier
  gg++ claude --kimi        Claude Code on Kimi K3, through the HAL gateway
                            (needs GRIP_INFINITE=1; witnessed and cost-tracked)
  gg++ claude --kimi --direct
                            ... straight to the vendor endpoint instead: the
                            key comes from the keychain for that one launch,
                            and the HAL witness and cost rows are NOT recorded
  gg++ codex --kimi --direct
                            Codex via a ~/.codex/kimi.config.toml profile that
                            names the vendor endpoint (same trade-offs)
  gg++ --list               what can be launched on this machine
  gg++ --card               the full launch card (the receipt's expansion)
  gg++ --help               this text

  Only installed harnesses are launchable. To see every harness GRIP knows and
  install the missing ones:  grip-harness-install --list
"""


class ArgError(ValueError):
    """A classification failure. The message is shown to the operator."""


def _installed() -> tuple[str, ...]:
    """Harnesses that are both registered and present on PATH."""
    try:
        import grip_harness_picker as picker
    except ImportError as exc:  # pragma: no cover - import guard
        raise ArgError(f"harness picker unavailable ({exc}) — cannot validate") from exc
    try:
        return tuple(picker.detect_installed())
    except (OSError, AttributeError) as exc:
        raise ArgError(f"harness detection failed ({exc}) — cannot validate") from exc


def _families() -> tuple[str, ...]:
    try:
        import grip_harness_family as fam
    except ImportError:
        return ()
    return fam.families()


def _is_digits(token: str) -> bool:
    return token.isdigit() and token != ""


def _classify_flag(token: str, state: dict) -> None:
    name = token.lstrip("-").lower()
    if name in ("list", "l"):
        state["action"] = "list"
        return
    if name in ("help", "h"):
        state["action"] = "help"
        return
    if name == "card":
        state["action"] = "card"
        return
    if name == "direct":
        state["direct"] = True
        return
    if name in _families():
        if state["family"]:
            raise ArgError(
                f"two model families given ({state['family']}, {name}) — pick one")
        state["family"] = name
        return
    raise ArgError(f"unknown flag '{token}' (see gg++ --help)")


def _classify_word(token: str, state: dict, installed: tuple[str, ...]) -> None:
    """A bare word: a family qualifier if one is pending, else a harness."""
    if state["family"] and not state["qualifier"] and token not in installed:
        state["qualifier"] = token
        return
    if state["harness"]:
        raise ArgError(
            f"two harnesses given ({state['harness']}, {token}) — pick one")
    if token not in installed:
        raise ArgError(_not_installed_message(token, installed))
    state["harness"] = token


def _attached_tool_server_message(token: str) -> str:
    """A tool server (REA first) is not launched, it is ATTACHED: an
    --mcp-config file spawns it inside the session that asks for it. Saying
    "lib/harness/ dispatches it" would name a path that does not exist for it."""
    return (f"'{token}' is an MCP tool server, not a session harness — "
            f"attach it per launch with: claude --mcp-config "
            f"mcp-servers/{token}/mcp.json; install the pinned version "
            f"with: grip-harness-install {token}")


def _not_installed_message(token: str, installed: tuple[str, ...]) -> str:
    """Explain a rejected harness name, distinguishing the two reasons it fails.

    'Not launchable here' collapses two very different situations: a name GRIP
    has never heard of, and a name GRIP fully supports that simply is not on this
    machine. The second one has a one-line fix, and printing the generic message
    for it hides that fix behind a wrong impression — that GRIP does not support
    the harness at all. It is also the common case now that the registry knows
    considerably more harnesses than any one machine has installed.
    """
    have = ", ".join(installed) or "none detected"
    try:
        import grip_harness_picker as picker
        known = picker.HARNESS_REGISTRY
    except ImportError as exc:
        # Unreachable on the normal path — _installed() raises ArgError when the
        # picker will not import, so classification aborts before any name is
        # rejected. It IS reachable when a caller injects `installed` directly
        # (tests, an embedding harness), and there the old silent `known = {}`
        # degraded the specific "known but not installed, run this to fix it"
        # message into the generic one with no indication that anything had gone
        # wrong. Say so on stderr: a fallback nobody can see is the shape GRIP
        # treats as a defect elsewhere. (AGORA #4936 round 2, concern 3.)
        sys.stderr.write(
            f"gg++: harness registry unavailable ({exc}) — falling back to a "
            f"generic message; install hints are unavailable\n")
        known = {}
    if token in known:
        spec = picker.install_spec(token)
        if known[token].get("attach") == "mcp":
            return _attached_tool_server_message(token)
        if not picker.is_interactive(token):
            return (f"'{token}' is a one-shot tool, not a session harness — "
                    f"lib/harness/ dispatches it; gg++ cannot open it")
        if (spec.get("manager") or "manual") == "manual":
            return (f"'{token}' is a known harness with no automatic install "
                    f"({spec.get('reason', 'no verified package')}) — "
                    f"install it yourself, then gg++ will find it")
        return (f"'{token}' is a known harness but is not installed here — "
                f"install it with: grip-harness-install {token}")
    return (f"'{token}' is not a launchable harness here (installed: {have}) — "
            f"see `grip-harness-install --list` for every harness GRIP knows")


def _classify_number(token: str, state: dict) -> None:
    if state["tokens"]:
        raise ArgError(
            f"two context budgets given ({state['tokens']}, {token}) — pick one")
    state["tokens"] = token


def classify(argv: list[str], installed: tuple[str, ...] | None = None) -> dict:
    """Classify argv into a launch plan. Raises ArgError on anything ambiguous."""
    state = {"action": "launch", "harness": "", "tokens": "",
             "family": "", "qualifier": "", "direct": False}
    known = installed if installed is not None else _installed()
    for token in argv:
        if state["action"] != "launch":
            break
        if token.startswith("-"):
            _classify_flag(token, state)
        elif _is_digits(token):
            _classify_number(token, state)
        else:
            _classify_word(token, state, known)
    state["tokens"] = state["tokens"] or DEFAULT_TOKENS
    return state


def resolve_model(state: dict) -> tuple[str, str]:
    """(model, effort) for the chosen family, or ('','') when none was given.

    Every refusal below fires here, before the shell has done anything: no cd,
    no worktree, no config write, no grip invocation.
    """
    _checked_direct(state)
    if not state["family"]:
        return "", ""
    _checked_family(state)
    import grip_harness_family as fam
    try:
        got = fam.resolve(state["family"], state["qualifier"])
    except fam.FamilyError as exc:
        raise ArgError(str(exc)) from exc
    if got["effort"]:
        return got["model"], _checked_effort(state, got["effort"])
    return got["model"], _default_effort(state)


def _claude_carries(family: str) -> bool:
    """Does Claude Code take this family at launch (the bin/grip tier map)?"""
    import grip_harness_family as fam
    return family in fam.CLAUDE_FAMILY_RECIPE


def _checked_family(state: dict) -> None:
    """Refuse a family the named harness cannot carry to the model.

    A bare launch is Claude Code. Claude carries a family only when HAL serves
    it on /v1/messages (lib/grip_harness_family.CLAUDE_FAMILY_RECIPE); any other
    harness carries one only when the picker can wire it (launch_plan "wire").
    Everything else would export GRIP_HARNESS_MODEL for nothing to read.
    """
    import grip_harness_picker as picker

    family = state["family"]
    harness = state["harness"] or picker._CLAUDE
    plan = picker.launch_plan(harness)
    if plan["action"] == "claude":
        if _claude_carries(family):
            return
        raise ArgError(
            f"Claude Code cannot run on a {family} model — HAL serves none of its "
            f"ids on /v1/messages. Use a harness that takes the model directly, "
            f"e.g. `gg++ codex --{family}`")
    if plan.get("wire"):
        return
    wireable = [h for h in picker.HARNESS_REGISTRY if picker.launch_plan(h).get("wire")]
    raise ArgError(
        f"--{family} cannot be honoured by {harness}: it takes no model from GRIP "
        f"at launch, so the choice would be silently dropped. Harnesses that do: "
        f"{', '.join(wireable)}; and Claude Code (through the gateway)")


def _checked_direct(state: dict) -> None:
    """--direct: a family to go direct to, on a harness that has a direct wire."""
    if not state["direct"]:
        return
    if not state["family"]:
        raise ArgError("--direct needs a model family to go direct to, "
                       "e.g. `gg++ claude --kimi --direct`")
    harness = state["harness"] or "claude"
    if harness not in ("claude", "codex"):
        raise ArgError(
            f"--direct is wired for claude and codex only, not {harness} — drop "
            f"--direct to route through HAL :4010")
    import grip_harness_family as fam
    try:
        fam.direct_endpoint(state["family"])
    except fam.FamilyError as exc:
        # Name the harness too (GRIP#5345): the family message alone reads as
        # if the family were the whole story, when the operator typed both.
        raise ArgError(f"{exc} — requested for {harness}") from exc


def _effort_applies(state: dict, effort: str) -> bool:
    """Can the chosen harness actually receive this effort?

    Codex takes it as argv (picker.effort_argv); Claude Code takes it as
    CLAUDE_CODE_EFFORT_LEVEL through the family tier map, so effort_argv stays
    empty for claude by design. A bare launch is Claude Code.
    """
    import grip_harness_picker as picker

    harness = state.get("harness") or ""
    if harness and picker.effort_argv(harness, effort):
        return True
    return harness in ("", picker._CLAUDE) and _claude_carries(state["family"])


def _default_effort(state: dict) -> str:
    """The family default, only where the harness can apply it.

    A default is not a request: refusing `gg++ aider --kimi` over a setting
    nobody asked for would be a regression, so an inapplicable default is
    simply absent rather than an error.
    """
    import grip_harness_family as fam
    level = fam.default_effort(state["family"])
    return level if level and _effort_applies(state, level) else ""


def _checked_effort(state: dict, effort: str) -> str:
    """Return the effort only if the chosen harness can actually apply it.

    The refusal here is narrower than it was, not gone. It used to fire for
    every harness because nothing consumed effort at all; now it fires only for
    a harness whose reasoning-effort surface has not been verified. The reason
    is unchanged and still the important part: accepting a setting that nothing
    applies would launch at the harness default while the operator believed
    they had asked for something else, and that failure is silent.
    """
    import grip_harness_picker as picker

    if _effort_applies(state, effort):
        return effort
    harness = state.get("harness") or picker._CLAUDE
    supported = list(picker.harnesses_supporting_effort())
    if _claude_carries(state["family"]):
        supported.append("claude (as env)")
    named = ", ".join(supported) or "no harness yet"
    raise ArgError(
        f"reasoning effort ({effort}) is not wired through to {harness} — "
        f"supported: {named}. Use a model tier instead, e.g. "
        f"--{state['family']} {_a_tier_for(state['family'])}")


def _a_tier_for(family: str) -> str:
    """A real tier of this family, so the error message is actionable.

    Excludes the family's own name, its provider name, and version-shaped
    tokens, so `--claude` suggests `opus` rather than the useless `claude`.
    """
    import grip_harness_family as fam
    models = fam.provider_models(family)
    if not models:
        return "<tier>"
    skip = {family, fam.FAMILY_PROVIDER.get(family, "")}
    parts = [p for p in fam._tokens(models[0])
             if p not in skip and not p[:1].isdigit()]
    return sorted(parts)[0] if parts else "<tier>"


def main(argv: list[str]) -> int:
    try:
        state = classify(argv)
        if state["action"] == "help":
            sys.stdout.write(USAGE)
            return 0
        if state["action"] == "list":
            sys.stdout.write("\n".join(_installed()) + "\n")
            return 0
        if state["action"] == "card":
            import grip_launch_card
            sys.stdout.write(grip_launch_card.render() + "\n")
            return 0
        model, effort = resolve_model(state)
    except ArgError as exc:
        sys.stderr.write(f"gg++: {exc}\n")
        return 2
    print(f"LAUNCH {state['harness'] or '-'} {state['tokens']} "
          f"{model or '-'} {effort or '-'} {'direct' if state['direct'] else '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
