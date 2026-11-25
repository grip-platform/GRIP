"""REA tool-call policy -- the deterministic floor under GRIP's REA attachment.

REA (npm ``rea-agents``, github.com/morluto/rea) is attached to a GRIP session
opt-in, per launch, as a stdio MCP server (``claude --mcp-config
mcp-servers/rea/mcp.json`` -> ``mcp-servers/rea/start.sh``; never in the
default ``.mcp.json``, GRIP#7964).
Its tools analyse binaries, apps, bundles and captures that are UNTRUSTED
CONTENT: strings inside a hostile binary can steer the agent toward a tool
that runs that binary, opens it in a browser, screenshots a live window or
bulk-extracts an archive onto disk. ``rules/untrusted-content-capability-lock.md``
therefore requires the capability surface to be locked by ALLOWLIST, with an
unknown name degrading to fewer capabilities, never more.

``rules/full-power-stop-override.md`` pulls the other way, and both hold here:
inspection tools read bytes the agent can already read with ``cat``, ``nm`` or
``otool``, so they are NOT gated; the lock sits on execution and bulk writes,
and it is one operator-owned switch (``GRIP_REA_ALLOW_RUNTIME=1``), not a
buried permission prompt. Target authorisation (own or authorised targets
only) is the conduct rule in ``skills/reverse-engineering/SKILL.md`` -- a
legal judgement a path check cannot make.

Classification source (2026-10-10, rea-agents 6.3.0, ``rea mcp`` tools/list,
139 tools): REA's own MCP annotations and ``rea capabilities --json``. Only 7
tools carry ``destructiveHint`` and three of those (``close_binary``,
``unset_bookmark``, ``export_evidence_bundle``) are destructive to the
ANALYSIS SESSION or an output file the agent chose, not to the target, so they
stay in the read class. The runtime class is the ``capture_*`` and
``observe_*`` families (they run the target, a browser or a UI session; REA's
capability table marks them ``may_show_ui`` / ``may_access_network``) plus the
``extract_*`` pair (bulk filesystem writes derived from untrusted archives).

rea-agents 6.4.0 (2026-10-11; 170 tools, 31 added, none removed) brought ADB
device tooling, JEB, apktool and PE-resource inspection. REA sets
``readOnlyHint`` false on all 31, so their own descriptions decide: the nine
that start, stop, install, uninstall or push onto a connected device, capture
its screen or a bugreport, or pull a package's complete split-APK set into a
host directory (a bulk write of untrusted archives -- the ``extract_*`` rule;
Codex review of GRIP#7988) join the runtime class, because they act on a live
device the way ``capture_*`` acts on a live target or write the way
``extract_*`` writes; the 22 that inspect, list, read, resolve, decode or
decompile join the read class. ``pull_adb_file`` stays read: it writes ONE file
to a caller-named path, the ``export_evidence_bundle`` shape, not a tree whose
names come from the target. ``decode_android_resources`` decodes into a
provider-owned workspace REA removes afterwards and returns structured data,
so no host tree the agent controls is written; it stays read.

This module is exec-safe on purpose: stdlib only, no I/O, no ``__file__``, so
``lib/auto_harness.py::evaluate_harness`` can exec its source against the
labelled examples in ``tests/fixtures/rea_tool_policy_examples.jsonl`` -- the
first real consumer of that module. The signature of ``is_legal_tool_call``
is the one ``auto_harness.HarnessSpec.function_sig`` names.
"""
from __future__ import annotations

PREFIX = "mcp__rea__"
RUNTIME_SWITCH = "GRIP_REA_ALLOW_RUNTIME"

