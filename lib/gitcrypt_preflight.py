"""git-crypt pre-flight guard — fail LOUD, never silently parse ciphertext.

Several GRIP runtime config files are git-crypt-encrypted (see `.gitattributes`):
config/channel-allowlist.json, config/team-ratification.json,
config/relay-listener-prompt.md (-> state/relay/listener-prompt.md),
state/users/isolated-users.yaml, plus rules/operational-context.md + facts/*.md.

On a host with the git-crypt key UNLOCKED, a `git checkout` smudges them to
plaintext and consumers parse them normally. On a host WITHOUT the key (a fresh
re-clone, a wiped keyring, a botched key rotation), the same checkout writes the
raw ciphertext to disk: a 10-byte magic header `\x00GITCRYPT\x00`, a 16-byte
nonce, then an AES body.

That ciphertext is dangerous precisely because it parses *quietly*:
  - a JSON/YAML allowlist load throws, the consumer catches it, and the
    allowlist ends up EMPTY (a confusing silent outage), and
  - a `.read_text(encoding="utf-8")` on the binary body throws a
    UnicodeDecodeError that crashes a per-event handler with no clear cause.

This guard turns either failure into ONE loud, plain-language error that names
the file and the exact remedy. It is the belt-and-braces safety net for the
case the deploy flow does not (and cannot easily) detect on its own.

Zero third-party dependencies. Path-portable. Importable by Python consumers
(`assert_decrypted(path)`) and callable from shell consumers via the CLI
(`python3 lib/gitcrypt_preflight.py assert <path>`), so the magic-byte
definition lives in exactly one place (DRY).
"""

from __future__ import annotations

import sys
from pathlib import Path

# git-crypt's on-disk header for an encrypted blob: NUL, "GITCRYPT", NUL (10 bytes),
# followed by a 16-byte nonce. Matching the 10-byte magic is sufficient and exact.
GITCRYPT_MAGIC = b"\x00GITCRYPT\x00"
_HEADER_READ = 16  # read a little past the 10-byte magic; covers the magic in full

# Exit code for the CLI when a file is still ciphertext. 3 (not 1/2) so a shell
# caller can distinguish "encrypted, key missing" from a generic/usage error.
EXIT_CIPHERTEXT = 3


class GitCryptCiphertextError(RuntimeError):
    """Raised when a config file is still git-crypt ciphertext (key not unlocked)."""


def _remedy(path: Path) -> str:
    """Plain-language fix naming the file and the exact unlock command."""
    return (
        f"git-crypt PRE-FLIGHT FAILED: {path} is still ENCRYPTED on this host.\n"
        f"  This means the git-crypt key is NOT unlocked here, so the file on disk\n"
        f"  is ciphertext (it begins with the bytes \\x00GITCRYPT\\x00), not its real\n"
        f"  contents. Parsing it would silently yield an EMPTY/garbage config and\n"
        f"  could take a security gate or the relay offline without an obvious cause.\n"
        f"  REMEDY: run `git-crypt unlock <key-file>` on this host (the same one-time\n"
        f"  step the VPS provisioning does), then re-deploy / restart the consumer."
    )


def is_ciphertext(path: str | Path) -> bool:
    """True iff `path` exists and begins with the git-crypt magic header.

    A missing file is NOT ciphertext (returns False) — callers that require the
    file to exist handle absence themselves; this guard only judges *content*.
    """
    p = Path(path)
    try:
        with p.open("rb") as fh:
            head = fh.read(_HEADER_READ)
    except (FileNotFoundError, IsADirectoryError):
        return False
    return head.startswith(GITCRYPT_MAGIC)


def assert_decrypted(path: str | Path) -> None:
    """Raise GitCryptCiphertextError if `path` is still git-crypt ciphertext.

    FAIL-CLOSED: call this immediately before parsing a git-crypt-managed config
    so a key-missing host fails LOUD instead of silently parsing ciphertext as an
    empty allowlist / garbage prompt. A missing or plaintext file passes through.
    """
    if is_ciphertext(path):
        raise GitCryptCiphertextError(_remedy(Path(path)))


def _cli(argv: list[str]) -> int:
    """`assert <path>` for shell consumers. Exit 3 + loud stderr on ciphertext."""
    if len(argv) != 2 or argv[0] != "assert":
        sys.stderr.write("usage: gitcrypt_preflight.py assert <path>\n")
        return 2
    try:
        assert_decrypted(argv[1])
    except GitCryptCiphertextError as exc:
        sys.stderr.write(str(exc) + "\n")
        return EXIT_CIPHERTEXT
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
