"""Provider API-key resolver — one ladder that works on Mac dev AND Linux/CI.

Syscall-doctrine shim for the "read a provider secret" boundary. GRIP's
pure-stdlib sovereign council/demo tools shelled straight out to
``security find-generic-password`` (macOS Keychain) with ``check=True``, so they
HARD-FAILED on Linux/CI where no Keychain exists — which is exactly why the
flagship "live non-Anthropic AI council review in CI" step could not go green.
This resolver adds the CI-side sources the Keychain-only lookup was missing, in
one deterministic ladder, so a SINGLE committed tool runs unchanged in both
environments.

Resolution ladder (first hit wins; the key is never printed or logged):

  1. explicit provider env var     e.g. ``DEEPSEEK_API_KEY``       (what CI sets)
  2. GRIP-namespaced env var       e.g. ``GRIP_DEEPSEEK_API_KEY``
  3. macOS Keychain (darwin only)  service ``grip-deepseek``
  4. Linux ``/proc/*/environ``     a sibling process carrying the GRIP-namespaced
                                   var (VPS: the hal-server pm2 process env)

This mirrors ``lib/hal_council_dispatch._token`` (env-first, keychain-fallback)
and the inline ladder in ``bin/grip-overnight-wave._opencode_env`` — extracted to
ONE place now that N>=3 callsites need it (YSH: pattern proven 3x -> abstract).

Every seam (env, platform, keychain reader, /proc reader) is injectable so the
resolver is fully unit-testable with zero subprocess and zero filesystem access.
"""
from __future__ import annotations

import glob as _glob
import os
import subprocess
import sys
from typing import Callable, Iterable, Mapping, Optional

__all__ = [
    "ProviderKeyError",
    "resolve_provider_key",
    "resolve_provider_key_or_raise",
    "inject_provider_key",
    "env_candidates",
]

# Providers whose canonical env-var stem differs from the provider name.
# (Alibaba Qwen authenticates against the DashScope key.)
_ENV_STEM: dict[str, str] = {"qwen": "DASHSCOPE", "dashscope": "DASHSCOPE"}


class ProviderKeyError(RuntimeError):
    """No API key could be resolved for a provider — carries a plain-language fix."""


def _provider_of(service: str) -> str:
    """``grip-deepseek`` -> ``deepseek``; a bare ``deepseek`` passes through."""
    s = service.strip().lower()
    return s[len("grip-"):] if s.startswith("grip-") else s


def _keychain_service(service: str) -> str:
    """The macOS Keychain service name (always the ``grip-<provider>`` form)."""
    s = service.strip().lower()
    return s if s.startswith("grip-") else f"grip-{s}"


def env_candidates(service: str) -> list[str]:
    """Ordered, de-duplicated env-var names to try for *service* (explicit first).

    e.g. ``grip-deepseek`` -> ``['DEEPSEEK_API_KEY', 'GRIP_DEEPSEEK_API_KEY']``;
    ``grip-qwen`` -> ``['DASHSCOPE_API_KEY', 'GRIP_DASHSCOPE_API_KEY',
    'GRIP_QWEN_API_KEY']``.
    """
    provider = _provider_of(service)
    # A hyphen is legal in a keychain service (`grip-kimi-coding`) but not in a
    # POSIX env-var name, so it becomes an underscore here or the env rung can
    # never answer for that provider on Linux/CI.
    provider_env = provider.upper().replace("-", "_")
    stem = _ENV_STEM.get(provider, provider_env)
    ordered = [f"{stem}_API_KEY", f"GRIP_{stem}_API_KEY", f"GRIP_{provider_env}_API_KEY"]
    seen: set[str] = set()
    return [n for n in ordered if not (n in seen or seen.add(n))]


def _from_env(names: Iterable[str], env: Mapping[str, str]) -> Optional[str]:
    for name in names:
        val = (env.get(name) or "").strip()
        if val:
            return val
    return None


