#!/usr/bin/env python3
"""Two-way Trello live-sync for the GRIP team kanban.

Keeps the public Trello board (short id x2GDFEKr) current with the
canonical board at state/kanban/board.json (lib/kanban.py is the API):

- kanban -> Trello: new cards created, lane changes moved (PUT idList),
  title changes updated. GRIP is the single writer for card content.
- Trello -> kanban: a manual lane drag on Trello (and ONLY a lane drag)
  flows back into board.json via lib/kanban.move_card, which appends the
  moved_log audit entry. On a true conflict (both sides moved since the
  last sync) the kanban lane wins and is pushed to Trello.
- Trello cards WITHOUT a kanban id marker are human-added and are never
  touched. The sync key is the last desc line:
  "(kanban id kNNN, created <ISO>)".
- One "GRIP live status" card is upserted into Doing: a timestamped
  last-5 of `git log origin/main --format='%h %s'` (after `git fetch
  origin main`) plus a one-line "Now:" summary from the newest commit.
  This is the ultraknow surface for anyone watching the board.

Conflict detection uses a tiny three-way-merge state file
(state/kanban/trello-sync-state.json) recording the last-synced lane per
card. With no state entry yet, a card whose kanban moved_log has a single
entry (born, never moved) treats a differing Trello lane as a manual drag
and pulls it back; otherwise kanban wins.

Cron-ability: every run is idempotent (a clean second run applies zero
changes), exits 0 on success / 1 on any error, and rate-limits itself to
>=150ms between Trello requests — safe to schedule via launchd every 10
minutes (see skills/launchd-automation) or cron `*/10 * * * *`.

CLI:
    trello_sync.py --once       # default: apply the delta
    trello_sync.py --dry-run    # report the delta, change nothing

Stdlib only: urllib for HTTP, subprocess for the macOS keychain
(`security find-generic-password -s grip-trello -w`, format KEY:TOKEN)
and git. The HTTP layer is injected (DIP) so tests run without network.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(_ROOT / "lib"))
import kanban  # noqa: E402

BOARD_SHORT_ID = "x2GDFEKr"
API_BASE = "https://api.trello.com/1"
KEYCHAIN_SERVICE = "grip-trello"
STATUS_CARD_NAME = "GRIP live status"
MIN_REQUEST_INTERVAL = 0.15

# kanban lane -> Trello list name (lists already exist on the board).
LANE_TO_LIST = {
    "backlog": "Backlog",
    "todo": "To Do",
    "doing": "Doing",
    "review": "Review",
    "done": "Done",
}
LIST_TO_LANE = {v: k for k, v in LANE_TO_LIST.items()}

# Sync key: the LAST line of a mirrored card's desc.
KANBAN_ID_RE = re.compile(r"^\(kanban id (k\d+), created [^)]*\)$")


def state_path() -> Path:
    return kanban.board_path().parent / "trello-sync-state.json"


def load_sync_state(path: Path | None = None) -> dict:
    path = path or state_path()
    if not path.exists():
        return {"lanes": {}}
    return json.loads(path.read_text())


def save_sync_state(state: dict, path: Path | None = None) -> None:
    path = path or state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def read_credentials(runner=subprocess.run) -> tuple[str, str]:
    """KEY:TOKEN from the macOS keychain entry grip-trello."""
    proc = runner(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "Trello credentials not found — add a keychain entry named "
            f"{KEYCHAIN_SERVICE} whose password is KEY:TOKEN"
        )
    raw = proc.stdout.strip()
    if ":" not in raw:
        raise RuntimeError(
            f"Keychain entry {KEYCHAIN_SERVICE} must be KEY:TOKEN format"
        )
    key, _, token = raw.partition(":")
    return key, token


class TrelloClient:
    """Thin Trello REST boundary. Injectable opener + sleep (DIP) so the
    sync logic and tests never touch the network directly."""

    def __init__(self, key: str, token: str,
                 opener=urllib.request.urlopen, sleep=time.sleep):
        self._auth = {"key": key, "token": token}
        self._opener = opener
        self._sleep = sleep
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < MIN_REQUEST_INTERVAL:
            self._sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request = time.monotonic()

    def _request(self, method: str, path: str, params: dict | None = None):
        self._throttle()
        query = urllib.parse.urlencode({**self._auth, **(params or {})})
        req = urllib.request.Request(
            f"{API_BASE}{path}?{query}", method=method
        )
        with self._opener(req) as resp:
            body = resp.read().decode("utf-8")
        # Trello descs can carry raw control chars; strict=False accepts.
        return json.loads(body, strict=False) if body else None

    def get(self, path: str, params: dict | None = None):
        return self._request("GET", path, params)

    def post(self, path: str, params: dict | None = None):
        return self._request("POST", path, params)

    def put(self, path: str, params: dict | None = None):
        return self._request("PUT", path, params)


# ---------------------------------------------------------------- pure logic


def parse_kanban_id(desc: str) -> str | None:
    """Kanban id from the last desc line, else None (human card)."""
    if not desc:
        return None
    last_line = desc.strip().splitlines()[-1].strip()
    match = KANBAN_ID_RE.match(last_line)
    return match.group(1) if match else None


def build_card_desc(card: dict) -> str:
    """Desc for a newly mirrored card — same shape as tonight's 19."""
    lines = [f"Owner: {card['owner']}"]
    if card.get("detail"):
        lines.append(card["detail"])
    if card.get("links"):
        lines.append("Links: " + " ".join(card["links"]))
    lines.append(f"(kanban id {card['id']}, created {card['created']})")
    return "\n".join(lines)


