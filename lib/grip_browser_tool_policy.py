"""grip-browser tool-call policy -- the deterministic floor under the attachment.

stealth-browser-mcp (vibheksoni, MIT; pinned sha ba95cca9, 101 tools in 11
sections) is attached to a GRIP session opt-in, per launch, as the stdio MCP
server ``grip-browser`` (``claude --mcp-config mcp-servers/grip-browser/mcp.json``,
never in the default ``.mcp.json``). It drives a Chrome that renders pages the
operator did not write: UNTRUSTED CONTENT, and a page can carry instructions
aimed at the agent. ``rules/untrusted-content-capability-lock.md`` therefore
requires the capability surface to be locked by ALLOWLIST, with an unknown
name degrading to fewer capabilities, never more.

The lock classifies every call by EFFECT, not by upstream section, because
the two councils that reviewed the plan (seals 87c8d0d6ddde124e and
cff4c84b2a918a1f) found the name-only design unsound twice: ``get_instance_state``
(browser-management) returns cookies plus local and session storage, so
denying the three cookie tools alone never stopped credential disclosure; and
any oversized result is written to disk by upstream's response handler, so an
"observe" tool performs a host write. Hence six classes, per-argument rules on
the tools whose arguments decide their effect, and a context that says whether
a persistent profile (an identity to spend) is configured.

- ``observe`` (59): read the page, the DOM, the network listing, the server's
  own state. Allowed. ``take_screenshot`` carries a path rule: a ``file_path``
  under the artefact root is required, so a screenshot never lands inline in
  the transcript (``rules/visual-artifact-leak-prevention.md``: pixels are a
  leak surface no gate reads).
- ``interact`` (19): spawn, navigate, click, type, scroll, tabs. Allowed in
  EPHEMERAL mode -- there is no identity to spend, and ``rules/full-power-stop-
  override.md`` says keep the power and add a brake -- with ``spawn_browser``'s
  arguments pinned and ``navigate``/``new_tab`` held to http(s) on a public
  host. Once a persistent profile is configured, a click is an action on the
  operator's account, so the class needs ``GRIP_BROWSER_ALLOW_INTERACT=1``.
- ``credential`` (5): ``get_instance_state``, the three cookie tools and
  ``modify_headers`` (it can set ``Cookie:`` and ``Authorization:`` on every
  request). Denied unless ``GRIP_BROWSER_ALLOW_COOKIES=1``. In persistent mode
  the three network detail tools join this class, because a logged-in
  session's requests carry its tokens.
- ``code-exec`` (10): page JavaScript, raw CDP, page bindings, upstream's
  Python-in-browser. Denied unless ``GRIP_BROWSER_ALLOW_CODE=1``.
- ``host-file`` (4): ``file_upload`` reads host files (upstream confines it
  to ``BROWSER_FILE_UPLOAD_ALLOWED_DIRS``, which the launcher pins to the
  artefact root; the path rule here is the second lock); the two network
  exports and the debug export write host paths with no allowlist upstream, so
  they need ``GRIP_BROWSER_ALLOW_FILES=1`` AND a path under the root.
- ``server-admin`` (4): ``hot_reload`` reloads server modules from disk;
  ``create_dynamic_hook`` / ``create_simple_dynamic_hook`` run agent-supplied
  Python on the host (upstream's ``dynamic_hook_system.py`` ``exec``/``eval``,
  which its authors say is not a sandbox); ``validate_hook_function`` is
  UNVERIFIED to only compile. No switch in this version.
- ``unknown``: every tool a later upstream adds. Denied until classified here
  and marked in the keep-up ledger.

Classification source (2026-10-18): the ``@section_tool`` decorators in
``src/server.py`` at ba95cca9 (``tests/fixtures/grip-browser/tool-names-0.2.5.json``),
the argument names read from the same signatures, and the effects read from
``browser_manager.py`` (``get_page_state`` fills cookies and storage),
``response_handler.py`` (the file fallback) and ``network_interceptor.py``
(the export paths). Target authorisation -- whose pages, what may be
submitted -- is the conduct gate in ``skills/browse/SKILL.md``, a judgement a
URL check cannot make; this module checks only what a URL check can.

Built on ``lib/mcp_tool_policy.py`` (the engine REA's lock will move onto at
N=3). Pure: stdlib only, no I/O; ``context_from_env`` is the one function
that reads the environment, and it reads the private configuration through
``lib/grip_browser_config.py``.
"""
from __future__ import annotations

import ipaddress
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import mcp_tool_policy as engine  # noqa: E402

SERVER = "grip-browser"
PREFIX = "mcp__grip-browser__"
MODULE = "lib/grip_browser_tool_policy.py"
CATALOGUE = "stealth-browser-mcp 0.2.5+ba95cca9"

