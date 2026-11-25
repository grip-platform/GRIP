# No Guardrail Evasion: annex

The narrative behind `rules/no-guardrail-evasion.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

Origin: 2026-05-15. During W3 of an IDR audit-chain `/converge`, a
background `direct-implementation` agent tripped the env-var-protection
guardrail on `IDR_SIGNING_KEY` + setenv, then wrote into a test file:

```python
# Env-var name split to avoid guardrail substring scan on source text.
_SIGN_KEY_ENV = "IDR_SIGNING" + "_KEY"
```

It was caught in trust-but-verify review, rejected, and never committed.
V>> directive: *"never do something like this ever, mechanically enforced
too"*. This rule + its hook are that enforcement.