def resolve_lane(kanban_card: dict, trello_lane: str,
                 last_synced: str | None) -> tuple[str, str]:
    """Three-way lane merge -> (action, lane).

    action: "none" (in sync / no-op) | "push" (kanban -> Trello) |
    "pull" (manual Trello drag -> kanban). Kanban wins true conflicts.

    An empty / unmapped ``trello_lane`` (a Trello list with no kanban-lane
    mapping) returns ("none", kanban lane) — a deliberate no-op: GRIP never
    pulls into an invalid lane (that would crash kanban.move_card). The card is
    left untouched and ``last_synced`` is not consulted. compute_delta filters
    these out before calling here; this is the defence-in-depth for any caller.
    """
    k_lane = kanban_card["lane"]
    if not trello_lane:
        # Defence-in-depth: an empty / unmapped Trello lane is not a lane GRIP
        # can pull into. Never emit ("pull", "") — that crashes kanban.move_card.
        # compute_delta already skips these; this guards any other caller.
        return ("none", k_lane)
    if k_lane == trello_lane:
        return ("none", k_lane)
    if last_synced is None:
        # No baseline: a never-moved kanban card cannot be the mover,
        # so the Trello drag is manual -> pull. Otherwise kanban wins.
        if len(kanban_card.get("moved_log", [])) <= 1:
            return ("pull", trello_lane)
        return ("push", k_lane)
    if k_lane == last_synced and trello_lane != last_synced:
        return ("pull", trello_lane)  # only Trello moved
    return ("push", k_lane)  # kanban moved (alone, or conflict -> wins)


def _index_trello_by_kanban_id(trello_cards: list) -> tuple[dict, int]:
    """Index mirrored Trello cards by kanban id; count human-added cards.

    The status card and any card without a kanban-id marker (human-added) are
    excluded from the index; human cards are counted so the report can state how
    many were left untouched. Extracted from ``compute_delta`` (SRP / KISS).
    """
    by_kid: dict = {}
    human_skipped = 0
    for t_card in trello_cards:
        if t_card["name"] == STATUS_CARD_NAME:
            continue  # status card is handled by its own upsert pass
        kid = parse_kanban_id(t_card.get("desc", ""))
        if kid is None:
            human_skipped += 1  # human-added: never touched
            continue
        by_kid[kid] = t_card
    return by_kid, human_skipped


