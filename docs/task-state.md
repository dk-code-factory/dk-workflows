# Task state contract v2

Native agents resolve and invoke skills. Python fingerprints inputs, claims work and retains outputs/counters. Private state, task specs and outputs stay outside repositories. State lives under `${XDG_STATE_HOME:-~/.local/state}/dk-workflows/v2/`; retain the printed manifest path.

All commands use `python3 PLUGIN_ROOT/scripts/dk.py --repository HOST/OWNER/REPO --pr https://HOST/OWNER/REPO/pull/N --checkout REPO COMMAND`. Direct skill calls bypass this cache unless their caller uses these commands.

## Define and capture tasks

Generate a spec with `spec --definition /private/reviewer-definition.json` and save stdout outside the repository. Canonical repository/PR come from the command’s global arguments. Example definition:

```json
{
  "skill_identity": "my-reviewer:review",
  "resources": {"skill": "/installed/reviewer/SKILL.md", "runtime": "/installed/reviewer/runtime"},
  "responsibility": "content-review",
  "assigned_instructions": "Review the complete pinned PR against the supplied scope and rules; return findings and completion.",
  "instructions": {"user": "/private/user-instructions.txt", "rules": "/private/rules.txt"},
  "inputs": {"context": "/private/review-context.json"},
  "git": {"checkout": "/review/checkout", "target": "TARGET_SHA", "base": "DIFF_BASE_SHA", "head": "HEAD_SHA", "history": false, "head_sensitive": false},
  "options": {},
  "governing_inputs_complete": true
}
```

Python callers may use `state.make_spec(repository=..., pr=..., **definition)`. Generation normalizes identity but does not read files, resolve refs or claim work. Completeness defaults to false. Cacheability requires explicit completeness and nonempty resources; use `cacheable:false` when capture is uncertain. The generator cannot discover omitted inputs.

Capture these governing inputs:

- Complete target/base/head trees and binary diff. SHAs and checkout path are provenance for ordinary content review. Set `history:true` for history consumers and `head_sensitive:true` for exact-commit consumers. Do not place head SHAs in ordinary content context unless consumed. CI and publications normally use noncacheable tasks and live checks.
- Exact original instructions, immutable source snapshots and execution options. Exclude generated progress, run/cycle ids and reviewer-list order. Use the same reviewer identity, responsibility, assigned instructions and consumed inputs in standalone review and iteration. Partition and cleanup remain distinct responsibilities.
- Every governing installed resource, including transitive references outside the skill directory. Resources capture exact bytes, executable modes, directory paths and symlink targets; traversal excludes only `.git`, `__pycache__` and `.pytest_cache`. Missing resources block claims.

Reuse unchanged guidance already read in the current context, checking resource identity at each boundary. Reread changed guidance/references and reload necessary guidance after context loss. This does not waive capture checks. Recording and final coverage checks require freshly supplied live-source snapshots; observed resource/context drift rejects completion. Fingerprints compare explicit inputs, not semantic equivalence.

## Inspect, claim and invoke

| Command | Use |
| --- | --- |
| `status --compact --run RUN_ID --spec A.json --spec B.json` | Routine coverage, validated result references, budgets and all PR-wide unfinished claims. |
| `status --run RUN_ID` | Full retained progress; read this and original results before judging dispositions, exact acceptances or recovery. Omit `--run` when evidence spans runs. |
| `status --explain --spec A.json` | Explain changed inputs and other reuse blockers; combines with compact/run options. |
| `start-batch --spec A.json --spec B.json` | Claim a pass before invoking tasks or consuming cached results, even when status reports reuse. |
| `start --spec A.json` | Claim a single task, including tasks needing different retry/budget options. |

Omitting status specs inspects runs/unfinished work. Run selection filters only runs, never coverage or unfinished claims; an unknown run is an error. Compact output omits progress bodies; follow its references and validated original result paths. See [status details](status-diagnostics.md) when decoding references or diagnostics.

Start returns each task’s key/id and action:

- `reuse`: read and use the validated original result, including findings.
- `invoke`: retain its new claim and invoke the selected native skill with pinned inputs.
- `recover_required`: do not invoke this task; resolve its unfinished claim. Eligible siblings may proceed.

Batch results are ordered and include the manifest and all PR-wide unfinished claims. Capture/validation/budget errors save no new batch claims or counters. The batch captures every spec before mutation, checks claims/budgets under one PR lock and replaces the manifest once. A lost response or storage failure is uncertain: inspect status and reconcile before retrying.

## Record and recover

Use `record --spec A.json --claim CLAIM_ID --original OUTPUT --normalized RESULT.json --status complete|failed|incomplete`. Preserve original bytes unmodified. Normalized results require `complete:true|false` and a `findings` list; retain domain-owned fields. Failed/incomplete results use false. Record every successful sibling immediately, even if another fails. Keep stale, failed or incomplete attempts honest and recover explicitly when required.

Use `record --progress PROGRESS.json` for append-only stage outcomes, finding dispositions and exact source-backed human acceptances. Keep these outside governing instruction/input files. Completed analysis can contain findings; readiness is the agent’s separate judgment. Failed, incomplete, damaged, invalidated or unfinished results never satisfy coverage.

Claims never expire. Establish that the prior agent stopped and reconcile its domain writes before recovery. Use `start --recover CLAIM_ID`, or repeated `start-batch --recover TASK_KEY=CLAIM_ID`, retaining the original run and unchanged-input cycle. Changed review inputs require a new cycle. To close a stopped claim without launching replacement work, use `invalidate --key TASK_KEY --reason TEXT --recover CLAIM_ID`; this also works when missing resources prevent recapture. Repeated `--key` invalidates several tasks.

Native reviewers follow [native reviewer handoffs](native-review.md). Retain the
host handle and terminal outcome in operational progress. The coordinator checks
host execution and skill completion before recording success; the store rejects
empty or whitespace-only original output for complete work, including reuse of
older empty results. It does not verify host events or infer skill completion
from report text. Failed/incomplete attempts may retain empty output.

## Retained budgets

Pass `--run RUN_ID --cycle CYCLE_ID` for iteration reviewers. All reviewers in one pass share a stable cycle; resume retains it. The first actual invocation reserves the cycle atomically; reuse reserves none. New review work after fixes uses a new cycle, up to five total.

Reserve counters before domain dispatch:

- `--retry`: one eligible same-cycle retry per task; retain the original incomplete attempt/reason.
- `--ci-repair`: five repairs per cycle; omit cycle for cycle zero before review.
- `--ci-rerun --head SHA`: three transient reruns per head.

Batch options apply to each actual invocation. Batch only tasks eligible for the same options; use separate batches/single starts otherwise. Never edit manifests, erase claims or replace a run to bypass limits. Concurrent callers serialize per PR; unfinished claims prevent duplicate launches. Immutable, content-addressed outputs retain original attempts and progress.

Caching never replaces live CI, publication validation/reconciliation or final completion checks. For capture implementation and benchmark reproduction, see [the developer reference](capture-performance.md).
