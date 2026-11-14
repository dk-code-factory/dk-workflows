---
name: dk-ci-flakes
description: Actively scan CI history and GitHub issues for flaky-test evidence, or inspect saved observations for a failed PR check.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Reuse the saved repository choice or initialize it through `dk-workflows:dk-repo-onboard` before adoption; preserve host instruction priority.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Resolve configuration for `dk-workflows:dk-ci-flakes` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

Follow [flake learning and discovery](../../../docs/ci-flakes.md). Ordinary fix/review CI learns from current-PR failures and targeted issue lookups automatically. Use this skill's active history scan when the user requests discovery or broader investigation; an ordinary PR failure alone does not request a repository-wide scan.

Run `python3 PACKAGE_ROOT/runtime/dk-ci/scripts/flaky_tests.py --repo HOST/OWNER/REPO --cwd REPO --compact discover`, using the PR's base repository. Discovery uses resolved `lookback_days`, `max_runs` and `max_issues`. Pass invocation-local options with `--options FILE` before the command, or override them deliberately with `--days`, `--max-runs` and `--max-issues`. Use `--targets FILE` for an explicitly narrowed scan. Resume with compact `status` pages and targeted `show` reads rather than repeating discovery or loading all observations. `status` is local saved evidence; `show`, `list` and `sync` refresh linked issues. `show --history` returns only the selected test's observations.

Read JSON on stdout. Compact output includes counts, a bounded page of test identities, `next_offset`, `registry_file` and available evidence references; use `--offset N --limit N` before the command for another page. Omit `--compact` for original verification/proof details. Discovery reports `missing` and `limits_reached`. Exit 0 does not establish that every failure is covered. Helper errors exit 2 with text on stderr.

Save the repository, head, job, attempt, test and proof that the linked issue is open. Missing logs, inaccessible or closed issues, unsupported reports and unknown failures block acceptance. A known flaky test cannot exempt an entire job.

Configured exceptions are separate from flake records. Read them with `python3 PACKAGE_ROOT/scripts/pr_config.py --cwd REPO --pr PR_URL ci-exceptions list`. Report each exception's id and reason from `entries`, with the returned `source` scope and `file`. Create issues or rerun jobs only when authorized.
