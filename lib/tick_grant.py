"""lib/tick_grant.py: the operator's grant for one autonomous act of the always-on tick.

A grant is a small JSON record the operator signs at a terminal with ``bin/grip-tick-grant``.
Its Ed25519 signing key lives in a macOS keychain item created with an EMPTY trusted-app
list, so every read of it prompts the user, and no background process can sign silently.
The store runs through a pseudo-terminal, so the secret is typed to ``security(1)`` and never
appears in a process's arguments. The tick verifies a grant with the VERIFYING key as
PUBLISHED on the repository's main branch (fetched through ``gh api`` once an hour and
cached in the tick's state), which only a reviewed, merged pull request can change; the
copy in the checkout, ``config/tick/grant-verifying.pub``, is what ``issue`` reads, and a
checkout whose copy differs from the published key is reported. The first commit of that key
is the operator's own pull request, under the bootstrap carve-out of
``rules/exogenous-anchor-law.md``. No published key, no valid grant, no act.

What a grant binds: the act, its triggers, its limits, when it was issued and when it
expires, a nonce, and the fingerprint of the key that may verify it. A copied grant
authorises nothing new. A grant that fails verification is a finding for the operator,
never a permission. Every grant the tick has seen is logged in its state and listed in
the daily digest, so a grant the operator did not create is visible within a day.

Two floors, stated honestly. The terminal check (``init`` and ``issue`` refuse without a
TTY) only stops the careless case; the keychain prompt is the presence check that holds.
And when GitHub cannot be reached the tick keeps the last published key it fetched, and
with none ever fetched it verifies nothing, so an outage closes the gate rather than
opening it.

Exit codes: 0 done, 1 a grant did not verify (``show``), 2 refused or could not run.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

_LIB = Path(__file__).resolve().parent
for _p in (_LIB, _LIB.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from claude_dir import get_claude_dir  # noqa: E402
from jcs import dumps as canonical_dumps  # noqa: E402  RFC 8785, verifier-first

SERVICE = "com.grip.tick.grant"
ACCOUNT = "signing-key"
KNOWN_ACTS = ("checkpoint", "throttle", "experiment")
VERSION = 1
MIN_DAYS = 1 / 1440
MAX_DAYS = 30
DEFAULT_TRIGGERS = ("fleet:hot", "fleet:stuck")
TRIGGERS_BY_ACT = {"checkpoint": DEFAULT_TRIGGERS, "throttle": ("spend:cap",), "experiment": ("hyp:overdue",)}
DEFAULT_SOFT_CAP = 0.8  # the share of the weekly Claude limit at which the throttle flag is raised (V>> 2026-10-29)
HYPOTHESIS_ID = re.compile(r"H\d{3,6}")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
PIN_REPO = os.environ.get("GRIP_TICK_PIN_REPO", "grip-platform/GRIP")
PIN_PATH = "config/tick/grant-verifying.pub"
PIN_REFRESH_S = 3600
STORE_TIMEOUT_S = 60
Run = Callable[..., "subprocess.CompletedProcess[str]"]
Fetch = Callable[[], Optional[bytes]]


class GrantError(RuntimeError):
    """A grant could not be made, stored or read; the message is for the operator."""


class CryptoMissing(GrantError):
    """The interpreter has no ``cryptography``; nothing can be signed or verified."""


def _run(run: Optional[Run]) -> Run:
    """The subprocess runner, looked up at call time so a test can stand in for the keychain and for gh."""
    return run if run is not None else subprocess.run


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    except ImportError as exc:
        raise CryptoMissing("cryptography is not installed in this interpreter") from exc
    return serialization, Ed25519PrivateKey, Ed25519PublicKey


def verifying_key_path() -> Path:
    return get_claude_dir() / "config" / "tick" / "grant-verifying.pub"


def grants_dir() -> Path:
    return get_claude_dir() / "state" / "grip-tick" / "grants"


# ----------------------------------------------------------------------------- keys

def generate_keypair() -> Tuple[bytes, bytes]:
    """(signing key PEM, verifying key PEM), both unencrypted; the signing half goes to the keychain only."""
    serialization, Ed25519PrivateKey, _ = _crypto()
    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
    return private_pem, _public_pem(key.public_key())


def _public_pem(public_key) -> bytes:
    serialization, _, _ = _crypto()
    return public_key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def _public(public_pem: bytes):
    serialization, _, Ed25519PublicKey = _crypto()
    try:
        key = serialization.load_pem_public_key(public_pem)
    except Exception as exc:  # noqa: BLE001  any unreadable PEM is one answer for the operator
        raise GrantError("the verifying key is not a readable PEM public key") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise GrantError("the verifying key is not an Ed25519 key")
    return key


def _private(private_pem: bytes):
    serialization, Ed25519PrivateKey, _ = _crypto()
    try:
        key = serialization.load_pem_private_key(private_pem, password=None)
    except Exception as exc:  # noqa: BLE001
        raise GrantError("the signing key is not a readable PEM private key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise GrantError("the signing key is not an Ed25519 key")
    return key


def public_of(private_pem: bytes) -> bytes:
    """The verifying key that belongs to a signing key, to check the keychain against the pin."""
    return _public_pem(_private(private_pem).public_key())


def fingerprint(public_pem: bytes) -> str:
    """The first sixteen hex digits of the SHA-256 of the raw verifying key."""
    serialization, _, _ = _crypto()
    raw = _public(public_pem).public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


PROMPT_LIMIT = 128  # characters the `security add-generic-password -w` prompt keeps; the rest is cut silently (measured 2026-10-29)


def _prompt_fit(secret: str) -> None:
    """Refuse a secret the keychain prompt would cut, because the cut is silent and the exit status is 0."""
    if len(secret) > PROMPT_LIMIT:
        raise GrantError(f"the keychain prompt keeps {PROMPT_LIMIT} characters and this secret has {len(secret)}")


def _store_via_pty(secret: str, timeout_s: float = STORE_TIMEOUT_S) -> None:
    """Type the secret to ``security add-generic-password -w`` on a pseudo-terminal, so it never reaches argv.

    Measured on macOS 26 (2026-10-29): the command asks ``password data for new item:`` and then
    ``retype password for new item:``; both are answered and the exit status is checked.
    """
    _prompt_fit(secret)
    import pty
    import select
    pid, fd = pty.fork()
    if pid == 0:  # the child: argv carries no secret
        os.execvp("security", ["security", "add-generic-password", "-U", "-a", ACCOUNT, "-s", SERVICE, "-T", "", "-w"])
    answered, buf, deadline = 0, b"", time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 1)
            if not ready:
                continue
            try:
                data = os.read(fd, 1024)
            except OSError:
                break
            if not data:
                break
            buf += data
            low = buf.lower()
            if answered == 0 and b"password data for new item" in low or answered == 1 and b"retype" in low:
                os.write(fd, secret.encode("ascii") + b"\n")
                answered, buf = answered + 1, b""
        else:
            os.kill(pid, 9)
            raise GrantError("the keychain did not take the secret within the time allowed")
    finally:
        os.close(fd)
    _, status = os.waitpid(pid, 0)
    if os.waitstatus_to_exitcode(status) != 0 or answered != 2:
        raise GrantError(f"keychain store failed (exit {os.waitstatus_to_exitcode(status)}, prompts answered {answered})")


def keychain_store(private_pem: bytes, store: Optional[Callable[[str], None]] = None) -> None:
    """Put the signing key in the login keychain with no trusted app, so every read asks the user.

    Stored as the base64 of the 32-byte seed (44 characters): the keychain's prompt keeps only
    PROMPT_LIMIT characters, and the base64 PEM (160) it held before was cut silently.
    """
    serialization, _, _ = _crypto()
    seed = _private(private_pem).private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                               serialization.NoEncryption())
    (store or _store_via_pty)(base64.b64encode(seed).decode("ascii"))


def keychain_load(run: Optional[Run] = None) -> bytes:
    """The signing key PEM from the keychain; the keychain asks the user before it answers."""
    done = _run(run)(["security", "find-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w"], capture_output=True, text=True)
    if done.returncode != 0:
        raise GrantError("the keychain refused or holds no signing key: run grip-tick-grant init at a terminal")
    try:
        blob = base64.b64decode((done.stdout or "").strip(), validate=True)
    except ValueError as exc:
        raise GrantError("the keychain item is not base64") from exc
    return _pem_from_keychain_blob(blob)


def _pem_from_keychain_blob(blob: bytes) -> bytes:
    """The PKCS8 PEM for what the keychain holds: a 32-byte seed (stored since 2026-10-29), a whole PEM,
    or a PEM the prompt cut at PROMPT_LIMIT characters, whose 64-character DER body still survives."""
    serialization, Ed25519PrivateKey, _ = _crypto()

    def pem(key) -> bytes:
        return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption())

    if len(blob) == 32:
        return pem(Ed25519PrivateKey.from_private_bytes(blob))
    if blob.startswith(b"-----BEGIN"):
        lines = blob.decode("ascii", "replace").splitlines()[1:]
        body = "".join(line.strip() for line in lines if re.fullmatch(r"[A-Za-z0-9+/=]+", line.strip()))
        try:
            return pem(serialization.load_der_private_key(base64.b64decode(body, validate=True), password=None))
        except Exception as exc:  # noqa: BLE001
            raise GrantError("the signing key is not a readable PEM private key") from exc
    raise GrantError("the keychain item holds neither a key seed nor a PEM")


def read_verifying_key(path: Optional[Path]) -> Optional[bytes]:
    """The checkout's copy of the verifying key, or None when there is none."""
    try:
        data = path.read_bytes() if path is not None else b""
    except OSError:
        return None
    return data if data.strip() else None


