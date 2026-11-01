# Workflow lifecycle

Run `python3 PLUGIN_ROOT/scripts/dk.py --host HOST --cwd REPO begin --skill NAME`.
Pass common `--parent PARENT_TOKEN` for a nested invocation. Retain the returned
`invocation` path, use `runtime_root` as PLUGIN_ROOT, and follow the lifecycle
action/message. The tool retains the runtime and creates the CLI reference.

Use `python3 PLUGIN_ROOT/scripts/dk.py --invocation FILE --cwd REPO HELPER ...`.
Common context precedes the helper; native arguments follow it. The receipt
supplies host, lifecycle token and reference without granting publication authority.
Direct helpers remain usable with `DK_HOST`, `DK_UPDATE_PARENT` and
`DK_CLI_REFERENCE`. Share the retained root and invocation path in child handoffs;
a child skill starts its own invocation with this token as parent.

`DK_HOST` identifies the calling host and lifecycle. Keep it unchanged when an
internal analysis provider is exhausted. Internal read-only analysis may use an
installed, authenticated alternate provider under the existing task authorization:
prefix the original helper command with `DK_ANALYSIS_HOST=claude` or
`DK_ANALYSIS_HOST=codex`. This per-command selection applies to unbound internal
semantic helpers, including Decisions, preparation, rule indexing and architecture
catalog analysis. It does not select configured reviewers, drafting bindings or
architecture reviewer/enforcer roles with explicit packets. Their bindings and all
review, CI, evidence and completion gates remain in force.

There is no automatic failover or retry. On quota exhaustion, either wait for that
provider or manually rerun the original helper with the alternate selector. Keep
the retained runtime, invocation, sources and correction budget. A journaled
provider failure with no response resumes its existing response slot; a rejected
or uncertain exchange remains blocked. Changing the selector never grants another
correction. Missing tools, authentication failures and exhausted alternates remain
failures; repair availability or report the blocker.

Selection is reported on stderr. Journaled helpers retain selected provider and
calling host in their exchange; other explicitly selected analysis calls retain
private `analysis-provider.json` request, runtime, lifecycle, attempt and response
provenance. A received response still requires the helper's normal validation.
Provider selection does not authorize publishing or accepting stale evidence.

Eligible helpers with JSON correction retain private source workspaces and
immutable exchanges even before an alternate is selected. Repeated discussion
analysis still reuses its current snapshot per PR and store; each completed
exchange retains its own response and validation history.

Always finalize with `python3 PLUGIN_ROOT/scripts/dk.py --invocation FILE end`,
including on failure. Wait for child work to finish before ending its parent.
When an executor supplies `DK_MANAGED_INVOCATION` for this skill, begin reuses it
and end leaves completion to that executor. The executor also supplies
`DK_UPDATE_MANAGED_RECEIPT`; never duplicate its lifecycle calls.

Read the typed terminal receipt even when a helper exits zero. Inspect complete
data in its artifact, or use `dk.py result --artifact FILE --section /FIELD` for
bounded inspection. Use `--full` for native output, `--dry-run` for routing, and
native `--help` for arguments. Follow a diagnostic's structured action; publication
reconciliation never repeats the mutation. Load [recovery details](recovery.md)
only for the reported condition. Keep authorization and engineering judgment in
the skill's normal procedure.
