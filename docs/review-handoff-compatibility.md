# Retired reviewer handoff compatibility

[Issue #82](https://github.com/dk-code-factory/dk-workflows/issues/82) requested native compatibility trials for the opt-in compact reviewer contract in workflow 1.6.11, inspected at `d9005d269d7d125e897f7255542f43eb44eb20d4`. [The v2 cutover in #112](https://github.com/dk-code-factory/dk-workflows/pull/112) subsequently removed both reviewer delivery contracts, host executors, preparation and automatic architecture delivery. The current resolution is to preserve that removal and explicit migration boundary. It does not establish the historical providers' compatibility or complete the requested v1 trials.

## Compatibility record

| Requested path | Contract and execution | Evidence and current status |
| --- | --- | --- |
| Codex AD | v1 executor; full `git-diff-v1` versus opt-in compact `git-diff-v2` | No native comparison recorded here. Both delivery contracts are removed from v2. |
| Claude Toolkit followed by Claude AD, including `run-claude-skill` | Separate v1 invocations with the same pinned scope; full versus compact | No native comparison recorded here. Those host/command bindings are not v2 execution routes. |
| Selected native reviewer in v2 | Current agent's native skill tools; no workflow-selected full/compact reviewer contract | The installed skill owns its procedure, required passes, options and completion. Installation availability and offline checks do not prove live compatibility. |

The historical provider versions, installed procedures and observed pass coverage were not recorded by issue #82. Do not infer them from today's installations. No provider is enabled for legacy compact delivery by this record. The [original 1.6.11 contract](https://github.com/dk-code-factory/dk-workflows/blob/d9005d269d7d125e897f7255542f43eb44eb20d4/runtime/dk-pr-review/references/handoff-contracts.md) remains historical evidence rather than current guidance.

## Reproduce the boundary checks

Run from the revision being assessed and record its `git rev-parse HEAD` alongside the results:

```sh
python3 -m pytest -q tests/test_config_packages.py tests/test_execution_boundary.py
python3 scripts/check.py
```

The migration check sends the requested Codex AD and separate Claude Toolkit/AD cohort plus a command binding through the public configuration CLI. It exercises explicit full, compact and unknown contracts. All contract-bearing bindings must remain intact in `unresolved_bindings`, with no proposed reviewers, saved v2 settings or edits to the original configuration. Direct v2 configuration must reject those legacy binding fields. Existing alias-only migration remains supported; an alias mapping is not a compatibility trial.

The distribution check rejects the known legacy contract markers in production Python, including nested reviewer scripts. Its mutation tests demonstrate rejection and retain historical examples in test fixtures. This is a source guard, not a semantic detector for every possible implementation of compact delivery. These offline checks do not run providers, measure recall or verify native nested-pass execution. The migration checks preserve behavior already present at the v2 cutover.

## Current and retained runs

For new v2 work, explicitly select installed native skills and follow their current procedures. Supply the complete pinned scope, original governing guidance and required evidence as described in the [review recipe](../runtime/pr-review.md). Missing required evidence or incomplete skill work remains incomplete coverage. The current [compact PR snapshot receipts](pr/supplied-evidence.md) serve feedback, Decisions and read-comments analysis; they do not reintroduce the retired reviewer protocol or replace full reviewer evidence.

Keep active v1 runs on their exact retained runtime with their original bindings, artifacts, counters, correction budgets and publication guards. This change neither repairs those runtimes nor grants an unverified provider compact opt-in. Any separately authorized historical trial still needs exact provider/skill versions and procedures, executor, pinned scope/base/head, contract, original evidence paths/hashes, independent-pass coverage and retained full/compact findings. Retain raw results privately and report incomplete trials honestly. v2 imports no v1 review cache and adds no provider-specific execution-proof gate or automatic architecture invocation.
