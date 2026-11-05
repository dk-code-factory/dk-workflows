# Publication and recovery

Confirm that the caller authorized the concrete action. Prepare the exact payload and expected live identity. Save intent before dispatch. After an uncertain write, reconcile read-only before preparing or attempting a replacement. Never reset pending intent after a timeout. Compare actor, target, exact payload, head, lease and remote receipt. Missing or ambiguous objects remain unresolved. Keep confirmed publications recorded even if readiness changes. They do not authorize another write.

Run `python3 PACKAGE_ROOT/scripts/publish.py prepare --file REQUEST.json` to save intent and get an operation path. `publish --operation FILE` checks current state and sends at most once. `reconcile --operation FILE` only reads. Repeating publish for pending or verified operations also only reads. Return the operation path and reuse it on resume. Other publishers may reuse `runtime/shared/publication.py` with their own check, send and observe callbacks.

Requests:

```json
{"kind":"edit-pr","pr":"https://github.com/owner/repo/pull/7","head":"FULL_HEAD_SHA","fields":{"title":"Reviewed title","body":"Reviewed body"}}
```

```json
{"kind":"reply-inline","pr":"https://github.com/owner/repo/pull/7","head":"FULL_HEAD_SHA","parent":123,"source":{"kind":"inline_comment","id":456,"url":"https://github.com/owner/repo/pull/7#discussion_r456","thread_id":"PRRT_original"},"body":"Verified answer"}
```

Select reply destinations from the structured original PR evidence. An actual inline review comment uses `reply-inline` in its originating thread; `parent` is the root comment's database id, including when answering a later reply. Preserve that later comment's own id and exact URL as `source.id` and `source.url`. Retain the thread node id as `source.thread_id`. Do not substitute a review id for an inline comment database id.

A review body or top-level comment without an inline parent uses `reply-comment`. Begin its exact body with `In Reply To: [descriptive source label](https://github.com/owner/repo/pull/7#pullrequestreview-789)`, followed by a blank line and the substantive response. Preserve the original URL and fragment. Embedded file/line references, code links and quoted inline findings do not create an inline parent; unrelated inline comments belonging to that review do not route a response to its review body.

```json
{"kind":"reply-comment","pr":"https://github.com/owner/repo/pull/7","head":"FULL_HEAD_SHA","source":{"kind":"review","id":789,"url":"https://github.com/owner/repo/pull/7#pullrequestreview-789"},"body":"In Reply To: [Original review](https://github.com/owner/repo/pull/7#pullrequestreview-789)\n\nVerified answer"}
```

Reply callers supply a `source` object with `kind` (`inline_comment`, `review` or `issue_comment`), the original database `id`, and exact `url`; inline sources also carry `thread_id`. The caller verifies identity, membership and destination from complete evidence before preparation, refreshing missing or ambiguous evidence. The publisher routes by `kind` and `parent`; it journals the supplied source record without independently classifying or validating it. The helper saves the actor and existing reply ids to distinguish this attempt from older identical replies.

Retain source identity, routing parent, head, exact body and operation path through handoffs and resume. Reuse the operation for reconciliation; do not infer a different destination from prose, replace the source URL or regenerate an uncertain reply's attribution. Exact-body observation must preserve the heading as well as the response.

```json
{"kind":"push","checkout":"/owned/checkout","remote":"https://github.com/owner/repo.git","branch":"pr-branch","head":"FULL_VERIFIED_HEAD_SHA","expected_head":"FULL_REMOTE_LEASE_SHA"}
```

Before preparing a push, verify branch and repository ownership, fetch and push URLs, authorship, target tip, tests and tree. Preparation saves the candidate tree. Dispatch uses an exact force-with-lease; observation checks the saved head. If the lease changes, inspect the remote change. Never update the lease and retry automatically. The publisher does not use a shell.

Other actions use the same commands and journal. Requests include canonical `pr` and captured `head`:

