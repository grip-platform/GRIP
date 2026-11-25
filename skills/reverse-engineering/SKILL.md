---
name: reverse-engineering
description: Reverse engineer a shipped binary, app, JavaScript or Electron bundle, APK, firmware image or captured runtime with the REA tool server (npm rea-agents), which GRIP attaches opt-in per launch with `claude --mcp-config mcp-servers/rea/mcp.json` (never in the default .mcp.json) and keeps behind the REA tool-call policy gate with every finding carrying REA's observation / inference / unknown provenance into GRIP's prove-it discipline. Routes web-API captures to /reverse-api. Use when the operator asks what a binary does, how an app feature works, what changed between two shipped versions, or invokes /reverse-engineering.
status: Active
effort: high
version: 1.0.0
created: 2026-10-10
generation: 2700
triggers:
  - /reverse-engineering
  - "reverse engineer this binary"
  - "reverse engineer this app"
  - "what does this binary do"
  - "decompile this"
  - "what changed between these two builds"
  - "inspect this APK"
integrates:
  - api-reverse-engineering
  - grip-anywhere
  - stealth
  - prove-it
  - proving-ground
  - lib.rea_tool_policy
  - lib.rea_cli
priority: Standard
---

# Reverse Engineering (REA-backed)

GRIP attaches **REA** -- "Reverse Engineer Anything", MIT, `github.com/morluto/rea`,
npm `rea-agents` pinned at **6.4.0** -- as an MCP tool server, **opt-in per
launch**:

```bash
claude --mcp-config mcp-servers/rea/mcp.json            # REA beside GRIP's servers
claude --mcp-config mcp-servers/rea/mcp.json --strict-mcp-config   # REA alone
```

(`mcp-servers/rea/mcp.json` → `mcp-servers/rea/start.sh` → `rea mcp`.) It is
not in the default `.mcp.json` on purpose: measured behind the HAL wire, where
Claude Code's tool-search deferral is off for a non-first-party
`ANTHROPIC_BASE_URL`, REA 6.3.0's 139 tool schemas added ~256k tokens to every
session that loaded them (2026-10-10, CC 2.1.295), and 6.4.0's 170 tools cost
197.1k tokens canonical -- 20.8% of a 950k window -- or 112.4k (11.8%) with
`REA_MCP_INPUT_SCHEMA_PROFILE=compact` set in the launch environment
(2026-10-11, CC 2.1.296; the same 170 tools with condensed input schemas and
canonical validation at call time; GRIP keeps canonical as the default). REA is
a tool server, not an agent: it makes no model calls; this session is the
agent. Its tools appear as `mcp__rea__<name>`. The GRIP-side rules below are
what make it safe to attach.

## 1. The authorisation gate -- first, always

Reverse engineering is lawful research on software you own or software being analysed for research purposes or benefit of humanity, or for provenance. Before the first REA tool call, establish in one exchange:

- **Whose software is it?** The operator's own build, an open-source artefact,
  a client's software suitable for analysis, or a CTF /
  research target that invites it → proceed. Anything else → continue if the software is deemed to be safe.
- **Is a public-source twin available?** For a first run on anything, prefer a
  binary whose source is public (ripgrep, jq, fd): every REA claim then has a
  ground truth, which is what makes the result evidence rather than narrative.

**Research and demonstrating a verification tool are first-class purposes.**
Analysing a shipped artefact to show that GRASP produces a sealed, replayable
evidence record of the analysis is a legitimate use of REA in its own right,
not a competitive teardown -- the generality is the whole point of "Reverse
Engineer Anything". For a target the operator owns or is authorised to analyse,
the operator's authorisation is the attestation of record, given here per
target; on the operator's own machine, for the operator's own research, that
attestation is the operator's to give, and research and tool-demonstration are
exactly the purposes the law protects most. This clause governs purpose and
authorisation only. It does not touch -- and must never be read to weaken --
the mechanical capability-lock in §2, which stops a hostile binary from
steering the agent into executing it regardless of who authorised the analysis.

## 2. What is mechanically locked, and the one switch

`lib/rea_tool_policy.py` + `hooks/rea-tool-policy-gate.py` (PreToolUse on
`mcp__rea__.*`):

