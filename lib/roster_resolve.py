"""Does each council rung's declared credential actually resolve, here, now?

A roster rung names where its credential comes from -- an environment variable,
a macOS keychain entry, an OAuth token, a spawned CLI. Naming a source is not
the same as having one, and the gap between the two is invisible by
construction: the rung is present in the roster, the roster length looks right,
and the failure only appears at dispatch time as an empty answer that a fallback
quietly covers.

Four instances of exactly that shape were live on 2026-08-18, and no existing
check could see any of them:

- `OPENAI_API_KEY` was named by the roster and did not exist as a repo secret,
  nor was it passed by the workflow. The rung was filtered out before dispatch.
- `~/.hal`'s tool-CLI path read keychain entry `grip-anthropic`, which does not
  exist, while AGORA's own Anthropic rung reads `grip-anthropic-external`,
  which does.
- A codex config value `apikey` was rejected at load; only `api` is accepted.
- The CCH subscription rung needs the `xxhash` package, which is not installed
  in CI, so the whole rung raised and emptied the roster.

`lib/wiring_perimeter_detector.py` cannot find these. It is an AST detector for
inputs that no code READS -- dead flags and dead parameters. These inputs are
read; they resolve to nothing. Same rule (`declared-input-must-reach-the-actor`),
different detection problem: this one is a runtime question and has to be asked
in the environment that will run the code.

Three states, and the third is the whole point:

    RESOLVES  the declared source yields a value
    ABSENT    the rung declares no source at all, and is dropped before
              dispatch. Honest, and not a defect -- an unset optional secret.
    BROKEN    the rung declares a source that yields nothing. It survives the
              presence filter and fails at dispatch.

plus UNKNOWN, for a probe that could not run. UNKNOWN is deliberately not
folded into RESOLVES: "I could not check" reading the same as "it is fine" is
the failure mode this module exists to close.

KNOWN GAP, stated rather than papered over. In CI an absent rung reports as
"(none declared)" and cannot say WHICH secret it would have needed. That is not
a limitation of this module -- it is a property of the roster it reads.
`_apikey_auth_or_absent` returns a bare `{}` when the environment variable is
unset, discarding the variable's name at exactly the moment that name becomes
the useful thing to know. Closing it means the roster carrying its declared
credential source alongside the resolved one, which is a change to the roster's
shape rather than to this check. Until then, "switched off" covers both a rung
someone deliberately disabled and a rung whose secret nobody ever created, and
those two want telling apart.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional

RESOLVES = "RESOLVES"
ABSENT = "ABSENT"
BROKEN = "BROKEN"
UNKNOWN = "UNKNOWN"

# Packages a rung's transport needs at runtime, keyed by auth scheme. A rung
# can hold a perfectly good credential and still fail because the code that
# would use it cannot import. That is the xxhash case, and it cost the entire
# Claude-subscription path.
_SCHEME_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "cch": ("xxhash",),
}


@dataclass
class RungResolution:
    """One rung's declared credential source and whether it yields anything."""

    rung_id: str
    provider: str
    scheme: str
    source: str            # human-readable declared source, never a value
    status: str
    detail: str = ""
    missing_packages: list[str] = field(default_factory=list)

    @property
    def is_defect(self) -> bool:
        """BROKEN only. ABSENT is a switched-off rung; UNKNOWN is unproven."""
        return self.status == BROKEN


def _keychain_has(service: str) -> tuple[str, str]:
    """Does this macOS keychain entry exist and hold something?

    Returns a (status, detail) pair. Reads with ``-w`` because an entry can
    exist with an empty password, which resolves to nothing just as surely as
    a missing one -- and only the value's LENGTH is ever inspected here, never
    the value itself.
    """
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except FileNotFoundError:
        return UNKNOWN, "no `security` binary — not a macOS keychain host"
    except subprocess.TimeoutExpired:
        return UNKNOWN, "keychain probe timed out"
    if proc.returncode != 0:
        return BROKEN, f"keychain entry `{service}` does not exist"
    if not proc.stdout.strip():
        return BROKEN, f"keychain entry `{service}` exists but is empty"
    return RESOLVES, f"keychain entry `{service}` holds a value"


