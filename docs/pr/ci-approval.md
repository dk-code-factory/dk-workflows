# Fork workflow approval evidence

`dk-pr-review` calls the configured `approve_fork_ci` task in `processes.pr-review.tasks.approve_fork_ci` role (default `dk-workflows:dk-ci-approve`) once review is ready for CI. `processes.pr-review.permissions.approve_fork_ci` defaults to true independently of posting PR approvals or merging. Analysis-only instructions and narrower mutation restrictions override it. This boundary does not alter `dk-pr-fix`, completion instructions, branch protection or deployment approvals. CI execution permission does not establish CI success.

Keep a private JSON evidence document with the following fields. Preserve original instructions and reports; a boolean readiness assertion is insufficient.

```json
{
  "workflow": "dk-workflows:dk-pr-review",
  "repository": "github.com/owner/repo",
  "pr": "https://github.com/owner/repo/pull/7",
  "checkout": "/owned/checkout",
  "head": "FULL_REVIEWED_HEAD_SHA",
  "base": "FULL_REVIEWED_TARGET_SHA",
  "run": "RETAINED_RUN_ID",
  "cycle": "RETAINED_CYCLE_ID",
  "analysis_only": false,
  "instructions": {"user": "/private/original-instructions.txt", "rules": "/private/rules.txt"},
  "discovery": ["/private/reviewer-spec.json"],
  "verification": null,
  "hooks": [],
  "publications": []
}
```

Include `upstream` when configuration inherits from an upstream repository. During inspection, run `python3 PACKAGE_ROOT/scripts/ci_approve.py snapshot --evidence FILE` to capture effective settings, original instruction fingerprints and live PR context. It saves `settings`, `instruction_fingerprints` and `context` in the document. Snapshotting does not establish readiness or invoke reviewers. Never refresh a changed snapshot merely to pass the gate: reassess affected review inputs, refresh discovery and verification as needed, complete hooks and reconcile publications first. After your own review publication, assess the new discussion and capture the reconciled context before finalization.

Every discovery and verification spec must capture the same original instruction paths under the same labels and pin Git `head` and `target` to the reviewed SHAs. Provide all current selected reviewer specs, including optional reviewers. The helper recaptures them, validates original immutable result artifacts and requires complete current coverage with no unfinished PR claims. Noncacheable results must have complete captured governing inputs and belong to this retained run and cycle; they cannot supply coverage across runs or cycles. Stale tasks block publication. Original results remain subject to the native completion contract.

When discovery has candidates, supply the independent `dk-workflows:dk-pr-review` task with responsibility `finding-verification`. Its `inputs` must include every original current candidate result artifact path returned by task status; this prevents reuse of verification for older candidates. Its normalized result includes `dispositions`, one per original candidate, identifying its task key and zero-based finding index:

```json
{"complete":true,"findings":[],"dispositions":[{"task":"DISCOVERY_TASK_KEY","index":0,"status":"rejected","evidence":"Original source or reproduction establishing the rejection"}]}
```

Only evidenced `rejected` or `resolved` candidates satisfy readiness. `confirmed`, `uncertain`, missing or duplicate dispositions block approval. Record actual unresolved verification findings in `findings`; they also block approval. A resolution must establish why the issue no longer requires action at the current reviewed inputs. Save original verification output unchanged.

Include every configured before/after hook spec for the current `run` and `cycle` in `hooks`. They must have completed current results and their generated `cycle_hook` occurrence options. Hook specs need not consume Git when the hook does not use it. List the journal paths of all review publications in `publications`; they must have verified receipts at this head. Pending publications anywhere in this PR journal block new writes until reconciled.

Noncacheable hook results use the retained run and their generated occurrence; hook claims omit the reviewer cycle flag. Discovery and verification attempts still require the current review cycle.

Run `python3 PACKAGE_ROOT/scripts/ci_approve.py approve --evidence FILE` at the finalization boundary. Readiness requires another author's open, non-draft PR and unchanged head, target tip, configuration, instructions, complete discussion and task coverage. It requires neither successful CI nor GitHub human approvals. Configuration changes, including newly configured reviewers or hooks, invalidate the snapshot.

The helper enumerates the unfiltered workflow-run listing across every page, avoiding GitHub's filtered-search result cap. It considers only `pull_request` events at this head with an unambiguous single PR association and matching fork and target repositories. An `action_required` status or conclusion, zero jobs for this attempt and no pending deployments identify the supported fork gate. Runs at earlier heads and unrelated runs are ignored; missing or ambiguous associations block. Deployment waiting gates receive no approval.

Each `ci-approve` request binds `pr`, `head`, `base`, `run_id`, `attempt` and the `evidence` document path. The journal deduplicates by PR/run/attempt even if the caller changes the evidence path. Immediately before dispatch, it rechecks evidence, policy, live context and run identity, then calls GitHub's [fork workflow approval endpoint](https://docs.github.com/en/rest/actions/workflow-runs#approve-a-workflow-run-for-a-fork-pull-request). Fine-grained tokens require Actions write permission. Permission errors are reported with the retained operation; they do not trigger a fallback endpoint.

After valid reassessment, an undispatched `prepared` operation can capture fresh evidence in the same journal operation. Preserve its earlier preparation bytes. Pending or verified operations retain their original intent and permit only reconciliation.

Receipts establish that the same attempt transitioned to queued, in progress, or completed execution with jobs. GitHub exposes no actor-specific receipt for this fork approval endpoint; the helper reports observed execution permission without attributing it to a particular approver. An advanced attempt, expired jobless completion, delayed transition or inaccessible run remains uncertain. Persist intent before dispatch, retain partial completion and reconcile pending operations read-only on resume. Never resend an uncertain approval. A verified no-op requires fresh readiness and complete discovery showing no eligible gates. Follow [publication recovery](publication.md) for manual reconciliation and retained paths.
