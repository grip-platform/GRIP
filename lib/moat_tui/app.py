"""moat_tui.app — INTERACTION: the key loop, the state machine, the only print.

Three properties make this module testable without a terminal, and each is a
deliberate structural choice rather than a style preference:

* **``handle_key`` is a PURE state transition.** ``AppState`` is a frozen
  dataclass and every transition returns a NEW state via ``dataclasses.replace``.
  A test therefore drives a whole navigation path as a list of keys and asserts
  the final state with no TTY, no terminal size, and no ledger. (Purity is the
  load-bearing property; freezing is simply how it is guaranteed by
  construction rather than by promising not to mutate.)
* **Every side effect is INJECTED.** ``run`` takes ``out`` / ``read`` / ``size``,
  so the real loop runs headlessly against a ``StringIO`` and a scripted key
  list. ``--demo`` is exactly that — it drives THIS loop, never a second copy of
  it.
* **Loading happens ONCE**, in ``initial_state``, never inside the loop. A
  keypress costs one seek plus four verify legs, so navigation stays instant
  over a 4.3 MB belief chain.

The terminal is restored in a ``finally`` at BOTH levels — per keypress (raw
mode) and around the whole loop (cursor visibility + a final attribute restore) —
so a crash, a ``SIGINT``, or an exception mid-render can never leave the shell in
raw mode with a hidden cursor.

``read_key`` is the third private copy of the house key vocabulary (after
``lib/grip_harness_picker.py`` and ``lib/grip_session_wizard.py``); past the
functor's N=2 threshold (N=3 until 2026-10-03) a shared helper is the extraction
candidate per ``rules/knowledge-maturation-functor.md``, deferred here only
because this file's path is pinned by the build contract.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

_PKG_DIR = Path(__file__).resolve().parent
_LIB_DIR = _PKG_DIR.parent
_REPO_ROOT = _LIB_DIR.parent
for _p in (str(_REPO_ROOT), str(_LIB_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.moat_tui import model as M  # noqa: E402
from lib.moat_tui import tamper as T  # noqa: E402
from lib.moat_tui.render import Frame, render_screen  # noqa: E402

__version__ = "1.0.0"

#: Frames the demo renders at, so CI output is byte-stable regardless of the
#: terminal it runs in.
DEMO_SIZE = (100, 44)

_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"


# --------------------------------------------------------------------------
# Raw keypress
# --------------------------------------------------------------------------


def _read_escape() -> str:
    """Resolve an escape sequence WITHOUT blocking on a bare ``ESC``.

    The house pickers read the two tail bytes unconditionally, which hangs until
    the next keypress when the user simply pressed ESC. Here ESC is a primary
    key (``back``), so the tail is polled with a short ``select`` timeout: an
    arrow key delivers its tail immediately, a bare ESC resolves in ~20 ms.
    """
    import select

    seq = ""
    while len(seq) < 2 and select.select([sys.stdin], [], [], 0.02)[0]:
        seq += sys.stdin.read(1)
    return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(seq, "esc")


def _line_key() -> str:
    """Fallback when raw mode is unavailable: one line, first char.

    Returns ``q`` at EOF so a piped or closed stdin terminates the loop instead
    of spinning forever.
    """
    line = sys.stdin.readline()
    if not line:
        return "q"
    return line.strip()[:1] or "enter"


def read_key() -> str:
    """One raw keypress: ``up``/``down``/``enter``/``esc``/single char.

    Matches the existing GRIP picker vocabulary exactly so muscle memory
    transfers. Falls back to a line read when ``termios`` is unavailable or
    stdin is not a TTY, and re-raises ``KeyboardInterrupt`` on Ctrl-C (raw mode
    delivers it as a byte, not a signal) so the outer handler restores the
    terminal.
    """
    try:
        import termios
        import tty
    except ImportError:  # pragma: no cover - POSIX-only import
        return _line_key()
    if not sys.stdin.isatty():
        return _line_key()
    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        return _line_key()
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "":
            return "q"
        if ch == "\x1b":
            return _read_escape()
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\x7f", "\b"):
            return "backspace"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def terminal_size() -> tuple[int, int]:
    """Current (columns, rows), with a sane fallback for a pipe.

    Re-queried on every frame, so a resize repaints correctly on the next
    keypress without a ``SIGWINCH`` handler. ``render_screen`` clamps to a
    minimum of 24x8, so a very narrow terminal degrades rather than crashing.
    """
    size = shutil.get_terminal_size(fallback=(100, 40))
    return max(24, size.columns), max(10, size.lines)


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AppState:
    """Everything one screen needs, loaded once and transitioned purely."""

    paths: M.MoatPaths
    belief_index: M.LedgerIndex
    decision_index: M.LedgerIndex
    build: M.ForestBuild
    rows: tuple[M.DecisionRow, ...] = ()
    citations: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    council: Mapping[str, dict] = field(default_factory=dict)
    scope: M.BeliefScope = field(default_factory=M.BeliefScope)
    head: str = ""
    have_key: bool = False
    counts: Mapping[str, int] = field(default_factory=dict)
    # navigation
    screen: str = "forest"
    prev_screen: str = "forest"
    filtered: tuple[int, ...] = ()
    cursor: int = 0
    top: int = 0
    query: str = ""
    searching: bool = False
    view: M.DecisionView | None = None
    session: T.TamperSession | None = None
    proof: Mapping[str, Any] | None = None
    proof_verified: bool = False
    message: str = ""

    @property
    def visible(self) -> tuple[M.DecisionRow, ...]:
        """The rows the forest pane shows, after the ``/`` filter."""
        return tuple(self.rows[i] for i in self.filtered if 0 <= i < len(self.rows))

    @property
    def row(self) -> M.DecisionRow | None:
        """The selected row, or None when the filter matched nothing."""
        rows = self.visible
        if not rows or not 0 <= self.cursor < len(rows):
            return None
        return rows[self.cursor]


def _counts(
    build: M.ForestBuild, belief: M.LedgerIndex, citations: Mapping[str, Any],
    council: Mapping[str, Any],
) -> dict[str, int]:
    """Header counts. ``*_raw`` exposes loaded-vs-raw so a drop is VISIBLE."""
    return {
        "decisions": build.loaded,
        "decisions_raw": build.raw,
        "belief": len(belief),
        "belief_raw": belief.raw_lines,
        "citations": sum(len(v) for v in citations.values()),
        "council": len(council),
        "rejected": len(build.rejected),
        "dropped": belief.dropped + build.raw - build.loaded,
    }


def _subject_score(
    row: M.DecisionRow, citations: Mapping[str, tuple[str, ...]],
    council: Mapping[str, dict],
) -> int:
    """Rank a decision by HOW MANY legs it lights up, deterministically.

    A council record (4) makes the DER leg live and is the only way to run the
    pincer; citing belief nodes (2) make the belief join visible; a stored
    cite.verify tally (1) makes the CITE leg live. Live truth on this tree: no
    single decision carries all three (415 rows, 138 council-joined, 164 cited,
    0 with both), so the demo opens on the richest one that EXISTS rather than
    on a curated illusion — and prints the shortfall.
    """
    score = 4 if row.node_id in council else 0
    score += 2 if citations.get(row.addr) else 0
    return score + (1 if row.kind == "prove-it-artifact" else 0)


def _best_subject(
    rows: Sequence[M.DecisionRow], citations: Mapping[str, tuple[str, ...]],
    council: Mapping[str, dict],
) -> int:
    """Index of the highest-scoring row; ties break on the first, so stable."""
    best, best_score = 0, -1
    for index, row in enumerate(rows):
        score = _subject_score(row, citations, council)
        if score > best_score:
            best, best_score = index, score
    return best


def initial_state(paths: M.MoatPaths, *, back: int = 0, limit: int = 64) -> AppState:
    """The ONE-TIME load: index both ledgers, discover roots, build the forest,
    compute the citation join, load council records, select the belief scope.

    Nothing here is repeated inside the key loop, which is what keeps navigation
    instant over a 4.3 MB chain.
    """
    belief = M.build_index(paths.context)
    decisions = M.build_index(paths.idr)
    records = M.load_idr_ledger(paths)
    build = M.build_idr_forest(records, M.discover_roots(records))
    have_key = M.key_present(paths)
    rows = M.decision_rows(build, have_key=have_key)
    citations = M.index_citations(belief, addrs={r.addr for r in rows})
    council = M.load_council_records(paths)
    state = AppState(
        paths=paths, belief_index=belief, decision_index=decisions, build=build,
        rows=rows, citations=citations, council=council,
        scope=M.snapshot_scope(belief, back=back, limit=limit),
        head=M.head_id(paths), have_key=have_key,
        counts=_counts(build, belief, citations, council),
        filtered=tuple(range(len(rows))),
        cursor=_best_subject(rows, citations, council),
    )
    if not have_key:
        return replace(state, message="signing key absent — legs report CANNOT VERIFY")
    return state


# --------------------------------------------------------------------------
# Pure transitions
# --------------------------------------------------------------------------


def refresh_view(state: AppState) -> AppState:
    """Rebuild the joined ``DecisionView`` for the cursor row.

    Isolates the only expensive operation (one seek plus four verify legs) to
    explicit user intent, rather than paying it on every cursor move.
    """
    row = state.row
    if row is None:
        return replace(state, view=None, message="no decision selected")
    view = M.build_decision_view(
        row, build=state.build, index=state.belief_index,
        citations=state.citations, council=state.council,
        scope=state.scope, paths=state.paths,
    )
    return replace(state, view=view, message="")


def _move(state: AppState, delta: int) -> AppState:
    total = len(state.visible)
    if total == 0:
        return state
    return replace(state, cursor=max(0, min(total - 1, state.cursor + delta)))


def _open_decision(state: AppState) -> AppState:
    if state.row is None:
        return replace(state, message="nothing to open")
    return replace(refresh_view(state), screen="decision", prev_screen="forest")


def _open_tamper(state: AppState) -> AppState:
    """Open the forger's desk on the current decision, capturing the baseline."""
    if state.view is None:
        state = refresh_view(state)
    if state.view is None:
        return replace(state, message="no decision selected")
    session = T.start_tamper(
        state.view, state.scope, state.view.council_record,
        build=state.build, have_key=state.have_key, paths=state.paths,
    )
    note = "" if state.view.council_record else "no council record — DER vectors inert"
    return replace(state, screen="tamper", prev_screen="decision",
                   session=session, message=note)


