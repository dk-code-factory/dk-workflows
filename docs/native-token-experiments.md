# Native token experiments

The [experiment plan](token-usage-experiment-plan.md) defines the ordered
candidates and acceptance gate. These are test-only exercises, not a production
execution engine or model launcher. Native agents choose their actions from
frozen workflow guidance. Offline task/domain results remain stand-ins; full
recipe regressions separately cover recovery and publication behavior.

Use new external arm directories for every trial. The iteration exercise accepts
`init before RUNTIME_SHA GUIDANCE_SHA` to freeze a baseline independently of the
runtime. `init after RUNTIME_SHA GUIDANCE_SHA` freezes checkout guidance. Pin the
interface, runtime dependencies, source, guidance and native assignment before
launch. Preserve actual host model, effort and service tier. The three execution
orders are baseline/candidate, candidate/baseline, baseline/candidate. Compare
each experiment with the latest accepted guidance, including prior accepted
changes only. Keep original assignments, conversations, telemetry and traces
private. Committed reports contain hashes, normalized counts and assessments.

Read per-response `token_usage_record` records with the test-only
`tests/fixtures/native_usage.py`; cumulative usage events are not additional
responses. Account for the measured parent turns and every descendant, including
handoff preparation, waiting and result consumption. Inherited conversation
records do not represent newly billed responses: deduplicate response IDs
globally and select only the explicit measured thread/turn scopes. Never infer
complete telemetry merely from the records that happen to be present. Retain
native terminal outcomes and reconcile expected response/descendant inventories
with execution evidence. Missing telemetry or unresolved native work prevents
acceptance.

Input includes cached reads and cache writes. Report those separately and
subtract both to calculate fresh input. Output includes reasoning; report
reasoning separately without adding it again to total tokens or output cost.
Cost estimates apply to each response before aggregation so long context
multipliers are not applied to an entire run by accident.

The pricing basis, retrieved 2026-11-05, is the official
[GPT-6.1 Sol model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol).
Standard rates per million tokens are input $2, cached input $0.10, cache writes
$2.50 and output $10. Above 272,000 input tokens, apply 2× to input/cache rates
and 1.5× to output for the complete response. Fast rates are 2× standard,
ultrafast 6×, and batch/flex 0.5×. These are API-equivalent estimates, not account
billing; subscription usage has no long context surcharge. Unknown controls or
pricing prevent an acceptance claim.

All correctness checks and a fresh independent review must pass. Require at
least 10% median paired total-token reduction, improvement in two of three pairs,
and no median estimated-cost increase. Preserve findings, required coverage,
preferences, recovery, counters and final CI. Record limitations even when the
gate passes: three pairs establish bounded evidence for the measured scenario.
Reject unsupported controls, missing evidence and inconclusive candidates.

## Experiment 1: coordination

The [three-pair report](evidence/145-experiment-1.json) rejects the additional
single-coordinator/notification guidance. Median total usage increased from
805,422 to 1,641,527 tokens; no pair improved. Median API-equivalent estimated
cost increased from $0.2299976 to $0.3575996. The candidate guidance was removed.

All six runs preserved required findings, selective refresh and retained state.
One candidate retried an unavailable optional provider without resolving its
blocker; strict native adherence therefore failed independently of performance.
Failed exploratory commands and progress-write attempts remain included in the
measurements. Both arms prohibited relay agents, so the sample cannot establish
the benefit of removing actual relay coordinators. The normalized report retains
the rejected patch as nonexecuting evidence; raw traces remain private.

## Experiment 2: review cycles

The [three-pair report](evidence/145-experiment-2.json) rejects the additional
coverage-preflight and stage-completion guidance. Median total usage increased
from 674,573 to 852,207 tokens; one pair improved. Median estimated API cost rose
from $0.2015252 to $0.2329612. The candidate guidance was removed.

Independent review reproduced all raw accounting and verified retained findings,
selective refresh, required coverage and final fixture outcomes. Candidates did
not retry the unavailable optional provider, while one baseline did. A candidate
progress append failed without losing original progress or immutable results.
Strict all-trial adherence therefore failed as well as the numeric gate. Hook
execution remains separately covered by regressions.

## Experiment 3 protocol: existing context and bounded verification

Compare inline continuation with a fresh native verifier, in three alternating
pairs separately for short and long existing contexts. The task assesses current
retained coverage, selective invalidation, original findings and sourced
dispositions, incomplete optional work, counters and missing completion evidence.
It does not execute pending reviews or complete the workflow.

The setup turn reads an immutable orientation corpus before the task assignment.
Short context contains the task-state, native-handoff, preference and reviewer
contracts. Long context adds complete frozen production Python and governing
Markdown sources. Original source chunks preserve exact bytes and full output;
unread or truncated evidence must be retrieved or reported incomplete. Source
bytes and the first parent task-response input establish the observed context
classes; no filler or compaction-setting changes are used.

Both task arms receive identical verification instructions and source access.
Candidate parents prepare one fresh verifier, wait on its native handle, validate
its terminal report and consume the result. The task-specific preference handoff
includes a scoped conditional instruction to preserve original finding URLs;
original reviewer selections remain separately preserved to avoid invalidating
the task whose reuse is being checked. Missing evidence requires retrieval or an
incomplete report. Neither arm may claim pending tasks, allocate cycles, invoke
domain work, publish, or infer final-head CI success.

Report task, setup and combined usage separately. The primary comparison concerns
a task from an existing context; task-phase savings do not imply whole-session
savings. All task-phase parent preparation, waiting, result consumption and
descendant responses count. Native launch and terminal records reconcile the
recursive descendant inventory; inherited history is deduplicated by response ID.
Report native tool calls for parent and descendants separately, alongside their
total. Preserve full original final outputs privately and commit only normalized
metrics, hashes, quality checks and independent decisions.

Independent orientation setup may overlap once its inputs are frozen. Keep the
measured task phases within each context class in their declared alternating
order. In this sample, long B1's task also overlapped short C3's task; later long
orientation overlapped an earlier long task. All usage remains counted. Shared
cache and service effects are limitations; these experiments make no global
serialization, strict cache-isolation or elapsed-time claims.

The [twelve-trial report](evidence/145-experiment-3.json) rejects delegation in both
context classes. Short median task tokens increased from 444,814 to 1,033,698;
long median task tokens increased from 1,230,168 to 2,540,372. No pair improved.
Median task API-equivalent cost increased from $0.1711008 to $0.3711368 for short
context and from $0.2296960 to $0.5722448 for long context. Setup and combined
comparisons also failed the gate. The candidate guidance was removed.

Independent review confirmed all usage, source pins and required assessment
content. Strict short-context correctness failed because one parent's structured
report repeated coverage keys, overwriting Boolean values with hashes when
parsed. Its child report and parent prose retained the correct conclusion; the
original output remains unchanged and the defect is recorded. Long-context
assessment correctness passed. These bounded verification trials do not support
a broader conclusion about investigation or general code review.