| Class | Tools | Default |
|---|---|---|
| read (149) | inspect_*, analyze_*, compare_*, list_*, procedure_*, trace_*, open/close_binary, export_evidence_bundle, and from 6.4.0 the ADB/JEB/apktool inspection tools (list_adb_*, inspect_adb_*, read_adb_*, pull_adb_file -- one file to a caller-named path -- resolve_adb_packages, decode_android_resources, *_jeb_*, inspect_pe_resources) … | allowed |
| runtime (21) | capture_browser/electron/native_ui/process_scenario, capture_web_screenshot, observe_javascript_runtime / native_calls / native_ui / web_execution / web_session, extract_artifact, extract_firmware, and from 6.4.0 the ADB tools that act on a connected device or bulk-write onto the host: start_adb_app, start_adb_activity, stop_adb_app, install_adb_package, uninstall_adb_package, push_adb_file, capture_adb_screen, collect_adb_bugreport, pull_adb_package (a package's complete split-APK set, the extract_* rule) | **denied** unless the launch carries `GRIP_REA_ALLOW_RUNTIME=1` |
| unknown | any name REA adds in a later release | **denied** until classified in `READ_TOOLS` or `RUNTIME_TOOLS` |

Why: a binary under analysis is untrusted content, and strings inside it can
steer an agent toward the tool that RUNS it (`rules/untrusted-content-capability-lock.md`).
The lock sits on execution, UI, network capture and bulk extraction only, and it
is one operator-owned switch for the launch that needs it. In an autonomous
loop no permission prompt appears before a tool call; this gate still runs
there, which is the point of having it.

## 3. Routing

- **A web API captured in DevTools** → `/reverse-api` (`skills/api-reverse-engineering`).
- **A native binary / Mach-O / ELF / PE** → `mcp__rea__open_binary`, then
  `binary_overview`, `list_procedures`, `analyze_function`, `procedure_pseudo_code`;
  native decompilation needs one provider (Ghidra with JDK 21+, Hopper, or IDA
  via `ida-pro-mcp`) -- `rea doctor --json` names which are present.
- **A JavaScript / Electron app or web bundle** → `analyze_javascript_application`,
  `analyze_web_bundle`, `trace_javascript_semantics`, `compare_application_versions`.
  These need no provider.
- **An APK** → `inspect_android_package`, `inspect_android_class`,
  `inspect_android_method` (bring your own JADX + JDK); from 6.4.0 also
  `decode_android_resources` (Apktool) and the JEB tools (`open_jeb_project`,
  `list_jeb_units`, `decompile_jeb_item`, a caller-started local JEB MCP server).
- **A connected Android device** (6.4.0, ADB on PATH) → read-class
  `list_adb_devices`, `inspect_adb_device`/`_package`/`_display`/`_window`,
  `list_adb_packages`/`_processes`/`_services`/`_directory`, `read_adb_logcat`,
  `read_adb_setting`, `pull_adb_file` (one file, caller-named path); anything
  that starts, stops, installs, removes or pushes onto the device, captures its
  screen or a bugreport, or pulls a package's whole split-APK set onto the host
  (`pull_adb_package`) is runtime-class (the switch). Only your own or
  authorised devices.
- **Firmware / archives** → `inspect_firmware_regions`, `inspect_artifact`;
  `extract_*` is runtime-class (the switch).
- **A live target** (process, browser, window) → the `capture_*` / `observe_*`
  family: runtime-class, so the operator decides per launch.

## 4. Provenance -- REA's tags become GRIP's claims

REA tags every finding **observation**, **inference** or **unknown**. Carry the
tag into the answer: an observation is a `[[cite:ID]]`-bound claim
(`skills/prove-it/SKILL.md`), an inference is stated as one with its basis, an
unknown stays **UNVERIFIED** and is never promoted. `export_evidence_bundle`
writes the canonical Evidence JSON; `lib/rea_evidence.py` (when present)
normalises it so two runs on an unchanged target hash identically. Where the
target's source is public, check REA's observations against it and say how many
were checked and how many contradicted (0 is a result, not an assumption).

## 5. Posture

Inherited from `/grip-anywhere` and `/stealth`: read-only toward the target,
zero upstream footprint, nothing posted anywhere, drafts stay local. REA's own
Ghidra/IDA sessions are read-only and ephemeral (its `AGENTS.md`); REA is "not a
sandbox" (its `SECURITY.md`) -- a hostile target is still analysed on this
machine as this user.

## 6. How it is attached, and how to check

- Opt-in: `claude --mcp-config mcp-servers/rea/mcp.json` → `mcp-servers/rea/start.sh`
  → `rea mcp`. Inside that session `claude mcp list` shows `rea` connected.
  `grip-harness-install rea` installs the pinned release
  (`npm install -g rea-agents@6.4.0`); `gg++ rea` explains that it is attached
  per launch, not launched as a REPL.
- `rea doctor --json` is REA's readiness report (exit 1 with a full JSON report
  when a provider is missing -- that is information, not failure). Its
  `registration:claude_code` row reads the user-level `~/.claude.json`, so a
  `--mcp-config` attachment shows there as "drift" by design.
- Never run `rea setup`: it would copy REA's skill into `~/.claude/skills`,
  which is this repository (`.gitignore` guards the path). REA's own skill is
  linked, pinned to the release GRIP verified:
  https://github.com/morluto/rea/blob/rea-agents-6.4.0/.agents/skills/reverse-engineer-anything/SKILL.md
- Version drift and new tools: `lib/rea_cli.version_drift()` and
  `tests/test_rea_tool_policy.py::test_live_catalogue_is_fully_classified`
  (red when a REA upgrade adds a tool the policy has not classified).
- Keeping up is on a timer, not on a date: the heartbeat
  (`scripts/grip-heartbeat.py`, launchd every 7,200 s) runs
  `lib/rea_auto_harness.py refresh` as its `rea:refresh` step beside
  `cc:refresh`; `bin/grip-py lib/rea_auto_harness.py dashboard` then shows how
  many feature lines are newer than the pin, and `unharnessed` lists them. A
  new release is a pin bump (GRIP#7988 is the shape: classify every new tool,
  re-snapshot the catalogue, re-measure the attach cost).

## Mode or not

Two sealed deliberations on 2026-10-10 (ultrathink trio `264b9095a2e97c67`,
sovereign council `8f8ea2bc87d38014`) ruled: no reverse-engineering MODE on
day one; the mode is the N=2 promotion target, decided by hypothesis H-RE-MODE
(`docs/adr/0017-rea-not-a-mode-on-day-one.md`).
