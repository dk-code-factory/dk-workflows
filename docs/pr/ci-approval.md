# Fork workflow approval evidence

## Standalone review followed by a merge request

A direct request to merge an exact PR, including “approve and merge” after a
standalone review, must arrange eligible fork workflow execution approvals before
waiting for CI or preparing a merge. PR review approval does not open a fork
execution gate; deployment approval and a failed CI run are different boundaries.
The original merge authorization survives the supported native handoffs below.
Narrower user restrictions, analysis-only instructions and disabled fork approval
policy still govern. Never ask the user to repeat an already authorized action
merely because review began outside the incoming-review workflow.

Use `python3 PACKAGE_ROOT/scripts/ci_approve.py bridge --request FILE` with the
existing private merge request, adding `analysis_only:false` after assessing the
current instructions. It contains `kind:"merge-pr"`, canonical `pr`, reviewed
`head`, live reviewed target `base` and the publisher's merge fields. Preserve
`upstream` and `review_options` when incoming-review invocation options were
supplied; the latter are separate from publisher options. Add `evidence` only
when an original incoming-review evidence document exists. Keep the exact original
authorization and instruction sources available to the native caller. The bridge
does not authorize mutations, save state, approve, invoke models or merge.

Execute the returned action and continue the same original request:

- `reconcile`: reconcile every returned retained publication operation read-only
  through `publish.py`; uncertain outcomes cannot be resent. Rerun the bridge
  only once reconciliation settles the pending actions. An uncertain review
  request with a current explicit `no_resend` disposition remains pending as a
  duplicate barrier for review requests, while allowing independent CI approval
  and merge. Changed review-request observations require inspection and
  reassessment before continuation; other uncertain actions still reconcile.
- `review`: invoke `dk-workflows:dk-pr-review` natively for the same PR and pinned
  source, original instructions and original merge authorization, using the
  returned review options and original upstream scope. Resume compact managed
  status and read targeted original reports; reuse current completed analysis
  under its existing keys and native evidence. Complete missing/stale discovery,
  required verification, architecture/configured work and publication
  reconciliation through that workflow. Obtain its current evidence document,
  attach its path to this same merge request and continue. Never invent managed
  records, adopt an unbound standalone verdict as coverage, or refresh snapshots
  merely to bypass stale inputs. Missing native execution/evidence is incomplete
  work with an exact prerequisite, never grounds for a CLI runner fallback.
  If that workflow already completes the original authorized merge, return its
  verified receipt instead of starting another bridge or merge operation.
- `approve`: invoke `dk-workflows:dk-ci-approve` with the returned original
  evidence path. It revalidates all readiness and association guards at dispatch
  and retains one journal operation per run/attempt. Complete its returned actions
  and retain partial results; permission failures need the stated permission and
  read-only reconciliation rather than another endpoint or duplicated dispatch.
- `observe-ci`: or after execution approval, use `dk-workflows:dk-ci` to observe
  actual CI execution and outcomes. `ci_success:null` in bridge/approval results
  means success has not been established. Failed CI blocks merging. When review,
  CI and repository requirements are satisfied at the current pins, return to
  `dk-workflows:dk-pr-publish` and complete the original authorized merge with its
  live checks and recovery journal. A handoff alone does not finish the request.

Changed head/target, ambiguous gates, disabled policy and inaccessible evidence
need their exact diagnostic and supported recovery step. Reassess affected inputs
through the owning workflow, retaining original results and operations. Do not
silently recapture a different target to make a pending request pass. Already
approved runs require no new approval; their CI results still need observation.
Malformed retained options produce a review handoff and diagnostic with valid
explicit request options or current defaults; damaged evidence cannot supply
approval coverage. Invalid explicit request options still block continuation.
This bridge also works when no eligible fork gates exist, without injecting a
new review or requiring fork approval permission for an unrelated merge.

## Evidence and finalization

`dk-workflows:dk-pr-review` calls `dk-workflows:dk-ci-approve` when selected in its finalizer list and review is ready for CI. Its `permissions.approve_fork_ci` defaults to true independently of posting PR approvals or merging. Analysis-only instructions and narrower mutation restrictions override it. This boundary does not alter `dk-workflows:dk-pr-fix`, completion instructions, branch protection or deployment approvals. CI execution permission does not establish CI success.

Keep a private JSON evidence document with the following fields. Preserve original instructions and reports; a boolean readiness assertion is insufficient.

```json
{
  "workflow": "dk-workflows:dk-pr-review",
  "configuration": "skill-owned",
  "options": {},
  "reviewer_options": {},
  "repository": "github.com/owner/repo",
  "pr": "https://github.com/owner/repo/pull/7",
  "checkout": "/owned/checkout",
  "head": "FULL_REVIEWED_HEAD_SHA",
  "base": "FULL_REVIEWED_TARGET_SHA",
  "run": "RETAINED_RUN_ID",
  "cycle": "RETAINED_CYCLE_ID",
  "analysis_only": false,
  "instructions": {"user": "/private/original-instructions.txt", "rules": "/private/rules.txt"},
  "discovery": [{"key": "DISCOVERY_TASK_KEY"}],
  "verification": null,
  "hooks": [],
  "publications": []
}
```

