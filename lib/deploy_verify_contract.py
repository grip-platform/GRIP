"""Verification-contract schema for the self-verifying deploy loop (WS2-W1).

Origin: GH #3023 WS2-W1. A machine-checkable contract a deploy must
satisfy to pass verification — the same contract a human would use to
say "is the deploy working?".

Three endpoint types:
- ``health_check`` — GET, expect 200 + body contains substring.
- ``content_check`` — GET, expect 200 + body matches expected text.
- ``isolation_check`` — GET with scoped credential, expect 403/401
  (tenant A cannot read tenant B's resource; a 200 means BROKEN).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CheckKind = Literal["health_check", "content_check", "isolation_check"]


@dataclass(frozen=True)
class VerificationEndpoint:
    """One endpoint the probe engine must verify."""
    url: str
    kind: CheckKind
    expected_substring: str = ""
    credential_tag: str = ""

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("VerificationEndpoint.url must be non-empty")


@dataclass(frozen=True)
class VerificationContract:
    """Machine-checkable contract for one deploy.

    ``hypothesis_id`` MUST be registered BEFORE deploy (C3).
    """
    deploy_id: str
    hypothesis_id: str = ""
    live_endpoints: tuple[VerificationEndpoint, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.deploy_id:
            raise ValueError("VerificationContract.deploy_id must be non-empty")


class ContractError(ValueError):
    """Raised when a contract or endpoint fails validation."""


def _check_endpoints_unique(contract: VerificationContract) -> None:
    seen: set[str] = set()
    for ep in contract.live_endpoints:
        if not ep.url:
            raise ContractError(f"endpoint url must be non-empty")
        if ep.url in seen:
            raise ContractError(f"duplicate endpoint url {ep.url!r}")
        seen.add(ep.url)


def _check_isolation_credentials(contract: VerificationContract) -> None:
    for ep in contract.live_endpoints:
        if ep.kind == "isolation_check" and not ep.credential_tag:
            raise ContractError(
                f"isolation_check {ep.url!r} must have credential_tag"
            )


def validate_contract(contract: VerificationContract) -> None:
    """Validate a ``VerificationContract``. Raises ``ContractError``.

    Checks: deploy_id non-empty, hypothesis_id non-empty, at least one
    endpoint, no duplicate URLs, isolation_check endpoints have credentials.
    """
    if not contract.hypothesis_id:
        raise ContractError(
            f"{contract.deploy_id!r}: hypothesis_id required"
        )
    if not contract.live_endpoints:
        raise ContractError(f"{contract.deploy_id!r}: no endpoints")
    _check_endpoints_unique(contract)
    _check_isolation_credentials(contract)


def serialize_contract(contract: VerificationContract) -> dict:
    """Deterministic JSON-safe dict."""
    return {
        "deploy_id": contract.deploy_id,
        "hypothesis_id": contract.hypothesis_id,
        "live_endpoints": [
            {
                "url": ep.url,
                "kind": ep.kind,
                "expected_substring": ep.expected_substring,
                "credential_tag": ep.credential_tag,
            }
            for ep in contract.live_endpoints
        ],
    }


def deserialize_contract(data: dict) -> VerificationContract:
    """Reconstitute a ``VerificationContract`` from the dict form."""
    try:
        deploy_id = data["deploy_id"]
        hypothesis_id = data.get("hypothesis_id", "")
        raw = data.get("live_endpoints", [])
        endpoints = tuple(
            VerificationEndpoint(
                url=ep["url"],
                kind=ep["kind"],
                expected_substring=ep.get("expected_substring", ""),
                credential_tag=ep.get("credential_tag", ""),
            )
            for ep in raw
        )
    except (KeyError, TypeError) as exc:
        raise ContractError(f"malformed contract data: {exc}") from exc
    return VerificationContract(
        deploy_id=deploy_id,
        hypothesis_id=hypothesis_id,
        live_endpoints=endpoints,
    )
