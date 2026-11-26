#!/usr/bin/env python3
"""grip_harness_family — resolve `--kimi`, `--qwen max`, `--claude xhigh`.

`gg++ codex --kimi` should launch Codex on the best Kimi model without the
operator memorising `kimi-k3`. Two rules, both operator-chosen (V>> 2026-07-31):

FLAGSHIP, not newest. config/providers.yaml lists models newest-first, but
newest is not best: taking the first entry gives `claude-sonnet-5` over
`claude-opus-5`, and `gemini-3.5-flash` over `gemini-4-carbon`. So each
family carries an explicit flagship pin. The pin is DATA, and
tests/test_grip_harness_family.py asserts every pin still exists in
providers.yaml, so a pin cannot rot into a model the gateway no longer routes.

TIER FIRST, THEN EFFORT. The second word is ambiguous by nature: `qwen3.7-max`
is a real model tier and `max` is also a reasoning-effort level. It is read as
a tier when it matches one inside that family, and as effort otherwise. So
`--qwen max` selects qwen3.7-max, `--kimi code` selects kimi-k2.7-code, and
`--claude xhigh` sets effort because no Claude model carries an `xhigh` token.
The resolution is always reported back so it is never silent.

Resolution is data-driven off providers.yaml — the same file
grip_harness_model.py already parses — so families and tiers cannot drift from
what HAL can actually route.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import grip_harness_model as ghm  # noqa: E402

# family flag -> provider name in config/providers.yaml
FAMILY_PROVIDER: "dict[str, str]" = {
    "kimi": "moonshot",
    "qwen": "qwen",
    "claude": "anthropic",
    "gemini": "gemini",
    "deepseek": "deepseek",
    "glm": "zhipu",
    "grok": "xai",
    "gpt": "openai",
    "mistral": "mistral",
    "groq": "groq",
}

# family flag -> the model a bare `--<family>` means. Deliberately curated:
# "newest" and "best" disagree, and the operator wants best.
FAMILY_FLAGSHIP: "dict[str, str]" = {
    "kimi": "kimi-k3",
    "qwen": "qwen3.7-max",
    # Opus 5.5 from 2026-09-24: V>> made it the launch default (cheaper than
    # Opus 5, and the vendor's recommended starting model).
    "claude": "claude-opus-5-5",
    # Gemini 4 Carbon from 2026-11-26: V>> replaced 3.1 Pro with Google's
    # strongest model wherever 3.1 Pro was a seat or a default.
    "gemini": "gemini-4-carbon",
    "deepseek": "deepseek-v4-pro",
    # glm-5.3, not glm-5.1: Zhipu's GLM Coding Plan has answered every `glm-5.1`
    # request with GLM-5.3 since at least 2026-11-24 (GRIP#9431 -- two probes,
    # each beside a glm-5.3 control, both HTTP 200 with the reply's `model`
    # field reading glm-5.3). V>>'s decision names the model that really
    # answers; glm-5.1 stays listed and reachable by name (`--glm 5` and the
    # full id), since Zhipu still serves it, just under this one.
    "glm": "glm-5.3",
    "grok": "grok-4.3",
    "gpt": "gpt-5.5",
    # mistral-large-3 retired 2026-08-30; providers.yaml names -latest as the
    # current large. A moving alias: repin when a dated large id is listed.
    "mistral": "mistral-large-latest",
    "groq": "openai/gpt-oss-120b",
}

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# Per-family reasoning effort applied when the operator names none. Kimi's
# vendor default is `max`, but docs/measurements/2026-08-13-watermarks-vs-
# witnesses.md:284 measured kimi-k3 at xhigh timing out at 120 s on Codex with
# zero output tokens, so `high` is the bounded choice. Codex receives it as
# `-c model_reasoning_effort=<level>`, Claude Code as CLAUDE_CODE_EFFORT_LEVEL.
FAMILY_EFFORT_DEFAULT: "dict[str, str]" = {"kimi": "high"}

# Claude Code needs a whole TIER MAP for a non-Anthropic family, not one model:
# the vendor recipe (platform.kimi.ai/docs/guide/claude-code-kimi, 2026-09-07)
# sets ANTHROPIC_DEFAULT_{OPUS,SONNET,FABLE}_MODEL and CLAUDE_CODE_SUBAGENT_MODEL
# to the main model, ANTHROPIC_DEFAULT_HAIKU_MODEL to the coding model, and
# warns that "configuring only some of the variables makes the corresponding
# scenarios fail silently". Only a family whose provider HAL routes behind
# :4010 may carry a recipe — today that is kimi (moonshot); a gemini/gpt row
# here would launch Claude Code on ids the gateway cannot serve on /v1/messages.
CLAUDE_FAMILY_RECIPE: "dict[str, dict]" = {
    "kimi": {"haiku": "kimi-k2.7-code", "compact_window_1m": 1_000_000},
}

# Vendor-direct endpoints, used ONLY by an explicit `--direct` launch. The
# credential is read from the named keychain entry at launch, in-process, and
# never written to any file; the HAL witness and cost rows are not recorded
# on this path, which is why it is opt-in rather than a fallback.
FAMILY_DIRECT: "dict[str, dict]" = {
    "kimi": {
        "anthropic_base_url": "https://api.moonshot.ai/anthropic",
        "openai_base_url": "https://api.moonshot.ai/v1",
        "keychain": "grip-moonshot",
        "codex_provider": "kimi",
        "codex_provider_name": "Kimi",
        "codex_env_key": "KIMI_API_KEY",
    },
}

#: A context window at or above this is the Claude Code `[1m]` variant.
ONE_MILLION = 1_048_576

# The launch-line protocol bin/grip validates before exporting anything. The
# bash twin lives in grip_handoff as `_fam_re`; tests/test_grip_harness_family.py
# runs both over one corpus and demands identical verdicts. GRIP_1M_MODEL is
# GRIP's own main-model seam (bin/grip execs `--model "$GRIP_1M_MODEL"`), so it
# rides in the same validated stream rather than a second channel.
LAUNCH_LINE_RE = re.compile(
    r"^(GRIP_1M_MODEL|ANTHROPIC_DEFAULT_(OPUS|SONNET|HAIKU|FABLE)_MODEL"
    r"|CLAUDE_CODE_SUBAGENT_MODEL|CLAUDE_CODE_AUTO_COMPACT_WINDOW"
    r"|CLAUDE_CODE_EFFORT_LEVEL)=[A-Za-z0-9._\[\]-]+$")
_BASE_URL_LINE_RE = re.compile(r"^ANTHROPIC_BASE_URL=https?://\S+$")


class FamilyError(ValueError):
    """Raised on an unknown family or an unusable qualifier."""


def families() -> tuple[str, ...]:
    return tuple(sorted(FAMILY_FLAGSHIP))


def _providers_path() -> Path:
    return ghm._default_providers_path()


def provider_models(family: str) -> tuple[str, ...]:
    """Model ids for a family, newest-first, straight from providers.yaml."""
    provider = FAMILY_PROVIDER.get(family)
    if provider is None:
        return ()
    try:
        rows = ghm._parse_providers_yaml(_providers_path())
    except (OSError, ValueError):
        return ()
    matches = [row for row in rows if str(row.get("name") or "") == provider]
    if not matches:
        return ()
    return tuple(str(m) for m in (matches[0].get("models") or []))


def _token_seq(model_id: str) -> "list[str]":
    """Hyphen/slash/dot-delimited tokens of a model id, lowercased, in order."""
    flat = model_id.lower().replace("/", "-").replace(".", "-")
    return [tok for tok in flat.split("-") if tok]


def _tokens(model_id: str) -> set[str]:
    """The tokens of :func:`_token_seq` as a set: order and repeats dropped."""
    return set(_token_seq(model_id))


def _ends_with(seq: "list[str]", want: "list[str]") -> bool:
    return seq[-len(want):] == want


def _contains_run(seq: "list[str]", want: "list[str]") -> bool:
    return any(seq[i:i + len(want)] == want for i in range(len(seq) - len(want) + 1))


def _sequence_match(family: str, qualifier: str) -> "str | None":
    """The ONE model a multi-token qualifier names; a tie is refused, not guessed.

    A token SET cannot see order or repetition: claude-opus-5-5 and claude-opus-5
    both reduce to {claude, opus, 5}, so before this `--claude 5.5`, `--claude
    opus-5-5` and even the full id were rejected (measured 2026-09-24, the Opus
    5.5 onboarding). A multi-token qualifier therefore matches the END of a
    model's token sequence first - `opus-5` names claude-opus-5, not
    claude-opus-5-5 - then a run anywhere (`haiku-4-5` inside
    claude-haiku-4-5-20251001). Unlike a one-token qualifier this never depends
    on list position: two candidates is an error naming both.
    """
    want = _token_seq(qualifier)
    models = provider_models(family)
    for rule in (_ends_with, _contains_run):
        hits = [m for m in models if rule(_token_seq(m), want)]
        if len(hits) > 1:
            raise FamilyError(
                f"{qualifier!r} matches {len(hits)} {family} models "
                f"({', '.join(hits)}); name one of them in full")
        if hits:
            return hits[0]
    return None


def _names_whole_family(family: str, qualifier: str) -> bool:
    """True when every model in a family of two or more carries the qualifier."""
    want = qualifier.strip().lower()
    models = provider_models(family)
    if not want or len(models) < 2:
        return False
    return all(want in _tokens(m) for m in models)


def tier_match(family: str, qualifier: str) -> "str | None":
    """The model in `family` that `qualifier` names.

    A full model id names itself. A multi-token qualifier (`5.5`, `opus-5`) must
    name exactly one model, see :func:`_sequence_match`. A one-token qualifier
    answers with the newest model carrying it as a whole token, so for those
    providers.yaml's order is the decision.

    None when the qualifier names the whole family. A token every model carries
    - `gpt`, `claude`, `kimi` - singles out nothing, so `hits[0]` would be a
    choice made by list position rather than by merit, which is the newest-over-
    best rule this module exists to refuse. Measured before this guard:
    `--claude claude` answered claude-sonnet-5 while a bare `--claude` answered
    the claude-opus-5 pin. `resolve` sends these to the flagship instead.

    A family of one is exempt: with a single model there is no position to
    choose by, so its only tokens are still legitimate tier names.
    """
    want = qualifier.strip().lower()
    if not want:
        return None
    models = provider_models(family)
    # Before the whole-family rule: an exact id names ONE model even when every
    # model carries it as a token (`o3` beside `o3-mini`), so it is never the
    # undiscriminating qualifier that rule exists to refuse.
    exact = [m for m in models if m.lower() == want]
    if exact:
        return exact[0]
    if _names_whole_family(family, want):
        return None
    if len(_token_seq(want)) > 1:
        return _sequence_match(family, want)
    hits = [m for m in models if want in _tokens(m)]
    return hits[0] if hits else None


def resolve(family: str, qualifier: str = "") -> dict:
    """Resolve a family flag (+ optional qualifier) to a model and effort.

    Returns {"family", "model", "effort", "how"}. `how` states which rule fired
    so the launch receipt can show it — an inference must never masquerade as
    something the operator typed.
    """
    fam = family.strip().lower().lstrip("-")
    if fam not in FAMILY_FLAGSHIP:
        raise FamilyError(
            f"unknown model family {family!r} (known: {', '.join(families())})")
    flagship = FAMILY_FLAGSHIP[fam]
    qual = qualifier.strip().lower()
    if not qual:
        return {"family": fam, "model": flagship, "effort": "", "how": "flagship"}
    tier = tier_match(fam, qual)
    if tier is not None:
        return {"family": fam, "model": tier, "effort": "", "how": f"tier {qual}"}
    if _names_whole_family(fam, qual):
        return {"family": fam, "model": flagship, "effort": "",
                "how": f"flagship ({qual!r} names the whole family, not a tier)"}
    if qual in EFFORT_LEVELS:
        return {"family": fam, "model": flagship, "effort": qual,
                "how": f"flagship + effort {qual}"}
    raise FamilyError(
        f"{qual!r} is neither a {fam} model tier nor a reasoning effort "
        f"({', '.join(EFFORT_LEVELS)})")


def unpinned_flagships() -> "list[tuple[str, str]]":
    """Pins that no longer exist in providers.yaml. Empty is the healthy state."""
    return [(fam, pin) for fam, pin in sorted(FAMILY_FLAGSHIP.items())
            if pin not in provider_models(fam)]


def default_effort(family: str) -> str:
    """The family's default reasoning effort, or '' when it has none."""
    return FAMILY_EFFORT_DEFAULT.get(family.strip().lower().lstrip("-"), "")


