# Default completion action

Read and follow [waiting and communication](waiting-communication.md).
Send brief pending updates when the host requires them. Report changes that affect
progress, scope, failures, or required decisions as soon as they are known.
Give nested workers the retained runtime path to `waiting-communication.md` and
require them to read it.

## Verify prerequisites

Act only on the identified PR and the authorized actions below. Never merge the PR.
Recheck live GitHub and Git state before every remote mutation. The supplied run
record retains operational history; verify remote results against live evidence.

Validate the active iteration run and its required configured-reviewer result
artifact against the final tree, scope, and Decisions. The `--review-complete`
argument alone does not prove coverage. Report executed and reused reviewers,
reviewed heads and trees, and skipped reviewers with their reasons. Report a
final-cycle fix without review coverage as a limitation, even when CI succeeds.

Explicit human acceptance recorded by `run_state.py accept-review-failures` permits
completion with those exact provider failures. Preserve the incomplete result and
diagnostics. Read the gate's `accepted_failures` and `failure_acceptances` evidence
and report the limitation. Apply an existing acceptance to the failures it covers
without asking again.
Omit `--review-complete` when the underlying result is incomplete. All checks for
the current tree, scope, findings, preparation, CI, and commit-series cleanup remain
required.

Validate the parent's current ready preparation and engineering verification
artifact. Verify behavior, code and test placement, test sufficiency, compatibility,
and cumulative complexity even when the first review finds no issues. Use the
parent's verified commit-series cleanup artifact. Do not assess, squash, split, or
otherwise rewrite the series again.

Report the blocker and perform no completion actions if any condition holds:

- Review failures lack applicable human acceptance, or reviewer coverage is missing.
- Actionable work within the PR's scope remains.
- CI on the current head has neither succeeded nor received freshly verified
  acceptance as `known_flakes` or `accepted_exceptions`.
- The PR head changed outside this run.
- Scope, design, or validation questions remain unresolved.
- Sources are stale or required synchronization is pending.
- Commit-series cleanup evidence is missing, stale, or incomplete.

Custom completion prompts cannot bypass these requirements.

## Run the completion gate

Before acting, run:

```sh
python3 scripts/completion.py --cwd REPO --pr PR_URL gate \
  --start-head START_SHA --final-head FINAL_SHA \
  --preparation-file PREPARATION.json --verification-file VERIFICATION.json \
  --run-record RUN_RECORD.json --review-complete --series-assessment ASSESSMENT.json
```

Omit `--review-complete` for an incomplete result with recorded human acceptance.
Add `--actionable` when work remains. Require `gate: clean`.

For failed CI, the gate reloads current CI exceptions and linked issues, checks
every failed test and step, and verifies the exact head and run attempts through
the shared policy. Saved CI acceptance JSON cannot replace these checks. Repeat
the live gate before subsequent mutations when CI or issue state may have changed.

Thread resolution is independent of overall review and CI completion. It should
already have run for fully answered threads with resolution authorization.
Coordinate any remaining cleanup with the existing reply agent as described below.

## Check the PR title and description

Check both fields against the final published diff, linked issue, template, and
settled Decisions. If either is stale, draft and review through `dk-pr-description`.
When scope and Decisions both change, compose and publish the reviewed body through
the Decisions publisher in one guarded edit. Refresh verified context and
preparation afterward. Preserve unrelated prose and title. Record whether an edit
was needed and verify the published fields.

The full commit series was assessed and any needed cleanup published before this
handoff. Use that verified result. A request solely to squash or regroup commits is
editorial feedback. Record it as ignored without a reply, resolution, or blocker.
Handle any code or description defect in the same request separately.

## Audit inline threads

Run `python3 scripts/completion_threads.py --cwd REPO --pr PR` to audit every inline
review thread. Require `complete: true`, the expected head, and a `threadCount`
matching the `threads` list. The command fetches every thread and comment,
including resolved threads, and requires two matching snapshots.

For a review link, preserve the anchor. Limit the audit worklist and mutations to
`selection.threadReferences`, while retaining all threads as context. Read each
selected open thread's entire conversation and the published code. Classify each
request as fully answered, still open with a specific reason, or squash-only
editorial feedback.

A fully answered request on the published head must have a verified adequate reply
in its own thread and be resolved. An existing explanation qualifies when its
claims remain supported. Retain its URL and normalized `text`; it need not match
a newly proposed acknowledgement. An earlier promise to fix does not qualify.
Already resolved threads need no mutation. Leave disputed, deferred, partially
answered, and pending-decision threads open and report each reason. Leave
squash-only threads alone and exclude them from required cleanup.