def _read_keychain(service: str) -> Optional[str]:
    """macOS Keychain read inside this process (key never printed). None on miss."""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _first_key_in_environ(raw: bytes, wanted: set[bytes]) -> Optional[str]:
    """Return the first ``NAME=value`` in one NUL-delimited environ blob whose
    NAME is in *wanted*. Single scan, hash lookup per entry."""
    for entry in raw.split(b"\x00"):
        name, sep, val = entry.partition(b"=")
        if sep and name in wanted:
            try:
                decoded = val.decode("utf-8").strip()
            except UnicodeDecodeError:
                # A non-UTF8 value cannot be a valid (ASCII) provider key — skip it
                # rather than silently corrupting it with U+FFFD replacements.
                continue
            if decoded:
                return decoded
    return None


def _scan_proc(names: Iterable[str], proc_glob: Callable[[str], list[str]]) -> Optional[str]:
    """Linux fallback: scan sibling ``/proc/*/environ`` for any of *names*.

    Used on the VPS, where a provider key lives in the hal-server pm2 process
    environment rather than in this process's own env. Per-blob scanning is
    delegated to :func:`_first_key_in_environ` (coordinator pattern — one loop each).
    """
    wanted = {n.encode() for n in names}  # hash lookup, not a per-entry name loop
    for environ_path in proc_glob("/proc/*/environ"):
        try:
            with open(environ_path, "rb") as fh:  # context-managed: FD closed even on error
                raw = fh.read()
        except OSError:
            continue
        hit = _first_key_in_environ(raw, wanted)
        if hit:
            return hit
    return None


def resolve_provider_key(
    service: str,
    *,
    env: Optional[Mapping[str, str]] = None,
    platform: Optional[str] = None,
    keychain_reader: Optional[Callable[[str], Optional[str]]] = None,
    proc_glob: Optional[Callable[[str], list[str]]] = None,
    allow_proc_scan: bool = False,
) -> Optional[str]:
    """Resolve a provider API key via the env -> keychain ladder.

    *service* is a keychain-style name (``grip-deepseek``) or a bare provider
    (``deepseek``). Returns the key string, or ``None`` if no source has it.
    The key is never printed or logged. All seams are injectable for testing.

    The Linux ``/proc/*/environ`` rung is **opt-in** (``allow_proc_scan=True``)
    and OFF by default: it reads sibling same-UID process environments, so it
    only belongs to the deliberate VPS case (a key living in the hal-server pm2
    process env), never to CI (where the env rung always answers first) or Mac
    dev (Keychain). Defaulting it off keeps the resolver from ever reaching into
    another process's secrets unless a caller explicitly asks for that behaviour.
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    names = env_candidates(service)

    hit = _from_env(names, env)
    if hit:
        return hit
    if platform == "darwin":
        return (keychain_reader or _read_keychain)(_keychain_service(service))
    if allow_proc_scan:
        return _scan_proc(names, proc_glob or _glob.glob)
    return None


def inject_provider_key(
    env_map: dict,
    service: str,
    *,
    target_var: Optional[str] = None,
    allow_proc_scan: bool = False,
) -> dict:
    """Set *service*'s resolved credential into *env_map* under its canonical
    variable name (``env_candidates(service)[0]`` or *target_var*), if absent
    and resolvable. Returns *env_map*; a no-op otherwise. The variable name is
    derived here, so callers need not spell it out at the call site.
    """
    var = target_var or env_candidates(service)[0]
    if env_map.get(var):
        return env_map
    resolved = resolve_provider_key(service, env=env_map, allow_proc_scan=allow_proc_scan)
    if resolved:
        env_map[var] = resolved
    return env_map


def resolve_provider_key_or_raise(service: str, **kwargs) -> str:
    """Like :func:`resolve_provider_key` but raise a plain-language error on miss."""
    key = resolve_provider_key(service, **kwargs)
    if key:
        return key
    provider = _provider_of(service)
    primary = env_candidates(service)[0]
    # Only mention the Keychain where it is actually consulted (darwin) — on
    # Linux/CI it never runs, so naming it would misdirect the reader.
    platform = kwargs.get("platform") or sys.platform
    keychain_hint = (
        f" or add the macOS Keychain entry '{_keychain_service(service)}'"
        if platform == "darwin" else ""
    )
    raise ProviderKeyError(
        f"No API key for '{provider}'. Set {primary} "
        f"(or GRIP_{provider.upper()}_API_KEY) in the environment{keychain_hint}."
    )
