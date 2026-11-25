"""The activation seam: which of the one folder's four parts a deployment runs.

Plain English: the one-folder product ships GRIP, HAL, HAPPI and GRASP together
(the gift kit, item B1). Not every recipient runs every part on day one. An
ActivationProfile names the parts that are ACTIVE for one deployment; the
orchestrator dispatches only those, and a dormant part is never asked to start,
so it starts no process, opens no socket, writes no file, calls no model and
spends nothing. Switching a part off changes the dispatch list and nothing else:
no part's own code needs an edit. That sentence is the hypothesis this module
rests on (plan item B3, falsifier: a part whose code must change, by 2026-11-08).

Three rules are not a profile's to decide, and the dataclass refuses them at
construction (the one-folder product plan, decisions 1 and 2 of 2026-10-18):

* GRIP is always active. A profile without GRIP is not a profile of this product.
* HAPPI needs HAL. HAPPI is the protocol HAL speaks; alone it is a document.
* No profile can disable a PARAMOUNT gate. The seven hooks in UNDISABLEABLE_HOOKS
  are the security floor every install carries (GRIP#8570); a profile that names
  one as disabled is refused, whatever else it says. The spend refusals are not
  part of a profile at all: lib/openai_billing.py and HAL's spend guard take no
  profile input, and tests/test_activation_profile.py pins that they stay that way.

Activation leaves a birth certificate: a signed decision record (the same
PrecogIDR every GRIP decision uses) naming the deployment, the active parts and
the floor, so a later reader can see what this installation was told to be and
check that nothing was quietly widened. Modelled on grasp/activate.py, whose
ActivationConfig refuses at construction and whose activate() signs an IDR.

This module is the seam, not the questionnaire: the life-wide activation
questionnaire (decision 5) produces a profile; this module consumes one.
"""
from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

PARTS: tuple[str, ...] = ("grip", "hal", "happi", "grasp")
ALWAYS_ACTIVE: frozenset[str] = frozenset({"grip"})
REQUIRES: dict[str, frozenset[str]] = {"happi": frozenset({"hal"})}

# The security floor every install carries; the same seven as
# tests/test_install_profile_security_floor.py pins into the install profile.
UNDISABLEABLE_HOOKS: frozenset[str] = frozenset({
    "guardrail-evasion-gate.py",
    "sensitive-info-guard.py",
    "no-stubs-gate.py",
    "measured-claim-gate.py",
    "rea-tool-policy-gate.py",
    "gitcrypt-staged-guard.py",
    "gitcrypt-filter-canary.py",
})

KIND = "grip-activation"


class ActivationError(ValueError):
    """A profile this product refuses, with the reason in plain words."""


@dataclass(frozen=True)
class ActivationProfile:
    """One deployment's answer to "which parts run here".

    `name` is a short slug for the deployment; `active` the parts that run;
    `disabled_hooks` the hook files this deployment asks to leave unbound, which
    may never include one of UNDISABLEABLE_HOOKS. Validation happens once, at
    construction, so an invalid profile cannot exist to be dispatched.
    """

    name: str
    active: frozenset[str]
    disabled_hooks: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("-", "").replace("_", "").isalnum():
            raise ActivationError(
                f"a profile needs a short name of letters, digits, '-' or '_', not {self.name!r}")
        unknown = sorted(set(self.active) - set(PARTS))
        if unknown:
            raise ActivationError(
                f"unknown part(s) {', '.join(unknown)}; the parts are {', '.join(PARTS)}")
        missing_always = sorted(ALWAYS_ACTIVE - set(self.active))
        if missing_always:
            raise ActivationError(
                f"{', '.join(missing_always)} is always active: a profile without it is not a "
                "profile of this product")
        for part, needs in REQUIRES.items():
            if part in self.active and not needs <= set(self.active):
                raise ActivationError(
                    f"{part} needs {', '.join(sorted(needs))} active: it is the protocol HAL "
                    "speaks, and alone it is a document")
        floor = sorted(set(self.disabled_hooks) & UNDISABLEABLE_HOOKS)
        if floor:
            raise ActivationError(
                f"no profile can disable a PARAMOUNT gate; {', '.join(floor)} stay bound in every "
                "install (the security floor, GRIP#8570)")

    def is_active(self, part: str) -> bool:
        return part in self.active

    def dispatch_list(self) -> tuple[str, ...]:
        """The active parts, in the product's fixed order."""
        return tuple(part for part in PARTS if part in self.active)


def dispatch(profile: ActivationProfile, starters: Mapping[str, Callable[[], object]]) -> tuple[str, ...]:
    """Start the active parts, in order, and return their names.

    `starters` maps a part name to the callable that initialises it. A dormant
    part's callable is never looked up, let alone called, so a profile that leaves
    a part out needs no starter for it. An active part with no starter is an
    error: a profile cannot activate what the deployment cannot start.
    """
    started: list[str] = []
    for part in profile.dispatch_list():
        if part not in starters:
            raise ActivationError(f"{part} is active in profile {profile.name!r} but has no starter")
        starters[part]()
        started.append(part)
    return tuple(started)


def fingerprint(profile: ActivationProfile) -> str:
    """A short, stable digest of the profile's content, for the certificate."""
    body = f"{profile.name}\0{','.join(profile.dispatch_list())}\0{','.join(sorted(profile.disabled_hooks))}"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def birth_certificate(profile: ActivationProfile, *, path: Path | None = None):
    """Sign and append the deployment's activation as a decision record.

    Returns the PrecogIDR. `path` names the chain file (tests pass a temporary
    one; None appends to the deployment's own chain). The record carries the
    active parts and the undisableable floor, so what this installation was
    told to be is checkable later against what it runs.
    """
    from lib.precog.idr import append_idr, build_idr

    idr = build_idr(
        prompt="grip activate",
        fingerprint=fingerprint(profile),
        decision={
            "what": "deployment activation",
            "why": "the operator chose which of the one folder's four parts run here",
            "how": "lib.activation_profile.birth_certificate",
            "profile": profile.name,
            "active": list(profile.dispatch_list()),
            "dormant": [part for part in PARTS if part not in profile.active],
            "disabled_hooks": sorted(profile.disabled_hooks),
            "undisableable_hooks": sorted(UNDISABLEABLE_HOOKS),
        },
        predecessor_idr=None,
        depth=0,
        kind=KIND,
    )
    append_idr(idr, path=path)
    return idr