Include `upstream` when configuration inherits from an upstream repository. Preserve incoming-review invocation-local `options` and the selected reviewer-run invocation's local `reviewer_options`; both are explicit empty objects when no overrides apply. During inspection, run `python3 PACKAGE_ROOT/scripts/ci_approve.py snapshot --evidence FILE` to capture effective settings, original instruction fingerprints and live PR context. It saves `settings`, `skill_settings_fingerprints`, `instruction_fingerprints` and `context` in the document. Snapshotting does not establish readiness or invoke reviewers. Never refresh a changed snapshot merely to pass the gate: reassess affected review inputs, refresh discovery and verification as needed, complete selected tasks and reconcile publications first. After your own review publication, assess the new discussion and capture the reconciled context before finalization.

Every discovery and verification spec must capture the same original instruction paths under the same labels and pin Git `head` and `target` to the reviewed SHAs. Provide all current selected reviewer specs, including optional reviewers and repeated occurrences, generated with `skill-spec --kind reviewers`. The helper recaptures them, validates original immutable result artifacts and requires complete current coverage with no unfinished PR claims. Noncacheable results must have complete captured governing inputs and belong to this retained run and cycle; they cannot supply coverage across runs or cycles. Stale tasks block publication. Original results remain subject to the native completion contract.

Managed task references `{"key":"TASK_KEY"}` are also accepted; replay uses the retained definition instead of rebuilding specs.

When the approval finalizer itself has an active managed claim, add `"approval_task":{"key":"TASK_KEY","claim":"CLAIM_ID"}` (or a legacy `spec` path). Capture an immutable starting evidence snapshot in the spec; keep this generated claim binding in the working evidence document outside governing inputs, so adding progress cannot change the active task's fingerprint. The helper permits only that exact current incoming-review finalizer claim, with matching scope, run and fingerprint; every other unfinished claim still blocks. Retain this binding for read-only reconciliation.

When discovery has candidates, supply the independent verification occurrence generated from the incoming-review task list, using a definition with responsibility `finding-verification`. The helper adds the occurrence identity; the instruction-only bundled verifier uses `dk-workflows:dk-pr-review` as its skill identity. Its `inputs` must include every original current candidate result artifact path returned by task status; this prevents reuse of verification for older candidates. Its normalized result includes `dispositions`, one per original candidate, identifying its task key and zero-based finding index:

```json
{"complete":true,"findings":[],"dispositions":[{"task":"DISCOVERY_TASK_KEY","index":0,"status":"rejected","evidence":"Original source or reproduction establishing the rejection"}]}
```

Only evidenced `rejected` or `resolved` candidates satisfy readiness. `confirmed`, `uncertain`, missing or duplicate dispositions block approval. Record actual unresolved verification findings in `findings`; they also block approval. A resolution must establish why the issue no longer requires action at the current reviewed inputs. Save original verification output unchanged.

Skill-owned lists place custom work directly before and after review; they do not inherit hidden process hooks. Keep `hooks:[]` for this mode. Complete required selected tasks before finalization. List the journal paths of all review publications in `publications`; they must have verified receipts at this head. Pending publications anywhere in this PR journal block new writes until reconciled, except an uncertain review request with a live-validated `no_resend` disposition.

Evidence without `configuration` retains the legacy process API: its selected reviewer and named verifier settings and complete configured hook occurrences remain required. Legacy noncacheable hook results use the retained run and their generated occurrence; hook claims omit the reviewer cycle flag. Discovery and verification attempts require the current review cycle in both modes.

Run `python3 PACKAGE_ROOT/scripts/ci_approve.py approve --evidence FILE` at the finalization boundary. Readiness requires another author's open, non-draft PR and unchanged head, target tip, configuration, instructions, complete discussion and task coverage. It requires neither successful CI nor GitHub human approvals. Configuration changes, including newly configured reviewers or hooks, invalidate the snapshot.

The helper enumerates the unfiltered workflow-run listing across every page, avoiding GitHub's filtered-search result cap. It considers only `pull_request` events at this head with an unambiguous single PR association and matching fork and target repositories. An `action_required` status or conclusion, zero jobs for this attempt and no pending deployments identify the supported fork gate. Runs at earlier heads and unrelated runs are ignored; missing or ambiguous associations block. Deployment waiting gates receive no approval.

Each `ci-approve` request binds `pr`, `head`, `base`, `run_id`, `attempt` and the `evidence` document path. The journal deduplicates by PR/run/attempt even if the caller changes the evidence path. Immediately before dispatch, it rechecks evidence, policy, live context and run identity, then calls GitHub's [fork workflow approval endpoint](https://docs.github.com/en/rest/actions/workflow-runs#approve-a-workflow-run-for-a-fork-pull-request). Fine-grained tokens require Actions write permission. Permission errors are reported with the retained operation; they do not trigger a fallback endpoint.

After valid reassessment, an undispatched `prepared` operation can capture fresh evidence in the same journal operation. Preserve its earlier preparation bytes. Pending or verified operations retain their original intent and permit only reconciliation.

Receipts establish that the same attempt transitioned to queued, in progress, or completed execution with jobs. GitHub exposes no actor-specific receipt for this fork approval endpoint; the helper reports observed execution permission without attributing it to a particular approver. An advanced attempt, expired jobless completion, delayed transition or inaccessible run remains uncertain. Persist intent before dispatch, retain partial completion and reconcile pending operations read-only on resume. Never resend an uncertain approval. A verified no-op requires fresh readiness and complete discovery showing no eligible gates. Follow [publication recovery](publication.md) for manual reconciliation and retained paths.