# Tools that run the target, a browser, an Electron app or a live UI session,
# or bulk-extract an untrusted archive onto disk. Denied unless the operator
# set RUNTIME_SWITCH=1 for the launch.
RUNTIME_TOOLS = frozenset({
    "capture_browser_scenario",
    "capture_electron_scenario",
    "capture_native_ui_scenario",
    "capture_process_scenario",
    "capture_web_screenshot",
    "observe_javascript_runtime",
    "observe_native_calls",
    "observe_native_ui",
    "observe_web_execution",
    "observe_web_session",
    "extract_artifact",
    "extract_firmware",
    # rea-agents 6.4.0: ADB tools that act on a connected device. REA's own
    # descriptions call push/stop/uninstall "device-mutating"; start_* launch
    # an app; install_* writes an APK onto the device; the screen capture and
    # the bugreport run device-side; pull_adb_package writes a package's
    # complete split-APK set into a host directory (bulk writes of untrusted
    # archives, the extract_* rule -- Codex review of GRIP#7988).
    "capture_adb_screen",
    "collect_adb_bugreport",
    "install_adb_package",
    "pull_adb_package",
    "push_adb_file",
    "start_adb_activity",
    "start_adb_app",
    "stop_adb_app",
    "uninstall_adb_package",
})

# REA marks these destructiveHint=true, but what they destroy is an analysis
# session's bookmark/handle or an output file the agent named -- the same
# class as Write. Listed so the exclusion is a decision, not an omission.
SESSION_MUTATING_TOOLS = frozenset({
    "close_binary",
    "unset_bookmark",
    "export_evidence_bundle",
})

# Every other tool REA 6.3.0 advertised (inspection, analysis, comparison,
# navigation and evidence-ledger tools), then the 6.4.0 additions below.
# Allowed with no switch.
READ_TOOLS = frozenset({
    "address_name", "address_to_file_offset",
    "analyze_function", "analyze_javascript_application", "analyze_swift_types",
    "analyze_web_bundle", "annotate_native_function", "batch_decompile",
    "binary_overview", "binary_session", "build_call_path",
    "build_reconstruction_obligation_ledger", "close_binary", "comment",
    "compare_application_versions", "compare_artifacts", "compare_bundles",
    "compare_functions", "compare_javascript_export_shapes",
    "compare_managed_members", "compare_process_captures",
    "compare_source_to_bundle", "compare_web_captures", "compare_web_screenshots",
    "correlate_static_and_runtime", "current_address", "current_document",
    "current_procedure", "decode_interface_builder", "demangle_swift",
    "discover_webmcp_tools", "evaluate_reconstruction_coverage",
    "export_evidence_bundle", "export_web_scripts", "find_changed_behavior",
    "find_xrefs_to_name", "get_call_graph", "get_evidence_bundle",
    "get_navigation_context", "get_objc_classes", "get_objc_protocols",
    "goto_address", "import_evidence_bundle", "import_managed_reconstruction",
    "inline_comment", "inspect_address_context", "inspect_analysis_view",
    "inspect_android_class", "inspect_android_method", "inspect_android_package",
    "inspect_artifact", "inspect_asset_catalog", "inspect_binary_layout",
    "inspect_electron_page", "inspect_evm_interface", "inspect_firmware_regions",
    "inspect_keyed_archive", "inspect_macho", "inspect_managed_artifact",
    "inspect_managed_members", "inspect_managed_native_boundaries",
    "inspect_native_api", "inspect_native_data_type",
    "inspect_native_dispatch_metadata", "inspect_native_instruction",
    "inspect_native_load_image", "inspect_plist", "inspect_recorded_crash",
    "inspect_signature", "inspect_web_event_listeners",
    "inspect_web_network_capture", "inspect_web_page", "list_architectures",
    "list_bookmarks", "list_browser_targets", "list_documents",
    "list_electron_targets", "list_javascript_runtime_targets", "list_names",
    "list_procedures", "list_segments", "list_strings", "list_unknowns",
    "next_address", "open_binary", "prev_address", "procedure_address",
    "procedure_assembly", "procedure_callees", "procedure_callers",
    "procedure_info", "procedure_pseudo_code", "procedure_references",
    "project_android_application_graph", "project_apple_application_graph",
    "project_managed_application_graph", "read_bytes",
    "read_function_instructions", "reconcile_javascript_runtime",
    "record_unknown", "recover_javascript_sources",
    "resolve_containing_procedure", "resolve_native_call_targets",
    "search_android_classes", "search_procedures", "search_strings",
    "set_address_name", "set_addresses_names", "set_bookmark", "set_comment",
    "set_inline_comment", "trace_android_references",
    "trace_application_feature", "trace_call_path", "trace_dylib_resolution",
    "trace_feature", "trace_javascript_semantics", "trace_native_ui_action",
    "trace_native_values", "trace_web_module_imports",
    "trace_web_source_location", "unset_bookmark", "update_unknown",
    "verify_managed_native_boundaries", "verify_reconstruction",
    "verify_unknown_resolution", "xrefs",
    # rea-agents 6.4.0 (2026-10-11): device and tool inspection, JEB and
    # apktool analysis, PE resources -- they read bytes or device state and
    # launch nothing. pull_adb_file writes one file to a caller-named path
    # (the export_evidence_bundle shape); pull_adb_package is runtime, above.
    "decode_android_resources", "decompile_jeb_item", "inspect_adb_client",
    "inspect_adb_device", "inspect_adb_display", "inspect_adb_package",
    "inspect_adb_window", "inspect_apktool_client", "inspect_jeb_client",
    "inspect_pe_resources", "list_adb_devices", "list_adb_directory",
    "list_adb_features", "list_adb_packages", "list_adb_processes",
    "list_adb_services", "list_jeb_units", "open_jeb_project", "pull_adb_file",
    "read_adb_logcat", "read_adb_setting", "resolve_adb_packages",
})

