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
