# CI failure recovery

The running native skill diagnoses failed jobs by comparing their relevant logs
with the actual PR changes. Temporary package mirror/DNS outages or runner
service interruptions can support an unrelated, retryable verdict. An error
signature alone is insufficient: a changed hostname, package source, dependency
or workflow can cause the same error. PR-caused failures need repair. Ambiguous
failures remain blocked with a concrete missing-evidence explanation.

After a failing `ci_wait.py` result, use its `decision_file`:

```sh
python3 runtime/dk-ci/scripts/ci_retry.py inspect --pr PR_URL --head FULL_HEAD_SHA --decision DECISION_FILE
```

The handoff returns current changed-file summaries, the PR diff command, every
supported failed workflow's failed jobs, references to existing source/log
archives and a `capture` fingerprint. Read the diff and relevant archive members;
there is no model subprocess or signature classifier. Missing logs or unsupported
check providers remain explicit blockers. The helper also includes failed jobs
from each attempt's job inventory, even when no displayed check points to them.

Produce a native diagnosis JSON using that fingerprint. For every listed run,
include `run_id`, `attempt` and `jobs`; every failed job needs `job_id`, `verdict`
(`unrelated`, `caused_by_pr` or `ambiguous`), `retryable`, `reason` and
`change_comparison`. An unrelated retryable job additionally needs nonempty
`log_evidence`, each entry containing the exact archive `member` and an `excerpt`
from that failed job's logs. For example, a job decision can be:

```json
{"job_id":456,"verdict":"unrelated","retryable":true,"reason":"The system package mirror failed DNS before project code ran","change_comparison":"The PR changes application formatting; it does not change the package mirror, hostname or runner network setup","log_evidence":[{"member":"test/3_Install packages.txt","excerpt":"Temporary failure resolving archive.ubuntu.com"}]}
```

Prepare eligible retries with the caller's resolved automatic allowance:

```sh
python3 runtime/dk-ci/scripts/ci_retry.py prepare --pr PR_URL --head FULL_HEAD_SHA --decision DECISION_FILE --diagnosis DIAGNOSIS_FILE --automatic-limit 3
```

The helper recaptures inputs, validates the report and returns existing or new
publication operation paths plus unresolved blockers. It does not send requests.
Each workflow is eligible only if every failed job has a supported unrelated,
retryable diagnosis. Independent eligible workflows can proceed while another
remains blocked, including when that workflow's metadata or log download is
unavailable. Shared PR input failures still block the entire diagnosis. The
retained intent contains the diagnosis and evidence references; no extra handoff
or change snapshot is saved.
Before an unsent operation dispatches, publication rechecks the PR changes,
workflow checks and retained source/log evidence. Changed inputs require a fresh
diagnosis in the same prepared operation. Retrying an independent workflow does
not invalidate an unchanged sibling diagnosis. Ambiguous job log directories
remain blocked because citations cannot identify their failed job uniquely.
Head/base SHAs, branch refs and repository identities govern diagnosis freshness;
unrelated repository stars, issue counts, timestamps or profile metadata do not.
Recollecting an attempt after repository descriptions or user profiles change
also preserves diagnosis. Original source/evidence JSON references remain intact;
fingerprints use normalized source contents, jobs and log archives rather than
the changed JSON reference paths. Changed repository/user identities, jobs, logs,
revisions and checks still require fresh diagnosis.
Accepted PR URL casing and trailing slashes are normalized before capture to
match publication, preserving diagnosis reuse for equivalent URL spellings.
When an unsent operation's original evidence is missing or corrupt, preparing
again can replace its references with validated equivalent recollection even
when the semantic capture is unchanged. Healthy original evidence remains in use;
replaced references and diagnosis remain in existing preparation history.
Existing diagnosis evidence and preparation history remain retained when changed
governing inputs require a fresh diagnosis in the same operation.
Before reusing a retained diagnosis or unsent intent, verify current original
guidance, permissions and applicable settings under the [shared PR contract](pr/contracts.md).
Managed work must also refresh its [governing inputs](task-state.md#governing-inputs-and-freshness).
The CI evidence fingerprint does not replace those caller checks. Reassess any
diagnosis affected by changed governing inputs while preserving its earlier evidence.

Publish each operation through `scripts/publish.py publish --operation FILE`.
The failed-job endpoint reruns only failed jobs and required dependencies,
preserving successful jobs. Publication reservations bound automatic retries per
PR/head (default three); zero disables them. Lower retained limits and original
caller counters remain in force across sessions. Preparing an operation reserves
its slot; resume the same intent instead of consuming another. Changed diagnostic
wording cannot create another operation for the same run/attempt.
Lowering the allowance also tightens an existing unsent operation. A later higher
limit cannot restore it; explicit one-shot user authorization can permit that
operation without erasing its original automatic reservation.
The tighter limit is saved before refreshing GitHub metadata or attempt evidence,
including when that refresh fails or is interrupted; resume the same operation.
An unsent operation can refresh current PR title/body metadata when prepared
again, retaining its identity and original preparation. Invalid user retry
identities are rejected before changing that operation or its allowance.

An uncertain dispatch remains pending. Use `scripts/publish.py reconcile
--operation FILE`; never resend based on an absent receipt. Reconciliation
requires exactly the next attempt, and a delayed or further advanced attempt
remains blocked. A verified operation opens one retained observation window via
`ci_wait.py --rerun-operation FILE`. After multiple workflow retries, supply the
latest newly verified operation and observe all current-head checks. The receipt
proves a retry occurred, not that CI passed. Resume the original review/fix/merge
task only with current successful checks; persistent failures report their job,
attempt and reason.

A user's explicit request to retry specified failures can exceed the automatic
allowance without resetting it. Prepare a normal `ci-rerun` publication request
for each specified exact run/attempt and add:

```json
{"user_retry":{"request_id":"stable-original-user-request-id","authorization":"Retry the failed Validate workflow once","source":"Reference to the user request"}}
```

Use the original request identity on resume. This authorizes one retry per
specified workflow, bound to its head and failed attempt; reusing that identity
for another attempt is rejected. Another unrelated workflow or repeated failures
need their own authorization or remaining automatic allowance. A general request
to fix or merge a PR grants bounded automatic recovery, not unlimited explicit
retries. Do not reserve an exhausted automatic caller budget for an explicitly
authorized retry; retain its original counters and the separate publication
intent. Exact-head observation and successful checks remain mandatory.
