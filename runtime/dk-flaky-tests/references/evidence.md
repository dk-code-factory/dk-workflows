# Evidence and CI decisions

The shared implementation lives in `shared/flaky_tests.py` (registry), `shared/test_evidence.py` (report readers), `shared/flaky_actions.py` (read-only collection/discovery), and `shared/ci_policy.py` (evaluation). Existing `dk-pr-iter/scripts/run_state.py flaky` and `flaky-show` commands retain their history and interface. Their `flaky: true` means historical evidence only; it cannot suppress CI.

## Reports and provenance

Use attempt-specific Actions metadata, job membership, and log archives, not the latest job list for an older attempt. The collector stores the complete API responses and archives in `flaky-evidence/` outside the checkout. It binds a step log to a unique job directory and exact step number. Unsupported directory mappings remain missing evidence. Limits are 100 MiB per archive and 10 MiB per report; exceeding either is explicit missing evidence. Attempt logs use [GitHub's Actions APIs](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt) and the workflow run attempt logs endpoint.

Recognized complete reports are JUnit XML with consistent counts, pytest output with a complete terminal and short failure summary, `go test -json`, and Jest complete default-reporter or `--json` output with consistent suite/assertion totals and no interruption or runtime errors. XML/JSON reports must be the complete step output to cover the step automatically. Arbitrary artifacts are not attached to a job/attempt by filename alone. An independently collected report may be supplied to the pure evaluator only when its source proves the exact job, attempt, head, and full failed-step coverage. Do not fabricate completeness flags or handwrite an accepted decision. Human text review can diagnose unsupported formats but cannot authorize suppression through a guessed parse.

Pytest verbose named `PASSED` lines, JUnit cases, Go terminal test events, and Jest assertions can establish named passes. Aggregate pass counts cannot. Failed signatures retain report detail without fuzzy normalization; differing errors do not match. Skips are not passes. A fail→pass proof must be chronological, on the same commit and workflow/job, with distinct attempt/job provenance and links.

## Matching issues

Automatic proof requires all four exact context fields. A matching issue can declare them in a fenced `dk-flaky` JSON object:

````text
```dk-flaky
{"workflow":".github/workflows/ci.yml","job":"tests (linux)","test":"tests/test_api.py::test_retry","signature":"AssertionError: response was late","flaky":true}
```
````

`flaky: true` is explicit issue proof. Without it, that exact context plus fail→pass evidence can verify flakiness. Prose can also match when it quotes each exact field in backticks; an unambiguous line beginning “Confirmed flaky test:”, “Known flaky test:”, or “This test is flaky:” is explicit proof. Uncertain or negated declarations are candidates and require historical proof. Issues mentioning the exact test and signature but lacking full context remain linked candidates; historical evidence cannot activate a partial match. Unmatched failures remain unlinked candidates. Report related but uncertain issues without claiming verification. Do not edit an issue to manufacture proof.

## Shared policy

`evaluate(snapshot, evidence, verification, repo)` is a pure evaluator for trusted pinned evidence. Its result contains effective `result`, `raw_result`, `ignored_failures` with issue URLs, and `blockers`. `decide(api, registry, snapshot, refresh_snapshot)` is the live entry point: it refreshes linked issues, collects current failure evidence, discovers unknown named failures at most once per unchanged head/failure fingerprint, reevaluates saved entries, and rechecks attempts and the live snapshot. Collection never starts or reruns jobs. Only `dk-pr-iter/scripts/ci_wait.py` polls CI; do not run discovery alongside its polling process.

Every failed test and every failed step needs complete coverage. Without a matching [explicit dk-config exception](../../dk-config/references/ci-exceptions.md), mixed failures, different signatures, build/deploy errors and missing logs block acceptance. Cancellations, pending checks, timeouts, missing checks and invalid head/attempt provenance remain blocking with either path. An automatic-only accepted decision has `result: known_flakes`, `raw_result: failure`, no blockers, and at least one ignored failure with verified open issue links. Report it as **“CI red; accepted known flakes”** with those links, preserving the raw result.

At completion, run `dk-pr-iter/scripts/completion.py --cwd REPO --pr PR_URL gate --start-head START_SHA --final-head FINAL_SHA --preparation-file PREPARATION.json --verification-file VERIFICATION.json --run-record RUN_RECORD.json --series-assessment ASSESSMENT.json --review-complete` after the waiter exits. Add `--actionable` when work remains. Require `gate: clean`. This performs a fresh one-shot shared CI decision for red CI; a stored label, old issue state, changed head, or changed run attempt cannot authorize completion. Python callers of `completion_gate` must supply the live `ci_verifier` callback for `known_flakes` or `accepted_exceptions`. Explicit exceptions are reloaded and fingerprinted at each decision, including completion; removed or changed configuration cannot authorize acceptance through a saved result. Repeat the live gate before completion mutations when the evidence may have changed.

## Bounded automatic classification

The waiter emits terminal raw CI status on stderr before starting classification;
stdout remains the final JSON decision. Phase/progress updates identify current
evidence collection, unknown-test discovery and final live verification. A shared
60-second classification budget is separate from polling `--timeout`; change it
with `--classification-timeout SECONDS`. Read-only GitHub subprocesses and their
transient retries, including final head/check reads, share the remaining budget.
Contended registry locks also share that budget; expiry propagates as a terminal
classification timeout rather than ordinary unavailable issue evidence.
Local evidence work is checked at phase and API boundaries. Expiry retains a
failure decision with `classification.timed_out: true`, the phase and elapsed time;
it never authorizes accepting red CI.

Automatic discovery runs only when every blocker is a complete named-test failure
lacking verified issue proof. Unsupported or malformed current reports, missing
provenance, build errors and mixed parser/test failures return blockers without
a historical scan. Discovery refreshes issues first; explicit issue proof needs
no historical logs. Remaining unknown identities restrict historical collection
to their workflow paths and exact jobs. Completed attempts retain content-addressed
metadata, archives and parsed evidence privately, keyed by selected jobs and the
collector/parser identity; verified retained evidence can
be reused while current checks, latest attempts, linked issue state and exception
configuration are still read live for every decision.

Timed-out scans retain their allocation and status. Automatic observation does
not silently repeat an unchanged failure scan. Use the existing explicit
`dk-flaky-tests discover` command with narrowed `--days`, `--max-runs` and
`--max-issues` to collect missing historical evidence, then rerun the one-shot
decision. Fix unsupported report collection or parsing before retrying those
blockers: historical flakes cannot prove completeness of the current failure.
