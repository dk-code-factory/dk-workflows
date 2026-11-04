# Task state contract v2

The agent resolves and invokes skills. Python only fingerprints inputs, claims work and retains outputs/counters. State lives under `${XDG_STATE_HOME:-~/.local/state}/dk-workflows/v2/`, outside all repositories. No review cache or active-run state is imported from v1. Keep private outputs and task specs outside the repository, too.

All commands use `python3 PLUGIN_ROOT/scripts/dk.py --repository HOST/OWNER/REPO --pr https://HOST/OWNER/REPO/pull/N --checkout REPO COMMAND`. Retain the printed manifest path.

A spec is JSON; paths must identify actual installed resources or immutable captured input files:

```json
{
  "repository": "github.com/owner/repo",
  "pr": "https://github.com/owner/repo/pull/7",
  "skill": {
    "identity": "my-reviewer:review",
    "resources": {"skill": "/installed/reviewer/SKILL.md", "runtime": "/installed/reviewer/runtime"}
  },
  "task": {"id": "content-review", "instructions": "Review the complete pinned PR against the supplied scope and rules; return findings and completion."},
  "instructions": {"user": "/private/user-instructions.txt", "rules": "/private/rules.txt"},
  "inputs": {"context": "/private/review-context.json"},
  "git": {"checkout": "/review/checkout", "target": "TARGET_SHA", "base": "DIFF_BASE_SHA", "head": "HEAD_SHA", "history": false, "head_sensitive": false},
  "options": {},
  "governing_inputs_complete": true,
  "cacheable": true
}
```

Capture complete target, diff-base and head trees and the binary diff. SHAs and checkout path are retained as provenance; they do not invalidate ordinary content review by themselves. Set `history:true` for history consumers and `head_sensitive:true` for tasks consuming exact commits. Head-specific CI and every publication are normally noncacheable and must be checked live. Do not include head SHAs in content context unless that task actually consumes them. Preserve original governing instructions; exclude generated cycle and CI progress. This is an explicit input contract, not semantic equivalence matching. Assign task ids by responsibility: for example, `scope-partition` and `series-cleanup` stay separate even when their selected skill is identical. Reviewer specs use the same identity, task id, assigned instructions and consumed inputs in standalone review and iteration; changing invocation mode alone does not create new work.

Resource maps hash exact bytes, executable mode, directory paths and symlink targets. Directory traversal excludes only `.git`, `__pycache__` and `.pytest_cache`. Include all resources actually governing the task, including references outside its skill directory. Missing resources fail before a claim. If instruction/config/resource capture is uncertain, use `cacheable:false`; never assert complete capture based on guesswork. Reading unchanged shared guidance once within the current agent context does not waive these checks. Reread changed resources and affected references, and reload necessary guidance after context loss. Resource or context drift observed at recording rejects completion; the caller must supply fresh snapshots from live sources before recording and before final coverage checks.

Generate ordinary specs with `spec --definition /private/reviewer-definition.json`.
The definition supplies these explicit fields; canonical repository and PR come
from the command's global arguments:

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

Save stdout outside the repository as the task spec. Python callers may use
`state.make_spec(repository=..., pr=..., **definition)`. Generation copies the
supplied values and normalizes PR identity without reading files, resolving refs
or claiming work. It adds no run, cycle, reviewer-order or progress fields.
Completeness defaults to false; cacheability defaults to true only with an
explicit completeness assertion and nonempty resource map. An explicit
`cacheable:true` requires both. This helper cannot discover omitted governing
resources; uncertain capture must remain noncacheable. Existing handcrafted
specs stay compatible and equivalent generated specs retain their task keys,
fingerprints and provenance. Standalone and iteration use the same definitions;
partition and cleanup use separate responsibilities.

