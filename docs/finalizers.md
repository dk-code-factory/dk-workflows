# PR fix finalizers

Follow [private workflow reporting](private-reporting.md). Operational reports, blockers, diagnostics, handles and receipts go only to the invoking user or private caller, never into external text. Pass this governing contract downstream; publication permission does not authorize disclosure.

`dk-pr-fix` runs `finalizers` after review/fix work and pending `after_cycle` hooks finish. Save the list through `dk-config` with the other workflow settings:

```json
{
  "finalizers": [
    "/skill/dk-workflows:dk-commit-split",
    "/skill/dk-workflows:dk-pr-decisions",
    "/skill/dk-workflows:dk-pr-description",
    "/skill/dk-workflows:dk-pr-feedback-reply",
    [
      "/skill/team-tools:check-links",
      "/skill/my-plugin:check-licenses"
    ],
    "/skill/dk-workflows:dk-ci",
    "/skill/dk-workflows:dk-pr-publish"
  ]
}
```

Each string is `/skill/PLUGIN:SKILL`. It selects any installed native skill, including external plugins. Resolve the qualified identity through the host before work; missing or ambiguous installations block execution. References are skill identities, not filesystem paths or shell commands. There are no `id`, `role`, options objects, agent references or deeper nested lists. Each skill may appear once across the whole list. Its own instructions may delegate to agents.

The outer list is sequential. A nested nonempty list is one parallel group. Claim the group's tasks before launch, invoke its independent skills concurrently through native tools, and wait for every launched task to settle before advancing. Record successful siblings immediately even when another fails. Failed, incomplete, cancelled, uncertain or interrupted work blocks later groups. If native parallel execution is unavailable, report the group blocked rather than silently running it serially. Do not put skills that write the same PR body, thread or branch in the same group. Resolve dependencies before launch; an unsafe group is a configuration blocker, not permission to reorder it.

## Defaults and scope

Omitting `finalizers` inherits the next lower scope. A supplied list replaces the entire inherited list; `[]` disables finalizers. Scope order is repository, explicitly supplied upstream, user, then defaults. Without an explicit list at any scope, the default sequential plan uses the effective `skills.cleanup`, `decisions`, `description`, `reply`, `ci` and `publish` selections, in that order. The default output contains qualified `/skill/` strings. If several default duties select the same skill, invoke it once and supply all those duties.

The default cleanup assesses the full series and publishes a tree-preserving rewrite only when needed. Decisions, description and reply finalizers finish outstanding work. CI observes the final published head. The default publisher reconciles outstanding operations and requests re-review from appropriate prior reviewers when authorized; invoke it with concrete prepared requests. Finalizers perform no-op assessments when their work is already satisfied.

Before selected commit cleanup, resolve [the effective commit policy](commit-policy.md). Supply its verified handoff to cleanup and include configuration, provider resources, consumed context and returned instructions in managed task inputs. Native policy providers only return grouping and message instructions; failure blocks cleanup and later finalizers. Run this before final CI. A custom list without cleanup does not run the provider or add cleanup. Policies do not change permissions or squash intermediate pushes.

Derived defaults preserve existing native role identities, including unqualified local skills, as `/skill/IDENTITY` references. Resolve those references using the original identity. Explicitly configured `finalizers` lists still require `/skill/PLUGIN:SKILL` references.

A custom list determines finalization actions. Do not run removed defaults through a hidden fallback or the completion prompt. This setting does not disable feedback fixes, scope assessment, description/Decisions synchronization or replies required earlier in review/fix work. Incoming review and standalone skills do not execute this list.

## Execution and freshness

The agent reads the plan and invokes skills. Python handles the shared [managed task lifecycle](task-state.md); hooks and finalizers use the same bookkeeping and preserve their existing occurrence identities. Read normalized groups with:

```sh
python3 PLUGIN_ROOT/scripts/dk.py --repository HOST/OWNER/REPO --pr PR_URL \
  finalizer-plan
```

The result contains `groups`, each an array of references, and configuration `sources`. A single string becomes a group of one. Pass `--upstream HOST/OWNER/REPO` consistently when inheriting that scope.

