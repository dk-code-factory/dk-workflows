# dk-workflows

Independent MIT plugin for Claude Code and Codex. Version 1.8.1.

## Install

Claude Code:

```text
/plugin marketplace add dk-code-factory/dk-workflows
/plugin install dk-workflows@dk-workflows
```

Codex CLI:

```sh
codex plugin marketplace add dk-code-factory/dk-workflows
codex plugin add dk-workflows@dk-workflows
```

Each repository catalogs only its own plugin. Installing this plugin does not install DK or another reviewer. The [organization catalog](https://github.com/dk-code-factory/skills) offers independent selection of all four plugins.

## Skills

- `dk-architect`
- `dk-architecture-review`
- `dk-architecture-enforcer`
- `dk-commit-message`
- `dk-config`
- `dk-flaky-tests`
- `dk-issue-description`
- `dk-pr-address-reviews`
- `dk-pr-decisions`
- `dk-pr-description`
- `dk-pr-iter`
- `dk-pr-post-review`
- `dk-pr-read-comments`
- `dk-pr-reply-review`
- `dk-pr-review`
- `dk-pr-split-commits`
- `dk-review-stats`

Both manifests select their own native skills directory. Instructions and runtime resources remain inside this plugin. No build step, sibling checkout, submodule, or generated host package is required. Use the host's skill selector with the names above; Claude also exposes `/PLUGIN:SKILL` commands.

## Requirements

Python 3.11+ for lifecycle helpers; Git for repository workflows. Authenticate the selected host using its normal login. GitHub workflows also use `gh` authenticated for the target repository. External reviewers and their tools are optional and needed only when selected. Credentials, state, and caches stay outside the plugin and reviewed repository.

## Updates

The self-contained `scripts/update.py` runs at skill entry and completion. It checks this repository's latest stable GitHub release at most once per 24 hours. Network failures do not block work. Default policy is `notify`; it reports a newer version once without installing it. `auto` schedules installation after all active workflows finish, including nested invocations across DK plugins. `off` skips release checks. Interrupted workflows retain completion tokens; complete them explicitly after their work has stopped. Do not discard active lifecycle records to force an update.

Set script policy with:

```sh
python3 PLUGIN_ROOT/scripts/update.py policy --host codex --mode auto
python3 PLUGIN_ROOT/scripts/update.py policy --host claude --plugin dk-workflows --mode notify
```

`PLUGIN_ROOT` means the installed plugin root. Settings live at `${XDG_CONFIG_HOME:-~/.config}/dk-code-factory/updates.json`; checks and lifecycle state live at `${XDG_STATE_HOME:-~/.local/state}/dk-code-factory/updates/`. Policy is global with per-plugin overrides. Host registry identity, version, marketplace, pins, and scopes govern installation. Pinned versions, non-main refs, local checkouts, and unregistered plugin directories do not auto-update. The script uses supported host commands and never edits a plugin cache or pulls inside it. It confirms the installed version before reporting success and reports reload requirements or failed updates.

Claude's native marketplace auto-update is separate: enable or disable it in `/plugin` → Marketplaces. Native updates may require `/reload-plugins` or a new session. Script `off` does not disable Claude's own updater. Manual Claude refresh uses `claude plugin marketplace update MARKETPLACE` then `claude plugin update PLUGIN@MARKETPLACE`.

Codex manual refresh uses `codex plugin marketplace upgrade MARKETPLACE` then `codex plugin add PLUGIN@MARKETPLACE`; start a new session after installation. Script policy does not replace host refresh behavior. Each reviewer updates independently.

## Development and releases

Run `python3 scripts/check.py` and `python3 scripts/test.py` (development dependencies: pytest and pytest-subtests). Runtime is standard-library Python; the AD inventory helper also uses Node.js. Host manifests must share a semantic version. Use `python3 scripts/version.py X.Y.Z` before changing skill/runtime behavior on main. CI enforces version bumps on pull requests; a `vX.Y.Z` tag validates main ancestry, runs checks, and creates the GitHub release. Catalogs consume released main; keep unpublished work on branches.

The offline [workflow behavior evaluation](docs/workflow-behavior-evaluation.md) checks synthetic trace controls and documents real-trace normalization and isolated version comparisons.

Owned source provenance is recorded in `PROVENANCE.json`. No installed system skills, external plugins, credentials, private rules/state, caches, or benchmark results are distributed.

Private [workflow usage attribution](docs/workflow-usage.md) observes lifecycle,
helper stages, provider attempts and actual review/completion outcomes across
standalone and iteration workflows. It preserves unknown host measurements and
checkpoint reuse separately from execution, without changing workflow gates or
the reviewer quality ledger.

## Plugin configuration and guards

These settings describe the installed plugin's behavior in repositories where it is used.

Use `dk-config` in plain English to inspect or change settings. There is no default reviewer. Commit messages, PR descriptions, and issue descriptions use bundled DK drafting by default. Unrelated DK workflows work without configured reviewers.

```json
{
  "reviewers": [
    {"skill": "code-review-ad:code-review-ad", "host": "current"},
    {"skill": "pr-review-toolkit:review-pr", "host": "claude"},
    {"command": ["review-helper", "--handoff", "{handoff}"]}
  ],
  "pr_size": {
    "added_lines_threshold": 1000,
    "evacuate_unrelated": "automatic",
    "split_related": "automatic"
  },
  "drafting": {
    "commit_message": "builtin",
    "pr_description": {"skill": "my-plugin:describe-pr", "host": "codex"},
    "issue_description": "builtin"
  }
}
```

This is an overview of the effective model; private files store reviewers and drafting roles separately. `repo_rules.py reviewers set --bindings-file FILE` accepts the ordered reviewer array. `repo_rules.py drafting set ROLE --binding-file FILE` accepts one binding. `reviewers list`, `drafting list`, and `diagnose` inspect settings. `reviewers reset` and `drafting reset ROLE` remove overrides; `drafting set ROLE builtin` explicitly selects a bundled implementation. Use `--scope user` for user defaults. Rules retain fork → upstream → user precedence; drafting roles inherit individually and reviewer lists replace as ordered selections. Native entrypoints pass `DK_HOST` and lifecycle tokens; use the same environment when invoking helpers directly.

Skill bindings use the selected host's registry and manifest. Commands receive a `git-diff-v1` handoff through an argument vector, without a shell. No DK-specific provider manifest is required. Missing or ambiguous selected installations fail with diagnostics; unused plugins do not affect lookup. Legacy wrapper identities normalize in memory to the corresponding native skill and `host: claude`. Existing private-state and telemetry paths remain unchanged; aliases are recorded in `runtime/shared/bindings.py` without rewriting past observations.

Native Codex skill bindings may select a named execution profile through the same
binding files, for example:

```json
{"skill": "my-plugin:review", "host": "codex", "codex_profile": "review"}
```

`codex_profile` must be a native name containing letters, numbers, hyphens or
underscores. `host: current` is checked against the resolved executor; Claude and
command bindings reject profile selection. Omission preserves existing provider
identities and execution arguments and performs no profile metadata discovery.
A readable profile-v2 file is required at `$CODEX_HOME/<profile>.config.toml`
(default home: `~/.codex`). This format was introduced in Codex 0.134; native
selection rejects matching legacy selectors or tables in base `config.toml`.
Definitions, model choices and user context thresholds stay in private Codex
configuration.

Resolution uses native no-turn validation and configuration discovery, including
profile-controlled project trust/root markers and managed precedence. Relative
`model_catalog_json` and `model_instructions_file` references use the selected
file's lexical parent; inherited references keep their native origins. Contents
and canonical targets are fingerprinted separately. Required facilities include
native configuration/requirements reads, active catalogue discovery and
`debug prompt-input`. Native default-model resolution also uses model listing
when the accepted API-key catalogue projection does not apply. The adapter was
validated with Codex 0.164.1; unavailable
facilities or unsupported metadata fail visibly, and native version changes
invalidate preparation.

For profiled reviewers, preparation captures execution evidence in its owned
execution checkout before analysis, retaining the supplied descriptor separately.
Internal analysis keeps its existing source context. Native project trust is
observed in the actual execution checkout; original-checkout trust is not copied.
Exact live evidence stays workspace-bound. Engineering comparison projects only
proved typed owned paths with content, source and target evidence; external
references, trust changes and unknown fields remain governing.

Preparation and telemetry distinguish configured values, effective request
settings and CLI header observations. Native catalogue defaults, effort aliases,
context maximum/headroom and compaction scope determine effective settings;
a native proven absence remains distinct from an unknown value. Provider
definitions and governing layers/catalogues are fingerprinted without retaining
credential/header values. The metadata helper selects from a private immutable
catalogue capture; actual execution continues through `codex exec --profile`
without a catalogue override. Bounded input/catalogue rereads reject observed
drift before launch, checkpoint reuse and result acceptance; they do not promise
an atomic snapshot against transient changes between observations.

Metadata helpers project required differences in selected-provider definitions,
catalogue paths, top-level routing/auth settings and features, together with
project trust/root markers and native sanitation controls. Discovery omits proxy
routing URLs; credential-family destinations may require a private baseline read
before the authoritative selected discovery round. Unchanged sensitive leaves
stay in native files. Broker/masking booleans, credential environment-variable
names and ordinary credential-family metadata remain supported.

Required generic header and eligible bearer overrides use child-only native
environment channels. Needed literal query/auth-argument, sensitive
`model_catalog_url` or message-board bearer-token overrides without a proven safe
channel fail before the affected helper starts. Explicit routing, OAuth, proxy,
credential-destination and message-board URL fields reject required username or
password user-info using native credential-position rules. This does not classify
arbitrary path/query or other String values as secrets. Unused static-catalogue
endpoint and inference fields are not copied or rejected. Original fingerprints
exclude random transport variable names. These channels never alter the actual
`codex exec --profile` environment or argv.

The native raw-catalogue facility cannot address the special internal Work/Chat
originator partitions used by some first-party hosts. Verification fails clearly
for those dynamic routes; use an ordinary CLI environment or an explicitly
configured native catalogue snapshot. An explicit static catalogue does not use
those partitions. Missing profiles/files, unverifiable metadata and applicable
observed-setting mismatches also stop execution. Known profile metadata stays
outside compact reviewer prompts. Validation starts no model turn; offline
regressions cover the adapter and retained-checkpoint guards.

Review bindings can explicitly select `"handoff_contract": "git-diff-v2"` for a
provider that supports compact input. It carries shared guidance, active Decisions,
the assigned addendum and hashed references to complete originals. Existing bindings
retain full v1 delivery; mixed formats are supported. Contract changes require fresh
preparation. See the [handoff contracts](runtime/dk-pr-review/references/handoff-contracts.md).

Providers return findings or draft content only. DK owns pinned revisions, engineering preparation, active Decisions, evidence checks, bounded correction/review loops, review-state tokens, aggregation, retries, telemetry, and authorized publication. Draft providers use the `dk-draft-v1` content contract and cannot grant publication authority. PR title/body and force-push guards still require completed review tokens and fresh evidence. The supplied `scripts/repo_rules_hook.py` is an optional generic JSON startup hook; installation does not change existing hooks.

AD, Mat Pocock and Toolkit reviewers, and providers declaring nested review roles, need retained native execution evidence before coverage can be complete. A clean findings object alone does not establish that those passes ran. Missing execution, unavailable evidence and unresolved conditional applicability remain explicit blockers; checkpoint reuse and final coverage revalidate the retained proof. Historical results remain unchanged and cannot supply missing execution evidence retroactively.

For a Codex startup integration that follows installed plugin versions, copy
`scripts/codex_rules_hook.py` from the installed plugin to a stable local hooks
directory. Configure your hook runner's `SessionStart` and `SubagentStart` commands
to invoke `python3 /absolute/path/to/codex_rules_hook.py`, passing its JSON event
payload on standard input. Set the outer timeout to at least **310 seconds**. This
is an optional adapter for hook runners with the generic JSON protocol; plugin
installation does not register it or change Codex settings.

The copied adapter runs `codex plugin list --json` with a **90-second** timeout,
selects the exact enabled `dk-workflows@dk-workflows` identity, and checks its
installed manifest name and version before starting rule refresh. It uses the
registered installation path when present, otherwise the versioned cache under
`CODEX_HOME` (default `~/.codex`). Use `--plugin-id dk-workflows@MARKETPLACE` for
another registered marketplace. Refresh has a separate **180-second** timeout.
Both budgets are configurable with `--lookup-timeout` and `--refresh-timeout`;
keep the outer timeout greater than their sum, with startup overhead allowed.
Timed-out commands and their summarizer descendants are terminated together in
an isolated process group before the hook returns. Cancelling the adapter with
`SIGTERM` also stops these workers and exits with a failure status.

Failures identify lookup, installation validation, or refresh, include the
failing command and its timeout, and state whether refresh started. For a lookup
failure, run `codex plugin list --json`, check the selected enabled identity and
manifest version, then retry the hook; raise the lookup and outer timeouts
together if needed. For a refresh failure, inspect that error and run
`$dk-workflows:dk-config refresh` in the repository. Updating the copied adapter
is explicit; installation changes are resolved through the registry on each run.

Command providers receive the evidence handoff through their configured `{handoff}` argument. They receive output instructions on standard input, including validator errors and the prior response on a correction attempt; `DK_PROVIDER_ATTEMPT` starts at 0. Return the declared JSON contract on standard output and diagnostics on standard error. The handoff stays unchanged during correction.

Provider processes have a 40-minute timeout per invocation, including each correction attempt.
Set `DK_PROVIDER_TIMEOUT_SECONDS` in the environment running the workflow to override it:

```sh
export DK_PROVIDER_TIMEOUT_SECONDS=1800  # 30 minutes
```

The value must be a positive integer number of seconds; invalid values fail before
starting the provider. This applies to Claude, Codex, and command providers used for
reviews, preparation, Decisions, and drafting. It is not a deadline for the entire workflow.

Native Codex profile verification runs before and after provider execution and at
other freshness gates outside this provider-process timeout. Each native metadata
operation has its own local 30-second budget; one app-server call shares that
budget across its requests. Several sequential operations may add minutes. This
includes an additional baseline operation when credential-family discovery needs
private leaf comparison. It is not a whole-attempt or whole-review deadline.

Internal read-only analysis uses the calling host by default. After quota
exhaustion, a coordinator may select an installed, authenticated alternate under
the current task authorization by prefixing the original helper command with
`DK_ANALYSIS_HOST=claude` or `DK_ANALYSIS_HOST=codex`. Keep `DK_HOST` and the retained
invocation unchanged. Selection is explicit; there is no automatic failover.
Configured reviewers, drafting bindings and architecture roles with explicit
packets retain their providers. Internal catalog analysis is eligible. The
selected provider and calling host are retained privately, and selection never
resets a correction budget or relaxes evidence, review, CI or completion gates.
See [the provider policy](common/lifecycle.md) for recovery and provenance details.
PR review sessions automatically restart usage-exhausted reviewers once with the
same provider, evidence and correction budget. Longer or ambiguous resets return
a durable incomplete checkpoint; see [quota recovery](runtime/dk-pr-review/references/sessions.md#quota-recovery).

Interruption aborts this invocation's in-flight provider transport and prevents later provider or correction launches, then waits for reviewer cleanup and checkpoint work before releasing session ownership. Completed checkpoints remain available for exact-evidence resume. Unfinished reviewers remain unknown rather than provider failures; a resume reruns them. Genuine failures observed before interruption retain their classification.

An in-progress native profile metadata observation is not a cancellation point.
It may finish its remaining locally bounded helpers before worker cleanup and
checkpoint acknowledgement release ownership; interruption does not promise
prompt cancellation of those metadata operations.

Reviewer description, execution and configuration diagnostics reuse one Codex
plugin inventory within each command invocation. A later invocation reads the
registry again; selected manifests and skill files are still checked on each
resolution. This inventory reuse never supplies cached review findings.

Known GitHub read operations retry transient connection and HTTP 5xx failures
at most three times, with jitter and one shared deadline per read (60 seconds
unless the caller supplies a shorter timeout). Authentication, schema failures,
normal CI outcomes and publication operations are not retried. Exact-head and
source-evidence checks still run after reads recover.

## Workflow helper CLI

Use `python3 PLUGIN_ROOT/scripts/dk.py commands` for the machine-readable helper
map. The entrypoint resolves packaged scripts independently of the working
directory, normalizes PR identities and supplies the selected host environment:

```sh
python3 PLUGIN_ROOT/scripts/dk.py --host codex --parent UPDATE_TOKEN \
  --cwd REPO --pr https://github.com/OWNER/REPO/pull/42 run-state show
```

Place common context flags before the helper name, followed by its existing
native arguments. `--repo OWNER/REPO --pr 42` is also accepted; enterprise hosts
can use a full PR URL or `--repo HOST/OWNER/REPO`. `--dry-run` prints the argument
vector and workflow environment without executing it. The router preserves
helper exit codes and publication guards. Existing script entrypoints remain
available. Lifecycle entry/completion still happen once per skill invocation.

`inline-feedback` and `replies` preserve `#pullrequestreview-ID` links to select
a review, and `#discussion_rID` links to select one thread, including GitHub Code
Quality findings. Complete PR evidence remains available as context; replies and
resolution stay within the selection. Resume with the original link.

At lifecycle entry, the receipt supplies `runtime_root` and `completion_script`.
Use these retained paths for the invocation and its child handoffs. Packages are
copied once per content hash into private lifecycle `runtimes/` storage, preserving
resources and executable bits. Installed cache replacement cannot remove these
copies. Resource changes are detected before reuse; retained copies are kept for
interruption recovery and review evidence. Release-check failures stay nonblocking;
failure to retain the required runtime blocks its use.

New reviewer preparation records bind native providers to retained package copies.
An active review continues with that exact package when the host registry changes;
it never silently selects the replacement. Fresh preparations use the current
registry. Legacy preparations retain their existing source and registry checks.

Command providers retain their packaged entrypoints and standalone executables
or interpreter scripts. Ordinary external data and output arguments keep their
original paths. Drafting corrections share one retained binding for the whole
operation. Interpreter and host installations remain runtime prerequisites.
Completed review reuse follows the same retained binding checks as execution.

When Codex exits zero, response-file write diagnostics and missing or unreadable
responses stop execution with `provider_output_failed`, before JSON correction.
Nonzero exits retain their ordinary provider-error classification. Repair storage
capacity, quota or permissions before retrying manually. A complete response on
stdout does not recover a failed response file. Genuine JSON/schema failures
still receive at most one correction.

These zero-exit response-file failures retain `provider-output-failure.json` in a
private workflow directory under
`${XDG_STATE_HOME:-~/.local/state}/dk-code-factory/workflows/`,
independently of optional review telemetry. It contains invocation metadata,
return status, complete stdout/stderr, and available response bytes in
`response_base64` (or `response_read_error` when unavailable). Directories use
`0700` and evidence files use `0600`. If persistence also fails, the error reports
both failures and the original provider temporary directory is retained. Inspect
the reported evidence path; remove that failure's evidence directory manually
when no longer needed. Retained diagnostics may contain private model output.

Provider temporary files continue to honor `TMPDIR`. To move them off a full or
quota-constrained temporary filesystem, set a writable private directory before
launching the helper so nested providers inherit it:

```sh
mkdir -p "$HOME/.local/state/dk-code-factory/tmp"
chmod 700 "$HOME/.local/state/dk-code-factory/tmp"
export TMPDIR="$HOME/.local/state/dk-code-factory/tmp"
```

Add `--result-file /ABSOLUTE/PRIVATE/result.json` before the helper name for a
typed terminal receipt. Its `outcome` distinguishes `success`, expected workflow
`blocked` states, infrastructure `error`, and `interrupted` work. `error_code`,
`next_action`, `exit_code` and `artifact` identify what needs attention. Read the
outcome even when the native helper exits zero: incomplete reviewers and pending
Decisions are blocks. `retryable` is false; reconcile mutations before retrying.

The result artifact retains parsed helper data and paths to complete stdout and
stderr files. All three paths must be new and outside the repository and plugin;
they are reserved before execution and created with private permissions. Storage
failures during reservation prevent dispatch. A `running` artifact without a
terminal result signals interrupted or uncertain work and requires reconciliation. Direct helper output
and exit codes remain available when the flag is omitted. The envelope reports
workflow gates; it cannot authorize publication or replace native validation.

The stable CLI automatically uses artifact receipts for `run-state`, `replies`,
`reviewers`, `conversation` and `inline-feedback`. Results live under
`${XDG_STATE_HOME:-~/.local/state}/dk-code-factory/helper-results/`; an explicit
`--result-file` selects another new private path. `--full` before the helper name
prints native output. Native help, `--dry-run` and direct script entrypoints keep
their existing output. Retained results are not automatically deleted.

Parse the artifact's `data` from disk in programmatic consumers. Never parse a
possibly truncated tool response as the full helper result. Inspect a bounded
page with `dk.py result --artifact FILE --section /events --since 20 --limit 10`.
Sections use JSON pointers relative to `data`; `/` and `~` in keys use `~1` and
`~0`. Collection entries show container counts and scalar previews; select a
deeper pointer for details. Long text stays in the artifact. `next` is an index
for the next page in that immutable result, not a cursor across different runs.

Create a versioned command reference once per root workflow with
`dk.py --parent ROOT_UPDATE_TOKEN reference`. Share its returned path as
`DK_CLI_REFERENCE` (or common `--reference FILE`) with child agents. Helper help
queries then return a small reference/section receipt; the complete help text is
retained once under that section. Concurrent identical help queries run one
helper process. Read relevant help from the reference once per agent context.
References live in private state, are scoped to the root workflow, and invalidate
when the package version, router or helper scripts change. No workflow output or
review findings are cached. Without a reference, help retains its native output.

Iteration instructions use a coordinator entry with mandatory stage contracts.
Read each contract before its stage; child handoffs select their role's contracts
and retain pinned evidence paths. Unchanged sections already read in the same
agent context can be reused with their immutable runtime identity. A parent read
does not replace a child's required reading, and live publication evidence is
always refreshed through the existing gates.

Reply reconciliation diagnostics identify changed heads, scope fields and
source/thread IDs without repeating conversation bodies. Receipt failures retain
their URL, provenance and expected/actual hashes. Diagnostics survive interruption
in the run record and supply a next action; unchanged publication guards still
require fresh complete evidence and independent approval.

Review providers receive valid JSON examples and a packaged response schema.
Field-level validation errors identify the rejected field without dumping all
eligible lines. The adapter still checks the pinned diff and allows one correction;
incomplete responses require an empty findings array and an explicit reason.
Use `dk.py --host HOST validate-review --schema` to inspect the provider contract.

Lifecycle entry retains and validates the package before consulting optional host
registry metadata. Registry failure keeps the frozen package pinned and cannot
schedule an update. All later manifest reads use that retained copy, including
when the host removes the original installation during lookup. Entry failures
without a usable runtime receipt stop work; offline release checks still return
a retained runtime and token and remain nonblocking.

Preparation v3 retains stable conclusion dependencies and immutable evidence snapshots.
`prepare --previous-preparation FILE` compares complete Git entries and governing
sources, reuses proven unchanged conclusions, and batches affected conclusions into
one reassessment. Identical and supported operational refreshes use zero model calls;
`--full-analysis` requests full reassessment. The refresh report exposes changed inputs,
affected/reused IDs and actual call counts. Review coverage, session identities, CI,
Decisions synchronization and iteration budgets retain their existing gates. See
[the preparation artifact contract](runtime/dk-pr-decisions/references/preparation.md).

Decisions can persist across independent runs by setting `DK_PR_DECISIONS_HOME` to
an absolute private directory shared by those runs. It is the complete cache root:
`<root>/v3/<host>/<base-owner>/<base-repo>/pr-<number>.json`, including fork PRs.
When unset, the default remains `${XDG_DATA_HOME:-~/.local/share}/dk-pr-decisions`.
Explicit stores are exclusive; invalid or repository-local paths fail without
fallback. Hosts provide persistent storage and access. See the
[Decisions storage contract](runtime/dk-pr-decisions/references/storage.md) for
seeding, migration, locking and interruption recovery. Recovered choices require
live context refresh and engineering preparation before dependent work.

Persist material human agreements immediately with Decisions `add` or explicit
`replace`, preserving exact instructions or discussion evidence, then refresh context
and preparation. Drafts and run records remain supporting history; local decision
imports are temporary. Publication still uses the existing authorization and guards.

Exported API v2 contexts now include `agreement_binding` version 1, covering the
resolved store root, sorted persisted IDs and a fingerprint of exact agreement
evidence and supersession. Preparation v3 and all live review/work/completion gates
check the configured store locally without network, models, migration or writes.
Changed agreements, missing/corrupt records, different store selection or pending
publication require reconciliation and refreshed context/preparation. Routine cache
bookkeeping does not invalidate review evidence. Older artifacts remain readable as
history or parents; current execution requires bound context. Discussion extraction
retains complete general comments and review bodies outside the prompt for bounded
reads, including unchanged proposals needed to interpret short acceptances.


Fresh workflows allocate durable private directories under
`${XDG_STATE_HOME:-~/.local/state}/dk-code-factory/workflows/`. Generated contexts,
isolated checkouts, manifests, evidence captures and conflict proposals belong there;
use the returned paths in handoffs. No automatic deletion policy applies. Preparation
gates read owned captures; arbitrary external evidence defaults to live checks, so
missing required rules or instructions still block. Live head/body, configuration
and local-choice checks remain mandatory.

`preparation_id` fingerprints the entire immutable artifact. Runtime-computed
`engineering_id` fingerprints its normalized governing evidence, tree/base, scope,
Decisions, ordered reviewer descriptors, analysis, readiness and policy. Supported
operational counters/status/receipts, storage paths, refresh reports and verified
lineage do not invalidate engineering compatibility; unknown fields still govern.
Session v2 checkpoints, review-result v3 coverage, scope assessments and completion
verification v2 can survive an operational refresh with the same engineering ID.
Interrupted resume also requires the same head. Tree-equivalent cleanup retains its
separate guarded reconciliation path. Provenance keeps exact preparation references.

New Decisions storage uses `dk-pr-decisions/v3/` under the private data home. Each
write checks a monotonic revision under the private-state lock; network/model work
runs outside it. A stale write returns API v2 `status: error` with
`error.code: metadata_conflict`, a retained proposal path and an explicit recovery
action. The stable CLI reports a workflow block. Publication intent is committed
before GitHub mutation; pending intent blocks other writers until its owner finishes
or `publish --reconcile` verifies the outcome without replaying the edit. A validated
legacy snapshot can be imported without changing its original file, including any
pending intent. Legacy active iteration runs require their retained runtime; a fresh
request after a terminal legacy run preserves history under the existing start rules.
There is no bulk migration of old runs, results or checkpoints.

Lifecycle entry is now one command:
`dk.py --host HOST --cwd REPO begin --skill SKILL_NAME`. Retain its `invocation`
path and use `dk.py --invocation FILE --cwd REPO HELPER ...`; finish with
`dk.py --invocation FILE end`. The receipt supplies a retained runtime, host,
token and CLI reference and grants no publication authority. Managed executors
create this receipt before entering the read-only provider and own completion.
Direct helpers and the earlier explicit lifecycle interface remain supported.

Publication helpers retain approved input hashes, target snapshots, intent and
verified receipts in private workflow storage. `edit-pr --prepare`,
`post-review prepare`, `publication-actions ci-rerun prepare`,
`publication-actions request-review prepare` and `branch-publish prepare`
cover the remaining mutations. Their publish/reconcile help describes the native
arguments. These publication routes also return artifact receipts by default;
use `--full` for native output. A pending operation permits read-only reconciliation, never an automatic
second mutation. Split and regroup pushes retain their distinct gates and recovery
state, including standalone split receipts.

Terminal receipts add bounded `message` and structured `action` fields
(command argv, an engineering task, or a human question). Complete diagnostics
remain in artifacts. `run-state next --head SHA` describes the current gate and
required artifacts without consuming review budgets. Load
[recovery details](common/recovery.md) only when a diagnostic requires them.

PR iteration shares a [waiting and communication policy](runtime/dk-pr-iter/references/waiting-communication.md)
across nested handoffs. Material changes are reported promptly; host-required
updates during unchanged waits use one brief pending-stage sentence. User-update
timing never drives extra worker polling, evidence queries, provider calls or CI
observations. The reference includes behavioral acceptance examples.

CI observation reports terminal raw status before issue-backed classification.
Automatic classification defaults to a shared 60-second deadline; both the
waiter and completion gate accept `--classification-timeout SECONDS`. It skips
history for unsupported current reports and collects relevant workflows/jobs
for unknown named failures. Historical attempt evidence can be reused; failure
classification keeps current head, attempt and issue checks live. Green checks
do not depend on flake issue history. Timeout leaves CI blocked. See [the
evidence contract][ci-evidence] for progress, retained diagnostics and
explicit discovery recovery.

[ci-evidence]: runtime/dk-flaky-tests/references/evidence.md

Version 1.7.0 adds [owned preparation refresh and typed cleanup evidence](runtime/dk-pr-iter/references/operational-refresh.md), concurrent architecture checkpoints, bounded same-cycle infrastructure recovery, persistent CI deadlines and private timing diagnostics. Fresh invocations use the new contracts; active older runs retain their original runtime. Update through the supported host plugin commands above. Reviewer installations, model selection and private configuration are unchanged.


PR iteration runs architecture review once after initial feedback fixes, immediately
before its configured-reviewer loop. Later cycles, CI repairs, commit cleanup and
completion retain that original result and reviewed head. Current architecture
rules still reach every worker, and unresolved architecture findings block progress.
Standalone PR reviews continue to run architecture alongside configured reviewers.

The offline [compact review compatibility report](docs/compact-review-compatibility.md)
compares retained full and compact provider evidence. Synthetic checks do not
establish live compatibility or enable compact reviewer bindings.

### Automatic PR scope evacuation and splitting

`dk-pr-iter` checks the complete final diff after preparation and published code
changes, before CI, review allocation and completion. At **1,000 added lines or
more**, it first evacuates unrelated work into separate drafts, then assesses
coherent splitting of the remaining scope. The primary deliverable stays in the
original PR, and all resulting PRs target the base repository default branch.
Tests, documentation, generated text and copied prerequisites count; deletions
never offset additions. Indivisible changes remain with recorded reasons.

Use `dk.py --host codex --cwd REPO config pr-size list`, or
`config pr-size set --added-lines-threshold 1000 --evacuate-unrelated automatic
--split-related manual`. Both modes independently support `automatic`, `manual`
and `disabled`, inheriting fork → upstream → user → default. `--scope user` sets
user defaults; `pr-size reset [SETTING ...]` restores inheritance. Manual mode
prepares verified local candidates before asking for action-bound approval; a
decline retains that work in original and is cached.

The stable `partition-prs check|prepare|verify|publish|reconcile` route owns local
change-preservation proofs, prerequisite DAGs and guarded recoverable publication.
See [the worker contract](runtime/dk-pr-split-commits/references/partition-prs.md)
for manifests and approval files. Prerequisites are copied once with `-x`, listed
in a fixed Dependencies table and frozen during later regrouping. All local
candidates verify before publication; new drafts/branches are observed before
removing source work. Interrupted writes reconcile through live reads without
duplicate pushes or PRs. After partitioning, iteration refreshes original scope,
Decisions, feedback and preparation, preserving review budgets and requiring
current-head review and CI. New drafts receive local tests only.

For a human decision to keep one PR together, use `dk.py --pr PR_URL config
pr-partition disable --reason "Keep this PR as one deliverable"`. The recorded
choice survives new commits and resumed runs. `enable --reason TEXT` restores
partitioning under the configured modes; `show` displays the preference, exact
human source and decision history. A declined individual proposal is audited
without implicitly disabling the PR.

Use `config pr-partition instructions --text "Partition only when each part has
an independent integration test"` with PR context to record conditional guidance.
The agent receives the exact text/source, assesses current evidence regardless of
line count, and records a reasoned partition/retain decision. Manual approval and
disabled behavior modes still apply. `clear-instructions --reason TEXT` restores
the numeric trigger; `reset --reason TEXT` restores the default preference while
preserving history. `--source-file FILE` can retain exact conversation evidence.
