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

The workflow agent reads the plan and invokes skills; Python only captures inputs and retains claims, outputs and counters. Read the normalized groups with:

```sh
python3 PLUGIN_ROOT/scripts/dk.py --repository HOST/OWNER/REPO --pr PR_URL \
  finalizer-plan
```

The result contains `groups`, each an array of references, and configuration `sources`. A single string becomes a group of one. Pass `--upstream HOST/OWNER/REPO` consistently when inheriting that scope.

For each skill, prepare a private definition with its resolved resources, exact assignment, original permissions and completion instructions, immutable starting context and all consumed governing inputs. Use [the task contract](pr/contracts.md), [preference handoffs](preferences.md#workflow-handoffs-and-reuse) and [task state](task-state.md). Include relevant preferences and their conditional decisions before finalizers implement or review code. For example:

```json
{
  "skill_identity": "team-tools:check-links",
  "resources": {"skill": "/installed/team-tools/check-links/SKILL.md"},
  "assigned_instructions": "Check links in the pinned PR documentation and return evidence and explicit completion. Do not edit or publish.",
  "instructions": {"user": "/private/original-instructions.txt"},
  "inputs": {"context": "/private/finalizer-start-context.json"},
  "options": {},
  "governing_inputs_complete": true
}
```

Generate a spec for the reference:

```sh
python3 PLUGIN_ROOT/scripts/dk.py --repository HOST/OWNER/REPO --pr PR_URL \
  finalizer-spec --run RUN_ID --finalizer /skill/team-tools:check-links \
  --definition /private/finalizer-definition.json
```

Save stdout privately. The helper verifies membership and the selected skill identity. It captures the effective plan and its source files and derives stable task identity from the run and skill. Supply exact assignments from the original request, applicable completion instructions and the selected skill; the helper does not infer a task from its name. A definition's `options` are execution context, not additional finalizer configuration.

Regenerate specs before claims and completion checks. Claim one group with `start-batch --spec A.json --spec B.json --run RUN_ID`, or `start` for one task. Do not claim later groups before this group completes. Do not pass reviewer `--cycle` or `--retry` flags. Follow `invoke`, `reuse` and `recover_required`, and record each original output and explicit completion. Verify successful terminal execution and the skill's nonempty completion report. Mere launch, progress, or an empty findings list is insufficient. Save native handles separately from governing inputs when delegated. Finalizer claims consume no review-cycle/retry budget; reserve the existing CI repair and rerun budgets before those actions.

Keep the same run on resume. Reconcile unfinished claims and uncertain side effects before recovery. Stable skill identity keeps unfinished work visible across reorderings. Changes to the plan, assignment, resource or consumed context invalidate affected results. Removing a skill does not erase its unfinished claim; reconcile it explicitly. Preserve completed unchanged siblings. New runs get separate finalizer tasks, while publication journals still prevent duplicate writes across runs.

Use immutable snapshots for starting inputs a skill intentionally changes, then verify live resulting state before accepting its output. CI and publication tasks normally use `cacheable:false` and always perform current checks. Cached completion is never proof of current CI or a remote write. Reconcile saved receipts before repeating an action. When a task is noncacheable, inspect its saved completion and effects before a new invocation.

Run commit changes and governing-content synchronization before CI covering the resulting head. A parallel group cannot contain CI and a skill that may change that head. If finalizer effects invalidate scope or review coverage, settle the current group and return to the affected fix/review stages before continuing the plan. Retain the same run, review budgets and CI deadlines. After CI repairs, refresh scope, affected reviews and any selected cleanup assessment, then obtain CI for the resulting final head. Later changes stale prior CI and require renewed evidence; they cannot be hidden by list ordering.

## Completion

After the selected list finishes, `dk-pr-fix` performs a final live check of scope, instructions, discussion, Decisions, head, target, review coverage, finalizer results and publication receipts. Follow effective completion instructions within the selected plan and permissions. These checks and the report are coordinator duties, including for `[]`.

Report selected-plan completion separately from PR readiness. Identify skipped default duties and missing evidence. A plan without CI may finish its selected actions, but cannot claim CI was verified; omitted cleanup cannot be reported as assessed. External finalizers may supply equivalent current evidence. Failed selected work remains blocked. Report optional incomplete reviews, accepted findings and CI exceptions. A completion prompt cannot silently restore disabled finalizers or expand permission.

All finalizers inherit the exact caller permissions. `dk-pr-fix` alone does not authorize merging, creating issues or unrelated writes. Preserve a separate explicit merge request and fulfill it through `dk-pr-publish` after current review, verification, CI and repository requirements pass, including when a custom finalizer list omits CI. Reordering or adding a skill grants no additional permission.
