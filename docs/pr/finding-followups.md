# Follow up on an unresolved finding

An earlier report does not permanently suppress an actionable finding. After at
least two new PR commits since its latest substantive report or fix request,
independently verify it at the current head and, when authorized, ask the author
for the correction still needed. Apply this rule to bots, people and earlier
workflow runs. Commit age proves neither a defect nor publication permission.

During inspection and discovery, examine the complete discussion and retained
original findings/dispositions, including previously reported findings. Group
reports and equivalent requests by the concrete finding, rather than by author,
wording or an entire bot summary. Include them among verification candidates
when they may remain actionable. A formatting-only summary edit does not create
a new substantive request or change an unchanged finding's original anchor.
Assess newly added or materially changed requests separately for each finding.

Give the independent native verifier original reports, current code and tests,
settled scope, complete discussion and relevant original publication operations.
Require a current `confirmed`, `fixed`, `rejected`, `uncertain`, `accepted` or
`deferred` disposition, with evidence. Exclude resolved threads, completed fixes,
rejections, uncertainty and explicit acceptances/deferrals from reminders.
Unresolved actionable or uncertain findings retain their usual readiness gates
even when no reminder is due. Do not rediscover or reverify unchanged valid work
solely to calculate age; use its original managed result while its governing
inputs remain current. Reuse, retry and cycle budgets retain their usual rules.

## Capture and assess

Use the existing [complete evidence handoff](supplied-evidence.md), then run:

```sh
python3 PACKAGE_ROOT/runtime/dk-pr-feedback-address/scripts/finding_followups.py inspect --cwd REPO --handoff PATH --sha256 HASH --pr PR_URL --head HEAD
```

This read-only helper verifies the handoff against live sources, captures stable,
complete PR commit membership and returns compact source/anchor candidates,
original publication references and a `context` fingerprint. It writes no state
and runs no agents. Default output includes membership count, captured target pins
when local proof is needed, and ages for source/operation anchor candidates;
complete commit membership stays internal to the fingerprint and plan validation.
Use `inspect --full-commits` with the same arguments only when the full proof is
needed. Both presentations return the same fingerprint. Read original discussion through the existing handoff and
only relevant journal operations by their returned paths. Native semantics must
identify every equivalent substantive request and the latest one for each
finding, including requests in review bodies, top-level comments and prior runs.
A recent equivalent request by any participant suppresses another reminder.

Review submissions provide their captured `commitId`; inline comments use their
associated review. Explicit commit links in other reports/requests are only
anchor candidates: the verifier must establish that the link identifies the
report/request head, rather than historical code cited as evidence. Do not infer
PR arrival from commit author/committer dates, comment `updatedAt`, wall-clock
age or target-branch ancestry. Do not silently use a review's old commit for a
substantive request newly added by an edit. Supply an evidenced request-head link
or leave that latest request's anchor unknown. Formatting-only edits retain the
unchanged finding's original review/link anchor, even though changed discussion
requires semantic reassessment.

Missing/rewritten latest anchors are uncertain. A later evidenced substantive request
can supersede an unanchored original report or verified receipt when the verifier establishes that
ordering. Incomplete/unstable commit lists or changed heads block calculation.
For complete histories within the endpoint's 250-commit cap, the helper uses API
parent links to prove all PR members reachable from the current head, then counts
members absent from the anchor's ancestry. No checkout is needed for this proof.
The API's chronological ordering is not PR arrival history: if A branches to B
and an older-dated X, merging X into B adds X and the merge after a request at B,
even if X appears before B in the list. Report, request and receipt anchors all
use the same ancestry calculation.

Beyond the cap, or when API parent evidence cannot prove the complete graph,
the helper supplements the stable list and GitHub total with full local Git
objects at the captured PR head and live target branch tip. It validates total membership and listed commits, requires each
anchor to be an ancestor of that head, and excludes target-only ancestry when
counting subsequent PR commits. It rechecks the live target before returning and
repeats capture for stability. Missing objects, shallow history, mismatched
counts/lists or changing source pins require restoration and recapture, with the
exact prerequisite in the diagnostic. Never substitute a timestamp guess or a
truncated list. Local inspection does not fetch, change refs or start agents.