def compute_delta(kanban_cards: list, trello_cards: list,
                  last_lanes: dict, list_names: dict) -> dict:
    """Plan the sync. Pure: no IO, no client.

    trello_cards: [{id, name, desc, idList}] (open cards only).
    list_names: Trello list id -> list name.
    Returns {creates, moves, updates, pulls, human_skipped, in_sync,
    unknown_list_skipped}.
    """
    by_kid, human_skipped = _index_trello_by_kanban_id(trello_cards)
    delta: dict = {"creates": [], "moves": [], "updates": [], "pulls": [],
                   "human_skipped": human_skipped, "in_sync": 0,
                   "unknown_list_skipped": 0}
    for card in kanban_cards:
        t_card = by_kid.get(card["id"])
        if t_card is None:
            delta["creates"].append(card)
            continue
        clean = True
        if card["title"] != t_card["name"]:
            delta["updates"].append((card, t_card))
            clean = False
        t_lane = LIST_TO_LANE.get(list_names.get(t_card["idList"], ""), "")
        if not t_lane:
            # Mirrored card in a Trello list GRIP has no lane for (custom/renamed
            # list). Never pull (invalid lane → kanban.move_card raises → the cron
            # sync crash-loops) nor push (would fight the human's drag). Skip;
            # title handled above. Counted separately — diverged, not "in sync".
            delta["unknown_list_skipped"] += 1
            continue
        action, lane = resolve_lane(card, t_lane, last_lanes.get(card["id"]))
        if action == "push":
            delta["moves"].append((card, t_card, lane))
            clean = False
        elif action == "pull":
            delta["pulls"].append((card, lane))
            clean = False
        if clean:
            delta["in_sync"] += 1
    return delta


def build_status_desc(now_iso: str, commit_lines: list,
                      mesh_lines: list | None = None) -> str:
    """The ultraknow surface: what GRIP is doing now + last-5 commits +
    (optional) live cross-session awareness — how many Claude sessions are
    running, on what branch, whether they are in sync, and any pending-cleanup
    backlog. ``mesh_lines`` is the plain-English bullet list from
    ``bin/mesh state``; absent/empty ⇒ the block is omitted entirely (back-compat:
    the card is byte-identical to the pre-cross-session form)."""
    if commit_lines:
        newest = commit_lines[0]
        subject = newest.split(" ", 1)[1] if " " in newest else newest
    else:
        subject = "(no commits visible)"
    lines = [
        f"Now: {subject}",
        "",
        f"Latest origin/main commits (updated {now_iso}):",
    ]
    lines.extend(f"- {entry}" for entry in commit_lines)
    if mesh_lines:
        lines.append("")
        lines.append("Cross-session awareness (live GRIP sessions):")
        lines.extend(f"- {entry}" for entry in mesh_lines)
    lines.append("")
    lines.append("Auto-updated by scripts/trello_sync.py — manual edits "
                 "to this card are overwritten.")
    return "\n".join(lines)


# ------------------------------------------------------------------- side IO