KNOWN_TOOLS = READ_TOOLS | RUNTIME_TOOLS


def bare_name(tool: str) -> str:
    """``mcp__rea__open_binary`` and ``open_binary`` name the same tool."""
    name = (tool or "").strip()
    return name[len(PREFIX):] if name.startswith(PREFIX) else name


def tool_class(tool: str) -> str:
    """One of ``read`` (allowed), ``runtime`` (switch-gated), ``unknown`` (denied)."""
    name = bare_name(tool)
    if name in RUNTIME_TOOLS:
        return "runtime"
    if name in READ_TOOLS:
        return "read"
    return "unknown"


def allow_runtime_from_env(environ: dict) -> bool:
    """The operator's switch: exactly the string ``1``, nothing looser."""
    return (environ or {}).get(RUNTIME_SWITCH) == "1"


def is_legal_tool_call(tool: str, args: dict, context: dict) -> bool:
    """The predicate ``auto_harness`` scores. ``context['allow_runtime']`` is
    the operator switch resolved by the caller; ``args`` is unused today and
    kept for the AutoHarness signature (a future per-argument rule plugs in
    here without changing callers).

    "Legal" here means allowed by this tool-classification policy -- read-class,
    runtime-class behind the operator switch, or an unknown name denied. It is
    NOT the authorisation/lawfulness judgement the module docstring delegates to
    the skill's conduct gate: whether the target is the operator's own or
    authorised is a question a path check cannot answer, and this predicate does
    not attempt it."""
    kind = tool_class(tool)
    if kind == "read":
        return True
    if kind == "runtime":
        return bool((context or {}).get("allow_runtime"))
    return False


def deny_reason(tool: str) -> str:
    """The sentence the gate prints: what was refused, and the way out."""
    name = bare_name(tool)
    if tool_class(tool) == "runtime":
        return (
            f"[GRIP] REA tool '{name}' runs a target, a browser or a live UI "
            f"session, or bulk-extracts an untrusted archive. It is denied "
            f"unless the operator launches with {RUNTIME_SWITCH}=1 "
            f"(lib/rea_tool_policy.py RUNTIME_TOOLS)."
        )
    return (
        f"[GRIP] REA tool '{name}' is not classified in lib/rea_tool_policy.py "
        f"(rea-agents 6.4.0 catalogue). An unknown capability degrades to "
        f"fewer capabilities, never more: classify it there (READ_TOOLS or "
        f"RUNTIME_TOOLS) and the call is allowed on the next session."
    )
