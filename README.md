# dk-workflows

Independent MIT plugin for Claude Code and Codex. Version 1.1.8.

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

Provider processes have a 20-minute timeout per invocation, including each correction attempt.
Set `DK_PROVIDER_TIMEOUT_SECONDS` in the environment running the workflow to override it:

```sh
export DK_PROVIDER_TIMEOUT_SECONDS=1800  # 30 minutes
```

The value must be a positive integer number of seconds; invalid values fail before
starting the provider. This applies to Claude, Codex, and command providers used for
reviews, preparation, Decisions, and drafting. It is not a deadline for the entire workflow.

Iteration requests use distinct bounded runs. Interrupted work resumes its existing budget; a new request after a terminal run retains history and receives a new budget. Completion requires configured-reviewer evidence for the final tree and current scope/Decisions. Passing CI cannot substitute for missing review coverage; reports distinguish executed, reused and skipped reviewers.

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