COOKIES_SWITCH = "GRIP_BROWSER_ALLOW_COOKIES"
CODE_SWITCH = "GRIP_BROWSER_ALLOW_CODE"
FILES_SWITCH = "GRIP_BROWSER_ALLOW_FILES"
INTERACT_SWITCH = "GRIP_BROWSER_ALLOW_INTERACT"
LOCAL_SWITCH = "GRIP_BROWSER_ALLOW_LOCAL"

OBSERVE_TOOLS = frozenset({
    # browser-management
    "list_instances",
    # element-interaction
    "query_elements", "get_element_state", "wait_for_element", "get_page_content",
    "take_screenshot",
    # network-debugging (read-only listings; the three detail tools move to
    # credential in persistent mode, see reclassify)
    "list_network_requests", "get_request_details", "get_response_details",
    "get_response_content", "search_network_requests", "get_network_capture_filters",
    # debugging (read-only, plus clearing the server's own debug buffer)
    "get_debug_view", "clear_debug_view", "get_debug_lock_status", "reload_status",
    "validate_browser_environment_tool",
    # tabs
    "list_tabs", "get_active_tab",
    # element-extraction
    "extract_element_styles", "extract_element_structure", "extract_element_events",
    "extract_element_animations", "extract_element_assets", "extract_element_styles_cdp",
    "extract_related_files", "clone_element_complete", "extract_complete_element_cdp",
    # progressive-cloning (a server-side store of extracted elements)
    "clone_element_progressive", "expand_styles", "expand_events", "expand_children",
    "expand_css_rules", "expand_pseudo_elements", "expand_animations",
    "list_stored_elements", "clear_stored_element", "clear_all_elements",
    # file-extraction: server-named files in the server-owned clone directory
    "clone_element_to_file", "extract_complete_element_to_file",
    "extract_element_styles_to_file", "extract_element_structure_to_file",
    "extract_element_events_to_file", "extract_element_animations_to_file",
    "extract_element_assets_to_file", "list_clone_files",
    # cdp-functions: discovery only
    "list_cdp_commands", "get_execution_contexts", "discover_global_functions",
    "discover_object_methods", "inspect_function_signature", "get_page_binding_calls",
    "get_function_executor_info",
    # dynamic-hooks: listings and documentation
    "list_dynamic_hooks", "get_dynamic_hook_details", "get_hook_documentation",
    "get_hook_examples", "get_hook_requirements_documentation", "get_hook_common_patterns",
})

INTERACT_TOOLS = frozenset({
    "spawn_browser", "navigate", "go_back", "go_forward", "reload_page", "close_instance",
    "click_element", "type_text", "press_key", "scroll_page", "select_option", "paste_text",
    "switch_tab", "close_tab", "new_tab",
    "set_network_capture_filters", "cleanup_clone_files", "remove_page_binding",
    "remove_dynamic_hook",
})

CREDENTIAL_TOOLS = frozenset({
    "get_instance_state",   # cookies + localStorage + sessionStorage (VERIFIED)
    "get_cookies", "set_cookie", "clear_cookies",
    "modify_headers",       # can set Cookie: / Authorization: on every request
})

# Join the credential class when a persistent profile is configured.
PERSISTENT_CREDENTIAL_TOOLS = frozenset({
    "get_request_details", "get_response_details", "get_response_content",
})

CODE_EXEC_TOOLS = frozenset({
    "execute_script", "execute_cdp_command", "inject_and_execute_script",
    "call_javascript_function", "execute_function_sequence", "create_persistent_function",
    "add_script_to_evaluate_on_new_document", "execute_python_in_browser",
    "create_page_binding", "resolve_page_binding_call",
})

HOST_FILE_TOOLS = frozenset({
    "file_upload", "export_network_data", "import_network_data", "export_debug_logs",
})

SERVER_ADMIN_TOOLS = frozenset({
    "hot_reload", "create_dynamic_hook", "create_simple_dynamic_hook", "validate_hook_function",
})

CLASSES = {
    "observe": OBSERVE_TOOLS,
    "interact": INTERACT_TOOLS,
    "credential": CREDENTIAL_TOOLS,
    "code-exec": CODE_EXEC_TOOLS,
    "host-file": HOST_FILE_TOOLS,
    "server-admin": SERVER_ADMIN_TOOLS,
}
KNOWN_TOOLS = frozenset().union(*CLASSES.values())

CLASS_NOTES = {
    "credential": "reads or sets cookies, storage or request credentials",
    "code-exec": "runs code in the page, over raw CDP or in upstream's Python-in-browser",
    "host-file": "reads or writes a host path outside what the launcher confines",
    "server-admin": "reloads the server or runs agent-supplied Python on the host",
    "interact": "acts on the page",
}