def _bare_model(model_id: str) -> str:
    """`moonshot/kimi-k3[1m]` -> `kimi-k3`: no provider prefix, no variant suffix."""
    bare = model_id.strip().rsplit("/", 1)[-1]
    return bare.split("[", 1)[0]


def family_of_model(model_id: str) -> "str | None":
    """Which family lists this model id in providers.yaml, or None.

    None, never a guess: bin/grip refuses the launch on None rather than
    inventing a tier map for a model no family owns.
    """
    bare = _bare_model(model_id)
    if not bare:
        return None
    for family in families():
        if bare in provider_models(family):
            return family
    return None


def _catalog_path() -> Path:
    """GRIP's model catalogue, resolved from the same root as providers.yaml."""
    return _providers_path().parent / "model_catalog.yaml"


def context_window(family: str, model_id: str) -> int:
    """The catalogue's context_window for `<provider>/<model>`; loud when absent.

    Read, never invented: a literal here would be the number that silently
    rots when the vendor changes a window, and CLAUDE_CODE_AUTO_COMPACT_WINDOW
    is derived from it.
    """
    address = f"{FAMILY_PROVIDER.get(family, family)}/{_bare_model(model_id)}"
    try:
        text = _catalog_path().read_text(encoding="utf-8")
    except OSError as exc:
        raise FamilyError(f"cannot read {_catalog_path()}: {exc}") from exc
    match = re.search(
        rf"^\s*-\s*address:\s*{re.escape(address)}\s*$(?:(?!^\s*-\s*address:).)*?"
        r"^\s*context_window:\s*(\d+)\s*$", text, re.M | re.S)
    if match is None:
        raise FamilyError(f"no context_window for {address} in {_catalog_path()}")
    return int(match.group(1))


