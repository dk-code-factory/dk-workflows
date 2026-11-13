# Simplification reviewer

`dk-pr-review` and `dk-pr-fix` independently own `simplification`. Defaults are
`enabled:true`, `priority:"change-first"`, `allow_untouched_code:false` and
`reviewer:{"skill":"dk-workflows:dk-code-simplify","options":{},"required":true}`.
The bundled reviewer owns separate standalone priority and scope defaults.

`change-first` favors small, easily reviewed improvements, then resulting code
clarity. `code-first` favors clearer resulting code, then minimizes the necessary
change. Line count alone never establishes simplicity. Preserve behavior, public
contracts and necessary operational guarantees. Each recommendation explains the
complexity reduced, proposed scope, priority tradeoff and verification needed.

With untouched code disabled, proposed edits stay within changed hunks. Enabling
it permits directly related surrounding code or call sites only when needed for a
concrete simplification of the PR. Every cycle remains anchored to the supplied
PR changes and agreed scope; previous recommendations cannot ratchet scope into a
full refactor. Broad redesigns, repository-wide cleanup and unrelated modernization
are excluded. Out-of-scope opportunities are separate deferred observations.

The reviewer recommends without editing or publishing. Incoming review verifies
concrete maintainability benefits independently; it need not invent a functional
bug. Verification challenges benefit, behavior preservation, scope, tradeoffs and
validation, then publishes only confirmed actionable recommendations through the
normal publisher with a changed-line anchor. Fix workflows assess original outputs
through existing assessment, implementation, testing, disposition and publication
stages. Reject or defer suggestions lacking sufficient benefit or violating scope.
Simplification never substitutes for ordinary review coverage; an empty ordinary
reviewer list still requires configuration before claiming full review readiness.

## Configuration examples

Save through `dk-config` using [skill-owned configuration](skill-configuration.md).
For incoming review only, disable the additional reviewer:

```json
{"simplification":{"enabled":false}}
```

For fix workflows, favor resulting code clarity and allow necessary related edits:

```json
{"simplification":{"priority":"code-first","allow_untouched_code":true}}
```

Replace the provider with a fully qualified skill invocation, preserving its own
options, exact additional instructions, supported native controls and optionality:

```json
{"simplification":{"reviewer":{"skill":"custom:simplify","instructions":"Focus on control flow.","options":{"detail":"high"},"agent":{"model":"HOST_SUPPORTED_MODEL","reasoning_effort":"high"},"required":false}}}
```

Replacement providers must honor the same governing priority, scope and read-only
recommendation contract. Provider options remain provider-owned. Managed bundled
calls receive workflow priority/scope as explicit option overrides as well as
instructions. Disabling skips provider resolution and preflight; malformed settings
still fail validation. Enabling requires the selected provider, native controls
and evidence transport to pass preflight, including optional providers.

## Live workflow selection

Composition occurs only inside an existing selected `dk-pr-reviewers-run` task.
It appends a reviewer after ordinary groups, leaving custom task ordering and
parallel boundaries intact. Removing the stage removes simplification too. Runner
`execution` and `max_parallel` still govern scheduling. Standalone runner calls
keep their configured reviewer list, with no injected reviewer.

Pass a live workflow selector to runner plan/preflight and every managed reviewer
leaf. For the default fix task index 6 (incoming review uses index 1):

```json
{"skill":"dk-workflows:dk-pr-fix","index":6,"options":{}}
```

Use the actual flattened task index in custom plans and the original workflow
invocation-local options. Read runner `skill-plan --kind reviewers --resolved`
and `skill-preflight` with `--workflow SELECTOR.json`. Do not also pass copied
runner options: the selector derives them from the live owning task. Managed
reviewer descriptors add `workflow:SELECTOR` alongside `kind:"skill"`,
`skill:"dk-workflows:dk-pr-reviewers-run"`, `selection:"reviewers"` and the leaf
index. `skill-spec` also accepts `--workflow`. The runner's managed assignment
supplies this selector in `options.workflow`.

Prepare, finish and resume resolve that owning occurrence afresh. Disabled or
removed selections cease to be required; never use old result counts as the live
required set. Provider/policy changes refresh affected simplification assignments
without changing ordinary reviewer identities or completed siblings. Interrupted
claims, original reports, checklists, native evidence, dispositions and existing
run/cycle budgets retain their normal lifecycle. No extra task store or allowance
is introduced. Select the live plan again before assessing completion.
