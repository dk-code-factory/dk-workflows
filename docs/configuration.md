# Workflow configuration

Follow [private workflow reporting](private-reporting.md). Operational reports, blockers, diagnostics, handles and receipts go only to the invoking user or private caller, never into external text. Pass this governing contract downstream; publication permission does not authorize disclosure.

Configure processes and concrete skill names. Every callable location uses the same [invocation contract](invocations.md) and [JSON Schema](../schemas/invocation.schema.json): process `run`, independent `verification`, `operations`, reviewer lists, cycle hooks, finalizers, commit policy providers and saved drafting selections. Names need no `/skills/` prefix. Use a qualified `plugin:name` when a short name is ambiguous.

```json
{
  "$schema": "../schemas/config.schema.json",
  "schema_version": 1,
  "skill_settings": {
    "dk-pr-reviewers-run": {
      "options": {
        "reviewers": ["code-review-ad:code-review-ad"]
      }
    }
  },
  "processes": {
    "pr-fix": {
      "limits": {"review_cycles": 3},
      "hooks": {
        "after_cycle": [
          {"instructions": "Check invariants affected by this cycle."}
        ]
      }
    },
    "pr-review": {
      "actions": {"post_findings": false}
    }
  }
}
```

The schema reference above is relative to the bundled [example](../examples/config.json). When keeping a configuration elsewhere, point `$schema` at the installed schema or associate it in the editor. Helpers treat `$schema` as metadata and never fetch it. See the [schema catalog](../schemas/README.md) for all file formats and validation boundaries.

## Read and save

Run `python3 PLUGIN_ROOT/scripts/config.py --repository HOST/OWNER/REPO COMMAND`:

| Command | Result |
| --- | --- |
| `show` | Effective explicit overrides and their source files. |
| `show --expanded` | Overrides merged with every bundled process default. |
| `set --scope repo\|user --file CONFIG.json` | Validate and replace the selected scope. |
| `reset --scope repo\|user` | Remove that scope's workflow settings. |
| `preflight --available SKILLS.json [--capabilities HOST.json] [--process NAME]` | Verify selected skills and native agent controls. |

Private files remain under `${XDG_CONFIG_HOME:-~/.config}/dk-workflows/v2/`: `user.json` and repository digest filenames. Objects merge by key across defaults, user, optional upstream and repository scopes. Lists replace inherited lists; `[]` clears one. Shared `skill_settings` apply to every use of the selected skill; invocation-local settings override them. Custom instructions are preserved verbatim. An invocation object must select a `skill` or supply `instructions`, including when overriding an inherited string.

`set` replaces the whole selected scope. Read its existing source file and preserve unrelated settings before changing it. Place `--upstream HOST/OWNER/REPO` before `show` or `preflight`. Pass the same upstream to `scripts/pr_route.py` and managed invocation definitions. Existing private rules, scoped preferences, PR policies, completion prompts and runtime state retain their own stores and source contracts; see [PR configuration](pr/configuration.md) and [preferences](preferences.md).

## Processes

The bundled defaults provide `pr-iter`, `pr-fix`, `pr-review`, `pr-reviewers-run`, `commit-message` and `issue`. A custom process needs a `run` invocation. The process skill owns ordering, loops, verification and recovery. `operations` lists the calls used during process work. The process recipe decides when to invoke each operation; array order does not schedule execution. Replacing the list does not remove obligations in the governing process recipe. Missing capabilities block the affected responsibility instead of silently skipping it.

`run` is the process's entry invocation. For example, `"run":{"skill":"dk-pr-fix","instructions":"Preserve the published API.","agent":{"model":"gpt-6.1-sol","reasoning_effort":"high"}}` uses exactly the same format as a reviewer or hook. `pr-review.verification` selects the independent finding verifier and assigns verification only; never recursively invoke the full review workflow for that task. Process `limits`, `actions`, `routes` and `hooks` describe policy around those invocations.