| kind | Additional request fields | Fresh validation and receipt |
| --- | --- | --- |
| ci-approve | base, run_id, attempt, evidence | Require [current incoming-review evidence](ci-approval.md) and the exact fork gate. Recheck before dispatch; observe execution permission for the same attempt. Never resend uncertain writes. |
| ci-rerun | run_id, attempt, reason | Require the current completed failed attempt at this head. Rerun only failed jobs. Observe exactly attempt + 1. |
| resolve-thread | thread_id, reply_id | Read every thread page. Require this PR and head and your verified posted answer. Observe resolved state and unchanged answer. |
| request-review | reviewers, team_reviewers | Use login and team-slug arrays with at least one entry. Observe the requested set. Already satisfied requests send nothing. |
| create-draft-pr | head_branch, branch_sha, base_branch, base_sha, title, body | Require unchanged candidate and default target refs, and no PR for the branch. Observe exactly one matching draft by the actor and its full payload. |
| post-review | event, body, comments | Use COMMENT, APPROVE or REQUEST_CHANGES. Comments use path, line, side and body and map to changed lines. Observe the exact review and comments by the actor at this head. |
| merge-pr | base, merge_method | Require separate permission, complete clean review and verification, current approval, an open non-draft mergeable PR, and successful CI at the reviewed head and target. Bind the request to the head. Observe merged state, actor and exact merge-commit parents. |

For `merge-pr`, set `base` to the reviewed target SHA and `merge_method` to `squash` or `merge`. The caller establishes permission and complete review and verification. The publisher independently rechecks target, head, actor, author, repository readiness and successful checks. It calls GitHub's [merge endpoint](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request) with the reviewed head SHA. It never bypasses protections.

The endpoint binds the head but cannot atomically bind the base. Reconciliation checks merge-commit parents against the reviewed base and method. A changed target or another actor's merge remains a blocker even if the remote PR merged. An uncertain outcome permits reads only. Never repeat the merge. This handler does not expand `dk-pr-fix` permissions, which exclude merging.

Example CI rerun request:

```json
{"kind":"ci-rerun","pr":"https://github.com/owner/repo/pull/7","head":"FULL_HEAD_SHA","run_id":123,"attempt":1,"reason":"Verified transient infrastructure failure"}
```

Reserve the caller's rerun budget before publication. Pass the verified operation to `ci_wait.py --rerun-operation FILE`. It opens one new attempt window and preserves it on resume. A lost response, delayed or advanced attempt, or ambiguous receipt permits read-only reconciliation only. Do not send again. [GitHub's failed-job endpoint](https://docs.github.com/en/rest/actions/workflow-runs#re-run-failed-jobs-from-a-workflow-run) reruns only failed jobs and dependencies.

For a new branch, use `kind:push`, `create_branch:true` and `expected_head:null`. The lease requires the remote branch to be absent, preventing overwrites. Capture and verify the candidate tree as for an existing branch.

Preparation deduplicates identical payloads in a private journal for each target, even if the caller lost the operation path. A pending publication blocks other writes to that target until reconciled. Independent targets have separate journals. Use a stable optional `action_id` only for a distinct, newly authorized action. Changing it cannot bypass a pending write. Already satisfied description edits and identical replies by the same actor return verified no-ops after current checks.

The publisher serializes dispatch per target and saves original intent before sending. Reused receipts still require current checks. Keep observed writes recorded even if readiness later fails. Inaccessible, changed or ambiguous remote results remain unresolved.

Review observation reads every comment in the matching review and fetches each individual comment by id. It verifies membership, PR, actor, original reviewed commit and exact payload, including the original location when a later push has moved or outdated a comment. Missing list locations do not invalidate matching individual evidence; conflicting or insufficient evidence remains pending. Reconcile an existing pending operation using its saved path without resubmitting or editing its intent. A confirmed historical submission is recorded as verified even if current PR metadata has changed; that readiness failure is reported separately and subsequent publications require fresh preparation and live checks.
