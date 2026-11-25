# Operator Anonymity — the doctrine: annex

The narrative behind `rules/operator-anonymity.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

Origin: V>> 2026-09-17 — "eventually V>> must become anonymous." Written after a real leak: the runtime URL-absorb log (`data/absorbed-urls.jsonl`) was tracked in git, plaintext, and carried an absorbed browsing entry alongside the operator's WhatsApp JID. It never reached `main`, but it reached two feature branches before being caught — the exact failure this rule exists to prevent.
