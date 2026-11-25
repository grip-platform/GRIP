"""Stop hook: out-of-band holistic kernel regeneration + propagation.

Fires at session end (covers /save→exit, /closeout, normal Stop) — the
out-of-band, NOT-launch trigger the grounded council R4 mandates (synthesis
must never be on the <1s/2s launch path).

regenerate() is compare-and-skip: unchanged substrate ⇒ a cheap no-op
(one read + digest), changed ⇒ atomic os.replace rewrite + a digest-bearing
`mesh broadcast` so concurrent peer sessions are notified within one
tool-call. Concurrent Stop hooks converge: every writer computes the same
payload from the same substrate (R2), last-write-wins via the atomic
rename is safe, readers never see a torn file.

Best-effort: a regeneration failure must NEVER break session end. Any
exception → silent allow.
"""

import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
sys.path.insert(0, str(CLAUDE_DIR / "hooks"))
sys.path.insert(0, str(CLAUDE_DIR))

from hook_utils import parse_hook_input, respond  # noqa: E402


def main() -> None:
    parse_hook_input()  # consume stdin; contents not needed for a Stop regen
    try:
        from lib.holistic_synthesiser import regenerate

        regenerate()
    except BaseException:  # noqa: BLE001 — INTENTIONAL, council-R3 absolute
        # This IS the session-end boundary. The R3 contract is absolute: a
        # regen/broadcast failure must NEVER break session end. Broader than
        # `Exception` on purpose so SystemExit/KeyboardInterrupt during the
        # best-effort regen cannot crash the Stop hook. Scope is this one
        # call; respond() still fires so the stop completes normally.
        pass
    respond()


if __name__ == "__main__":
    main()