def _open_proof(state: AppState) -> AppState:
    row = state.row
    if row is None:
        return replace(state, message="no decision selected")
    proof, verified = M.inclusion_proof(state.build, row.node_id)
    return replace(state, screen="proof", prev_screen=state.screen,
                   proof=proof, proof_verified=verified,
                   message="" if proof else "no inclusion proof for this node")


def _apply_vector(state: AppState, key: str) -> AppState:
    if state.session is None:
        return state
    vector = T.vector_by_key(key)
    if vector is None:
        return replace(state, message=f"no vector bound to {key!r}")
    session = T.apply_vector(state.session, key)
    return replace(state, session=session, message=vector.label)


def _start_search(state: AppState) -> AppState:
    return replace(state, searching=True, query="", screen="forest",
                   message="search: (enter to accept, esc to cancel)")


def _refilter(state: AppState, query: str) -> AppState:
    matches = M.search_rows(state.rows, query)
    return replace(state, query=query, filtered=matches,
                   cursor=min(state.cursor, max(0, len(matches) - 1)),
                   message=f"search: {query}" if query else "search:")


def _key_search(state: AppState, key: str) -> AppState | None:
    """While ``/`` is open every key edits the filter — including ``q``."""
    if key == "enter":
        return replace(state, searching=False,
                       message=f"filter {state.query}" if state.query else "")
    if key == "esc":
        return replace(_refilter(state, ""), searching=False, message="")
    if key == "backspace":
        return _refilter(state, state.query[:-1])
    if len(key) == 1 and key.isprintable():
        return _refilter(state, state.query + key)
    return state