SWITCHES = {
    "credential": COOKIES_SWITCH,
    "code-exec": CODE_SWITCH,
    "host-file": FILES_SWITCH,
    "interact": INTERACT_SWITCH,
}

FORBIDDEN_HEADERS = frozenset({"cookie", "authorization", "proxy-authorization"})
LOCAL_HOSTNAMES = frozenset({"localhost", "0.0.0.0", "broadcasthost", "ip6-localhost", "ip6-loopback"})


# --- helpers ----------------------------------------------------------------------

def _realpath(value) -> str:
    return os.path.realpath(os.path.expanduser(str(value)))


def _under_root(value, context: dict) -> bool:
    root = context.get("artefact_root")
    if not root or value in (None, ""):
        return False
    target = Path(_realpath(value))
    try:
        target.relative_to(Path(_realpath(root)))
    except ValueError:
        return False
    return True


def _path_reason(tool: str, key: str, value, context: dict) -> str | None:
    if value in (None, ""):
        return (f"[GRIP] {SERVER} tool '{tool}' needs a {key} under the artefact root "
                f"{context.get('artefact_root')}; a result that lands in the transcript "
                f"or outside the root is refused ({MODULE}).")
    if not isinstance(value, str) or not _under_root(value, context):
        return (f"[GRIP] {SERVER} tool '{tool}' {key}={value!r} is outside the artefact root "
                f"{context.get('artefact_root')} after realpath; only paths under it are "
                f"allowed ({MODULE}).")
    return None


def _is_local_host(host: str) -> bool:
    name = host.strip("[]").lower().rstrip(".")
    if not name:
        return True
    if name in LOCAL_HOSTNAMES or name.endswith(".localhost") or name.endswith(".local"):
        return True
    try:
        addr = ipaddress.ip_address(name.split("%", 1)[0])
    except ValueError:
        return False
    return (addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_unspecified
            or addr.is_multicast or addr.is_reserved)


def url_reason(tool: str, value, context: dict, *, key: str = "url") -> str | None:
    """http(s) on a public host, unless the operator opened local targets.
    A hostname that RESOLVES to a private address (DNS rebinding) is beyond a
    gate and is stated as a limit in skills/browse/SKILL.md."""
    if not isinstance(value, str) or not value.strip():
        return f"[GRIP] {SERVER} tool '{tool}' needs a {key} string ({MODULE})."
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in ("http", "https"):
        return (f"[GRIP] {SERVER} tool '{tool}' {key} scheme {parts.scheme or '(none)'!r} is "
                f"refused; only http and https are rendered (file:, javascript:, data: and "
                f"chrome: are not) ({MODULE}).")
    host = parts.hostname or ""
    if _is_local_host(host) and not context.get("allow_local"):
        return (f"[GRIP] {SERVER} tool '{tool}' {key} host {host!r} is loopback, private, "
                f"link-local or local; refused unless the launch carries {LOCAL_SWITCH}=1 "
                f"({MODULE}).")
    return None


# --- argument rules ---------------------------------------------------------------

def _spawn_rule(args: dict, context: dict) -> str | None:
    profile = args.get("user_data_dir")
    configured = context.get("profile_dir")
    if profile not in (None, ""):
        if not configured or _realpath(profile) != _realpath(configured):
            return (f"[GRIP] {SERVER} spawn_browser user_data_dir={profile!r} is not the configured "
                    f"profile ({configured or 'none: ephemeral mode'}); only the profile in "
                    f"config/grip-browser.local.json may be opened ({MODULE}).")
    browser_args = args.get("browser_args")
    if browser_args not in (None, [], ()):
        return (f"[GRIP] {SERVER} spawn_browser browser_args must be empty: a --user-data-dir, "
                f"--remote-debugging-port or --proxy-server there would bypass every other rule "
                f"({MODULE}).")
    proxy = args.get("proxy")
    if proxy not in (None, "") and proxy != context.get("proxy"):
        return (f"[GRIP] {SERVER} spawn_browser proxy={proxy!r} is not the configured proxy; "
                f"configure it in config/grip-browser.local.json ({MODULE}).")
    if args.get("sandbox") is False:
        return f"[GRIP] {SERVER} spawn_browser sandbox=false is refused ({MODULE})."
    headers = args.get("extra_headers") or {}
    if not isinstance(headers, dict):
        return f"[GRIP] {SERVER} spawn_browser extra_headers must be an object ({MODULE})."
    bad = sorted(k for k in headers if str(k).lower() in FORBIDDEN_HEADERS)
    if bad:
        return (f"[GRIP] {SERVER} spawn_browser extra_headers may not set {', '.join(bad)}: "
                f"that is a credential, class credential ({MODULE}).")
    return None


