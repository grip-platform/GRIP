# Jev shadows: off by default since 17 October 2026

Two shadow readings asked TypeSafe's decision model, Jev, a question and logged
the answer beside what GRIP already did, so that Jev could be measured before it
changed anything. Both have answered their question, and V>> stopped both on
17 October 2026. Each now runs only when its variable is exactly `1`:
`GRIP_PROMPT_ROUTING_SCORE` for the routing shadow and
`GRIP_JEV_VERDICT_PREDICTOR` for the verdict predictor.

## The routing shadow (H806, refuted)

`lib/prompt_routing_score.py`, run by `hooks/prompt-routing-worker.py` on each
prompt, asked Jev which effort pattern a prompt needs. H806 predicted that Jev
would match the pattern the operator states in the prompt at least as often as
the router's heuristic does.

Read from `logs/prompt-routing-shadow.jsonl` on 2026-10-17 at 03:08 UTC (1087
rows from 2026-10-10 16:38 UTC): of 883 rows with a Jev reading, 532 carry a
stated pattern. Jev matched it 61 times (0.1147) and the heuristic 212 times
(0.3985), a gain of -0.2838. The ask rate, 256 of 883 (0.2899), stayed inside its
0.30 bound, so only the agreement half failed. An earlier reading the same night,
before more rows landed, gave 55 of 456 against 208. The router keeps its
heuristic.

Opt in again with `GRIP_PROMPT_ROUTING_SCORE=1`.

## The verdict predictor (H812, refuted by its registered rule)

`jev_tick_detached` in `bin/grip-pr-monitor` started
`lib/jev_verdict_predictor.py` for each open pull request, which asked Jev
whether AGORA's verdict would be MERGE and whether every required check would
pass on the first run. H812 predicted a lower Brier score than the base rate on
the merge question, over the first 50 settled predictions, with at least 50 on
that question.

Read with `lib/jev_calibration.py report --first 50` on 2026-10-17 at 03:08 UTC:
on the merge question n=41, Jev 0.2454 against 0.2499 for the base rate (base
rate 0.5122). 34 of the 41 predictions sat in the 0.4 to 0.6 bin at a mean of
0.505, which is the base rate restated. Only 41 of the first 50 settled
predictions carried a merge score, so the registered n condition failed. On the
CI question n=39, Jev 0.3258 against 0.0487, far worse than the base rate.

Under the registered rule, the refutation rests on the n condition alone. The
score condition held: 0.2454 is below 0.2499. The Brier score is a mean over
single predictions, so no binning rule changes it: the bins feed only the
reliability table.

Outside the rule, a bootstrap asks whether that small lead is real. The 41
predictions come from 32 pull requests, and a pull request's several heads share
one change and one review, so the bootstrap resamples whole pull requests: 20,000
times, with seed 20261017, recomputing the base rate in each sample. Jev's lead
over the base rate has a 95% interval of -0.0457 to 0.0235, and Jev comes out
ahead in 41.3% of the samples. So these 41 predictions cannot tell its lead from
zero. This is a finding beside the verdict, not a second refutation.

Every settled merge prediction (56 from 44 pull requests, the last settled at
04:03 UTC on 17 October, also outside the rule) gives an interval of -0.0345 to
0.0210, with Jev ahead in 47.1% of the samples. So with 56 predictions, more than
the registered 50, Jev still shows no detectable skill on this question.

The inputs are committed in `docs/measurements/2026-10-17-h812-verdict-points.json`:
all 50 rows of the registered window, with a null outcome for the 9 that had no
verdict, and all 80 settled rows. The calculation is `paired_bootstrap` in
`lib/jev_calibration.py`. To recompute both figures without the log, run this
from a checkout:

    bin/grip-py -m lib.jev_calibration bootstrap --points docs/measurements/2026-10-17-h812-verdict-points.json

Add `--set all_settled` for the second figure. A checkout without GRIP's venv can
use `python3` in place of `bin/grip-py`, because the command needs only the
standard library and writes nothing.

Opt in again with `GRIP_JEV_VERDICT_PREDICTOR=1`. The bash guard in
`jev_tick_detached` reads the same variable before anything is spawned, so off
costs nothing per sweep.

## What stays

Unset, empty, `0` and any other value leave each shadow off, and the suite's
`GRIP_CLASSIFY_NO_DECIDE=1` fence still overrides either opt-in. The predictor
module, its log `state/jev-calibration.jsonl` and its report keep working:

```bash
bin/grip-py lib/jev_calibration.py report --first 50
```

Neither shadow ever changed what the router chose or what the sweep merged, so
turning them off changes neither.