def _key_forest(state: AppState, key: str) -> AppState | None:
    if key == "enter":
        return _open_decision(state)
    if key == "/":
        return _start_search(state)
    if key == "t":
        return _open_tamper(_open_decision(state))
    if key == "p":
        return _open_proof(state)
    if key == "esc" and state.query:
        return _refilter(state, "")
    return state


def _key_decision(state: AppState, key: str) -> AppState | None:
    if key == "t":
        return _open_tamper(state)
    if key == "p":
        return _open_proof(state)
    if key == "esc":
        return replace(state, screen="forest", message="")
    if key == "enter":
        return refresh_view(state)
    return state


def _key_tamper(state: AppState, key: str) -> AppState | None:
    if key == "esc":
        return replace(state, screen="decision", message="")
    if key == "r" or (len(key) == 1 and key.isdigit()):
        return _apply_vector(state, key)
    return state


def _key_back(state: AppState, key: str) -> AppState | None:
    if key in ("esc", "?"):
        return replace(state, screen=state.prev_screen, message="")
    return state


_SCREEN_KEYS: Mapping[str, Callable[[AppState, str], "AppState | None"]] = {
    "forest": _key_forest,
    "decision": _key_decision,
    "tamper": _key_tamper,
    "proof": _key_back,
    "help": _key_back,
}