def fetch_published_key(run: Optional[Run] = None) -> Optional[bytes]:
    """The verifying key as it stands on the repository's main branch, through gh; None when unreachable."""
    done = _run(run)(["gh", "api", f"repos/{PIN_REPO}/contents/{PIN_PATH}?ref=main", "--jq", ".content"],
                     capture_output=True, text=True)
    if done.returncode != 0:
        return None
    try:
        pem = base64.b64decode("".join((done.stdout or "").split()), validate=True)
    except ValueError:
        return None
    return pem if pem.strip() else None


def pinned_key(cache: dict, now: float, fetch: Optional[Fetch] = None) -> Tuple[Optional[bytes], str]:
    """The published verifying key, refreshed once an hour and kept across outages; (None, why) when none is known."""
    if now - float(cache.get("fetched_at") or 0.0) >= PIN_REFRESH_S:
        pem = (fetch or fetch_published_key)()
        if pem is not None:
            cache.update(fetched_at=now, pem_b64=base64.b64encode(pem).decode("ascii"),
                         sha256=hashlib.sha256(pem).hexdigest())
        else:
            cache["failed_at"] = now
    if cache.get("pem_b64"):
        return base64.b64decode(cache["pem_b64"]), "ok"
    return None, "the published verifying key could not be fetched and none is cached"


