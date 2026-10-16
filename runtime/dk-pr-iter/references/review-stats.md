# Private reviewer statistics (API v1)

Collection is enabled for future `iteration` preparation runs launched by
`dk-pr-review/scripts/run_reviewers.py`. It reads the latest recorded cycle and
cycle retry from the private operational record. `--stats-cycle N` and
`--stats-attempt STABLE_ID` provide explicit identities when handing off a cycle;
reuse them on resume. Standalone preparation does not collect statistics.
Configured parent skills receive all attribution; nested agent roles do not become
separate reviewers. Missing cycle/preparation metadata warns on stderr and does
not alter the PR review result. Operational counters are unchanged.

Each configured invocation has a stable run ID and reviewer attempt ID, repository,
PR, cycle, preparation ID, pinned head, prompt fingerprint, explicit status, local
duration, final structured findings and private evidence paths. All configured
reviewers start as `not_run`; `running` after interruption is unknown, never clean.
Resuming the same run reuses terminal results. Legacy operational record casing is
read without moving or resetting counters. Use a new attempt identity for an
explicit retry; completed earlier runs and all evidence remain retained.

Storage uses `shared.private_state` locking/atomic writes under
`$XDG_DATA_HOME/dk-pr-iter/HOST/OWNER/REPO` (or `~/.local/share/dk-pr-iter`).
The ledger is `review-stats.json`; replayable mutation payloads, raw responses,
execution/correction attempts, pinned handoff, preparation, patch and PR metadata
live under `review-stats-artifacts`. Files are private and outside the checkout.
`review-stats.md` is the repository report, linked both ways with the local user's
`dk-pr-iter/review-stats.md`. JSON evidence remains the detailed source of truth.
Provider/model/usage are null unless exposed by execution results. Built-in Codex
and Claude adapters retain correction attempts separately; parent duration and
leaf usage are never added together. Custom adapters without telemetry have
unknown correction/usage data. Collection/render failures warn and leave retained
payloads for replay without changing review findings or completion status.
If private initialization fails, `review-stats-recovery` beside the external pinned
patch retains `begin` and `record-review` payloads and execution evidence. Replay
them once storage recovers; an unknown cycle must first be supplied from the saved
iteration handoff. The saved explicit run ID keeps review replay attribution stable.
Collection refuses destinations inside the source repository or review checkout.

## Coordinator assessment

The runner leaves an analytics-only `review-stats.json` sidecar beside the pinned
patch; it does not add fields to reviewer/aggregated finding JSON. Pass that path
through review-cycle handoffs, never the report history. It lists each review's
finding IDs, exact reported findings, and default canonical issue IDs. Identical
findings deduplicate by path, line, message and fix, ignoring severity, within a PR.
During the ordinary assessment, link differently worded or relocated observations
of the same defect (including prior cycles) to one canonical issue ID. No judge or
extra model call is needed. IDs are repository/PR scoped; do not merge across PRs.

Reuse the existing `thread` validity classification and `assess` scope record.
Retain those explanations/evidence once, then save compact links as a JSON list:

```json
[{"id":"cycle-1-assessment-1","reference":"finding-reference","head":"FULL_SHA",
  "finding_ids":["f-ID-REVIEWER-A","f-ID-REVIEWER-B"],
  "canonical_issue":"i-CANONICAL-ID","severity":"Minor"}]
```

Run `run_state.py --repo HOST/OWNER/REPO --pr N review-assess --file LINKS.json`.
`valid`, `pre_existing` and `already_fixed` map to confirmed; `unsupported` maps to
rejected. Scope exclusions, decision holds and deferred size limits remain separate
from validity. A previously confirmed issue fixed at a later head retains credit.
Use the low-level assessment API for explicitly unverified findings or corrections;
never guess a semantic verdict from a transport failure. Analytics errors are
missing-data warnings to the coordinator, not PR-result blockers. Keep `LINKS.json`
for replay and report any missing collection.

## Python and JSON API

Load `scripts/review_stats.py` using `importlib.util.spec_from_file_location`.
`API_VERSION = 1`; stored ledgers and returned summaries/details are versioned.
Mutators: `begin_review(pr_url=..., cycle=..., head=..., preparation_id=...,
reviewers=[...], attempt='1', run_id=None)`,
`record_review(repo, run_id, reviewer, status=..., findings=[...], **metadata)`,
`record_assessments(repo, assessments)`, `render_reports()`.
Queries: `summary(repo=None, reviewer=None)`, `repositories()`, `details(repo, run_id)`.
Queries never write or call external processes. `summary` returns `total: null` for
missing data, report paths and explicit incomplete-status warnings.
Severity transitions and warnings are aggregated counts; individual observations
and their history stay in `details` and artifact paths to keep summary context compact.

CLI responses wrap results in `{"api_version":1,"data":...}`:

```text
review_stats.py summary [--repo HOST/OWNER/REPO] [--reviewer SKILL]
review_stats.py repositories
review_stats.py details --repo HOST/OWNER/REPO --run RUN_ID
review_stats.py begin --file RUN.json
review_stats.py record-review --repo HOST/OWNER/REPO --file REVIEW.json
review_stats.py assess --repo HOST/OWNER/REPO --file ASSESSMENTS.json
review_stats.py render
```

`begin` takes the Python keyword payload. `record-review` takes `run_id`, `reviewer`,
`status` (`not_run|running|completed|failed|incomplete|exhausted`), `findings` and
optional `duration_seconds`, `prompt_fingerprint`, `correction_retries`, `provider`,
`model`, `usage`, `artifacts`, `reason`. Completed findings use the existing five-field
contract. Terminal results are immutable; identical replay is idempotent.

`assess` takes a list with exactly `id`, `finding_id`, `canonical_issue`, `verdict`
(`confirmed|rejected|unverified`), `severity` (`Major|Minor|Nit`; null only when
unverified), `disposition` (`actionable|excluded|deferred|fixed|unknown`), `evidence`
(nonempty reference list), and `reason`. Reuse an ID to replay an identical assessment.
Use a new ID to correct verdict, severity or canonical linkage. Latest assessment
per observation wins, and every prior assessment/link remains in history. Corrections
can merge or split deduplication without losing observations. Replaying an old ID
never rolls back a newer correction. Retained review/assessment payloads can be fed
back into `record-review`/`assess`; run replay artifacts contain the `begin` identity
plus initial review state, so extract its identity fields before replaying `begin`.

## Metric definitions

Reported/outcome/scope metrics count observations (identical duplicates within one
invocation count once); reported severity stays separate from validated severity.
Unassessed findings are unverified, with unknown scope. Repeated observations across
cycles remain visible. Confirmed-issue totals deduplicate by repository, PR and
canonical link across cycles; each reviewer gets at most one credit per issue and
the overall issue total counts it once. The latest confirmed assessment supplies the
canonical severity. Shared means more than one configured reviewer confirmed that
issue. Exclusive requires only one credited reviewer plus a run in which every
configured reviewer completed the same pinned review and all findings were assessed;
otherwise exclusivity is unknown. Single-reviewer runs cannot establish exclusivity.
Reviewer filtering preserves global overlap information.

Precision is confirmed / (confirmed + rejected), including excluded/deferred real
issues and excluding unverified observations; each severity and total has a sample
size, and an empty denominator yields null. Completed, clean (completed with no
reported findings), failed/incomplete, exhausted and correction-retry counters describe
operations separately. No missing data becomes a measured zero, recall or overall score.