_MOVES: Mapping[str, int] = {"j": 1, "down": 1, "k": -1, "up": -1}


def handle_key(state: AppState, key: str) -> AppState | None:
    """PURE state transition. Returns the next state, or None meaning QUIT.

    Pure means a test drives a whole navigation path as a list of keys and
    asserts the final state without a TTY, a terminal size, or a ledger.
    """
    if state.searching:
        return _key_search(state, key)
    if key == "q":
        return None
    if key == "?":
        if state.screen == "help":
            return replace(state, screen=state.prev_screen, message="")
        return replace(state, screen="help", prev_screen=state.screen, message="")
    if key in _MOVES and state.screen in ("forest", "decision"):
        moved = _move(state, _MOVES[key])
        return refresh_view(moved) if moved.screen == "decision" else moved
    return _SCREEN_KEYS.get(state.screen, _key_forest)(state, key)


def _scroll(top: int, cursor: int, height: int) -> int:
    """Sticky viewport: scroll only far enough to keep the cursor on screen."""
    if height <= 0:
        return 0
    if cursor < top:
        return cursor
    if cursor >= top + height:
        return cursor - height + 1
    return max(0, top)


def to_frame(state: AppState, size: tuple[int, int], colour: bool) -> Frame:
    """Adapt ``AppState`` into the immutable plain-data ``Frame`` render consumes.

    This adapter is the seam that lets ``render.py`` stay pure and import-free of
    this module.
    """
    width, height = size
    return Frame(
        screen=state.screen, width=width, height=height, colour=colour,
        paths_line=state.paths.line, head_id=state.head, counts=state.counts,
        rows=state.visible, cursor=state.cursor,
        top=_scroll(state.top, state.cursor, max(1, height - 14)),
        query=state.query, view=state.view, scope=state.scope,
        entries=(state.view.believed_by if state.view else ()),
        session=state.session, proof=state.proof,
        proof_verified=state.proof_verified, message=state.message,
    )


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


def run(
    state: AppState,
    *,
    out: TextIO,
    read: Callable[[], str],
    size: Callable[[], tuple[int, int]],
    colour: bool,
    observer: Callable[[AppState, str], None] | None = None,
) -> int:
    """``to_frame`` -> ``render_screen`` -> ONE write -> read key -> transition.

    ``read``, ``size`` and ``out`` are INJECTED, so a test (and ``--demo``) drives
    the REAL loop headlessly with a scripted key list and a ``StringIO``, and
    asserts on the emitted frames. ``observer`` is the annotation seam ``--demo``
    uses to caption and to capture the final state — without it the demo would
    have to reimplement this loop, which is the one thing it must not do.
    """
    while True:
        frame = to_frame(state, size(), colour)
        state = replace(state, top=frame.top)
        out.write(render_screen(frame) + "\n")
        out.flush()
        key = read()
        nxt = handle_key(state, key)
        if observer is not None:
            observer(state if nxt is None else nxt, key)
        if nxt is None:
            return 0
        state = nxt