# ----------------------------------------------------------------------------- grants

def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime(TIME_FORMAT)


def _epoch(value: object) -> Optional[float]:
    try:
        return datetime.strptime(str(value), TIME_FORMAT).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _throttle_limits(soft_cap: Optional[float]) -> dict:
    cap = DEFAULT_SOFT_CAP if soft_cap is None else float(soft_cap)
    pct = int(round(cap * 100))  # a whole percent: the canonical signer takes no float
    if not 0 < cap < 1 or not 1 <= pct <= 99:
        raise GrantError("the soft cap is a share of the weekly limit strictly between 0 and 1, and a whole "
                         "percent from 1 to 99 once rounded (0.004 would be 0 and fire on every reading, "
                         "0.999 would be 100 and never fire)")
    return {"soft_cap_pct": pct}


def _experiment_limits(experiments: Optional[Dict[str, str]]) -> dict:
    """The approved manifests by hypothesis id: at least one, each an engine id bound to a manifest's SHA-256."""
    if not experiments:
        raise GrantError("an experiment grant names at least one hypothesis (--hypothesis H857)")
    for hid, sha in experiments.items():
        if not HYPOTHESIS_ID.fullmatch(str(hid)) or not SHA256_HEX.fullmatch(str(sha)):
            raise GrantError(f"{hid!r} is not an engine id bound to a manifest's SHA-256")
    return {"experiments": dict(sorted(experiments.items()))}


