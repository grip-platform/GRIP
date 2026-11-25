"""Short-lived GitHub App installation tokens for one repository.

``bin/grip-chain-sync`` pushes the public decision chain. Pushed with a person's own
credential, every sync names that person's account on the repository's public
activity page. A GitHub App installed on that one repository pushes as the App
instead. This module turns the App's id and private key into an installation
access token limited to that repository and to ``contents: write``; GitHub makes
it expire after one hour.

The configuration is a JSON file, by default ``~/.config/grip/chain-app/app.json``::

    {"app_id": 123, "owner": "Org", "repo": "name",
     "key": "~/.config/grip/chain-app/private-key.pem"}

Every failure of ``app_jwt``, ``installation_token`` and ``load_config`` raises
``AppTokenError``, and the command line then exits 1 with nothing on stdout: the
caller must refuse to push rather than fall back to a personal credential. On success the command line prints the token and nothing
else, so a shell can capture it::

    python -m lib.github_app_token [config.json]
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

API = "https://api.github.com"
DEFAULT_CONFIG = Path.home() / ".config" / "grip" / "chain-app" / "app.json"
REQUIRED_KEYS = ("app_id", "owner", "repo", "key")
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "grip-chain-sync",
}


class AppTokenError(RuntimeError):
    """No App token could be produced. Never fall back to another credential."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _compact(obj: dict) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode()


def app_jwt(app_id: int | str, pem: bytes, now: int | None = None) -> str:
    """An RS256 JSON Web Token that authenticates as the App for ten minutes.

    ``iat`` is backdated 60 s against clock drift, and ``exp`` is nine minutes
    ahead, inside GitHub's ten-minute ceiling."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        raise AppTokenError("cannot sign: the cryptography package is not installed") from None

    now = int(time.time()) if now is None else now
    signing_input = ".".join((
        _b64url(_compact({"alg": "RS256", "typ": "JWT"})),
        _b64url(_compact({"iat": now - 60, "exp": now + 540, "iss": str(app_id)})),
    ))
    try:
        key = serialization.load_pem_private_key(pem, password=None)
        signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    except Exception as exc:  # not a PEM, not a private key, not an RSA key
        raise AppTokenError(f"App key unusable: {type(exc).__name__}") from None
    return f"{signing_input}.{_b64url(signature)}"


def _call(method: str, path: str, bearer: str, payload: dict | None = None,
          opener=urllib.request.urlopen) -> dict:
    """One GitHub API call; any transport, HTTP or JSON failure raises
    AppTokenError naming the method, the path and the status, never a secret."""
    data = None if payload is None else json.dumps(payload).encode()
    headers = {**_HEADERS, "Authorization": f"Bearer {bearer}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with opener(req, timeout=30) as resp:
            reply = json.load(resp)
    except Exception as exc:  # HTTPError, URLError, timeout, a reply that is not JSON
        status = getattr(exc, "code", "")
        raise AppTokenError(f"{method} {path} failed: {type(exc).__name__} {status}".rstrip()) from None
    if not isinstance(reply, dict):
        raise AppTokenError(f"{method} {path} returned a {type(reply).__name__}, not an object")
    return reply


def _signed_jwt(cfg: dict, now: int | None) -> str:
    """The App's JWT, or AppTokenError when the config or the key is unusable.
    app_jwt's own AppTokenError passes through unchanged, so its reason survives."""
    try:
        pem = _key_bytes(Path(cfg["key"]).expanduser())
        app_id = cfg["app_id"]
    except AppTokenError:
        raise
    except Exception as exc:  # KeyError for a missing field, OSError for the key file
        raise AppTokenError(f"App config or key file unusable: {type(exc).__name__}") from None
    return app_jwt(app_id, pem, now)


def _key_bytes(path: Path) -> bytes:
    """The App's private key, refused when another account could read it or does
    not own it: whoever can read the key can mint tokens for the repository.
    POSIX permission bits only; Windows carries no such bits and is not checked."""
    st = path.stat()
    if os.name != "nt":
        if st.st_mode & 0o077:
            raise AppTokenError(f"App key {path} is readable by other accounts "
                                f"(mode {oct(st.st_mode & 0o777)}); chmod 600 it")
        if st.st_uid != os.getuid():
            raise AppTokenError(f"App key {path} is not owned by this account")
    return path.read_bytes()


_GRANTABLE = frozenset({"contents", "metadata"})


def _check_scope(reply: dict, repo: str) -> None:
    """Refuse a token GitHub does not confirm is as narrow as the one requested.
    The reply must grant ``contents: write`` and nothing more (``metadata: read``
    always comes with it) and must list exactly the configured repository; a
    reply without that list cannot show the limit was applied, so the token is
    not used."""
    perms = reply.get("permissions") or {}
    if perms.get("contents") != "write" or set(perms) - _GRANTABLE:
        raise AppTokenError("GitHub granted more than contents: write; refusing the token")
    repos = reply.get("repositories")
    only = repos[0] if isinstance(repos, list) and len(repos) == 1 else None
    if not (isinstance(only, dict) and only.get("name") == repo):
        raise AppTokenError("GitHub did not confirm the token is limited to the one repository; refusing it")


def installation_token(cfg: dict, opener=urllib.request.urlopen, now: int | None = None) -> str:
    """An installation token for the configured repository, ``contents: write`` only."""
    jwt = _signed_jwt(cfg, now)
    owner, repo = cfg.get("owner"), cfg.get("repo")
    if not (owner and repo):
        raise AppTokenError("App config lacks owner or repo")
    installation = _call("GET", f"/repos/{owner}/{repo}/installation", jwt, opener=opener)
    if not installation.get("id"):
        raise AppTokenError("GitHub returned no installation id")
    reply = _call("POST", f"/app/installations/{installation['id']}/access_tokens", jwt,
                  {"repositories": [repo], "permissions": {"contents": "write"}}, opener=opener)
    token = reply.get("token")
    if not token:
        raise AppTokenError("GitHub returned no token")
    _check_scope(reply, repo)
    return token


def load_config(path: Path | str) -> dict:
    """The App configuration, or AppTokenError when it is unreadable or incomplete."""
    try:
        cfg = json.loads(Path(path).expanduser().read_text())
    except (OSError, ValueError) as exc:
        raise AppTokenError(f"cannot read App config {path}: {type(exc).__name__}") from None
    if not isinstance(cfg, dict):
        raise AppTokenError(f"App config {path} is not a JSON object")
    missing = [k for k in REQUIRED_KEYS if not cfg.get(k)]
    if missing:
        raise AppTokenError(f"App config {path} lacks {', '.join(missing)}")
    return cfg


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else DEFAULT_CONFIG
    try:
        token = installation_token(load_config(path))
    except Exception as exc:  # report and refuse; the shell caller then refuses to push
        print(f"github_app_token: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