class _TerminalGuard:
    """Hide the cursor for the session and ALWAYS restore it plus raw-mode attrs.

    ``read_key`` already restores attributes per keypress; this is the outer
    belt-and-braces for an exception raised mid-render, when no ``read_key``
    ``finally`` is on the stack to run.
    """

    def __init__(self, out: TextIO, active: bool) -> None:
        self._out, self._active, self._saved, self._fd = out, active, None, -1
        self._prior_handlers: dict[int, object] = {}
        self._restored = False

    def __enter__(self) -> "_TerminalGuard":
        if not self._active:
            return self
        try:
            import termios

            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
        except Exception:  # noqa: BLE001 - no tty / no termios: nothing to restore
            self._saved = None
        self._install_signal_handlers()
        self._out.write(_HIDE_CURSOR)
        self._out.flush()
        return self

    def _install_signal_handlers(self) -> None:
        """Route SIGTERM/SIGHUP through __exit__ so the terminal is always restored.

        SIGINT already unwinds as KeyboardInterrupt and reaches ``__exit__`` via the
        ``with`` block. SIGTERM and SIGHUP default to immediate termination, which
        would leave the shell in raw mode with the cursor hidden — recoverable only
        by ``stty sane``. Converting them to SystemExit gives the guard its
        ``__exit__``, then re-raises the default disposition so the exit status
        still reflects the signal.
        """
        import signal

        def _restore_then_exit(signum: int, _frame: object) -> None:
            self.__exit__()
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

        for name in ("SIGTERM", "SIGHUP"):
            sig = getattr(signal, name, None)
            if sig is None:  # not on this platform (SIGHUP is absent on Windows)
                continue
            try:
                self._prior_handlers[sig] = signal.signal(sig, _restore_then_exit)
            except (ValueError, OSError):  # noqa: PERF203 - not the main thread
                pass

    def __exit__(self, *_exc: object) -> None:
        if not self._active or self._restored:
            # Idempotent: a SIGTERM handler calls this, then the `with` block
            # calls it again on the way out. Restoring twice would emit a second
            # show-cursor + newline.
            return
        self._restored = True
        self._uninstall_signal_handlers()
        if self._saved is not None:
            try:
                import termios

                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            except Exception:  # noqa: BLE001 - best effort restore
                pass
        self._out.write(_SHOW_CURSOR + "\n")
        self._out.flush()

    def _uninstall_signal_handlers(self) -> None:
        """Put back whatever handlers were installed before we took over."""
        import signal

        for sig, prior in self._prior_handlers.items():
            try:
                signal.signal(sig, prior)  # type: ignore[arg-type]
            except (ValueError, OSError, TypeError):  # noqa: PERF203 - best effort
                pass
        self._prior_handlers.clear()


# --------------------------------------------------------------------------
# --demo: the same loop, scripted and captioned
# --------------------------------------------------------------------------

#: (key, caption). Walks the join, the proof, and the full tamper cascade.
DEMO_SCRIPT: tuple[tuple[str, str], ...] = (
    ("enter", "open the highest-scoring decision — THE JOIN on one screen"),
    ("p", "inclusion proof: this decision is committed to the root, revealing no other"),
    ("esc", "back to the decision"),
    ("t", "become the forger — capture the pristine baseline first"),
    ("1", "ACT 1 — rewrite a belief. Watch BELIEF fall and the Merkle root move."),
    ("r", "reset to pristine (nothing was written, so nothing to undo)"),
    ("4", "ACT 2, THE PINCER step 1 — edit the belief-state. Signature still passes."),
    ("5", "step 2 — repair the hash to cover it. The signature dies THIS instant."),
    ("6", "step 3 — repair the binding too. Two legs green, signature still invalid."),
    ("7", "ACT 3, THE HONEST LIMIT — re-sign with a fresh key. This forge SUCCEEDS."),
    ("q", "quit"),
)


