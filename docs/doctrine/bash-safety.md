# Bash Safety: annex

The narrative behind `rules/bash-safety.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

## ripgrep flag footguns (PARAMOUNT — verify-canonical corruption source)

Origin 2026-07-14: an `rg -rn` typo mangled HAL-ladder reads and was mis-blamed on RTK, spawning a whole false "RTK is bad" council+HITL before `/falsify-self` traced it to the flag.

Verified 2026-09-20 on ripgrep 15.2.0, at a cost of one large context dump.

## Looser Search — reach gitignored content without token waste (canonical)

[Dir-exclude glob MUST be `!**/name/**` — `!name/*` does NOT exclude] (verified 2026-05-29)[.]

## RTK Token Compression (auto-active via hook)

### RTK footgun -- `rtk gh pr create` / `rtk gh pr edit` get an EMPTY stdin

**Proof, measured 2026-10-05 on rtk 0.38.0** with a controlled, reversible probe on
  GRIP#7373: `cat body.md | rtk gh pr edit N --body-file -` and the same command fed by a
  heredoc both left the body at 1 byte, while `rtk gh api graphql --input -` and
  `rtk git commit -F -` received their stdin intact.

## Syntax Safety (zsh eval)

Verified 2026-06-24: `git checkout HEAD -- $OVERLAP` → `pathspec '…' did not match`; a backup loop saw the list as 1 item.

`git push origin "$COMMIT:refs/heads/main"` lost `:r` and pushed `…efs/heads/main` (2026-06-24); `git show "$ref:lib/x.py"` lowercased `$ref`, addressed a path that does not exist, and read as an empty file rather than as an error (2026-08-14, again 2026-10-03).

Until 2026-10-03 the hook stripped long quoted strings before this check, so it never saw the quoted form both instances used.

Verified 2026-07-18: a background CI poller died at `(eval):6: read-only variable: status` on its first iteration (never polled a single time).
