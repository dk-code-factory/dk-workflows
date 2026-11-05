# Native dollar experiment record

The [plan](dollar-experiment-plan.md) defines five independent candidates and the dollar acceptance gate. Experiments use test-only fixtures, frozen production helpers and fresh native coordinators. Production adoption requires complete telemetry and correctness plus the dollar gate; incomplete telemetry is inconclusive.

## Provenance and accounting

`tests/fixtures/native_usage.py` and `tests/test_native_usage.py` are imported byte-for-byte from PR #145, commit `c9c2a181e72f7a3673013e9a6c901d5347918240`. No rejected production guidance, runtime changes or historical decisions are imported. The historical token evaluator is unchanged. `tests/fixtures/dollar_usage.py` supplies the independent dollar evaluator.

The rate card was verified on 2026-11-05 against the [official model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol) and [official pricing](https://developers.openai.com/api/docs/pricing). Standard USD per million: input 2, cache reads .10, 30-minute cache writes 2.50, 12-hour cache writes 5, output 10. Above 272,000 input tokens, input/cache rates double and output is multiplied by 1.5 for the complete response. Fast/priority is 2x, ultrafast 6x, batch/flex .5x; regional processing adds 10%. Reasoning is already part of output. Estimates are API-equivalent costs, separate from account billing.

Freeze runtime, guidance, resources, scenario, assignment and execution conditions before the first trial. Keep all completed arms. Private artifacts retain original assignments, terminal messages, event streams, rollouts and source references. Public reports normalize paths and retain hashes, correctness outcomes, cost evidence and limitations. Observed cache usage is never manufactured.

## Verification

Accounting tests cover duplicate parent history and complete descendants, missing responses/turns/terminals, cache/context/tier charges, exact 5% savings, smaller savings, equal costs, total regressions and insufficient improving pairs. Focused candidate checks precede native measurement. The initial focused run passed 237 tests before two test setup errors from disk quota; the same complete focused selection then passed all 239 tests using disposable shared-memory test output. Neither attempt was a performance trial.

## Decisions

Native trials and final decisions are pending. No production proposal is accepted by this infrastructure commit.