- `status --spec A.json --spec B.json` reports reusable results, changed components, missing/incomplete work, unfinished claims and retained run counters/progress. Omitting specs inspects unfinished work and runs.
- `status --compact --run RUN_ID --spec A.json` is the routine inspection view. It preserves the same task fields (including findings and validated result paths), run budget fields and PR-wide unfinished claims. Each run replaces its progress array with `progress_count` and `progress_ref` (manifest path and JSON Pointer), and adds `cycle`, the last allocated review cycle or null. It does not include progress bodies. Omit `--run` to summarize every run; a selected unknown run is an error in either view. Run selection only filters `runs`, never task coverage or unfinished claims from other runs. Without `--compact`, the response remains the full view; without either new option, its existing shape is unchanged.
- `status --explain --spec A.json` adds an `explanation` to each supplied task, and can be combined with `--compact` and `--run`. `changes` identifies added, removed or changed captured fields with JSON Pointers, including resource labels/files, instructions, inputs, nested options and consumed Git trees/history/commits. Existing content and Git hashes appear as `hash`; other values appear as `sha256` of their canonical captured JSON, avoiding instruction, option and discussion bodies. Added/removed subtrees receive one identity at their root. Each task retains its key/id; only that supplied task's captured inputs are compared.
- `start --spec A.json` atomically returns `action:reuse`, `recover_required`, or `invoke`. Only `invoke` permits a new invocation. Claims have no automatic expiry. Ensure a previous agent has stopped, reconcile its domain writes, then use `--recover CLAIM_ID` with the original run to launch a replacement. Unchanged inputs retain the original cycle; changed review inputs require a new cycle within the remaining budget.
- `start-batch --spec A.json --spec B.json` returns ordered `tasks`, each with its key, task id and individual `action:reuse`, `recover_required` or `invoke`, plus `manifest` and all PR-wide `unfinished` claims. Reuse includes the validated result reference; recovery-required includes the existing claim as `unfinished`; invocation includes its new `claim`. Only each task's `invoke` authorizes that task. One batch accepts common `--run`, `--cycle`, `--retry`, `--ci-repair`, `--ci-rerun` and `--head` options, charged per actual invocation. Batch only tasks eligible for the same options; use a separate batch or the compatible single start for a selective retry. Explicit recovery uses repeated `--recover TASK_KEY=CLAIM_ID` entries, retaining the original run and unchanged-input cycle. Native agents still invoke skills and record each result individually and immediately.
- `record --spec A.json --claim CLAIM_ID --original OUTPUT --normalized RESULT.json --status complete|failed|incomplete` stores immutable original bytes and normalized result. Normalized result requires `complete:true|false`; `findings` is a list. Incomplete and failed use false. Extra skill-owned fields are retained. Save every successful task immediately, including successful siblings when another fails. Keep generated stage/progress records out of original instruction files and consumed source snapshots; record the original output as well as its normalized result. `--progress PROGRESS.json` appends run progress, finding dispositions and exact human acceptances without replacing earlier records.
- `invalidate --key TASK_KEY --reason TEXT` requires fresh execution. Repeated `--key` selects multiple tasks. For a stopped active claim, pass `--recover CLAIM_ID` to close it without launching work, after reconciling its domain writes. This preserves all counters and records the interrupted attempt; it also works when missing resources prevent recapture.

Native reviewers follow [native reviewer handoffs](native-review.md). Retain the
host handle and terminal outcome in operational progress. The coordinator checks
host execution and skill completion before recording success; the store rejects
empty or whitespace-only original output for complete work, including reuse of
older empty results. It does not verify host events or infer skill completion
from report text. Failed/incomplete attempts may retain empty output.

For iteration, use `start --run RUN_ID --cycle CYCLE_ID` for reviewers. Reuse never allocates a cycle. All reviewers in one pass share a cycle; resuming keeps it. New work after fixes uses a new cycle. The five-cycle limit is retained atomically before invocation. `--retry` records one eligible same-cycle retry per task. Use `--ci-repair` for five-per-cycle repair counters (omit cycle for cycle zero) and `--ci-rerun --head SHA` for three-per-head transient reruns. These counters are reserved before invoking the domain skill. Original attempts and append-only progress retain interruptions and human acceptances.

Prefer `start-batch --run RUN_ID --cycle CYCLE_ID` for a reviewer pass. All specs
are captured before mutation, then all claim, recovery and budget checks run
under one PR lock before one durable manifest replacement. Invalid specs,
duplicate task keys, wrong PRs, unused/invalid recovery mappings and exhausted
budgets reject the entire batch without saving new claims or counters. No staged
task is authorized when capture or validation fails.
A task blocked by an unfinished claim is a normal `recover_required` outcome:
eligible siblings may still receive `invoke` or `reuse`. Save successful results
even when a sibling fails or needs recovery. A lost response or storage failure
has an uncertain outcome: inspect `status` for committed claims and reconcile
before retrying. Claims never expire automatically, and concurrency cannot
authorize two invocations for the same task. A batch neither erases prior
successful outcomes nor hides unfinished claims belonging to other runs.

A reusable result may contain findings. The helper validates completion and artifact integrity; the coordinating agent assesses dispositions and readiness. Failed, incomplete, damaged, explicitly invalidated or unfinished results cannot satisfy coverage. Concurrent callers serialize on a per-PR lock; the loser sees the unfinished claim. Artifacts are immutable and content-addressed. Do not edit manifests, erase a claim or create a new run to bypass a limit.