def _family_key(family: str) -> str:
    fam = family.strip().lower().lstrip("-")
    if fam not in FAMILY_FLAGSHIP:
        raise FamilyError(
            f"unknown model family {family!r} (known: {', '.join(families())})")
    return fam


def _tier_model(fam: str, tier: "str | None") -> str:
    """The main model for a tier word, a full id, or the family flagship."""
    want = (tier or "").strip()
    if not want:
        return FAMILY_FLAGSHIP[fam]
    if _bare_model(want) in provider_models(fam):
        return _bare_model(want)
    got = resolve(fam, want)
    if got["effort"]:
        raise FamilyError(f"{want!r} is a reasoning effort, not a {fam} tier")
    return got["model"]


def claude_env(family: str, tier: "str | None" = None,
               effort: "str | None" = None) -> "dict[str, str]":
    """The Claude Code tier map for a family: what bin/grip exports at launch.

    Main/opus/sonnet/fable/subagent all name the chosen model (the `[1m]`
    variant when its catalogue window reaches 1M), haiku names the family's
    coding model, the compact window follows the window, and the effort is the
    operator's or the family default. GRIP_1M_MODEL is the main-model seam:
    bin/grip execs `claude --model "$GRIP_1M_MODEL"`, so an ANTHROPIC_MODEL
    line would sit under an explicit --model and be a silent drop.
    """
    fam = _family_key(family)
    recipe = CLAUDE_FAMILY_RECIPE.get(fam)
    if recipe is None:
        raise FamilyError(
            f"Claude Code cannot run on a {fam} model: HAL serves none of its "
            f"ids on /v1/messages (families with a recipe: "
            f"{', '.join(sorted(CLAUDE_FAMILY_RECIPE))})")
    if recipe["haiku"] not in provider_models(fam):
        raise FamilyError(f"{fam} haiku tier {recipe['haiku']!r} is not in providers.yaml")
    main = _tier_model(fam, tier)
    window = context_window(fam, main)
    one_m = window >= ONE_MILLION
    main_id = f"{main}[1m]" if one_m else main
    level = (effort or "").strip().lower() or default_effort(fam)
    if level not in EFFORT_LEVELS:
        raise FamilyError(f"unknown reasoning effort {effort!r} ({', '.join(EFFORT_LEVELS)})")
    return {
        "GRIP_1M_MODEL": main_id,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": main_id,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": main_id,
        "ANTHROPIC_DEFAULT_FABLE_MODEL": main_id,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": recipe["haiku"],
        "CLAUDE_CODE_SUBAGENT_MODEL": main_id,
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": str(recipe["compact_window_1m"] if one_m else window),
        "CLAUDE_CODE_EFFORT_LEVEL": level,
    }