Coordinate missing replies with the run's existing reply agent, using its canonical
ID and the same run record. Do not spawn another reply agent or draft replies in
the completion agent. Require the reply agent to follow
[the script controller](../../dk-pr-reply-review/references/reply-controller.md).
Its `next` action fetches both complete live snapshots and directs inline matching
from all sources before top-level publication. Wait for verified inline results
and retain the request mappings and publication journals.

Record the existing iteration resolution authorization in the run record as the
controller requires. Have the same reply agent submit
`reply_passes.py resolve --assessment FILE` for each fully answered thread. Each
assessment pins the head, scope, conversation, verified reply, and supporting
evidence. Reuse an adequate reply and resolve only. Otherwise wait for verified
reply publication before assessing resolution. Preserve resolution intents and
receipts. Use `reconcile` after uncertain outcomes; do not issue direct resolution
mutations.

After these actions, fetch another complete snapshot and reconcile every selected
thread. If a fully answered request lacks a verified adequate reply or its thread
remains unresolved, report incomplete thread cleanup as a completion blocker.
Do not report success while that blocker remains.

## Audit top-level feedback

Audit this feedback even when no inline threads exist. Reuse the same reply agent
and controller state for the top-level phase. Before preparing a top-level batch,
its `next` action must refresh both complete feedback snapshots after inline
publication and resolution. Include resolved threads and later comments.

Reconcile each actionable request against published code, retained source mappings,
and live replies. Skip informational feedback, squash-only requests, and equivalent
answers expressed differently. Retain verified answer URLs. Only unanswered
actionable requests without a suitable inline thread receive a source-linked
response at the PR level. Group them by source review or comment, with an exact
quote and answer for each request.

If new feedback needs an inline reply, follow the controller back to the inline
phase. Resolve eligible fully answered threads, then refresh both snapshots before
top-level publication.

Each nonempty batch needs its own independent validator approval and permits one
correction. A final rejection or uncertain publication stops further replies,
including completion replies. Empty phases skip publication. Restoring or
dismissing a still-present stale request requires independent validation even in
an empty batch.

Inspect `stale_answers`. Stale mappings cannot satisfy cleanup or authorize
resolution. Held requests remain outstanding. Preserve holds for pending user
decisions. Do not resolve top-level feedback or post a general summary.

Call `next` after publication to refresh and verify cleanup. Its `done` action does
not prove cleanup is complete. Inspect `unresolved_threads`, `cleanup_complete`,
and outstanding holds. An open squash-only thread may remain as a documented
editorial exclusion from the inline audit; report that exception explicitly.
Report each outstanding actionable request by source URL and reason. Missing
responses to completed requests make feedback cleanup incomplete.

## Request reviews again

Proceed only when the final published head has successful CI or freshly verified
CI acceptance (`known_flakes` or `accepted_exceptions`), and both inline and
top-level feedback cleanup are complete.

Request reviews from eligible human and bot authors of submitted reviews. Paginate
all submitted reviews and pending review requests. Exclude the PR author, anyone
with a submitted review on the final head, anyone already pending, and anyone
already requested by this run for that final head.

Save the same completion-gate arguments as a JSON array in GATE_ARGUMENTS, including
the active `--run-record`. Run:

```sh
dk.py --invocation INVOCATION_FILE --pr PR_URL publication-actions request-review prepare \
  --head FINAL_HEAD --run-record RUN_RECORD --reviewer LOGIN --gate-arguments GATE_ARGUMENTS
dk.py --invocation INVOCATION_FILE --pr PR_URL publication-actions request-review publish --operation FILE
```

The helper owns complete pagination, current eligibility, fresh completion checks,
and records of attempts for the final head. Follow its diagnostic action. Report
rejected or unconfirmed requests.

## Report the result

Report:

- Final head, cleanup outcome and assessment path, and title/body check or edit.
- CI evidence. For `known_flakes`, say “CI red; accepted known flakes” and include
  issue links and raw `failure`. For `accepted_exceptions`, say “CI red; accepted
  configured exceptions” and include setting IDs, scopes, reasons, and raw `failure`.
- Discovery limits and missing evidence.
- Replied and resolved threads, source-linked top-level replies, and every
  outstanding actionable request with its source URL and reason.
- Review requests and any blocked or rejected action.

Stop on a changed head, incomplete pagination, or uncertain mutation. Report the
unverified state.