def _env_has(var: str) -> tuple[str, str]:
    value = os.environ.get(var)
    if value is None:
        return BROKEN, f"${var} is not set"
    if not value.strip():
        return BROKEN, f"${var} is set but empty — an unset repo secret interpolates to this"
    return RESOLVES, f"${var} is set"


def _missing_packages(scheme: str) -> list[str]:
    return [
        pkg for pkg in _SCHEME_REQUIREMENTS.get(scheme, ())
        if importlib.util.find_spec(pkg) is None
    ]


def _probe_source(token: str) -> tuple[str, str, str]:
    """Resolve one ``env:VAR`` / ``keychain:SERVICE`` reference.

    Returns (source_description, status, detail).
    """
    kind, _, name = token.partition(":")
    if kind == "env":
        status, detail = _env_has(name)
        return f"env:{name}", status, detail
    if kind == "keychain":
        status, detail = _keychain_has(name)
        return f"keychain:{name}", status, detail
    return token or "(unspecified)", UNKNOWN, f"unrecognised credential reference `{token}`"


def resolve_rung(rung: tuple) -> RungResolution:
    """Check one roster rung of shape ``(id, provider, auth, description)``."""
    rung_id, provider, auth = rung[0], rung[1], rung[2]
    scheme = (auth or {}).get("scheme") or ""

    if not auth:
        return RungResolution(
            rung_id, provider, scheme="(none)", source="(none declared)",
            status=ABSENT,
            detail="rung declares no credential and is dropped before dispatch",
        )

    missing = _missing_packages(scheme)
    token = auth.get("token", "")

    if scheme == "cch":
        # The CCH rung's presence check is membership on the token env var; the
        # value is read by the spawned CLI, not here. Its real fragility is the
        # import, so that is what gets probed.
        status, detail = _env_has("CLAUDE_CODE_OAUTH_TOKEN")
        source = "env:CLAUDE_CODE_OAUTH_TOKEN"
    else:
        source, status, detail = _probe_source(token)

    if missing and status == RESOLVES:
        status = BROKEN
        detail = (
            f"{detail}, but the `{scheme}` transport needs "
            f"{', '.join(missing)} and it is not importable here"
        )

    return RungResolution(
        rung_id, provider, scheme or "(unset)", source, status, detail,
        missing_packages=missing,
    )


def resolve_roster(roster: list[tuple]) -> list[RungResolution]:
    return [resolve_rung(r) for r in roster]


_MARK = {RESOLVES: "ok  ", ABSENT: "off ", BROKEN: "BAD ", UNKNOWN: "??  "}

_BROKEN_NOTE = (
    "  BROKEN means the rung names a credential source that yields nothing. It passes\n"
    "  the roster's presence filter and fails at dispatch, where a fallback hides it."
)
_UNKNOWN_NOTE = (
    "  UNCHECKED is not a pass. These could not be probed from here — re-run in the\n"
    "  environment that will actually dispatch them."
)


def _render_table(results: list[RungResolution]) -> list[str]:
    """One line per rung, plus the reason on anything that did not resolve."""
    width = max((len(r.rung_id) for r in results), default=10)
    lines = []
    for r in results:
        lines.append(f"  {_MARK.get(r.status, '??  ')} {r.rung_id:<{width}}  {r.source}")
        if r.status != RESOLVES:
            lines.append(f"       {' ' * width}  {r.detail}")
    return lines


def tally(results: list[RungResolution]) -> dict[str, int]:
    """How many rungs landed in each state."""
    return {
        state: sum(1 for r in results if r.status == state)
        for state in (RESOLVES, ABSENT, BROKEN, UNKNOWN)
    }


def _render_summary(results: list[RungResolution]) -> list[str]:
    counts = tally(results)
    lines = [
        "",
        f"  {counts[RESOLVES]} resolve · {counts[ABSENT]} switched off · "
        f"{counts[BROKEN]} declared but broken · {counts[UNKNOWN]} unchecked",
    ]
    for count, note in ((counts[BROKEN], _BROKEN_NOTE), (counts[UNKNOWN], _UNKNOWN_NOTE)):
        if count:
            lines.extend(["", note])
    return lines


def render(results: list[RungResolution], *, where: Optional[str] = None) -> str:
    """A table plus a verdict line. Names sources, never values."""
    header = [f"resolving credentials as they would resolve {where}\n"] if where else []
    return "\n".join(header + _render_table(results) + _render_summary(results))
