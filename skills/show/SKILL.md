---
name: show
description: "Opens a file in its default application, opens a folder in the file manager, or reveals an item selected inside its parent folder, on macOS, Linux, Windows Git Bash, WSL and a headless box, by running bin/grip-show. Use when the operator says /show, open this, show me the document, reveal it in Finder, or wants to read a deliverable the session just produced."
status: Active
effort: low
version: 1.0.0
created: 2026-10-26
user_invocable: true
command: show
triggers:
  - /show
  - show me the doc
  - open this file
  - reveal in finder
  - open the folder
---

# /show -- open or reveal anything on this machine

One verb that does the obvious thing with a local path. A file opens in its default application. A folder opens as a file-manager window. With `-r` the item is revealed, selected, inside its parent folder. All the platform logic lives in `bin/grip-show`; this skill is the `/show` surface over it and adds no second implementation.

## What it does (plain language)

When the operator wants to look at something the session made, such as a PDF, a note or a folder of evidence, `/show` opens it where they can read it. It never edits, sends or publishes anything. Opening a file is reversible: the operator closes the window.

## Usage

| Command | Effect |
|---|---|
| `/show <path>` | Open the file in its default application, or the folder in the file manager |
| `/show -r <path>` | Reveal the item selected inside its parent folder |
| `/show -n <path>` | Dry run: print the exact command that would run, run nothing |
| `/show the doc` | Resolve "the doc" to the most recent deliverable in the session, then open it |

## Behaviour contract

1. An explicit path is used as given, after `~` expansion. Nothing else is inferred.
2. A reference such as "the doc", "it" or "that PDF" resolves to the most recent deliverable this session wrote or named. Prefer the rendered form (the PDF over its markdown source) when both exist. If two candidates fit equally, ask once, naming both paths, and open nothing.
3. When the path was inferred rather than typed, run the dry run first and print its one line, so the operator sees what will open before it opens. Then run it for real.
4. Pass `-r` through for "reveal", "show me where it is" or "in Finder". Pass `-n` through for "dry run" or "what would that run".
5. Local paths only. A URL goes to the browser tools, never to `bin/grip-show`.
6. On failure, report the exit code in words: 1 means the path does not exist, 2 means a usage error, 3 means no launcher is available on this machine.

## Implementation

```bash
"${CLAUDE_DIR:-$HOME/.claude}/bin/grip-show" [-r|--reveal] [-n|--dry-run] <path>
```

The shell function `show` in `lib/shell-functions.sh` calls the same binary, so `/show` in a session and `show` at a prompt behave identically. `GRIP_SHOW_PLATFORM=darwin|linux|windows|wsl|headless` overrides platform detection, which is how `tests/test_grip_show.py` exercises every branch on every runner.

## Examples

```bash
# The operator asks to read the brief the session just rendered.
"${CLAUDE_DIR:-$HOME/.claude}/bin/grip-show" -n ~/matters/m2/drafts/brief.pdf   # prints: open ~/brief.pdf
"${CLAUDE_DIR:-$HOME/.claude}/bin/grip-show" ~/matters/m2/drafts/brief.pdf      # Preview opens the PDF

# "Where did that land?"
"${CLAUDE_DIR:-$HOME/.claude}/bin/grip-show" -r ~/matters/m2/evidence/2026-10-26/   # Finder window, item selected

# A headless box: the file is paged, a folder is listed.
GRIP_SHOW_PLATFORM=headless "${CLAUDE_DIR:-$HOME/.claude}/bin/grip-show" README.md
```

## Platforms

| Platform | Open | Reveal |
|---|---|---|
| macOS | `open <path>` | `open -R <path>` |
| Linux desktop | `xdg-open <path>` | D-Bus `FileManager1.ShowItems`, else `xdg-open` on the parent |
| Windows Git Bash, WSL | `explorer.exe <path>` | `explorer.exe /select,<path>` |
| Headless | page the file, list the folder | list the parent with the item marked |

## Provenance

`bin/grip-show` was ratified with V>> on 2026-10-02 after a sovereign council sitting (seal `1f47e253330877a5`) and reviewed on GRIP#7009. The verb is `show`, not `view`, because `/usr/bin/view` is vim's read-only mode on macOS and most Linux. This skill, written on 2026-10-26 with Claude Fable 5.1, adds the slash surface only.

## Falsification

This skill is wrong if, over the next twenty uses, an inferred target is wrong more than twice (then drop inference and require a path), or if the dry-run line before an inferred open is never read (then drop the dry run and open directly).

## Changelog

- 1.0.0 (2026-10-26): first version, a thin surface over `bin/grip-show`.