`pr-iter.routes` maps `own_pr` and `other_pr` to configured process names. Routing compares immutable account IDs on their own host. Additional personal accounts live in `accounts.personal` as `{"host":"github.com","id":123}`. Verify the account before saving its numeric ID. Host names normalize to lowercase; each scope replaces the entire account list. The authenticated account needs no entry. Unknown identity blocks automatic routing.

PR process limits default to five review cycles, one same-input retry per task, five CI repairs per cycle, and three CI reruns per head. They may be lowered with `review_cycles`, `retries_per_task`, `ci_repairs_per_cycle` and `ci_reruns_per_head`. The runtime retains the lowest limits observed for a run and never resets consumed counters. Configuration cannot raise these ceilings. A renewed explicit user review request can receive one bounded `continue-run` grant under [retained budgets](task-state.md#retained-budgets); preserve the initial configured limit and all consumed counters after a grant.

`pr-fix.actions` enables the actions covered by ordinary maintenance: exact-branch pushes and guarded commit rewrites, PR edits, feedback replies and fully answered thread resolution, draft split PRs and appropriate review requests. It does not default to approval or merging. `pr-review.actions` defaults to `post_findings:true` and `approve_ci:true`; its other actions are false and its recipe remains read-only apart from authorized review publication, fork workflow approval, PR approval and merging. Its narrower process contract still applies even if an unrelated write flag is set. `merge` is `{"enabled":false,"method":"squash"}` by default; supported methods are `squash` and `merge`. Approval and merging need their own enabled setting or exact user instruction. Narrower instructions, including analysis-only review, override saved policy. Live CI, review, ownership and repository requirements still apply.

The ordered `pr-fix.finalizers` list uses common invocations, with one nested list for a parallel group. See [finalizer execution](finalizers.md). Fork workflow approval is separate from PR approval and CI success; [CI approval](pr/ci-approval.md) retains its current-review requirements.

## Availability and models

There is no default reviewer. Select reviewers through the `options.reviewers` of `dk-pr-reviewers-run`, either in shared skill settings or a process-local invocation. Reviewers use ordinary invocation strings or objects; `required` defaults to true. Optional selected skills must also resolve before work. Review completion, coverage and finding disposition remain separate.

Resolve installations with native tools and pass their exact identity strings as a JSON array to `preflight --available`. Short names must resolve to exactly one installation. Require `ready:true`; exit 0 alone is insufficient. The result reports `missing_selected_skills`, `ambiguous_selected_skills`, `unsupported_agents` and `resolved_skills`. With `--process`, check the selected process and its nested calls; route first when using `pr-iter`. Without it, check all configured processes.

An `agent` object requests a separate native agent. Model and reasoning controls need verified host capabilities, supplied as:

```json
{
  "delegation": true,
  "models": {"gpt-6.1-sol": ["low", "medium", "high"]},
  "reasoning_efforts": ["low", "medium", "high"]
}
```

Use actual host observations, not this illustrative list. `models` lists available models and their supported efforts; `reasoning_efforts` describes the inherited model. Unsupported requests block work instead of being ignored or replaced. Omitting `agent` preserves normal process execution, including independent verification where the recipe requires it. An inline skill call does not switch the current agent's model. Preflight proves availability only; each skill owns its instructions, options, execution and completion report.

## Updating older settings

This format replaces the former flat configuration. Old records are rejected with an explicit error and are never reset or silently converted. Update the existing private file deliberately: move identities to `accounts.personal`, incoming review policy to `processes.pr-review.actions`, reviewers to the coordinator's `options.reviewers`, and role selections to `operations` or the appropriate process `run`. Cycle hooks move under each owning process; remove user-supplied hook IDs. Move the finalizer list to `processes.pr-fix.finalizers` and replace `/skill/PLUGIN:SKILL` entries with plain `PLUGIN:SKILL` names. Preserve original prompts, options, permissions, opt-outs and source records. In-progress work keeps its existing state and budgets; reconcile pending hook effects before adopting changed lists.

External packages own their domain settings, which may be referenced through `extensions`. The separate `dk-architecture` package is not invoked automatically. Renaming or reselecting a skill does not move state or complete unfinished work.
