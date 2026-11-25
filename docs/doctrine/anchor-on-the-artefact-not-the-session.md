# Anchor on the Artefact, Not the Session: annex

The narrative behind `rules/anchor-on-the-artefact-not-the-session.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

Origin: 2026-08-17. Given a 23,828-character string of `+` and `-` and the
instruction "decode this", I rendered it as a 1-bit audio waveform — chose sample
rates, low-pass filtered it, and ran it through the hearing pipeline — because the
preceding hour of the session had been spent analysing two songs. Two symbols in a
long string says "binary to text" to any first-time reader. The operator's verdict:
*"you were going for audio since that is what we were doing earlier in the session.
That's an obvious mistake even a child would have noticed."*

## Why this failure is invisible from the inside

When the measurement finally ran it returned 0.9996 bits/symbol, ruling out audio,
plaintext, Morse and Bacon in a single command. It had been available from the
first turn.