def _checkpoint_limits(max_mb: int) -> dict:
    if max_mb <= 0:
        raise GrantError("the size limit must be a positive number of megabytes")
    return {"max_mb": int(max_mb)}


def payload(act: str, days: float, max_mb: int, triggers: Tuple[str, ...], now: float,
            soft_cap: Optional[float] = None, experiments: Optional[Dict[str, str]] = None) -> dict:
    """The unsigned grant body; refuses an unknown act, a bad limit, or a span under a minute or over MAX_DAYS.

    The checkpoint act's limit is ``max_mb``; the throttle act's is ``soft_cap_pct``, the share of the weekly
    limit (given as a fraction strictly between 0 and 1, stored as a whole percent) at which the flag is raised;
    the experiment act's is ``experiments``, each approved hypothesis id bound to its manifest's SHA-256
    (``lib/tick_experiment.py``).
    """
    if act not in KNOWN_ACTS:
        raise GrantError(f"unknown act {act!r}; the tick knows: {', '.join(KNOWN_ACTS)}")
    if not MIN_DAYS <= days <= MAX_DAYS:
        raise GrantError(f"a grant lasts between one minute and {MAX_DAYS} days, not {days} day(s)")
    if act == "throttle":
        limits = _throttle_limits(soft_cap)
    elif act == "experiment":
        limits = _experiment_limits(experiments)
    else:
        limits = _checkpoint_limits(max_mb)
    return {"v": VERSION, "act": act, "triggers": sorted(set(triggers)), "limits": limits,
            "issued": _iso(now), "expires": _iso(now + days * 86400), "nonce": secrets.token_hex(8)}


def sign(body: dict, private_pem: bytes, public_pem: bytes) -> dict:
    """The body with the verifying key's fingerprint and an Ed25519 signature over its canonical JSON."""
    key = _private(private_pem)
    signed = dict(body)
    signed["key"] = fingerprint(public_pem)
    signed["sig"] = base64.b64encode(key.sign(canonical_dumps(signed))).decode("ascii")
    return signed


REQUIRED = {"v", "act", "triggers", "limits", "issued", "expires", "nonce", "key"}


def verify(grant: object, public_pem: Optional[bytes], now: float) -> Tuple[bool, str]:
    """(True, "ok") only when the pinned key signed this exact body, for a known act, inside its window."""
    if public_pem is None:
        return False, "no verifying key pinned"
    if not isinstance(grant, dict) or not isinstance(grant.get("sig"), str):
        return False, "malformed"
    body = {k: v for k, v in grant.items() if k != "sig"}
    if not REQUIRED <= set(body) or body.get("v") != VERSION:
        return False, "malformed"
    if body["act"] not in KNOWN_ACTS:
        return False, "unknown act"
    try:
        if body["key"] != fingerprint(public_pem):
            return False, "key mismatch"
        _public(public_pem).verify(base64.b64decode(grant["sig"], validate=True), canonical_dumps(body))
    except CryptoMissing:
        return False, "cryptography missing"
    except GrantError:
        return False, "verifying key unreadable"
    except Exception:  # noqa: BLE001  any failure to verify is one answer: not signed by the pinned key
        return False, "bad signature"
    return _in_window(body, now)


def _in_window(body: dict, now: float) -> Tuple[bool, str]:
    issued, expires = _epoch(body["issued"]), _epoch(body["expires"])
    if issued is None or expires is None:
        return False, "malformed"
    if now < issued:
        return False, "not yet valid"
    if now >= expires:
        return False, "expired"
    return True, "ok"


def in_force(grant: dict, now: float) -> Tuple[bool, str]:
    """(True, "ok") while ``now`` lies inside a verified grant's window, else (False, why). The tick verifies its
    grants when it starts; an act that begins seconds later checks the window again with the clock of that moment,
    so it never starts after its authorisation lapsed (Codex on GRIP#8856)."""
    try:
        return _in_window(grant, now)
    except (KeyError, TypeError):
        return False, "malformed"


def grant_hash(grant: object) -> str:
    try:
        return hashlib.sha256(canonical_dumps(grant)).hexdigest()[:12]
    except (TypeError, ValueError):
        return ""