One `status` or `start-batch` command shares Git calculations for identical full commit IDs,
checkout context and Git options. Each task still captures its own resources,
instructions, input files and options, including `cacheable:false` tasks. A new
status, start, start-batch or record gets a fresh capture boundary. Mutable refs, abbreviated
IDs and revision expressions always resolve independently. Unsupported Git
layouts or indirect configuration, replacements, grafts and shallow repositories
also fall back to independent capture. Ordinary repositories and linked worktrees
check configuration, environment and affected-path attributes before reuse.
System and global paths come from the running Git installation. Git versions
without the required path variables and relative XDG configuration paths use
independent capture; the process probe skips unsupported Git versions.
Alternate attribute sources, configured worktree roots and signature-verifying
history also fall back. Index context retains a fixed-size content hash.
Sorted packed refs use fresh binary searches for ambiguous names and replacements;
unsupported packed-ref formats fall back rather than enumerate all refs.
Observed context files and their ancestry retain identity/version guards until
capture or reuse completes. Detected mutation declines sharing, including a
file restored to its earlier bytes or an optional file created and removed.

Reproduce the capture-process comparison with:

```sh
python3 -m pytest -q -s tests/test_state.py -k status_batch_process_count_comparison
```

The probe completes two reviewer specs with identical pinned Git inputs, then
runs initial status, two independent reuse starts, and final status. It compares
independent captures (42 Git processes) with batched status captures (28).
The fixture isolates Git configuration and environment so user includes and
other deliberately unsupported contexts do not disable the measured fast path.
Each optimized status uses two Git path-variable probes, two changed-path
probes and three calculations: batched commit resolution, batched tree resolution
and the binary diff. Each separate start still performs seven Git processes.
The mixed content/history/head-sensitive probe with three distinct pins measures
66 independent processes versus 54 with status batching. Reproduce it with
`python3 -m pytest -q -s tests/test_state.py -k status_batch_mixed_modes_process_count_comparison`.
Counts exclude Store initialization, initial
result recording, publication and CI. No reviewers or new cycles launch during
the measured recipe. This measures subprocess count, not elapsed time or tokens.

The batch-start comparison uses that same two-completed-reviewer recipe with
one initial status, one `start-batch` and one final status:

```sh
python3 -m pytest -q -s tests/test_batch_state.py -k start_batch_process_count_comparison
```

It measures 42 Git processes with independent captures and single starts,
28 with status batching and single starts, and 21 with status and start batching,
in ordinary and linked worktrees. The recipe requires four helper commands with
single starts and three with batch start. Setup and record commands are excluded;
the deterministic spec generator itself uses no Git processes. Tasks still
check their own resources and instructions, even when noncacheable. Later
recording and final status remain separate fresh boundaries. No elapsed-time
or token savings are claimed.


Compact output is a read projection, not a readiness assessment. Before assessing finding dispositions, exact human acceptances or interrupted-work recovery, read detailed progress with `status --run RUN_ID` (omit `--run` when evidence spans runs). The `progress_ref` JSON Pointer identifies the same array in the retained manifest; `~1` decodes to `/` and `~0` to `~` in run names. Follow each task's validated `result` path to its immutable JSON envelope: `normalized` retains findings and skill-owned fields, and `original` is the exact original output encoded as base64 (`base64.b64decode(envelope['original'], validate=True)`). Older result envelopes remain in the manifest's sibling `results/` directory; full attempt history in `entries` references their filenames. A missing or damaged current envelope yields a null result path and cannot be reused in either view. Status reads hold the same lock and perform the same integrity validation in both views; they never change the manifest, claims or counters. Compact output size does not scale with retained progress bodies, but still reflects the requested tasks, budget maps, runs and all unfinished claims. It does not reduce manifest I/O or replace fresh input, CI and publication checks.

Explained status uses the same capture and reuse decision as ordinary status. Its `blockers` distinguishes changed inputs, explicit invalidation, noncacheable tasks/results, unfinished claims, absent results, missing result artifacts, damaged results and failed/incomplete results. Multiple blockers can coexist. `previous_capture` references the manifest's last captured components, which can belong to an unfinished or interrupted attempt rather than the retained result. `previous_capture_fingerprint`, `current_fingerprint` and `result_fingerprint` distinguish those identities; `result_fingerprint_mismatch` also explains when the last capture is current but the result is older. The ordinary `result` field still references validated original result evidence. A task with no previous capture reports `no_previous_capture` and additions, rather than implying drift from an earlier review. Missing governing resources still fail capture with the existing blocked error before any claim or budget changes. Diagnostics do not change fingerprints or coverage, allocate cycles, recover claims or authorize reuse/publication/completion. Without `--explain`, no detailed comparisons are returned and routine compact output retains its existing bound.

Direct invocation of any external skill bypasses this cache unless its caller explicitly uses these commands. External skills require no custom SDK, execution receipts or workflow lifecycle tokens.