def _demo_banner(state: AppState) -> str:
    """The live join shape, stated plainly — including what is NOT joined."""
    cited = sum(1 for r in state.rows if state.citations.get(r.addr))
    joined = sum(1 for r in state.rows if r.node_id in state.council)
    both = sum(
        1 for r in state.rows
        if r.node_id in state.council and state.citations.get(r.addr)
    )
    row = state.row
    return "\n".join(
        [
            "=" * 78,
            "grip-moat --demo — scripted walkthrough over the LIVE ledgers",
            "=" * 78,
            f"  root            {state.paths.line}",
            f"  decisions       {state.counts.get('decisions')} loaded "
            f"/ {state.counts.get('decisions_raw')} raw, "
            f"{state.counts.get('rejected')} rejected",
            f"  belief nodes    {state.counts.get('belief')} "
            f"({state.counts.get('citations')} cite a decision)",
            f"  council records {len(state.council)}",
            f"  signing key     {'present' if state.have_key else 'ABSENT — legs report cannot-verify'}",
            f"  crypto          {'present' if M.crypto_available() else 'ABSENT — DER reports cannot-verify'}",
            "",
            "  HONEST SHAPE OF THE LIVE JOIN (not curated):",
            f"    {joined} decisions carry a council record (DER leg live)",
            f"    {cited} decisions are cited by a belief node (belief join live)",
            f"    {both} decisions carry BOTH — so no single screen lights every leg.",
            "    The demo opens on the highest-scoring decision that EXISTS.",
            f"    subject: {row.node_id if row else '(none)'} [{row.kind if row else '-'}]",
            "=" * 78,
        ]
    )


def _demo_height(state: AppState | None) -> int:
    """Grow the demo viewport so a deep cascade is never clipped.

    An interactive terminal has a fixed height and ``render`` clips the body
    with an honest "… N more line(s)" marker. The demo has no such constraint,
    and clipping is exactly wrong here: the pincer only reads AS a pincer when
    all three steps are on screen together. Each step block costs ~12 lines.
    """
    if state is None or state.screen != "tamper" or state.session is None:
        return DEMO_SIZE[1]
    return DEMO_SIZE[1] + 12 * len(state.session.steps)


def run_demo(state: AppState, out: TextIO, *, colour: bool = False) -> int:
    """Drive the REAL loop with a scripted key list and captions.

    Not a second rendering path: ``run`` is called verbatim with an injected
    reader. The reader writes the caption for the key it is about to return, so
    captions land between frames in the order a viewer experiences them.
    """
    out.write(_demo_banner(state) + "\n\n")
    script = list(DEMO_SCRIPT)
    seen: dict[str, AppState] = {"final": state, "current": state}

    def _read() -> str:
        key, caption = script.pop(0)
        out.write(f"\n{'-' * 78}\n>>> press {key!r} — {caption}\n{'-' * 78}\n")
        return key

    def _observe(next_state: AppState, _key: str) -> None:
        seen["final"] = seen["current"] = next_state

    # ``run`` calls size() at the top of each iteration, AFTER the previous
    # iteration's observer, so the cell always holds the state about to be drawn.
    code = run(state, out=out, read=_read,
               size=lambda: (DEMO_SIZE[0], _demo_height(seen.get("current"))),
               colour=colour, observer=_observe)
    final = seen.get("final")
    if final is not None and final.session is not None:
        out.write("\n" + "=" * 78 + "\n")
        out.write(T.summarise_session(final.session) + "\n")
        out.write("=" * 78 + "\n")
    return code


# --------------------------------------------------------------------------
# --json: a non-interactive verdict dump for CI
# --------------------------------------------------------------------------


def _leg_json(leg: M.LegResult) -> dict[str, Any]:
    detail = leg.detail if isinstance(leg.detail, Mapping) else {}
    return {
        "name": leg.name, "status": leg.status, "headline": leg.headline,
        "note": leg.note,
        "detail": {k: v for k, v in detail.items() if isinstance(v, (str, int, float, bool))},
    }