def load_grants(folder: Path, public_pem: Optional[bytes], now: float) -> Tuple[Dict[str, dict], List[Tuple[str, str, str]]]:
    """(valid grants by act, problems) over every ``*.json`` in the folder.

    A valid entry is ``{"grant", "hash", "file"}``. A problem is ``(file, hash, reason)``. Two valid
    grants for one act are a conflict: neither is used, both are reported, and the gate stays shut
    until the operator revokes one, because filename order must never choose an authorisation.
    """
    valid: Dict[str, dict] = {}
    problems: List[Tuple[str, str, str]] = []
    clashes: List[Tuple[str, str, str]] = []  # (act, file, hash) of every valid grant of an act that has more than one
    clashing: set = set()
    if not folder.is_dir():
        return valid, problems
    for path in sorted(folder.glob("*.json")):
        try:
            grant = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            problems.append((path.name, "", "unreadable"))
            continue
        digest = grant_hash(grant)
        ok, reason = verify(grant, public_pem, now)
        if not ok:
            problems.append((path.name, digest, reason))
        elif grant["act"] in valid or grant["act"] in clashing:
            first = valid.pop(grant["act"], None)
            clashing.add(grant["act"])
            clashes += ([(grant["act"], first["file"], first["hash"])] if first else []) + [(grant["act"], path.name, digest)]
        else:
            valid[grant["act"]] = {"grant": grant, "hash": digest, "file": path.name}
    return valid, problems + _clash_problems(clashes)


