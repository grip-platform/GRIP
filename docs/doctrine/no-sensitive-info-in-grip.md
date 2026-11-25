# No Sensitive Info — Financial / Client-Commercial / PII — in the GRIP Repo: annex

The narrative behind `rules/no-sensitive-info-in-grip.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

Origin: 2026-05-19. V>> decision after a real grounded AGORA council on
`prep/invoice-history.json`. The council recommended status-quo git-crypt —
**conditioned on key segregation, which a verified scan refuted**:
`.git-crypt/keys/{default,codetonight}/0/2EE681B7…DFF82891.gpg` are both
tracked, both wrap to the **same single GPG identity**, and are pushed to
a **client-org mirror** of the GRIP repo. There is no real
per-client key isolation — invoice confidentiality from that client rests
*only* on "no client GPG key ever gets `git-crypt add-gpg-user`'d", on a
repo actively mirrored to that client (open tasks #1882/#1883).

Generalised 2026-06-23 (V>>): the original rule named only *financial*
data; the same exposure applies to **any third-party PII** (emails, phone
numbers, national ID numbers, addresses, DOB) that rides the client mirror.
Renamed `no-sensitive-financial-in-grip.md` → `no-sensitive-info-in-grip.md`
and the guard `sensitive-financial-guard.py` → `sensitive-info-guard.py`
so the name matches the full data class it governs.

## Enforcement tier (knowledge-maturation-functor)

Migration evidence: `docs/migrations/sensitive-financial-2026-05-19.md`
(9 migrated paths, GITCRYPT-on-remote verification, R0 refutation of the
"dangling generate-invoice.py" claim).
