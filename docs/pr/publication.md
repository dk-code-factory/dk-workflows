# Publication and recovery

Authorize a concrete action from the caller's instructions, prepare its exact payload and expected live identity, then persist intent before dispatch. Reconcile an uncertain write read-only before preparing or attempting any replacement. Never reset pending intent after a timeout. Compare actor, target, exact payload, head/lease and remote receipt. An absent or ambiguous object remains unresolved. A confirmed publication remains recorded even if current readiness has changed; it does not authorize another write.

`python3 PACKAGE_ROOT/scripts/publish.py prepare --file REQUEST.json` stores intent and returns an operation path. `publish --operation FILE` performs fresh validation and sends at most once. `reconcile --operation FILE` only observes; it never sends. Repeated publish on pending or verified state also observes. Keep the operation path in the caller's result and use it on resume. The package-owned `runtime/shared/publication.py` can be reused by other domain publishers with their own check/send/observe callbacks.

Requests:

```json
{"kind":"edit-pr","pr":"https://github.com/owner/repo/pull/7","head":"FULL_HEAD_SHA","fields":{"title":"Reviewed title","body":"Reviewed body"}}
```

```json
{"kind":"reply-inline","pr":"https://github.com/owner/repo/pull/7","head":"FULL_HEAD_SHA","parent":123,"body":"Verified answer"}
```

For top-level source-linked responses, use kind `reply-comment` and include the source URL in the exact body. The helper captures the actor and preexisting reply ids so an older identical reply is not attributed to this attempt.

```json
{"kind":"push","checkout":"/owned/checkout","remote":"https://github.com/owner/repo.git","branch":"pr-branch","head":"FULL_VERIFIED_HEAD_SHA","expected_head":"FULL_REMOTE_LEASE_SHA"}
```

Verify branch/repository ownership, fetch/push URL, authorship, target tip, tests and tree before preparing. Push preparation stores the candidate tree; dispatch uses exact force-with-lease and observation verifies the captured head. A changed lease requires inspecting the remote change; never update the lease and retry automatically. No shell is used.

All remaining publication actions use the same `prepare`, `publish`, and `reconcile` commands and journal. Requests include canonical `pr` and its captured `head`:

| kind | Additional request fields | Fresh validation and receipt |
| --- | --- | --- |
| ci-rerun | run_id, attempt, reason | Current failed completed attempt at this head; send only rerun-failed-jobs; observe exactly attempt + 1. |
| resolve-thread | thread_id, reply_id | Complete paginated thread belongs to this PR/head and contains your verified posted answer; observe resolved state and unchanged answer. |
| request-review | reviewers, team_reviewers | Native login/slug arrays, at least one entry; observe the requested set. Already satisfied requests send no mutation. |
| create-draft-pr | head_branch, branch_sha, base_branch, base_sha, title, body | Candidate and default target refs unchanged; no existing PR for the branch; observe exactly one matching authored draft and its full payload. |
| post-review | event, body, comments | Event is COMMENT, APPROVE or REQUEST_CHANGES; comments use path/line/side/body and must map to changed lines in the current diff; observe the exact authored head-bound review and comments. |

Example CI rerun request:

```json
{"kind":"ci-rerun","pr":"https://github.com/owner/repo/pull/7","head":"FULL_HEAD_SHA","run_id":123,"attempt":1,"reason":"Verified transient infrastructure failure"}
```

Reserve the caller's rerun budget before invoking publication. Pass the verified operation to `ci_wait.py --rerun-operation FILE`; it opens one new attempt window and retains it on resume. A lost write response, delayed attempt increment, advanced attempt or ambiguous receipt permits read-only reconciliation, never another send. [GitHub's failed-job endpoint](https://docs.github.com/en/rest/actions/workflow-runs#re-run-failed-jobs-from-a-workflow-run) limits the rerun to failed jobs and dependencies.

For a new branch, use `kind:push`, `create_branch:true` and `expected_head:null`. The exact absence lease prevents overwriting an existing branch. The candidate tree is captured and verified just as for an existing-branch push.

Preparation deduplicates the same payload within a target-specific private journal, including when the caller lost the printed operation path. Pending publication on that target blocks a different write until reconciliation. Independent targets have separate journals. Use an optional stable `action_id` only for a distinct newly authorized action; changing it never bypasses a pending write. Already satisfied description edits and identical authored replies also return verified no-ops after current checks.

The publisher serializes dispatch per target and records original intent before sending. Fresh checks still apply to reused receipts; an observed write stays recorded even if current readiness later fails. An inaccessible, changed or ambiguous remote result stays unresolved.
