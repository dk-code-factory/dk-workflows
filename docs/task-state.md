# Task state contract v2

Native agents resolve and invoke skills. Python fingerprints inputs, claims tasks, and saves outputs and counters. Keep private state, specs and outputs outside repositories. State lives under `${XDG_STATE_HOME:-~/.local/state}/dk-workflows/v2/`. Save the printed manifest path.

Run commands as `python3 PLUGIN_ROOT/scripts/dk.py --repository HOST/OWNER/REPO --pr https://HOST/OWNER/REPO/pull/N --checkout REPO COMMAND`. Direct skill calls use this cache only when their caller manages them with these commands.

## Define and capture tasks

Generate a spec with `spec --definition /private/reviewer-definition.json`. Save stdout outside the repository. Global command arguments supply the canonical repository and PR. Example definition:

```json
{
  "skill_identity": "my-reviewer:review",
  "resources": {"skill": "/installed/reviewer/SKILL.md", "runtime": "/installed/reviewer/runtime"},
  "responsibility": "content-review",
  "assigned_instructions": "Review the complete pinned PR against the supplied scope and rules. Return findings and completion.",
  "instructions": {"user": "/private/user-instructions.txt", "rules": "/private/rules.txt"},
  "inputs": {"context": "/private/review-context.json"},
  "git": {"checkout": "/review/checkout", "target": "TARGET_SHA", "base": "DIFF_BASE_SHA", "head": "HEAD_SHA", "history": false, "head_sensitive": false},
  "options": {},
  "governing_inputs_complete": true
}
```

Python callers may use `state.make_spec(repository=..., pr=..., **definition)`. Generation normalizes identity. It does not read files, resolve refs, claim work or discover omitted inputs. Completeness defaults to false. Caching requires explicit completeness and nonempty resources. If capture is uncertain, use `cacheable:false`.

Capture all governing inputs:

- Complete target, base and head trees and binary diff. For ordinary content review, SHAs and checkout path record origin only. Set `history:true` for tasks that use history and `head_sensitive:true` for tasks tied to exact commits. Include head SHAs in ordinary context only when the task uses them. CI and publication normally use noncacheable tasks and current checks.
- Exact original instructions, immutable source snapshots and execution options. Exclude generated progress, run and cycle ids, and reviewer order. Standalone review and iteration use the same reviewer identity, responsibility, assigned instructions and consumed inputs. Partition and cleanup have separate responsibilities.
- [Cycle hook receipts](cycle-hooks.md) are scoped to a specific occurrence, so `hook-spec` deliberately includes run, cycle, phase and hook id in their task identity and options. Claim them with the run only, without reviewer budget flags. Their reuse confirms that occurrence completed; it never supplies reviewer or CI coverage.
- [Finalizer receipts](finalizers.md) use `finalizer-spec` with the retained run and qualified skill reference. The helper includes the effective plan and source configuration. Sequential groups advance only after every current task completes. Parallel siblings record separately, and failed or interrupted siblings preserve successful work. Finalizer task identity follows the skill across reorderings; plan changes stale evidence without hiding unfinished claims. Claim with the run only, keeping CI repair/rerun budgets on their owning actions. CI and publication still need current checks.
- Every governing installed resource, including indirect references outside the skill directory. Capture exact bytes, executable modes, directory paths and symlink targets. Traversal excludes only `.git`, `__pycache__` and `.pytest_cache`. Missing resources block claims.

