# Verdict template

A sealed Proving Ground verdict. Fill every field; where a field does not apply, say why, never
leave it silently blank.

```markdown
# Proving Ground verdict — <tool> <version>

- verdict: ACCEPT | ACCEPT-WITH-CONDITIONS | REJECT | INCONCLUSIVE
- scope: <the exact use case tested, and what was NOT tested>
- artifact: <name + version + hash or source>
- environment: <os, harness, sandbox type, isolation limits as declared>
- learned claims -> tests: <claim -> test -> observation mapping>
- failures: <list, or "none observed in scope">
- untested claims: <vendor claims not exercised>
- user approvals: <each acceptance the operator actually gave>
- honesty: <"safe rehearsal with coverage limits" — coverage gaps called out>
- provenance: <GRASP seal or IDR leaf reference>
```

## What "ACCEPT" does not mean

- It does not mean the tool is safe beyond the tested scope.
- It does not mean the tool's untested claims are true.
- It does mean: within the declared environment and coverage, the operator accepted the observed
  outcomes. Nothing more.