def _navigate_rule(args: dict, context: dict) -> str | None:
    reason = url_reason("navigate", args.get("url"), context)
    if reason:
        return reason
    referrer = args.get("referrer")
    if referrer not in (None, ""):
        return url_reason("navigate", referrer, context, key="referrer")
    return None


def _new_tab_rule(args: dict, context: dict) -> str | None:
    url = args.get("url")
    return None if url in (None, "") else url_reason("new_tab", url, context)


def _screenshot_rule(args: dict, context: dict) -> str | None:
    return _path_reason("take_screenshot", "file_path", args.get("file_path"), context)


def _upload_rule(args: dict, context: dict) -> str | None:
    paths = args.get("paths")
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, (list, tuple)) or not paths:
        return f"[GRIP] {SERVER} file_upload needs a non-empty list of paths under the artefact root ({MODULE})."
    for path in paths:
        reason = _path_reason("file_upload", "path", path, context)
        if reason:
            return reason
    return None


def _export_rule(key: str):
    def rule(args: dict, context: dict, _key=key) -> str | None:
        return _path_reason("export", _key, args.get(_key), context)
    return rule


def _interact_context_rule(context: dict) -> str | None:
    if context.get("persistent_profile") and "interact" not in (context.get("open_classes") or ()):
        return (f"[GRIP] {SERVER} interaction is denied while a persistent profile is configured: "
                f"a click is then an action on the operator's account. Launch with "
                f"{INTERACT_SWITCH}=1 to allow it ({MODULE}).")
    return None


def _reclassify(tool: str, context: dict) -> str | None:
    if tool in PERSISTENT_CREDENTIAL_TOOLS and context.get("persistent_profile"):
        return "credential"
    return None


ARGUMENT_RULES = {
    "spawn_browser": _spawn_rule,
    "navigate": _navigate_rule,
    "new_tab": _new_tab_rule,
    "take_screenshot": _screenshot_rule,
    "file_upload": _upload_rule,
    "export_network_data": _export_rule("filepath"),
    "import_network_data": _export_rule("filepath"),
    "export_debug_logs": _export_rule("filename"),
}

POLICY = engine.Policy(
    server=SERVER,
    prefix=PREFIX,
    module=MODULE,
    catalogue=CATALOGUE,
    classes=CLASSES,
    default_allow=frozenset({"observe", "interact"}),
    switches=SWITCHES,
    class_notes=CLASS_NOTES,
    argument_rules=ARGUMENT_RULES,
    context_rules={"interact": _interact_context_rule},
    # file_upload is confined by its path rule and by upstream's own allowlist,
    # which the launcher pins to the root; the FILES switch covers the three
    # exports, whose paths upstream does not confine.
    switch_exempt=frozenset({"file_upload"}),
    reclassify=_reclassify,
)


# --- the public surface -------------------------------------------------------------

def bare_name(tool: str) -> str:
    return engine.bare_name(POLICY, tool)


def tool_class(tool: str, context: dict | None = None) -> str:
    return engine.tool_class(POLICY, tool, context)


def context_from_env(environ: dict | None, config=None, artefact_root=None) -> dict:
    """The context the gate passes: which classes the operator opened, whether
    local targets are allowed, the configured profile and proxy, the artefact
    root. ``config`` and ``artefact_root`` are injectable for tests; by default
    they come from ``lib/grip_browser_config.py`` and the launch surface."""
    env = dict(environ or {})
    if config is None:
        import grip_browser_config
        config = grip_browser_config.read(env)
    if artefact_root is None:
        import grip_browser_surface
        artefact_root = config.scratch_dir or grip_browser_surface.scratch_dir(env)
    return {
        engine.OPEN_CLASSES: engine.open_classes_from_env(POLICY, env),
        "allow_local": env.get(LOCAL_SWITCH) == "1",
        "persistent_profile": config.profile_dir is not None,
        "profile_dir": str(config.profile_dir) if config.profile_dir else None,
        "proxy": config.proxy,
        "artefact_root": str(artefact_root),
    }


def evaluate(tool: str, args: dict | None, context: dict | None) -> engine.Verdict:
    return engine.evaluate(POLICY, tool, args, context)


def is_legal_tool_call(tool: str, args: dict, context: dict) -> bool:
    """The AutoHarness-shaped predicate. "Legal" means allowed by this
    effect-class policy under ``context``; it is NOT the authorisation
    judgement the skill's conduct gate makes about whose page it is."""
    return engine.is_legal(POLICY, tool, args, context)


def deny_reason(tool: str, args: dict | None = None, context: dict | None = None) -> str:
    """The sentence the gate prints: what was refused, and the way out."""
    return evaluate(tool, args, context).reason