def direct_endpoint(family: str) -> dict:
    """The vendor-direct row for a family; loud when the family has none."""
    fam = _family_key(family)
    row = FAMILY_DIRECT.get(fam)
    if row is None:
        raise FamilyError(f"--direct is not wired for {fam} (wired: "
                          f"{', '.join(sorted(FAMILY_DIRECT))})")
    return dict(row)


def direct_env(family: str) -> "dict[str, str]":
    """The one extra line a `--direct` Claude launch exports: the vendor base."""
    return {"ANTHROPIC_BASE_URL": direct_endpoint(family)["anthropic_base_url"]}


def is_launch_line(line: str) -> bool:
    """Would bin/grip's validator accept this line? Mirror of `_fam_re`."""
    return bool(LAUNCH_LINE_RE.match(line) or _BASE_URL_LINE_RE.match(line))


def direct_profile_name(family: str) -> str:
    """`kimi` -> `kimi-direct`: the Codex profile a `--direct` launch selects.

    Deliberately NOT the bare family name. `bin/grip-harness --profiles` writes
    gateway-routed `~/.codex/<name>.config.toml` profiles from
    grip_harness.CROSS_WIRE_PROFILES, so a `kimi.config.toml` written here would
    be one file with two writers of opposite meaning: a `--direct` launch would
    silently turn the gateway profile vendor-direct, and the next `--profiles`
    run would silently turn it back. The suffix also makes the exec line say
    what it does — `codex -p kimi-direct` — where `-p kimi` would not.
    """
    return f"{_family_key(family)}-direct"


