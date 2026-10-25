# dk-workflows

Independent MIT plugin for Claude Code and Codex. Version 1.1.22.

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

Owned source provenance is recorded in `PROVENANCE.json`. No installed system skills, external plugins, credentials, private rules/state, caches, or benchmark results are distributed.

## Configuration and guards

Use `dk-config` in plain English to inspect or change settings. There is no default reviewer. Commit messages, PR descriptions, and issue descriptions use bundled DK drafting by default. Unrelated DK workflows work without configured reviewers.

```json
{
  "reviewers": [
    {"skill": "code-review-ad:code-review-ad", "host": "current"},
    {"skill": "pr-review-toolkit:review-pr", "host": "claude"},
    {"command": ["review-helper", "--handoff", "{handoff}"]}
  ],
  "drafting": {
    "commit_message": "builtin",
    "pr_description": {"skill": "my-plugin:describe-pr", "host": "codex"},
    "issue_description": "builtin"
  }
}
```

This is an overview of the effective model; private files store reviewers and drafting roles separately. `repo_rules.py reviewers set --bindings-file FILE` accepts the ordered reviewer array. `repo_rules.py drafting set ROLE --binding-file FILE` accepts one binding. `reviewers list`, `drafting list`, and `diagnose` inspect settings. `reviewers reset` and `drafting reset ROLE` remove overrides; `drafting set ROLE builtin` explicitly selects a bundled implementation. Use `--scope user` for user defaults. Rules retain fork → upstream → user precedence; drafting roles inherit individually and reviewer lists replace as ordered selections. Native entrypoints pass `DK_HOST` and lifecycle tokens; use the same environment when invoking helpers directly.

Skill bindings use the selected host's registry and manifest. Commands receive a `git-diff-v1` handoff through an argument vector, without a shell. No DK-specific provider manifest is required. Missing or ambiguous selected installations fail with diagnostics; unused plugins do not affect lookup. Legacy wrapper identities normalize in memory to the corresponding native skill and `host: claude`. Existing private-state and telemetry paths remain unchanged; aliases are recorded in `runtime/shared/bindings.py` without rewriting past observations.

Providers return findings or draft content only. DK owns pinned revisions, engineering preparation, active Decisions, evidence checks, bounded correction/review loops, review-state tokens, aggregation, retries, telemetry, and authorized publication. Draft providers use the `dk-draft-v1` content contract and cannot grant publication authority. PR title/body and force-push guards still require completed review tokens and fresh evidence. The supplied `scripts/repo_rules_hook.py` is an optional generic JSON startup hook; installation does not change existing hooks.

Command providers receive the evidence handoff through their configured `{handoff}` argument. They receive output instructions on standard input, including validator errors and the prior response on a correction attempt; `DK_PROVIDER_ATTEMPT` starts at 0. Return the declared JSON contract on standard output and diagnostics on standard error. The handoff stays unchanged during correction.

Provider processes have a 40-minute timeout per invocation, including each correction attempt.
Set `DK_PROVIDER_TIMEOUT_SECONDS` in the environment running the workflow to override it:

```sh
export DK_PROVIDER_TIMEOUT_SECONDS=1800  # 30 minutes
```

The value must be a positive integer number of seconds; invalid values fail before
starting the provider. This applies to Claude, Codex, and command providers used for
reviews, preparation, Decisions, and drafting. It is not a deadline for the entire workflow.

Iteration requests use distinct bounded runs. Interrupted work resumes its existing budget; a new request after a terminal run retains history and receives a new budget. Completion requires configured-reviewer evidence for the final tree and current scope/Decisions. Explicit human acceptance can cover recorded provider failures for that exact run and result; incomplete reviews remain incomplete in reports. Missing/unrun reviewers, changed evidence and actionable findings cannot be waived. Passing CI alone cannot substitute for review coverage; reports distinguish completed, reused, skipped and explicitly accepted failed reviewers. Authorized fully answered threads are resolved during reply processing, independently of final review/CI completion.

Standalone reviews and iteration cycles share durable review sessions and immutable results. Fresh requests start fresh sessions; explicit interruption resumes can reuse completed reviewers with identical evidence. Iteration owns its cycle budgets and consumes the shared results. Optional statistics never supply cached review evidence.

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
