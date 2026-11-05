# Native dollar experiment record

The [plan](dollar-experiment-plan.md) defines five independent candidates and the dollar acceptance gate. Experiments use test-only fixtures, frozen production helpers and fresh native coordinators. Production adoption requires complete telemetry and correctness plus the dollar gate; incomplete telemetry is inconclusive.

## Provenance and accounting

`tests/fixtures/native_usage.py` and `tests/test_native_usage.py` are imported byte-for-byte from PR #145, commit `c9c2a181e72f7a3673013e9a6c901d5347918240`. No rejected production guidance, runtime changes or historical decisions are imported. The historical token evaluator is unchanged. `tests/fixtures/dollar_usage.py` supplies the independent dollar evaluator.

The rate card was verified on 2026-11-05 against the [official model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol) and [official pricing](https://developers.openai.com/api/docs/pricing). Standard USD per million: input 2, cache reads .10, 30-minute cache writes 2.50, 12-hour cache writes 5, output 10. Above 272,000 input tokens, input/cache rates double and output is multiplied by 1.5 for the complete response. Fast/priority is 2x, ultrafast 6x, batch/flex .5x; regional processing adds 10%. Reasoning is already part of output. Estimates are API-equivalent costs, separate from account billing.

Freeze runtime, guidance, resources, scenario, assignment and execution conditions before the first trial. Keep all completed arms. Private artifacts retain original assignments, terminal messages, event streams, rollouts and source references. Public reports normalize paths and retain hashes, correctness outcomes, cost evidence and limitations. Observed cache usage is never manufactured.

## Verification

Accounting tests cover duplicate parent history and complete descendants, missing responses/turns/terminals, cache/context/tier charges, exact 5% savings, smaller savings, equal costs, total regressions and insufficient improving pairs. Focused candidate checks precede native measurement. The initial focused run passed 237 tests before two test setup errors from disk quota; the same complete focused selection then passed all 239 tests using disposable shared-memory test output. Neither attempt was a performance trial.

## Decisions

Proposal 1 is inconclusive: all six native correctness checks pass, but the original WebSocket transport lacks independent response inventories. Preserve all arms; no production change is adopted. Observed medians are $0.1619104 baseline and $0.1539644 candidate (4.91% savings, below the dollar threshold independently of incomplete telemetry). See [proposal 1 evidence](evidence/151-experiment-1.json) and the archived candidate patch under tests/fixtures/dollar-candidates/.

Proposal 2 is accepted: all six native arms pass correctness and complete independent SSE/host/rollout inventories. Baseline costs are $0.2143820, $0.2183528 and $0.2303840; candidate costs are $0.1901388, $0.2310276 and $0.1873048. Median savings are 12.92%, total cost falls from $0.6631188 to $0.6084712 and two pairs improve. See [proposal 2 evidence](evidence/151-experiment-2.json). The second pair's regression is retained. First accepted production change synchronizes the patch release to 3.5.1.

The resume packet was exercised against the original production baseline, without proposal 1. Its focused Python 3.12 suite passes 225 tests. Accounting, collection and frozen fixture construction pass 84 checks, including missing creation/completion inventories, unexpected descendants and unknown cache-write TTL. Later proposals and combined bundle validation remain pending; individual acceptance does not establish bundle readiness.

For proposal 2 and later, an invocation-only provider with the same OpenAI endpoint/authentication and model disables WebSocket capability so raw SSE creation/completion inventories can be independently retained. Personal settings are unchanged. Both arms use the same gpt-6.1-sol/high/default controls and transport. Proposal 1 remains under its original frozen transport and is never rerun.


Proposal 3 is declined. Baseline costs are $0.1271160, $0.1662800 and $0.1637432; candidate costs are $0.1624512, $0.1570100 and $0.2054872. Median cost changes from $0.1637432 to $0.1624512 (less than 1% savings), total cost rises from $0.4571392 to $0.5249484 and only one pair improves. All telemetry is complete. Three reports declare complete:false while accurately identifying the expected partial coverage; the strict completion check also does not pass. Preserve those declarations, all original reports and [proposal 3 evidence](evidence/151-experiment-3.json). The source and seven focused candidate tests are archived as a patch; active production changes are removed. Focused runs passed 167 and 83 tests, with overlap.

Subsequent assignments explicitly distinguish completion of the bounded exercise from overall workflow coverage. This clarification applies equally to both arms and does not rerun or alter earlier trials. Combined validation is prepared to exercise exactly the accepted proposal set, with the original baseline as its comparison. No completed measurement is retried.