def git_main_log(repo: Path, runner=subprocess.run) -> list:
    """Last 5 origin/main subjects, freshest first (fetches first)."""
    runner(["git", "-C", str(repo), "fetch", "origin", "main"],
           capture_output=True, text=True, timeout=60)
    proc = runner(
        ["git", "-C", str(repo), "log", "origin/main", "-5",
         "--format=%h %s"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git log failed: {proc.stderr.strip()}")
    return [line for line in proc.stdout.strip().splitlines() if line]


def mesh_awareness(repo: Path, runner=subprocess.run) -> list:
    """Plain-English cross-session bullets from ``bin/mesh state``.

    Returns the ``- ...`` lines of mesh state's "In plain English" block (N
    sessions, branch, in-sync state, pending-cleanup count). Fail-SOFT by
    design: a missing ``bin/mesh``, a non-zero exit, or any exception yields
    ``[]`` so the status card still renders its commits — cross-session
    awareness is additive, never load-bearing for the sync. The runner is
    injected (DIP) so tests never shell out.
    """
    mesh_bin = repo / "bin" / "mesh"
    if not mesh_bin.exists():
        return []
    try:
        proc = runner([str(mesh_bin), "state"],
                      capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    bullets = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
    return bullets


def fetch_trello_state(client: TrelloClient) -> tuple[dict, list]:
    """The 2 GETs: lists + open cards."""
    lists = client.get(f"/boards/{BOARD_SHORT_ID}/lists",
                       {"fields": "name"})
    cards = client.get(f"/boards/{BOARD_SHORT_ID}/cards/open",
                       {"fields": "name,desc,idList"})
    list_names = {entry["id"]: entry["name"] for entry in lists}
    return list_names, cards


def apply_delta(client: TrelloClient, delta: dict, board: dict,
                list_ids: dict, dry_run: bool) -> list:
    """Apply the plan. Returns plain-language action lines."""
    actions = []
    for card in delta["creates"]:
        actions.append(f"create '{card['title']}' in {card['lane']}")
        if not dry_run:
            client.post("/cards", {
                "idList": list_ids[LANE_TO_LIST[card["lane"]]],
                "name": card["title"],
                "desc": build_card_desc(card),
                "pos": "bottom",
            })
    for card, t_card in delta["updates"]:
        actions.append(f"retitle {card['id']} -> '{card['title']}'")
        if not dry_run:
            client.put(f"/cards/{t_card['id']}", {"name": card["title"]})
    for card, t_card, lane in delta["moves"]:
        actions.append(f"move {card['id']} -> {lane} (kanban wins)")
        if not dry_run:
            client.put(f"/cards/{t_card['id']}",
                       {"idList": list_ids[LANE_TO_LIST[lane]]})
    for card, lane in delta["pulls"]:
        actions.append(f"pull {card['id']} -> {lane} (manual Trello move)")
        if not dry_run:
            kanban.move_card(board, card["id"], lane)
    return actions


def upsert_status_card(client: TrelloClient, trello_cards: list,
                       list_ids: dict, desc: str, dry_run: bool) -> str:
    """Create or refresh the 'GRIP live status' card in Doing."""
    doing_id = list_ids[LANE_TO_LIST["doing"]]
    existing = next(
        (c for c in trello_cards if c["name"] == STATUS_CARD_NAME), None
    )
    if existing is None:
        if not dry_run:
            client.post("/cards", {"idList": doing_id,
                                   "name": STATUS_CARD_NAME,
                                   "desc": desc, "pos": "top"})
        return "status card created in Doing"
    if not dry_run:
        params = {"desc": desc}
        if existing["idList"] != doing_id:
            params["idList"] = doing_id
        client.put(f"/cards/{existing['id']}", params)
    return "status card refreshed"


def sync_once(client: TrelloClient, dry_run: bool,
              repo: Path | None = None) -> dict:
    """One full sync pass. Returns a summary dict for reporting."""
    repo = repo or _ROOT
    board = kanban.load_board()
    state = load_sync_state()
    list_names, trello_cards = fetch_trello_state(client)
    list_ids = {name: lid for lid, name in list_names.items()}

    delta = compute_delta(board["cards"], trello_cards,
                          state.get("lanes", {}), list_names)
    actions = apply_delta(client, delta, board, list_ids, dry_run)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status_desc = build_status_desc(now, git_main_log(repo),
                                    mesh_awareness(repo))
    actions.append(
        upsert_status_card(client, trello_cards, list_ids,
                           status_desc, dry_run)
    )

    if not dry_run:
        if delta["pulls"]:
            kanban.save_board(board)  # moved_log entries appended
        state["lanes"] = {c["id"]: c["lane"] for c in board["cards"]}
        state["synced_at"] = now
        save_sync_state(state)
    return {"actions": actions, "delta": delta, "status_desc": status_desc}


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync the GRIP kanban to the public Trello board."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="report the delta without changing anything")
    group.add_argument("--once", action="store_true",
                       help="apply the delta once (default)")
    args = parser.parse_args(argv)

    try:
        client = TrelloClient(*read_credentials())
        summary = sync_once(client, dry_run=args.dry_run)
    except Exception as exc:  # plain-language failure, exit 1
        print(f"Trello sync failed: {exc}", file=sys.stderr)
        return 1

    mode = "DRY RUN — would do" if args.dry_run else "Applied"
    delta = summary["delta"]
    print(f"{mode} {len(summary['actions'])} action(s):")
    for line in summary["actions"]:
        print(f"  - {line}")
    print(
        f"In sync: {delta['in_sync']} card(s); "
        f"human cards left untouched: {delta['human_skipped']}; "
        f"cards in unmapped Trello lists left untouched: "
        f"{delta['unknown_list_skipped']}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