def emit_direct_codex_profile(
    profile: str, family: str, model: str, provider: str, provider_name: str,
    base_url: str, env_key: str, context_window: int,
) -> str:
    """A ~/.codex/<profile>.config.toml overlay that names a VENDOR endpoint.

    Only for `gg++ codex --<family> --direct`. The base config stays on
    hal-gateway; `codex -p <profile>` layers this over it for one launch
    (codex-cli 0.148.0 `--help`: "Layer $CODEX_HOME/<name>.config.toml on top
    of the base user config"). ``env_key`` names the variable bin/grip fills
    from the keychain for that exec — the credential is never written here
    ("Do not write the key into config.toml", the vendor's own recipe). Off the
    gateway there is no witness row and no cost row, and the overlay says so.
    Fenced with grip_harness's GRIP-MANAGED markers, so merge_managed_toml
    rewrites only GRIP's span and lines the operator adds to the profile
    survive a re-run.
    """
    import grip_harness as gh
    body = (
        f'model = "{model}"\n'
        f'model_provider = "{provider}"\n'
        f"model_context_window = {int(context_window)}\n"
        "\n"
        f"[model_providers.{provider}]\n"
        f'name = "{provider_name}"\n'
        f'base_url = "{base_url}"\n'
        f'env_key = "{env_key}"\n'
        'wire_api = "responses"\n'   # the vendor's native Responses API
    )
    return (
        f"{gh.MANAGED_BEGIN}\n"
        f"# Codex profile '{profile}' — DIRECT to the vendor (gg++ codex --{family} --direct).\n"
        f"# Bypasses HAL :4010: no witness row, no cost row. ${env_key} is filled from\n"
        f"# the keychain at launch and is never written here.\n"
        f"{body}{gh.MANAGED_END}\n"
    )