Inspect existing relevant intents even when their comments are absent from a
discussion read. A verified equivalent request's captured head supplies its
anchor. PR identity matching uses the publisher's lowercase, trailing-slash-free
scope while retaining exact original source URLs and publication evidence.
Pending equivalent writes and other pending publication kinds require read-only
reconciliation; an unrelated pending review request permits independent replies
under the publisher's existing policy. Undispatched equivalent
intents require recovery of the existing operation rather than fresh wording or
a second intent. Verify unavailable/edited historical replies through
[reply recovery](publication.md#recover-a-previously-verified-reply) before deciding
whether any replacement obligation remains. Preserve original payloads, sources,
receipts and uncertainty; a historical receipt never authorizes resending it.

Return the native assessment in the existing verification result; a temporary
JSON extraction for the helper is sufficient. No follow-up registry is needed.
For example (substitute real references and full SHAs):

```json
{
  "head": "CURRENT_FULL_SHA",
  "context": "INSPECTION_CONTEXT_FINGERPRINT",
  "findings": [{
    "key": "empty-input",
    "source": "issuecomment-901",
    "disposition": "confirmed",
    "evidence": "Original independent verification result reference and current code/reproduction evidence",
    "requests": [{
      "reference": "issuecomment-901",
      "commit": "REPORT_FULL_SHA",
      "anchorEvidence": "The source labels this link as the report head; the finding was unchanged by its formatting edit."
    }],
    "latestRequest": "issuecomment-901",
    "operations": [],
    "correction": "Please return an empty list before indexing input in code.py:42."
  }]
}
```

`source` identifies the original finding; `requests` includes that report and all
equivalent substantive requests in the discussion. `latestRequest` selects the
latest substantive request reference or equivalent retained operation path.
`operations` lists all relevant equivalent intents/receipts, including ones not
yet visible remotely. Review-based requests default to their `reviewCommit`;
other requests need an explicit `commit` from `linkedCommits` and a finding-specific
`anchorEvidence` explanation. Use `commit:null` when that binding is unsupported.
The helper counts from the newest evidenced equivalent request/receipt and blocks
an unknown latest anchor. The verifier owns completeness, substantive equivalence,
ordering, disposition and the targeted correction; helpers do not infer them.

## Plan and publish

Run the same command with `plan --assessment FILE` instead of `inspect`. It
recaptures live discussion, commits and original intents and rejects a changed
context or head. Results distinguish `excluded`, `uncertain_age`,
`recover_required`, `recent_request` and `eligible`. An unchanged rerun or one new
commit does not produce another eligible request after a substantive request.
The plan is transient; retain original native decisions and publication journals
through their existing task lifecycle, without copying the rendered plan.

For an eligible independently confirmed finding, review the generated exact
public body and use its `request` through the configured publisher and ordinary
[publication journal](publication.md). An inline reply stays under the original
thread root and retains the finding comment's own source. Review bodies and
top-level findings receive source-linked `reply-comment` responses beginning
with `In Reply To:`. Every response links the earlier finding, identifies the
current head and asks the author for a concrete supported correction. Embedded
file/line references never create a new inline destination. Follow-ups use this
source route rather than publishing a duplicate new inline finding.

Refresh the plan immediately before preparing/publishing, and recheck current
authorization, head, scope and discussion at that boundary. Exclude the original
finding from new-review duplicate publication when it is handled by this route.
Posting remains disabled for analysis-only or denied policy. Missing native
verification/evidence stays incomplete. Use the original operation on interruption
and reconcile uncertain writes read-only before further sends. Do not regenerate
wording to evade an existing intent. Publication permission grants no approval,
merge, fixes, thread resolution or budget extension.