def dump_json(state: AppState) -> dict[str, Any]:
    """Machine-readable verdict for the selected decision. Counts, never payloads."""
    view = refresh_view(state).view
    row = state.row
    return {
        "version": __version__,
        "root": str(state.paths.root),
        "head": state.head,
        "counts": dict(state.counts),
        "key_present": state.have_key,
        "crypto_available": M.crypto_available(),
        "roots": list(state.build.roots),
        "rejected": [{"id": i, "reason": r} for i, r in state.build.rejected[:20]],
        "subject": None if row is None else {
            "id": row.node_id, "kind": row.kind, "ts": row.ts,
            "addr": row.addr, "status": row.status,
            "believed_by": len(view.believed_by) if view else 0,
            "council_record": bool(view.council_record) if view else False,
        },
        "legs": [_leg_json(leg) for leg in (view.legs if view else ())],
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grip-moat",
        description="Four-pane viewer for GRIP's cryptographic-causation moat. "
                    "Press 't' to try to forge a decision and watch it break "
                    "under the real verifiers.",
    )
    colour = p.add_mutually_exclusive_group()
    colour.add_argument("--no-colour", "--no-color", dest="colour",
                        action="store_false", default=None, help="plain text")
    colour.add_argument("--colour", "--color", dest="colour",
                        action="store_true", help="force ANSI colour")
    p.add_argument("--claude-dir", metavar="PATH",
                   help="data root (default $CLAUDE_DIR, else ~/.claude)")
    p.add_argument("--back", type=int, default=0, metavar="N",
                   help="belief scope: Nth-from-last snapshot (wider window)")
    p.add_argument("--limit", type=int, default=64, metavar="N",
                   help="max belief nodes in the verify scope")
    p.add_argument("--demo", action="store_true",
                   help="scripted non-interactive walkthrough (no tty needed)")
    p.add_argument("--json", action="store_true",
                   help="machine-readable verdict dump, then exit")
    p.add_argument("--no-tty", action="store_true",
                   help="render one frame and exit (no input)")
    p.add_argument("--version", action="version", version=f"grip-moat {__version__}")
    return p


def _missing(paths: M.MoatPaths) -> str:
    """A loud, specific message when the ledgers are not where we pinned them."""
    absent = [str(p) for p in (paths.idr, paths.context) if not p.is_file()]
    if not absent:
        return ""
    return (
        "grip-moat: no moat ledger found.\n"
        + "".join(f"  missing: {p}\n" for p in absent)
        + f"  data root resolved to: {paths.root}\n"
        "  set CLAUDE_DIR or pass --claude-dir to point at a GRIP install.\n"
    )


def _empty(state: AppState) -> str:
    """Zero records is NO DATA — never a green screen over nothing."""
    if state.rows or state.belief_index.entries:
        return ""
    return (
        "grip-moat: the ledgers are present but contain no records.\n"
        "  NO DATA — an empty chain is not a verified chain.\n"
    )


def _colour_default(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    # NO_COLOR (https://no-color.org): when the variable is present and non-empty,
    # colour is suppressed regardless of tty/TERM. An explicit --colour flag still
    # wins, because that is the user asking for it directly on this invocation.
    if os.environ.get("NO_COLOR", ""):
        return False
    return sys.stdout.isatty() and os.environ.get("TERM", "") not in ("", "dumb")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point behind ``bin/grip-moat``.

    Fails loudly when the ledgers are absent, and NEVER renders an empty chain as
    green: zero records is NO DATA, not VERIFIED.
    """
    args = _parser().parse_args(list(argv) if argv is not None else None)
    paths = M.resolve_paths(args.claude_dir)
    problem = _missing(paths)
    if problem:
        sys.stderr.write(problem)
        return 2
    state = initial_state(paths, back=args.back, limit=args.limit)
    problem = _empty(state)
    if problem:
        sys.stderr.write(problem)
        return 2
    if args.json:
        json.dump(dump_json(state), sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    colour = _colour_default(args.colour)
    if args.demo:
        return run_demo(state, sys.stdout, colour=colour)
    if args.no_tty:
        sys.stdout.write(render_screen(to_frame(state, terminal_size(), colour)) + "\n")
        return 0
    with _TerminalGuard(sys.stdout, active=colour or sys.stdin.isatty()):
        try:
            return run(state, out=sys.stdout, read=read_key,
                       size=terminal_size, colour=colour)
        except KeyboardInterrupt:
            return 130


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