For the current group, call `prepare --file REQUEST.json` with the retained run and one task per skill. Each task uses `kind:"finalizer"`, `finalizer:"/skill/PLUGIN:SKILL"` and an ordinary `definition` with resolved resources, exact assignment, original permissions, immutable starting context and consumed inputs. Omit `responsibility`; the helper derives it and captures live plan/configuration. For example:

```json
{
  "run": "RUN_ID",
  "tasks": [{"kind": "finalizer", "finalizer": "/skill/team-tools:check-links", "definition": {
    "skill_identity": "team-tools:check-links",
    "resources": {"skill": "/installed/team-tools/check-links/SKILL.md"},
    "assigned_instructions": "Check links in the pinned PR documentation. Return evidence and explicit completion.",
    "instructions": {"user": "/private/original-instructions.txt"},
    "inputs": {"context": "/private/finalizer-start-context.json"},
    "options": {}, "governing_inputs_complete": true
  }}]
}
```

Include the [task contract](pr/contracts.md) and relevant [preference handoff](preferences.md#workflow-handoffs-and-reuse). An optional `upstream` on each task preserves inherited configuration. The helper verifies membership and runner identity; it does not infer assignments. Supply all consumed sources and permissions.

Prepare only the current group. Follow the shared `invoke`, `reuse`, `recover_required` and `finish` protocol; no reviewer cycle/retry flags apply. Wait for all siblings before later groups and finish successful tasks immediately. Terminal execution and explicit skill completion remain required. Reserve CI repair/rerun budgets on their owning actions. On a fresh session use `resume` and retained task keys instead of reconstructing definitions. Refresh changed scope and inputs before accepting coverage. Low-level `finalizer-spec` remains available for existing integrations.

Keep the same run on resume. Reconcile unfinished claims and uncertain side effects before recovery. Stable skill identity keeps unfinished work visible across reorderings. Changes to the plan, assignment, resource or consumed context invalidate affected results. Removing a skill does not erase its unfinished claim; reconcile it explicitly. Preserve completed unchanged siblings. New runs get separate finalizer tasks, while publication journals still prevent duplicate writes across runs.

Use immutable snapshots for starting inputs a skill intentionally changes, then verify live resulting state before accepting its output. CI and publication tasks normally use `cacheable:false` and always perform current checks. Cached completion is never proof of current CI or a remote write. Reconcile saved receipts before repeating an action. When a task is noncacheable, inspect its saved completion and effects before a new invocation.

Run commit changes and governing-content synchronization before CI covering the resulting head. A parallel group cannot contain CI and a skill that may change that head. If finalizer effects invalidate scope or review coverage, settle the current group and return to the affected fix/review stages before continuing the plan. Retain the same run, review budgets and CI deadlines. After CI repairs, refresh scope, affected reviews and any selected cleanup assessment, then obtain CI for the resulting final head. Later changes stale prior CI and require renewed evidence; they cannot be hidden by list ordering.

## Completion

After the selected list finishes, `dk-pr-fix` performs a final live check of scope, instructions, discussion, Decisions, head, target, review coverage, finalizer results and publication receipts. Follow effective completion instructions within the selected plan and permissions. These checks and the report are coordinator duties, including for `[]`.

Report selected-plan completion separately from PR readiness. Identify skipped default duties and missing evidence. A plan without CI may finish its selected actions, but cannot claim CI was verified; omitted cleanup cannot be reported as assessed. External finalizers may supply equivalent current evidence. Failed selected work remains blocked. Report optional incomplete reviews, accepted findings and CI exceptions. A completion prompt cannot silently restore disabled finalizers or expand permission.

All finalizers inherit the exact caller permissions. `dk-pr-fix` alone does not authorize merging, creating issues or unrelated writes. Preserve a separate explicit merge request and fulfill it through `dk-pr-publish` after current review, verification, CI and repository requirements pass, including when a custom finalizer list omits CI. Reordering or adding a skill grants no additional permission.