def _clash_problems(clashes: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
    """Each file of an act with more than one valid grant, as a problem naming every file of that act."""
    names: Dict[str, List[str]] = {}
    for act, name, _ in clashes:
        names.setdefault(act, []).append(name)
    return [(name, digest, f"conflicting grants for the same act ({', '.join(names[act])}); none is used until one is "
             f"revoked") for act, name, digest in clashes]


# ----------------------------------------------------------------------------- the operator's command

def _at_a_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _init(force: bool) -> int:
    pub_path = verifying_key_path()
    if pub_path.exists() and not force:
        print(f"grip-tick-grant: a verifying key is already pinned at {pub_path}; pass --force to replace it "
              f"(every existing grant then fails verification)", file=sys.stderr)
        return 2
    private_pem, public_pem = generate_keypair()
    keychain_store(private_pem)
    pub_path.parent.mkdir(parents=True, exist_ok=True)
    pub_path.write_bytes(public_pem)
    print(f"grip-tick-grant: signing key stored in the login keychain ({SERVICE}); verifying key {fingerprint(public_pem)} "
          f"written to {pub_path}.\nCommit that file in your own pull request, which only you merge; the tick trusts the "
          f"copy on main. When the keychain asks on each issue, click Allow, never Always Allow.")
    return 0


def _approved_manifest(hid: str) -> Tuple[dict, str]:
    """One hypothesis's manifest and its SHA-256, refused unless it is readable, names this id, and every input
    it pins still has the bytes it had when it was drafted."""
    import tick_experiment
    try:
        manifest = tick_experiment.load_manifest(tick_experiment.manifest_path(tick_experiment.experiments_dir(), hid))
    except tick_experiment.ManifestError as exc:
        raise GrantError(f"{hid}: {exc}; draft it with grip-tick-experiment draft {hid} -- <command>") from exc
    if manifest["hypothesis"] != hid:
        raise GrantError(f"{hid}: its manifest names {manifest['hypothesis']}")
    drift = tick_experiment.input_drift(manifest, get_claude_dir())
    if drift:
        raise GrantError(f"{hid}: {'; '.join(drift)}; draft it again before approving it")
    print(f"grip-tick-grant: approving {tick_experiment.describe(manifest)}")
    return manifest, tick_experiment.manifest_sha256(manifest)


def _approve_experiments(hypotheses: Tuple[str, ...]) -> Dict[str, str]:
    """Each named hypothesis bound to its checked manifest's SHA-256, printed for the operator before the keychain
    asks, so what is signed is what was read."""
    if not hypotheses:
        raise GrantError("an experiment grant names at least one hypothesis (--hypothesis H857)")
    return {hid: _approved_manifest(hid)[1] for hid in hypotheses}


def _issue(act: str, days: float, max_mb: int, triggers: Tuple[str, ...], run: Optional[Run] = None,
           soft_cap: Optional[float] = None, hypotheses: Tuple[str, ...] = ()) -> int:
    public_pem = read_verifying_key(verifying_key_path())
    if public_pem is None:
        print("grip-tick-grant: no verifying key is pinned; run grip-tick-grant init first", file=sys.stderr)
        return 2
    experiments = _approve_experiments(hypotheses) if act == "experiment" else None
    private_pem = keychain_load(run)
    if fingerprint(public_of(private_pem)) != fingerprint(public_pem):
        raise GrantError("the keychain's signing key does not match the pinned verifying key; run grip-tick-grant init "
                         "--force to make a new pair, then pin and merge the new key")
    grant = sign(payload(act, days, max_mb, triggers, time.time(), soft_cap, experiments), private_pem, public_pem)
    folder = grants_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{act}.json"
    path.write_text(json.dumps(grant, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    print(f"grip-tick-grant: {act} granted until {grant['expires']} (hash {grant_hash(grant)}) at {path}; "
          f"revoke with grip-tick-grant revoke {act}")
    return 0


def _show() -> int:
    public_pem = read_verifying_key(verifying_key_path())
    valid, problems = load_grants(grants_dir(), public_pem, time.time())
    try:
        label = fingerprint(public_pem) if public_pem else "none pinned"
    except GrantError as exc:
        label = f"unreadable ({exc})"
    print(f"verifying key in this checkout: {label}")
    for act, entry in sorted(valid.items()):
        ids = ", ".join(sorted((entry["grant"].get("limits") or {}).get("experiments") or {}))
        print(f"  {act}: valid until {entry['grant']['expires']} (hash {entry['hash']}, {entry['file']})"
              f"{' for ' + ids if ids else ''}")
    for name, digest, reason in problems:
        print(f"  {name}: NOT VALID ({reason}){' hash ' + digest if digest else ''}")
    if not valid and not problems:
        print("  no grants")
    return 1 if problems else 0


def _revoke(act: str) -> int:
    path = grants_dir() / f"{act}.json"
    try:
        path.unlink()
    except FileNotFoundError:
        print(f"grip-tick-grant: no grant file for {act} at {path}", file=sys.stderr)
        return 2
    print(f"grip-tick-grant: {act} revoked; the tick stops acting on it from its next run")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Grant, show or revoke one autonomous act of the always-on tick.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init", help="make the signing key (keychain) and pin the verifying key (repository)")
    init.add_argument("--force", action="store_true")
    issue = sub.add_parser("issue", help="sign a grant for one act")
    issue.add_argument("act", choices=KNOWN_ACTS)
    issue.add_argument("--days", type=float, default=14.0)
    issue.add_argument("--max-mb", type=int, default=200, help="checkpoint: the storage cap in megabytes")
    issue.add_argument("--soft-cap", type=float, default=DEFAULT_SOFT_CAP,
                       help="throttle: the share of the weekly limit at which the flag is raised")
    issue.add_argument("--triggers", default=None, help="the finding kinds that fire the act; default per act")
    issue.add_argument("--hypothesis", action="append", default=[],
                       help="experiment: a hypothesis whose drafted manifest this grant approves; repeat for several")
    sub.add_parser("show", help="every grant file and whether it verifies against this checkout's key")
    sub.add_parser("revoke", help="delete a grant file").add_argument("act", choices=KNOWN_ACTS)
    args = ap.parse_args(argv)
    try:
        if args.cmd in ("init", "issue") and not _at_a_terminal():
            print("grip-tick-grant: init and issue run only at a terminal, by the operator", file=sys.stderr)
            return 2
        if args.cmd == "init":
            return _init(args.force)
        if args.cmd == "issue":
            if args.hypothesis and args.act != "experiment":
                raise GrantError("--hypothesis belongs to the experiment act only")
            chosen = args.triggers if args.triggers is not None else ",".join(TRIGGERS_BY_ACT[args.act])
            return _issue(args.act, args.days, args.max_mb, tuple(t for t in chosen.split(",") if t),
                          soft_cap=args.soft_cap, hypotheses=tuple(args.hypothesis))
        if args.cmd == "revoke":
            return _revoke(args.act)
        return _show()
    except GrantError as exc:
        print(f"grip-tick-grant: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