def codex_direct_profile(model_id: str) -> "tuple[str, str, str]":
    """(family, profile name, fenced TOML) for a vendor-direct Codex overlay.

    The overlay is the vendor's Codex recipe for the family: the chosen model,
    its catalogue window, and a `[model_providers.<family>]` table naming the
    vendor base URL and the env variable that will carry the key. The key
    itself is never part of the text — "Do not write the key into config.toml".
    """
    fam = family_of_model(model_id)
    if fam is None:
        raise FamilyError(f"{model_id!r} belongs to no model family in providers.yaml")
    row = direct_endpoint(fam)
    model = _bare_model(model_id)
    profile = direct_profile_name(fam)
    body = emit_direct_codex_profile(
        profile=profile, family=fam, model=model, provider=row["codex_provider"],
        provider_name=row["codex_provider_name"], base_url=row["openai_base_url"],
        env_key=row["codex_env_key"], context_window=context_window(fam, model))
    return fam, profile, body


def write_codex_direct_profile(model_id: str, codex_home: "str | None" = None) -> dict:
    """Write `<CODEX_HOME>/<family>-direct.config.toml` as a fenced overlay; idempotent.

    Only the GRIP-MANAGED fence is rewritten, so lines the operator adds to the
    profile survive; the base config.toml is never the target. Returns the
    facts bin/grip needs to launch: profile, path, env_key, keychain.
    """
    import grip_harness as gh
    fam, profile, block = codex_direct_profile(model_id)
    home = Path(codex_home or os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
    path = home / f"{profile}.config.toml"
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    if gh.has_truncated_fence(existing):
        raise FamilyError(f"{path} has an unterminated GRIP-MANAGED fence — repair it by hand")
    home.mkdir(parents=True, exist_ok=True)
    gh.atomic_write_text(path, gh.merge_managed_toml(existing, block))
    row = direct_endpoint(fam)
    return {"profile": profile, "path": path,
            "env_key": row["codex_env_key"], "keychain": row["keychain"]}


def claude_env_lines(model_id: str, effort: "str | None" = None,
                     direct: bool = False) -> "list[str]":
    """`KEY=VALUE` lines for a resolved model id — the shape bin/grip consumes."""
    fam = family_of_model(model_id)
    if fam is None:
        raise FamilyError(f"{model_id!r} belongs to no model family in providers.yaml")
    env = claude_env(fam, tier=model_id, effort=effort)
    if direct:
        env = {**direct_env(fam), **env}
    lines = [f"{k}={v}" for k, v in env.items()]
    bad = [l for l in lines if not is_launch_line(l)]
    if bad:  # a value the validator would reject must never be printed
        raise FamilyError(f"refusing to emit lines the launch validator rejects: {bad}")
    return lines


def _user_settings_path() -> Path:
    """Claude Code's user settings — its `env` block overwrites the launch env."""
    try:
        from claude_dir import get_claude_dir
        root = get_claude_dir()
    except ImportError:  # an install without the shim: same default it encodes
        root = Path(os.environ.get("CLAUDE_DIR") or "~/.claude").expanduser()
    return Path(root) / "settings.json"


def settings_env_pins(env: "dict[str, str]",
                      settings_path: "Path | None" = None) -> "dict[str, str]":
    """Keys of `env` that the settings file's `env` block pins to ANOTHER value.

    Anthropic's settings reference, `env`: "A value here overwrites the same
    variable exported in your shell." So a tier variable bin/grip exports is
    overwritten at Claude Code startup by a same-named pin in settings.json —
    the launch looks right, and the pinned value wins. GRIP's shipped settings
    pin CLAUDE_CODE_EFFORT_LEVEL=max, which overwrites the family's effort.
    Returns {key: pinned value}. Fail-open by design: a missing, unreadable or
    malformed file, or an `env` that is not an object, reports nothing — this
    is a notice, never a launch blocker.
    """
    import json
    path = settings_path or _user_settings_path()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    pinned = doc.get("env") if isinstance(doc, dict) else None
    if not isinstance(pinned, dict):
        return {}
    return {k: str(pinned[k]) for k in env if k in pinned and str(pinned[k]) != env[k]}


def _warn_settings_pins(lines: "list[str]") -> None:
    """One stderr note per exported line that settings.json would overwrite."""
    env = dict(line.split("=", 1) for line in lines)
    path = _user_settings_path()
    for key, pinned in settings_env_pins(env, path).items():
        sys.stderr.write(
            f"[grip] note: {path} env pins {key}={pinned}. Claude Code applies a "
            f"settings-file env value over the launch environment, so the "
            f"{key}={env[key]} this launch exports will not take effect. Remove that "
            f"pin, or set it there to the value you want, to let the model family decide.\n")


def _cmd_resolve(argv: "list[str]") -> int:
    if not argv:
        sys.stderr.write("usage: grip_harness_family.py resolve <family> [qualifier]\n")
        return 2
    try:
        got = resolve(argv[0], argv[1] if len(argv) > 1 else "")
    except FamilyError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    print(f"MODEL {got['model']}")
    print(f"EFFORT {got['effort']}")
    print(f"HOW {got['how']}")
    return 0


def _cmd_families(_argv: "list[str]") -> int:
    print("\n".join(families()))
    return 0


def _cmd_audit(_argv: "list[str]") -> int:
    stale = unpinned_flagships()
    for fam, pin in stale:
        sys.stderr.write(f"STALE {fam} -> {pin} (not in providers.yaml)\n")
    return 1 if stale else 0


def _split_flags(argv: "list[str]") -> "tuple[list[str], dict]":
    """Positionals plus {--effort: X, --direct: True, --carried: True}; anything else is an error."""
    words: "list[str]" = []
    flags: dict = {}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--effort" and i + 1 < len(argv):
            flags["effort"], i = argv[i + 1], i + 1
        elif tok == "--direct":
            flags["direct"] = True
        elif tok == "--carried":
            flags["carried"] = True
        elif tok.startswith("-"):
            raise FamilyError(f"unknown flag {tok!r}")
        else:
            words.append(tok)
        i += 1
    return words, flags


def _cmd_claude_env(argv: "list[str]") -> int:
    """Print the validated launch lines for a resolved model id (bin/grip)."""
    try:
        words, flags = _split_flags(argv)
        if len(words) != 1:
            raise FamilyError("usage: grip_harness_family.py claude-env <model-id> "
                              "[--effort <level>] [--direct] [--carried]")
        lines = claude_env_lines(words[0], effort=flags.get("effort"),
                                 direct=bool(flags.get("direct")))
    except FamilyError as exc:
        sys.stderr.write(f"{exc}\n")   # nothing on stdout: a refusal exports nothing
        return 2
    # --carried: bin/grip also passes these lines in a --settings env, which
    # outranks a settings file, so no settings.json pin can overwrite them and the
    # "will not take effect" note would be false. It only applies without that.
    if not flags.get("carried"):
        _warn_settings_pins(lines)   # stderr only; bin/grip reads stdout alone
    print("\n".join(lines))
    return 0


def _cmd_direct_keychain(argv: "list[str]") -> int:
    """Print the keychain service holding the vendor key for a model's family."""
    fam = family_of_model(argv[0]) if argv else None
    if fam is None:
        sys.stderr.write("usage: grip_harness_family.py direct-keychain <model-id>\n")
        return 2
    try:
        print(direct_endpoint(fam)["keychain"])
    except FamilyError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    return 0


def _cmd_codex_direct(argv: "list[str]") -> int:
    """Write the vendor-direct Codex overlay; print PROFILE / ENV_KEY / KEYCHAIN.

    bin/grip reads those three lines, fills ENV_KEY from the keychain for the
    one exec, and launches `codex -p PROFILE`. Nothing secret crosses stdout.
    """
    if len(argv) != 1:
        sys.stderr.write("usage: grip_harness_family.py codex-direct <model-id>\n")
        return 2
    try:
        info = write_codex_direct_profile(argv[0])
    except (FamilyError, OSError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    print(f"PROFILE {info['profile']}")
    print(f"ENV_KEY {info['env_key']}")
    print(f"KEYCHAIN {info['keychain']}")
    return 0


_DISPATCH = {"resolve": _cmd_resolve, "families": _cmd_families, "audit": _cmd_audit,
             "claude-env": _cmd_claude_env, "direct-keychain": _cmd_direct_keychain,
             "codex-direct": _cmd_codex_direct}


def main(argv: "list[str]") -> int:
    if not argv or argv[0] in ("-h", "--help"):
        sys.stderr.write(__doc__ or "")
        return 0
    handler = _DISPATCH.get(argv[0])
    if handler is None:
        sys.stderr.write(f"unknown command {argv[0]!r} (try: {', '.join(_DISPATCH)})\n")
        return 2
    return handler(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
