#!/usr/bin/env python3
"""GRIP launch-time harness picker — "which harness do you want to open?".

Origin: 2026-06-22, task #2212. When V>> runs `grip`, present a frictionless
TUI picker over the AI harnesses that are actually INSTALLED on this machine:
always Claude Code, plus Codex / Aider / opencode / Hermes when their binary is
on PATH. Selecting Claude → the normal GRIP launch; selecting any other harness
→ launch that harness (already wired at GRIP's HAL gateway :4010 by
`grip-harness <name>`, per lib/grip_harness.py).

This module is the PURE + TESTABLE core (stdlib only — no heavy deps). bin/grip
calls its thin CLI (`resolve` / `plan` / `remember` / `pick`) and acts on the
single emitted directive line. The keyboard menu itself lives in
lib/grip_tui_menu.py, shared with the model picker: rows never wrap, long lists
scroll, NO_COLOR is honoured, and Ctrl-C at the menu exits 130 so bin/grip
launches nothing.

Frictionless contract (the falsifier for H912):
  * a clear DEFAULT (Claude Code, or the remembered last choice) — one key Enter;
  * a fast-path that skips the menu entirely: `grip --harness <name>` flag OR the
    `GRIP_HARNESS` env var;
  * the menu is NEVER shown when only Claude Code is installed (no 1-option menu);
  * the last choice is remembered in state/harness-choice.json (gitignored).

Resolution precedence (highest wins), in resolve_choice():
  1. explicit flag (`--harness <name>`) / `GRIP_HARNESS` env  → that harness, no prompt
  2. only Claude installed                                    → claude, no prompt
  3. not a TTY (relay / CI / pipe)                            → claude, no prompt
  4. interactive                                              → TUI, default = remembered|claude
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Callable, List, Optional

# --- harness registry -------------------------------------------------------
# name -> spec. Claude Code is ALWAYS first and is the default. Detection is by
# `binary` presence, decoupled from grip_harness's emitter registry on purpose —
# Hermes has a real binary (`hermes`) but no emitter yet (#2210), and the picker
# only needs to DETECT + LAUNCH, never wire.
#
# Spec keys:
#   label / binary / blurb  — required; what the menu shows and what it detects.
#   launch_argv             — full argv when the bare binary is not a session.
#                             `dsh` is the reason this exists: it boots a named
#                             PROFILE, so `dsh` alone is a usage error, not a
#                             harness. Absent → [binary], the historical shape.
#   effort_argv             — argv that sets reasoning effort; absence means the
#                             surface is UNVERIFIED, never that it is unsupported.
#   interactive             — False for a one-shot/transform tool that cannot host
#                             a session. Those stay OUT of the launch menu and out
#                             of the live-session process scan, and stay IN the
#                             installer, because installing them is still useful:
#                             lib/harness/ dispatches them programmatically.
#   install                 — how bin/grip-harness-install obtains it. Every entry
#                             was verified against the canonical registry on
#                             2026-08-18 (npm registry / PyPI JSON / brew info),
#                             never from recall. A harness with no verified path
#                             carries manager "manual" + the reason, rather than a
#                             plausible-looking command nobody has run.
#
# Verification notes worth keeping, because each one refutes an obvious guess:
#   * `pi` is npm @mariozechner/pi-coding-agent (bin `pi`). The shorter
#     @mariozechner/pi ships bin `pi-pods` — a different tool.
#   * `goose` is brew block-goose-cli. The brew formula literally named `goose`
#     is the database-migration tool, so the obvious command installs the wrong
#     binary and nothing errors.
#   * npm `omnigent` ships bins `omniagent`/`omni-agent`, not `omnigent`, so the
#     adapter's binary and that package do not correspond; it is manual here.
_CLAUDE = "claude"

HARNESS_REGISTRY: "dict[str, dict]" = {
    "claude": {
        "label": "Claude Code",
        "binary": "claude",
        "blurb": "the native GRIP harness — full session continuity + 1M context",
        "install": {"manager": "npm", "package": "@anthropic-ai/claude-code"},
    },
    "codex": {
        "label": "Codex",
        "binary": "codex",
        "blurb": "OpenAI's terminal agent, pointed at GRIP's HAL gateway :4010",
        # Codex takes arbitrary config overrides as `-c key=value`, and
        # `model_reasoning_effort` is a real key — it is already set in this
        # operator's ~/.codex/config.toml, which is stronger evidence than the
        # CLI help, since --strict-config was measured NOT to reject an invented
        # key and therefore proves nothing on its own.
        "effort_argv": ("-c", "model_reasoning_effort={effort}"),
        # --- outbound one-shot dispatch (docs/harness-abstraction-layer.md §2.2) ---
        # Probed 2026-09-17: `codex exec "<prompt>"` exited 1 with "Reading
        # additional input from stdin…". Codex wants the prompt on STDIN, not as
        # an argument — so the argv carries NO `{prompt}` placeholder and
        # `delivery` says where the prompt goes. Without the delivery field Codex
        # cannot be represented at all, and an argv-only adapter concludes it is
        # broken when it is merely being spoken to incorrectly.
        "oneshot_argv": ("codex", "exec"),
        "delivery": "stdin",
        "verified": True,
        "install": {"manager": "npm", "package": "@openai/codex"},
    },
    "aider": {
        "label": "Aider",
        "binary": "aider",
        "blurb": "pair-programming in your terminal, via GRIP's HAL gateway :4010",
        "install": {"manager": "pipx", "package": "aider-chat"},
    },
    "opencode": {
        "label": "opencode",
        "binary": "opencode",
        "blurb": "open-source coding agent, via GRIP's HAL gateway :4010",
        # --- outbound one-shot dispatch (docs/harness-abstraction-layer.md §2.2) ---
        # Probed 2026-09-17: `opencode run "<prompt>"` exited 0 and returned
        # `BOOTSTRAP_OK` exactly. `{prompt}` is substituted into the argv by
        # HarnessAdapter, which reads this field rather than branching on the
        # harness name (H-UC-HARNESS).
        "oneshot_argv": ("opencode", "run", "{prompt}"),
        "delivery": "argv",
        "verified": True,
        "install": {"manager": "npm", "package": "opencode-ai"},
    },
    "hermes": {
        "label": "Hermes Agent",
        "binary": "hermes",
        "blurb": "NousResearch agent (persistent memory, sandboxed exec)",
        "install": {"manager": "manual",
                    "reason": "installed from the NousResearch checkout; no "
                              "published package provides the `hermes` binary"},
    },
    "agy": {
        "label": "Antigravity CLI",
        "binary": "agy",
        "blurb": "Google's Antigravity agent, wired to GRIP infrastructure",
        "install": {"manager": "manual",
                    "reason": "ships with the Antigravity IDE; npm "
                              "@google/antigravity-cli does not exist (404)"},
    },
    "gemini": {
        "label": "Gemini CLI",
        "binary": "gemini",
        "blurb": "Google's Gemini CLI — already a lib/harness/ kind, now launchable",
        "install": {"manager": "npm", "package": "@google/gemini-cli"},
    },
    "dsh": {
        "label": "DeepSeek Harness",
        "binary": "dsh",
        "blurb": "DeepSeek's Cordis-plugin harness; boots its web profile",
        # Launched through bin/grip-dsh, not the bare binary, for two reasons.
        #
        # First, `dsh` alone exits 1 with "error: --profile <name> is required",
        # so it is not a launch at all; grip-dsh defaults to `--profile web`,
        # the profile that gives an interactive surface (`headless` is the
        # one-shot form lib/harness/dsh.py uses).
        #
        # Second, and the reason a plain `dsh --profile web` here would be a
        # quiet failure: $DSH_HOME/settings.yaml routes DSH's models through
        # HAL's gateway via `apiKeyEnv: HAL_GATEWAY_KEY`, and bin/grip exports
        # that variable ONLY for a wireable harness. `dsh` is not in _WIREABLE
        # (it has no HAL config emitter), so a gg++-launched dsh would register
        # the route and then fail every request with "no credential for provider
        # route hal". grip-dsh reads the key from the keychain at run time and
        # execs dsh with it in-process — no secret on disk, and the route works.
        #
        # `binary` stays `dsh` on purpose: that is what must be INSTALLED, and
        # what detection and the installer must look for. Only the launch differs.
        # tests/test_grip_harness_install.py pins the shim's existence so the two
        # cannot drift into a launch that falls back to Claude Code.
        "launch_argv": ("grip-dsh",),
        "install": {"manager": "npm", "package": "@deepseek-ai/dsh"},
    },
    "kimi-code": {
        "label": "Kimi Code CLI",
        "binary": "kimi",
        "blurb": "Moonshot's Kimi Code CLI; bin/grip-kimi routes it through the HAL gateway",
        # Launched through bin/grip-kimi, not the bare binary, for the dsh reason
        # in a sharper form: the CLI reads its providers from
        # ~/.kimi-code/config.toml and "does not fall back to shell environment
        # variables for credentials" (vendor providers page, text render :42,
        # read 2026-09-07), so the HAL route has to be IN THE FILE before the
        # process starts. grip-kimi keeps it there (lib/kimi_hal_route.py —
        # idempotent, refuses rather than clobbers), exports the gateway
        # doorstop for anything kimi spawns, and execs `kimi` with the caller's
        # argv unchanged. Not in _WIREABLE, deliberately: a wireable name must
        # carry an EMITTERS entry and a compatible_with row
        # (tests/test_pool_c_wiring.py); this leaf wires itself through its own
        # route module, exactly as dsh does.
        #
        # `binary` stays `kimi` on purpose: that is what must be INSTALLED and
        # what detection looks for. Only the launch differs. lib/harness/kimi.py
        # points its EXECUTABLE at the same shim, and
        # tests/test_kimi_code_wiring.py pins that the two registries agree.
        "launch_argv": ("grip-kimi",),
        # manual, and the reason is not a missing verification. The vendor
        # documents two installs: the recommended script
        # `curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash`
        # (getting-started :42; one native binary to ~/.kimi-code/bin/kimi after
        # a sha256 check, install.sh :512-541) and
        # `npm install -g @moonshot-ai/kimi-code` — which registry.npmjs.org
        # confirmed on 2026-09-07 ships bin `kimi` (0.41.0, node >=22.19.0), so
        # an npm row would be a one-line promotion. It stays manual because
        # installing this third-party binary is the operator's opt-in step for
        # this leaf (plans/enable-usage-of-kimi-adaptive-pebble.md, W4), and
        # bin/grip-harness-install never pipes the network into a shell.
        "install": {"manager": "manual",
                    "reason": "install it yourself with the vendor's documented command: "
                              "curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash "
                              "(or npm install -g @moonshot-ai/kimi-code, Node 22.19.0+), "
                              "then run grip-kimi-verify to prove it boots through the HAL route"},
    },
    "kiro": {
        "label": "Kiro CLI",
        "binary": "kiro-cli",
        "blurb": "AWS Kiro — terminal AI agent with steering, MCP and agent profiles",
        # Not available via npm/brew/pipx: Kiro is distributed as a signed
        # platform binary from kiro.dev/downloads. The install command is
        # platform-specific and requires browser authentication to an AWS
        # account (Builder ID or IAM Identity Center), so it cannot be
        # automated here. Instructions at https://kiro.dev/docs/cli/setup/
        "install": {"manager": "manual",
                    "reason": "signed platform binary from kiro.dev/downloads; "
                              "requires browser auth (AWS Builder ID or IAM "
                              "Identity Center) — no npm/brew/pipx package",
                    "url": "https://kiro.dev/docs/cli/setup/"},
    },
    "qwen": {
        "label": "Qwen Code",
        "binary": "qwen",
        "blurb": "Alibaba's Qwen coding agent (gemini-cli lineage)",
        "install": {"manager": "npm", "package": "@qwen-code/qwen-code"},
    },
    "continue": {
        "label": "Continue CLI",
        "binary": "cn",
        "blurb": "Continue's terminal agent — the `cn` binary",
        "install": {"manager": "npm", "package": "@continuedev/cli"},
    },
    "kilo": {
        "label": "Kilo Code",
        "binary": "kilo",
        "blurb": "Kilo Code's CLI agent",
        "install": {"manager": "npm", "package": "@kilocode/cli"},
    },
    "cline": {
        "label": "Cline",
        "binary": "cline",
        "blurb": "Cline's terminal agent",
        "install": {"manager": "npm", "package": "cline"},
    },
    "crush": {
        "label": "Crush",
        "binary": "crush",
        "blurb": "Charm's glamourous terminal coding agent",
        "install": {"manager": "npm", "package": "@charmland/crush"},
    },
    "codewhale": {
        "label": "CodeWhale",
        "binary": "codewhale",
        "blurb": "CodeWhale terminal agent (ex DeepSeek-TUI)",
        "install": {"manager": "npm", "package": "codewhale"},
    },
    "pi": {
        "label": "pi",
        "binary": "pi",
        "blurb": "the minimal pi coding agent from the pi-mono toolkit",
        # --- outbound one-shot dispatch (docs/harness-abstraction-layer.md §2.2) ---
        # argv shape UNVERIFIED: `pi -p "<prompt>"` exited 1 with "No API key
        # found for deepseek" — an auth gap, not a shape problem, so the
        # invocation stays recorded but carries `verified: False`. `available()`
        # deliberately does NOT gate on that flag: a logged-in operator must
        # still be able to dispatch pi, and absence-of-verification is not a
        # reason to refuse a real binary.
        "oneshot_argv": ("pi", "-p", "{prompt}"),
        "delivery": "argv",
        "verified": False,
        "install": {"manager": "npm", "package": "@mariozechner/pi-coding-agent"},
    },
    "goose": {
        "label": "goose",
        "binary": "goose",
        "blurb": "Block's goose agent (extensions + MCP)",
        "install": {"manager": "brew", "package": "block-goose-cli"},
    },
    "openhands": {
        "label": "OpenHands",
        "binary": "openhands",
        "blurb": "OpenHands (ex OpenDevin) autonomous software agent",
        "install": {"manager": "manual",
                    "reason": "PyPI openhands-ai 1.11.0 is a LIBRARY — its wheel "
                              "contains no entry_points.txt, so it declares no "
                              "console script and can never put `openhands` on "
                              "PATH (pipx: 'No apps associated with package'). "
                              "Upstream runs it via Docker or `uvx --from`",
                    "url": "https://github.com/All-Hands-AI/OpenHands"},
    },
    "interpreter": {
        "label": "Open Interpreter",
        "binary": "interpreter",
        "blurb": "Open Interpreter — natural-language code execution REPL",
        # 3.11 because open-interpreter 0.4.3 pins tiktoken 0.7.0, which ships no
        # cp314 wheel; building it from source fails PyO3's abi3 forward-compat
        # check. A resolver refusal, not a flake — the same command cannot succeed
        # on the default interpreter no matter how often it is retried.
        "install": {"manager": "pipx", "package": "open-interpreter",
                    "python": "python3.11"},
    },
    # --- one-shot tools: installable + dispatchable, never a session ---------
    "fabric": {
        "label": "fabric",
        # The binary is `fabric-ai`, not `fabric`: Homebrew renamed it because
        # `fabric` was already taken by the Python deployment tool, so the
        # formula installs Cellar/fabric-ai/<v>/bin/fabric-ai. Verified by
        # `brew list fabric-ai` after a real install. Note this diverges from
        # lib/harness/fabric.py, whose adapter still invokes `fabric` — that
        # adapter cannot run on a brew-installed fabric, which is recorded in
        # docs/harness-in-harness.md rather than silently reconciled here.
        "binary": "fabric-ai",
        "blurb": "Daniel Miessler's fabric — pattern-driven text transforms",
        "interactive": False,
        "install": {"manager": "brew", "package": "fabric-ai"},
    },
    "sgpt": {
        "label": "shell-gpt",
        "binary": "sgpt",
        "blurb": "shell-gpt — one-shot shell/Q&A assistant",
        "interactive": False,
        "install": {"manager": "pipx", "package": "shell-gpt"},
    },
    "metagpt": {
        "label": "MetaGPT",
        "binary": "metagpt",
        "blurb": "MetaGPT multi-agent software company (one-shot run)",
        "interactive": False,
        # Attempted on 3.14, then on 3.11; both refused, for DIFFERENT reasons,
        # which is what makes this a property of the package rather than of the
        # interpreter. 3.14: every version pins faiss-cpu==1.7.4, whose newest
        # wheel ABI tag is cp311. 3.11: the resolver clears that and backtracks
        # all the way to metagpt 0.1, which pulls pandas 1.4.1 — a source build
        # importing pkg_resources, removed from modern setuptools. No pipx
        # invocation resolves, so naming one would be a command that fails on
        # every run and reads like a flake.
        "install": {"manager": "manual",
                    "reason": "no resolvable pipx install — 3.14 is blocked by "
                              "faiss-cpu==1.7.4 (newest wheel cp311), and 3.11 "
                              "backtracks to metagpt 0.1 -> pandas 1.4.1, whose "
                              "source build needs the removed pkg_resources. "
                              "Upstream installs from a git checkout",
                    "url": "https://github.com/FoundationAgents/MetaGPT"},
    },
    "plandex": {
        "label": "Plandex",
        "binary": "plandex",
        "blurb": "Plandex — planning-first agent for large changes",
        "interactive": False,
        "install": {"manager": "manual",
                    "reason": "no npm/PyPI/brew-core package; upstream ships "
                              "`curl -sL https://plandex.ai/install.sh | bash`, "
                              "which is a remote script this installer will not "
                              "run on the operator's behalf",
                    "url": "https://plandex.ai/install.sh"},
    },
    "swe-agent": {
        "label": "SWE-agent",
        "binary": "sweagent",
        "blurb": "Princeton SWE-agent — issue-to-patch benchmark agent",
        "interactive": False,
        "install": {"manager": "manual",
                    "reason": "PyPI `sweagent` is a 0.0.1 placeholder, not the "
                              "Princeton agent; upstream installs from the "
                              "checkout",
                    "url": "https://github.com/SWE-agent/SWE-agent"},
    },
    "omnigent": {
        "label": "Omnigent",
        "binary": "omnigent",
        "blurb": "meta-harness — a harness-of-harnesses peer of our composites",
        "interactive": False,
        "install": {"manager": "manual",
                    "reason": "npm `omnigent` ships bins omniagent/omni-agent, "
                              "not `omnigent` — the package and this adapter's "
                              "binary do not correspond"},
    },
    # REA is a tool server, not an agent: a stdio MCP server (`rea mcp`) plus a
    # --json CLI, and it makes no model calls of its own. It sits in this
    # registry so the installer can obtain the PINNED version (the one place
    # that version is written) and so `gg++ rea` explains how it is reached
    # (attached per launch with `claude --mcp-config mcp-servers/rea/mcp.json`;
    # its schemas cost ~256k tokens on 6.3.0, so it is never in the default
    # .mcp.json) instead of claiming lib/harness/ dispatches it.
    # Verified 2026-10-10 (6.3.0) and 2026-10-11 (6.4.0): npm `rea-agents`
    # ships bins `rea` and `rea-agents`, both -> scripts/rea.mjs.
    "rea": {
        "label": "REA (rea-agents)",
        "binary": "rea",
        "blurb": "reverse-engineering MCP tool server — attached per launch "
                 "via --mcp-config mcp-servers/rea/mcp.json, not a REPL",
        "interactive": False,
        "attach": "mcp",
        "install": {"manager": "npm", "package": "rea-agents",
                    "version": "6.4.0"},
    },
    # Verified 2026-10-18: vibheksoni/stealth-browser-mcp (MIT) at HEAD
    # ba95cca9 of 2026-10-12 -- 101 tools in 11 sections, python >= 3.10,
    # pydantic-core 2.33.2 whose wheels stop at CPython 3.13 (GRIP's venv is
    # 3.15, so the server gets its own 3.13 venv), no console entry point (the
    # launch is always `<venv>/bin/python src/server.py`), one release
    # (v0.2.5, 2026-02-10) with eight months of unreleased work after it, so
    # the pin is a sha. The `git-venv` manager clones --no-checkout, detaches
    # at the sha, builds the venv, installs requirements.txt (the py2js git
    # pin needs git on PATH) and verifies by RUNNING `--list-sections` under a
    # throwaway HOME: 11 sections summing to expect_tools. `binary` names
    # nothing on PATH on purpose: presence is the sha pin in
    # ${GRIP_TOOLS_DIR:-~/.grip-tools}/stealth-browser-mcp/current/.grip-pin.
    "grip-browser": {
        "label": "grip-browser (stealth-browser-mcp)",
        "binary": "grip-browser",
        "blurb": "unattended fresh-identity Chrome MCP tool server -- attached "
                 "per launch via --mcp-config mcp-servers/grip-browser/mcp.json, "
                 "gated by hooks/grip-browser-tool-policy-gate.py, not a REPL",
        "interactive": False,
        "attach": "mcp",
        "install": {"manager": "git-venv",
                    "package": "vibheksoni/stealth-browser-mcp",
                    "repo": "https://github.com/vibheksoni/stealth-browser-mcp.git",
                    "sha": "ba95cca933c8e90bfeaa515c1d05dad2927794e6",
                    "version": "0.2.5+ba95cca9",
                    "python": "python3.13",
                    "expect_sections": 11,
                    "expect_tools": 101},
    },
}

# The harnesses lib/grip_harness.py can wire (best-effort pre-launch rewire).
#
# Widened 2026-08-19 once EMITTERS actually gained entries for them. The
# previous comment was right to refuse the widening while no emitter existed:
# claiming a wire GRIP cannot perform rewrites nothing and reports success
# (rules/no-stubs.md). That objection is now answered rather than waived —
# every name below has an emitter, and each was probed against the running
# binary and adversarially verified before being added.
#
# Two of the sixteen supported harnesses are deliberately EXCLUDED here,
# because "supported" and "wireable at launch" are different questions:
#   * crush     — configured PER PROJECT (./crush.json); there is no global
#                 file a pre-launch rewire could write.
#   * trueforge — a server; its provider is POSTed to the running instance
#                 after launch, so a pre-launch wire has nothing to talk to.
# Both still answer `grip-harness <name>` by printing their exact steps.
#
# Also absent, and not an oversight: `cursor` is GUI-only (no file), and
# `gemini` speaks a /v1beta protocol HAL 404s. `claude` is absent for a
# different reason — it IS reachable through HAL's /v1/messages facade, but it
# is wired per LAUNCH by environment rather than by a config file: the
# GRIP_INFINITE=1 gateway seam plus the model-family tier map in bin/grip
# (`_grip_family_env`, fed by lib/grip_harness_family.py claude_env). So
# launch_plan("claude") stays {"action": "claude"} and a `--kimi` family is
# honoured on the claude branch of bin/grip, not here.
_WIREABLE = (
    "codex", "aider", "opencode", "agy",
    "hermes", "codewhale", "interpreter", "goose", "kilo",
    "cline", "continue", "pi", "qwen",
)


def effort_argv(name: str, effort: str) -> tuple:
    """Argv that sets reasoning effort on this harness, or () if it cannot.

    Absence is a real answer, not a gap to paper over. A harness with no entry
    is one whose reasoning-effort surface has not been VERIFIED, and passing a
    guessed flag would either crash the launch or — worse — be ignored, leaving
    the operator believing they were running at an effort they were not. That
    silent version is exactly what the refusal in grip_launch_args exists to
    prevent, so an unverified harness keeps refusing rather than pretending.
    """
    spec = HARNESS_REGISTRY.get(name) or {}
    template = spec.get("effort_argv")
    if not template or not effort:
        return ()
    return tuple(part.format(effort=effort) for part in template)


def harnesses_supporting_effort() -> tuple:
    """Names whose reasoning-effort argv is known and verified."""
    return tuple(sorted(
        name for name, spec in HARNESS_REGISTRY.items() if spec.get("effort_argv")
    ))


def _choice_path() -> Path:
    """state/harness-choice.json — resolved at call time ($CLAUDE_DIR may be set
    after import). Gitignored via the existing state/**/*.json rule, so a clone
    never ships a stale remembered choice."""
    base = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
    return base / "state" / "harness-choice.json"


def is_interactive(name: str) -> bool:
    """True when this harness can host an interactive session.

    Defaults to True so every historical entry keeps its behaviour and a new
    entry has to opt OUT deliberately. The flag exists because the registry now
    also carries one-shot tools (fabric, sgpt, metagpt, and the manual-install
    entries): they are worth installing and lib/harness/ dispatches them, but
    offering them in a "which harness do you want to open?" menu would promise a
    session that the binary has no mode for.
    """
    spec = HARNESS_REGISTRY.get(name) or {}
    return bool(spec.get("interactive", True))


def harness_binaries() -> tuple:
    """Process names that constitute a live GRIP session, registry-derived.

    The registry is the single answer to "what counts as a harness", so it also
    owns the derived question "what process names should be looked for". Callers
    that hardcode ``claude`` are exact-name-matched and therefore structurally
    blind to codex, opencode, aider, hermes and agy — a foreign harness then
    fails to be counted, protected, or reported, silently and with no error.

    Scoped to INTERACTIVE entries, which is both the semantics ("a live session")
    and a false-positive guard: callers feed these to ``pgrep -x``, and a
    one-shot tool is never somebody's open session. Public because three separate
    callers need it — the session wizard, the closeout advisory, and the gg++
    concurrency counter — and a private helper copied three times is how the
    drift this fixes started.
    """
    names = {str(spec.get("binary") or "").strip()
             for name, spec in HARNESS_REGISTRY.items() if is_interactive(name)}
    names.discard("")
    return tuple(sorted(names)) or (_CLAUDE,)


def detect_installed(
    which: Optional[Callable[[str], Optional[str]]] = None,
    *,
    include_oneshot: bool = False,
) -> List[str]:
    """Names of installed harnesses, registry order, Claude Code always first.

    `which` is injected (defaults to shutil.which) so tests need no real binaries
    on PATH. Claude is always included — GRIP's own launch path assumes it (and
    bin/grip installs it on demand); the others are included iff their binary
    resolves.

    One-shot tools are excluded by default because this list is what the launch
    menu offers and what ``gg++ <name>`` validates against; ``include_oneshot``
    gives the installer the full installed-or-not picture.
    """
    if which is None:
        import shutil
        which = shutil.which
    out: List[str] = []
    for name, spec in HARNESS_REGISTRY.items():
        if not (include_oneshot or is_interactive(name)):
            continue
        if name == _CLAUDE or which(spec["binary"]):
            out.append(name)
    return out


# --- install specs (consumed by bin/grip-harness-install) -------------------

def install_spec(name: str) -> dict:
    """The verified install spec for *name*, or {} when the entry has none.

    Returns a copy so a caller cannot mutate the registry in place.
    """
    spec = (HARNESS_REGISTRY.get(name) or {}).get("install") or {}
    return dict(spec)


def installable() -> tuple:
    """Names this installer can obtain automatically, in registry order.

    "manual" is a real answer, not a gap: it means no canonical package was found
    to provide that binary. Excluding those here is what keeps the installer from
    running a command nobody verified.
    """
    return tuple(
        name for name in HARNESS_REGISTRY
        if (install_spec(name).get("manager") or "manual") != "manual"
    )


def read_last_choice(path: Optional[Path] = None) -> Optional[str]:
    """The remembered harness name, or None. Fail-safe: missing / malformed /
    unknown-name → None (never raises, never returns a bogus harness)."""
    p = path or _choice_path()
    try:
        name = str(json.loads(p.read_text()).get("harness", "")).strip().lower()
    except (OSError, ValueError):
        return None
    return name if name in HARNESS_REGISTRY else None


def write_choice(name: str, path: Optional[Path] = None) -> bool:
    """Persist the chosen harness (best-effort). Returns True on write, False on
    an unknown name or any I/O error — a failed remember never breaks launch."""
    if name not in HARNESS_REGISTRY:
        return False
    p = path or _choice_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"harness": name, "version": 1}) + "\n")
        return True
    except OSError:
        return False


def _norm_flag(value: object) -> Optional[str]:
    """Map a flag/env value to a known harness name, or None."""
    v = str(value or "").strip().lower()
    return v if v in HARNESS_REGISTRY else None


def launch_plan(name: str) -> dict:
    """Pure map: harness name -> launch directive.

    Returns a dict the shell consumes:
      claude       -> {"action": "claude"}            (fall through to GRIP's flow)
      other        -> {"action": "launch", "argv": [<bin>, ...], "wire": <name|None>}

    argv is the entry's ``launch_argv`` when it declares one, else the bare
    binary — the shape every pre-2026-08-18 entry keeps. bin/grip already
    forwards the extra tokens (``_harness_exec_directive "$a" "$b" $rest`` under
    bash), so a multi-token argv needs no shell change.

    Raises KeyError on an unknown harness (the shell surfaces it loud — never a
    silent mis-launch)."""
    if name not in HARNESS_REGISTRY:
        raise KeyError(name)
    if name == _CLAUDE:
        return {"action": "claude"}
    spec = HARNESS_REGISTRY[name]
    argv = list(spec.get("launch_argv") or (spec["binary"],))
    return {
        "action": "launch",
        "argv": argv,
        "wire": name if name in _WIREABLE else None,
    }


def resolve_choice(
    *,
    flag: Optional[str] = None,
    env: Optional[dict] = None,
    installed: Optional[List[str]] = None,
    is_tty: bool = False,
    last: Optional[str] = None,
) -> dict:
    """Resolve the launch directive WITHOUT any I/O or prompting.

    This is the precedence kernel (pure → fully unit-testable). Returns the same
    directive shape as launch_plan, plus {"action": "prompt", "default": name,
    "options": [...]} when an interactive menu is warranted. The shell runs the
    TUI for the "prompt" action and feeds the result back through `plan`.
    """
    env = os.environ if env is None else env
    installed = detect_installed() if installed is None else installed

    forced = _norm_flag(flag) or _norm_flag(env.get("GRIP_HARNESS"))
    if forced:
        return launch_plan(forced)

    # An EXPLICIT request that resolves to nothing must NEVER degrade to Claude
    # Code. Previously `--harness bogus` and a stale GRIP_HARNESS both returned
    # the normal precedence, so the operator asked for one harness and silently
    # got another -- the exact failure the fail-closed contract forbids. Only an
    # explicit request is fatal; an absent flag and an empty env still fall
    # through, so nothing can brick a bare launch.
    requested = (str(flag or "").strip()
                 or str(env.get("GRIP_HARNESS") or "").strip())
    if requested:
        return {"action": "error", "requested": requested,
                "known": sorted(HARNESS_REGISTRY)}

    others = [n for n in installed if n != _CLAUDE]
    if not others:                      # only Claude → no pointless 1-option menu
        return {"action": "claude"}
    if not is_tty:                      # relay / CI / pipe → safe default
        return {"action": "claude"}

    default = last if (last in installed) else _CLAUDE
    return {"action": "prompt", "default": default, "options": list(installed)}


# --- the menu (lib/grip_tui_menu.py, shared with the model picker) ---------
#
# Everything on screen goes to STDERR; only the chosen name reaches STDOUT,
# because bin/grip captures it with $(). The menu module is imported lazily so a
# `resolve` / `plan` / `detect` call never depends on it.

_PICK_HINT = ("↑↓ move", "Enter open", "Esc opens Claude Code", "Ctrl-C cancels",
              "/ search", "or type a number")


def _supports_ansi() -> bool:
    """True when the live keyboard menu can run (the seam the tests patch)."""
    import grip_tui_menu as tui
    return tui.interactive_ok()


def _menu_items(options: List[str], last: Optional[str]) -> list:
    import grip_tui_menu as tui
    items = []
    for name in options:
        spec = HARNESS_REGISTRY[name]
        items.append(tui.MenuItem(
            key=name, label=spec["label"], detail=spec["blurb"],
            badge="last used" if name == last else "",
            note=f"{spec['label']}: {spec['blurb']}"))
    return items


def _menu_result(options: List[str], verb: str, index: Optional[int],
                 default: str) -> Optional[str]:
    """Map a menu result to a harness name; None means Ctrl-C (launch nothing).
    Esc or q opens Claude Code, as the hint has always said."""
    if verb == "abort":
        return None
    if verb == "choose" and index is not None:
        return options[index]
    return _CLAUDE if _CLAUDE in options else default


def render_and_pick(options: List[str], default: str,
                    last: Optional[str] = None) -> Optional[str]:
    """Interactive picker. Returns the chosen harness name, or None when the
    operator pressed Ctrl-C, in which case the caller launches nothing.

    `last` marks the remembered harness with a "last used" badge. With no
    usable terminal the menu degrades to a plain numbered prompt, where an
    empty line keeps the default."""
    import grip_tui_menu as tui
    if default not in options:
        default = options[0]
    items, start = _menu_items(options, last), options.index(default)
    verb, index = tui.UNAVAILABLE, None
    if _supports_ansi():
        verb, index = tui.run_menu(
            items, title="which harness do you want to open?", cursor=start,
            subtitle=f"{len(options)} installed", hint=_PICK_HINT)
        if verb != tui.UNAVAILABLE:
            chosen = _menu_result(options, verb, index, default)
            tui.announce("cancelled -- nothing launched" if chosen is None
                         else f"opening {HARNESS_REGISTRY[chosen]['label']}")
            return chosen
    verb, index = tui.plain_pick(items, title="Which harness do you want to open?",
                                 default=start)
    return _menu_result(options, verb, index, default)


# --- thin CLI (bin/grip consumes ONE directive line per call) ---------------

def _emit(plan: dict) -> int:
    """Print the shell directive for a plan dict and return an exit code.
    Shapes:  CLAUDE                | LAUNCH <bin> [args...]  (with WIRE=<name>)
             PROMPT <default> <opt...>  (shell then runs `pick`)
             ERROR <requested> <known...>  (explicit request, unknown harness)."""
    action = plan["action"]
    if action == "claude":
        print("CLAUDE")
    elif action == "error":
        print("ERROR %s %s" % (plan["requested"], " ".join(plan["known"])))
        return 2
    elif action == "launch":
        wire = plan.get("wire") or "-"
        print("LAUNCH %s %s" % (wire, " ".join(plan["argv"])))
    elif action == "prompt":
        print("PROMPT %s %s" % (plan["default"], " ".join(plan["options"])))
    return 0


def _build_parser():
    import argparse

    p = argparse.ArgumentParser(prog="grip-harness-picker")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="emit the launch directive (CLAUDE/LAUNCH/PROMPT)")
    r.add_argument("--flag", default=None, help="explicit --harness <name> value")
    r.add_argument("--tty", action="store_true", help="treat stdout as interactive")
    det = sub.add_parser("detect", help="print installed harness names, one per line")
    det.add_argument("--all", action="store_true",
                     help="include one-shot tools that cannot host a session")
    pl = sub.add_parser("plan", help="emit LAUNCH/CLAUDE for an already-chosen name")
    pl.add_argument("name")
    rem = sub.add_parser("remember", help="persist the chosen harness name")
    rem.add_argument("name")
    sub.add_parser("pick", help="run the interactive TUI; print the chosen name")
    eff = sub.add_parser("effort", help="argv that sets reasoning effort, one per line")
    eff.add_argument("name")
    eff.add_argument("level")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.cmd == "detect":
        print("\n".join(detect_installed(include_oneshot=args.all)))
        return 0
    if args.cmd == "effort":
        argv_out = effort_argv(args.name, args.level)
        if not argv_out:
            # Non-zero on "this harness cannot take an effort setting", so a
            # shell caller cannot mistake empty output for success and launch at
            # the harness default while the operator believes otherwise.
            return 1
        print("\n".join(argv_out))
        return 0
    if args.cmd == "resolve":
        last = read_last_choice()
        plan = resolve_choice(flag=args.flag, is_tty=args.tty, last=last)
        return _emit(plan)
    if args.cmd == "plan":
        try:
            return _emit(launch_plan(args.name))
        except KeyError:
            print("UNKNOWN %s" % args.name, file=sys.stderr)
            return 2
    if args.cmd == "remember":
        return 0 if write_choice(args.name) else 1
    if args.cmd == "pick":
        installed = detect_installed()
        last = read_last_choice()
        chosen = render_and_pick(installed, last if last in installed else _CLAUDE,
                                 last=last)
        if chosen is None:
            # Ctrl-C: print nothing and exit 130, the shell's code for an
            # interrupted command. bin/grip then exits 130 and launches nothing.
            return 130
        write_choice(chosen)
        print(chosen)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
