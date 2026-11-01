# Workflow behavior evaluation

Issue [#83](https://github.com/dk-code-factory/dk-workflows/issues/83) adds an offline
trace evaluator and reproducible **synthetic policy examples**. These examples test
the evaluator's plumbing and rejection controls. They do not execute an agent,
measure context savings, establish real orchestration compliance, or resolve the
separate preparation-refresh issue #76. No paid model trials are part of this work.

Run the checks from a development checkout:

```sh
python3 -m pytest -q tests/test_workflow_behavior.py
python3 scripts/check.py
```

Evaluate the checked-in example with a new report filename in an existing private
directory outside the checkout:

```sh
python3 scripts/evaluate_workflow_trace.py \
  tests/fixtures/workflow_behavior/representative.json \
  --report /ABSOLUTE/PRIVATE/behavior-report.json
```

The evaluator writes a new `0600` full report and prints a small receipt. It refuses
to overwrite a report or write one inside this repository. Exit codes are `0`
(passed normalized checks), `1` (violations), `2` (invalid trace/storage), and `3`
(unknown observations). A failed trace can also contain unknown families. Reports
are diagnostic only: they grant no preparation, review, publication or completion
authority. All normal workflow gates, coverage requirements and budgets still apply.

## What the examples check

| Family | Positive example and rejection controls |
| --- | --- |
| Waiting | Three unchanged timeouts; required updates without new evidence; transitions caused by notifications. Timeout/update-driven agent status, file, Git, GitHub, provider and CI queries are rejected. |
| Communication | Stable brief host-required updates by their recorded deadlines; nonblank failure/decision reporting before the next action; automatic completion once. Heartbeats, duplicate completion, missed updates and delayed or blank material reports are rejected. |
| CI | One waiter for a head with three observations at the existing 30-second cadence. Each new session may observe immediately. An explicitly captured timeout deadline permits its terminal shortened interval. Concurrent waiters, extra check queries and premature observations are rejected. Nondefault cadence remains unknown in this evaluator. |
| Handoffs | New agents with `fork_turns: none`, task/actions, artifact pins and no inline conversation dump; fresh reviewer identities; permitted reply/fixer reuse. Inherited context, broad inline context, reviewer reuse and wrong roles/stages are rejected. |
| Output | Bounded receipts/pages with a retained hashed complete artifact; help discovery once. Full conversation/diff/log output and repeated help require a written concrete diagnostic need and an earlier notification reference. Missing retained output is rejected. |
| Reuse | Operational preparation refresh with identical governing inputs uses zero model calls; changed scope is reassessed. Completed checkpoints survive compatible interruption/resume; unfinished checkpoints rerun. Changed heads/governing inputs or missing evidence cannot authorize reuse. |

The `representative.json` fixture and its `synthetic-capture.jsonl` contain authored
observations, not a recording from Codex or Claude. `negative-controls.json` changes
one observation at a time; tests retain new synthetic raw bytes and recompute their
hashes so controls fail on policy, rather than accidental hash mismatch. Additional
controls cover invalid fields, references, ordering, hashes, output storage and
unavailable observations. The entrypoint cases exercise shared waiting/communication/output/reuse contracts
for iteration, standalone review, Decisions, feedback and reply. They select
relevant worker roles and leave absent CI/complete handoff coverage unknown. They
include a notification-justified read and reject an invalid future justification.
They do not invoke those workflows or show their host behavior.

Existing executable tests remain the evidence for real helper implementation:
[artifact receipts and lossless paging](../tests/test_artifact_results.py),
[shared help references](../tests/test_cli_reference.py),
[preparation and metadata stability](../runtime/dk-pr-decisions/tests/), and
[session/checkpoint behavior](../runtime/dk-pr-review/tests/).
This evaluator does not reimplement the production compatibility or review gates.

## Normalizing a real observation

There is deliberately no automatic host-log importer. Host versions expose
different subsets of messages, tool arguments, yields and notifications. A human
normalizer must retain and inspect the complete originals. Never fill a gap by
copying a synthetic event or asserting that a policy passed.

1. Capture the original tool/message records and helper artifacts privately,
   outside the repository and plugin. Record the host/version, package version and
   commit, retained runtime identity, task/scope, host communication requirements,
   and capture start/end in a separate retained manifest. Preserve complete raw
   files before producing summaries; record SHA-256 hashes of their exact bytes.
2. Create a schema-v1 JSON trace using the example's structure. Set `provenance`
   to `manual-host` and the actual `entrypoint` to `iteration`, `review`,
   `decisions`, `feedback` or `reply`. `sources` maps stable source IDs to paths
   (relative to the trace, or absolute) and SHA-256 hashes. Include the raw capture,
   provenance manifest, actual complete outputs and every pinned handoff artifact.
3. Translate each observed action to a supported event, retaining its unique ID,
   nonnegative monotonic `at_ms`, and `evidence: {source, line}` locator into the
   original text. Locate JSON records by the line containing the relevant record;
   keep a separate normalization note for multiline records. Do not edit raw bytes
   to make them match the normalized interpretation. Record all intervening actions
   in the evaluated interval; omitting an inconvenient query invalidates a claim of
   complete observation.
4. Record the exact causal event ID for queries, updates and transitions. A query
   that follows a timeout but serves an independently evidenced failure should
   point to that failure notification. A timeout or user-update tick alone cannot
   justify another query. Queries require a direct reference to an earlier observed
   notification. Query-to-query chains and output/preparation or other incidental
   event justifications are unsupported and invalidate the trace; the normalizer
   must identify the original observed notification rather than imply causality.
   Normalize genuine external instructions/status requests
   as result notifications with the original source locator; do not invent them.
   For a CI waiter whose timeout shortens its final sleep, include the optional
   integer `deadline_ms` on `ci_start`, in the same time coordinates as `at_ms`.
   Capture the actual invocation timeout and session start in retained evidence;
   derive the deadline from them, never from an observation you want to excuse.
   A shortened interval is accepted only when the previous observation precedes
   that deadline, the new observation reaches it, and the next CI event for that
   head/waiter pair is `ci_end`. Without a deadline, every interval within the
   session must meet its cadence. Ending a session resets its observation history.
5. For handoffs, copy the actual task/actions and relevant inline broad context;
   list the runtime, head/base revisions, Decisions, preparation and engineering IDs,
   architecture packet, invocation, CLI reference, waiting policy and role-specific
   contract pins. Artifact-valued pins reference source IDs; revision/ID pins are
   immutable strings. Inspect the original handoff to verify the selected contract
   applies to that role. The evaluator checks structure, identity and stage reuse;
   it cannot judge the semantic sufficiency of task wording or contract contents.
6. For preparation, normalize **all** governing inputs into `governing` (`tree`,
   `base`, `scope`, `decisions`, `rules`, ordered `reviewers`, `runtime`). Use hashes
   of complete canonical inputs from retained artifacts, rather than an arbitrary
   `compatible` label. Include any otherwise unknown governing fields in the
   corresponding complete evidence hash. Preparation IDs retain exact provenance.
   `tree`, `base`, `scope` and `runtime` must be nonblank identity strings.
   `reviewers` is an ordered list of nonblank identities. `decisions` and `rules`
   may be nonblank canonical hashes/identities, lists of nonblank identities, or
   objects mapping nonblank identities to nonblank hashes/identities. Empty lists
   or objects represent genuinely empty Decisions or rules collections; blank
   strings and malformed collection members are invalid.
   Equality of normalized governing inputs determines this diagnostic's reuse
   comparison. Normalize actual helper-reported call counts and checkpoints.
   Production preparation/engineering-ID gates remain authoritative.
7. Mark each `coverage` family `complete` only if its observations are fully
   available for the interval; otherwise use `unavailable`. An absent family or a
   family lacking representative anchors is unknown even if labeled complete.
   Missing source files make affected observations unknown; missing evidence used
   for retained-output or checkpoint/preparation reuse also produces a violation.
   Mismatched hashes and malformed/unsupported events are invalid, never passing.

`SCHEMAS` and `OPTIONAL_FIELDS` in the evaluator declare the event fields; extra fields, including
self-reported `passed` labels, are rejected. `ROLES`, `PINS` and `GOVERNING` declare
the supported handoff and reuse model. Event timestamps measure recorded ordering;
material-report checks require the next action to report the notification. They
cannot establish wall-clock reporting latency across missing host observations.
The fixture's 160-character pending sentence and 4096-byte receipt/page bounds are
local diagnostic examples, not production token limits or a replacement for native
helper pagination. Retained complete evidence must still be inspected when needed.

Hash checks establish byte identity and resolvable source locations. They do not
authenticate a host capture, prove that a source supports a human classification,
prove an artifact complete, or infer causality from prose. A written `need` and `need_event` must be
reviewed against the referenced diagnostic; the evaluator checks the explanation
is present and the notification precedes the output, rather than judging prose.
Even a `manual-host` report retains `agent_behavior_verified: false`. A real
behavior-evaluation report must separately identify who reviewed the normalization,
which observations were actually available, the capture's completeness limits and
any unresolved semantic judgments. Unsupported observations stay unknown; do not
reinterpret an evaluator pass as proof of an agent's behavior or token savings.

## Supported version-adoption comparison

Use separate, disposable development checkouts at two pinned release commits and
separate private state/data directories. Run this same offline fixture/evaluator
at each revision that contains it (for an older release, keep the evaluator in an
independent analysis checkout and compare captured inputs). Record exact commits,
manifest versions, Python/host versions and fixture hashes. Run existing helper
plumbing tests with mocked providers as usual. Compare diagnostic findings and
available observations; retain both full reports. Tests using the same authored
trace establish evaluator reproducibility, not differences in agent behavior.

For an installed-host adoption check, finish every active invocation with its
original retained runtime first. Use the supported host marketplace/plugin update
commands documented in [README](../README.md#updates), then start a new session and
new invocation. For side-by-side historical examination use isolated development
checkouts and independent state, never mutate installed plugin caches, override an
active invocation's runtime root, or replace its retained package. No paid live
agent trial is required or authorized by this document. Any future real comparison
needs separately authorized captures of comparable tasks and the same normalization
review, with unavailable observations explicitly unknown. Do not claim savings
without actual comparable measurements.