- The [scoped preference handoff](preferences.md#workflow-handoffs-and-reuse) in `inputs.preferences`: complete selected records, applicable paths, consumed condition context and applicability decisions. Refresh selection before claims, recording and final coverage checks. Fingerprint the task-specific handoff rather than the catalog revision; relevant changes refresh affected work while unchanged selections and decisions permit reuse.
- The [commit policy handoff](commit-policy.md) in cleanup's `inputs.commit_policy`: effective configuration, provider resources and consumed context, complete provider report and returned grouping/message instructions. Capture consumed live resources in the corresponding task maps as well. Verify current handoffs before claims and acceptance; refresh affected policy and cleanup evidence when inputs change. Keep immutable starting evidence for cleanup's intentional history changes and verify its resulting head/tree separately.

Reuse unchanged guidance already read in the current context. Check resource identity at each boundary. Reread changed guidance and references. Reload necessary guidance after context loss. Capture checks still apply. Recording and final coverage checks require fresh snapshots of live sources. Observed resource or context changes reject completion. Fingerprints compare explicit inputs, not equivalent meanings.

## Inspect, claim and invoke

| Command | Use |
| --- | --- |
| `status --compact --run RUN_ID --spec A.json --spec B.json` | Current coverage, validated result paths, budgets and all unfinished PR claims. |
| `status --run RUN_ID` | Full progress. Read it and original results before assessing dispositions, exact acceptances or recovery. Omit `--run` when evidence spans runs. |
| `status --explain --spec A.json` | Changed inputs and other reuse blockers. Can combine with compact and run options. |
| `start-batch --spec A.json --spec B.json` | Claim tasks before invoking them or reading cached results, even when status reports reuse. |
| `start --spec A.json` | Claim one task, including tasks needing different retry or budget options. |

Without specs, status inspects runs and unfinished work. Run selection filters runs only; coverage and unfinished claims remain PR-wide. An unknown run is an error. Compact output omits progress bodies. Follow its references and validated original result paths. See [status details](status-diagnostics.md) for decoding.

For a new run, omit `--run` from status until a start actually invokes a task with that run id. Reuse alone does not create a run. Continue passing the chosen run id to starts so later invocations retain its budgets.

Both start commands return an `action` for each task:

- `reuse`: read the validated original result, including findings.
- `invoke`: save the new claim and invoke the native skill with pinned inputs.
- `recover_required`: resolve the unfinished claim before invoking this task. Other eligible tasks may proceed.

For single-task `start`, `invoke` returns `claim`, `manifest` and `changed`; use `claim.key` for the task key and `claim.id` for recording or recovery. `reuse` and `recover_required` instead include top-level `key` and `task`, plus `result` or `unfinished` respectively. `start-batch` wraps results in `tasks` and includes top-level `key` and `task` in every item, regardless of action.

Batch results preserve order and include the manifest and all unfinished PR claims. Capture, validation or budget errors save no new claims or counters. The batch captures every spec before mutation, checks claims and budgets under one PR lock, then replaces the manifest once. A lost response or storage failure leaves the outcome uncertain. Inspect status and reconcile before retrying.

## Record and recover

Run `record --spec A.json --claim CLAIM_ID --original OUTPUT --normalized RESULT.json --status complete|failed|incomplete`. Preserve original bytes. Normalized results require `complete:true|false` and a `findings` list. Keep skill-owned fields. Failed and incomplete results use false. Record successful tasks immediately, even if others fail. Save stale, failed and incomplete attempts accurately. Recover explicitly when required.

Use `record --progress PROGRESS.json` for append-only outcomes, dispositions and exact user acceptances with sources. Keep progress outside governing instruction and input files. Completed analysis may contain findings; assess readiness separately. Failed, incomplete, damaged, invalidated or unfinished results never satisfy coverage.

Claims never expire. Confirm the prior agent stopped and reconcile its writes before recovery. Use `start --recover CLAIM_ID` or repeated `start-batch --recover TASK_KEY=CLAIM_ID`. Keep the original run and, for unchanged inputs, cycle. Changed review inputs require a new cycle.

To close a stopped claim without replacement work, use `invalidate --key TASK_KEY --reason TEXT --recover CLAIM_ID`. This also works when missing resources prevent recapture. Repeat `--key` to invalidate several tasks.

Follow [native reviewer handoffs](native-review.md). Save the host handle and terminal outcome in operational progress. Before recording success, the coordinator checks host execution and skill completion. The store rejects empty or whitespace-only original output for complete tasks, including reuse of older empty results. It does not verify host events or infer completion from report text. Failed or incomplete attempts may keep empty output.

## Retained budgets

Pass `--run RUN_ID --cycle CYCLE_ID` for iteration reviewers. Reviewers in one pass share a stable cycle. Resume keeps it. The first actual invocation reserves the cycle atomically; reuse reserves none. New review after fixes uses a new cycle, up to five total.

Reserve counters before dispatch:

- `--retry`: one eligible retry per task in the same cycle. Keep the original incomplete attempt and reason.
- `--ci-repair`: five repairs per cycle. Omit cycle for cycle zero before review.
- `--ci-rerun --head SHA`: three transient reruns per head.

Batch options apply to each invocation. Group only tasks eligible for the same options. Otherwise use separate batches or single starts. Never edit manifests, erase claims or replace a run to bypass limits. Concurrent callers serialize per PR. Unfinished claims prevent duplicate launches. Immutable outputs identified by content retain original attempts and progress.

Caching never replaces live CI, publication checks, reconciliation or final completion checks. See [the developer reference](capture-performance.md) for capture implementation and benchmark reproduction.
