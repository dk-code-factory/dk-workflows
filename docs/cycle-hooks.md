# Cycle hooks

Use `dk-config` to save ordered agent instructions or selected native skills before and after each new PR review cycle. Hooks apply to `dk-pr-fix` and `dk-pr-review`, including calls routed through `dk-pr-iter`. Standalone supporting skills do not invoke them.

For example, include this in the workflow settings JSON passed to `scripts/config.py --repository HOST/OWNER/REPO set --scope repo --file SETTINGS.json`:

```json
{
  "cycle_hooks": {
    "before_cycle": [
      {
        "id": "refresh-context",
        "instructions": "Read the current Decisions and list the constraints that apply to this review pass."
      }
    ],
    "after_cycle": [
      {
        "id": "check-invariants",
        "skill": "team:check-invariants",
        "instructions": "Check the affected invariants and return evidence, findings and explicit completion.",
        "options": {"focus": "changed-code"}
      }
    ]
  }
}
```

`set` replaces the entire selected scope: read its existing source file and preserve unrelated settings. IDs use letters, digits, dots, underscores or hyphens, starting with a letter or digit, and must be unique within a phase. Instructions are preserved verbatim. Each phase list replaces that phase from lower scopes; omitting a phase inherits it, and an empty list disables it. Missing selected skills block preflight just like missing reviewers. An instruction-only hook runs in the current workflow agent; a skill hook uses native skill tools with its exact instructions and configured options. Resolve and read the selected skill and its governing references before invoking it. Assign only the hook responsibility; never recursively launch the owning PR workflow.

## Boundaries

| Phase | `dk-pr-fix` | `dk-pr-review` |
| --- | --- | --- |
| `before_cycle` | After inspection, initial feedback fixes and scope assessment, immediately before the review pass. | After inspection and pinning, immediately before discovery. |
| `after_cycle` | After reviewer outputs, finding dispositions, justified fixes, their publication and scope refresh; before another review cycle or commit cleanup. | After discovery, independent verification and finding dispositions; before review publication, approval or merging. |

A cycle is one pass of review and its associated assessment/fixes or verification. Before opening a new cycle, inspect current reviewer coverage. If unchanged results cover the work, reuse them without starting a cycle or running hooks. Check the retained five-cycle budget before running any before hooks. Save the chosen cycle id before its first hook; an interrupted before phase resumes that same occurrence even if no reviewer has started yet. Reviewer claims still reserve the review cycle at their first actual invocation. A pending after phase from an already started pass must finish on resume, even if its reviewers are now reusable. A reviewer retry stays within the cycle and does not repeat completed hooks.

Run each phase list sequentially in configured order. A failed, incomplete or interrupted hook blocks the remaining hooks and the next boundary. Record its original outcome, stop and report the blocker; these are required steps, not best-effort notifications. `after_cycle` is a successful-cycle step, not a failure/finally handler. Save a failed cycle's evidence without running its after phase. New or changed instructions require reconciling pending work and its effects before continuing; removing a hook does not erase an unfinished claim.

Hooks cannot expand publication, edit, approval or merge permission. Pass the invoking workflow's current constraints to each hook. Instruction hooks may call skills only after resolving them through the normal native process. After any hook changes source, instructions, discussion or scope, refresh affected inspection, scope and reviewer inputs. Invalidate stale review/verification coverage and use the remaining cycle budget. After hooks that change reviewed code require another review pass before cleanup or publication. Hooks never bypass final cleanup, CI or live completion checks.

Only these two positions are configurable today. More precise placement would add named boundaries such as before/after inspection, scope assessment, cleanup and final CI. Those boundaries would also need occurrence identities and freshness rules for stages that repeat after changes. Arbitrary insertion between dependent actions requires an explicit ordering model and recovery rules.

## Define, claim and resume a hook

Use the normal [task state contract](task-state.md) to retain hook receipts. `hook-spec` binds a configured hook to one run, cycle, phase and id; it does not execute anything:

```sh
python3 PLUGIN_ROOT/scripts/dk.py --repository HOST/OWNER/REPO --pr PR_URL \
  hook-spec --run RUN_ID --cycle CYCLE_ID --phase before_cycle \
  --hook-id refresh-context --definition /private/hook-definition.json
```

Pass `--upstream HOST/OWNER/REPO` to `hook-spec` when configuration inherits upstream settings. Save its stdout as a private spec file. A definition supplies the resolved `skill_identity`, `resources`, `instructions`, `inputs`, execution `options`, and task-capture flags. For an instruction-only hook use `dk-workflows:dk-pr-fix` or `dk-workflows:dk-pr-review`, matching the active workflow; a skill hook must match its configured skill. For example:

```json
{
  "skill_identity": "dk-workflows:dk-pr-fix",
  "resources": {
    "skill": "/installed/dk-workflows/codex/skills/dk-pr-fix/SKILL.md",
    "hook_recipe": "/installed/dk-workflows/docs/cycle-hooks.md"
  },
  "instructions": {"user": "/private/user-instructions.txt"},
  "inputs": {"context": "/private/cycle-start-context.json"},
  "options": {},
  "governing_inputs_complete": true
}
```

Capture all governing resources and inputs, including indirect guidance. Responsibility and assigned instructions come from the configured hook; omit those fields from the definition. The helper adds the live configuration source files and exact configured options. It preserves caller execution options separately. For a skill hook, pass `options.configured` to the native skill. `options.execution` contains caller execution context; `options.cycle_hook` identifies the occurrence.

Recheck effective configuration and regenerate the spec before each claim or completion check. Claim each hook separately with `start --spec HOOK.json --run RUN_ID`, without `--cycle`, `--retry`, or CI flags. Do not batch an ordered phase. Follow `invoke`, `reuse` and `recover_required` normally and record original output and explicit completion immediately. The hook's occurrence is encoded in its task identity, so its claims do not consume reviewer cycle/retry budgets. Unlike reusable reviewer analysis, hook receipts deliberately include run and cycle identity: completed unchanged hooks reuse within that occurrence, while the next cycle and a new run execute their own hooks. Retain the original run and cycle ids after interruption. Before-phase hook progress may create a run whose review-cycle list is still empty.

A hook receipt proves that occurrence completed; it does not establish current review or CI coverage. Keep immutable cycle-start context and result artifacts separate from live files the hook intentionally changes. When a hook mutates Git, supply pinned Git inputs from a separate immutable checkout, or describe the pinned starting state in an immutable context artifact. Live captured-input drift rejects recording and requires explicit reconciliation/recovery. Use `cacheable:false` if governing capture is incomplete; such tasks require checking saved completion evidence explicitly before any repeat invocation. Never automatically replay a hook with uncertain effects. Reconcile effects and confirm the prior agent stopped before recovering its claim. Failed hooks remain blockers until their cause is addressed and a deliberate retry is recorded in progress.
