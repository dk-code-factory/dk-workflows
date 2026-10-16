# Explicit CI exceptions

Use this mode when the user explicitly identifies flaky tests or pipelines to accept,
including when no tracking issue exists. These are private settings consumed by the
CI waiter and both completion gates. They do not modify workflows, GitHub checks,
branch protection, or the automatic issue-backed flake registry.

All examples below are arguments to
`python3 PLUGIN_ROOT/runtime/dk-config/scripts/repo_rules.py --cwd REPO`.
For a PR, put `--pr PR_URL` before `ci-exceptions` to use its base repository even
from a fork checkout. Other configuration modes keep their existing lookup order.

```sh
ci-exceptions list
ci-exceptions list --scope user
ci-exceptions add --kind test --workflow .github/workflows/ci.yml \
  --job 'tests (linux)' --test 'tests/test_api.py::test_retry' \
  --signature 'AssertionError: response was late' --reason 'Known intermittent timeout'
ci-exceptions add --kind pipeline --workflow .github/workflows/nightly.yml \
  --reason 'Unreliable optional nightly pipeline' --scope user
ci-exceptions remove ci-ENTRY_ID
ci-exceptions reset --scope repo
```

For `test`, workflow path, exact job/matrix name and exact test identity are required.
`--signature` is optional: omitting it intentionally accepts any reported failure
signature for that exact test in that workflow/job. Matching is literal, with no
regex, glob, substring or case folding of workflow/job/test/signature. Reports must
still be complete; an excepted test cannot cover another test, build error or extra
failed step. Reuse identities from the existing evidence parser rather than guessing.

For `pipeline`, the exact `.github/workflows/FILE.yml` or `.yaml` path is required.
Omit `--job` to cover every job in that workflow, or give an exact job/matrix name
to restrict it. The selected job's non-test failures are covered too, and logs are
not required for that selected job. Pipeline exceptions currently apply to GitHub
Actions; other providers cannot be identified through an Actions workflow path.

`--reason` is required for each entry. Adding the same matcher again updates its
reason without duplicating the entry or changing its ID. `remove` targets an ID in
the selected scope; it does not remove an inherited entry in another scope.

The first present configuration wins: repository, then user. Lists do not merge.
Removing the last repository entry leaves an explicit empty list that disables
user defaults for this repository. `reset` removes that scope's file and restores
inheritance. To edit an inherited default, use `--scope user`. Malformed selected
configuration blocks CI decisions instead of silently falling back; `reset` can
remove corrupt JSON. Clones share normalized remote identity; separate repositories
and hosts remain separate. Local repositories without origin use canonical path.

Files live outside the checkout under `${XDG_DATA_HOME:-~/.local/share}/codex-repo-rules/`:
repository settings use the existing identity hash directory, user settings use
`user/ci-exceptions.json`. The versioned JSON contains `schema_version: 1`, `identity`
and `entries`; writes are locked, atomic and mode 0600. Effective `list` output
includes selected `source`, `file`, entries, and source fingerprints.

An entry accepts **terminal failures** only. Pending/cancelled checks, timeouts,
missing checks, wrong head, stale attempt, unsupported providers and unknown
failures outside the selected matcher remain blocking. Workflow/job provenance
must be verified even for an entire pipeline exception.

If explicit settings cover any failures and every other failure is also covered
(by settings or verified automatic flakes), the live policy returns
`accepted_exceptions`, keeps `raw_result: failure`, and records each matcher ID,
reason, scope, config file/fingerprint and exact run/attempt/job in `ignored_failures`.
Report **“CI red; accepted configured exceptions”** and identify the matched settings.
Automatic-only acceptance retains `known_flakes` and its open issue evidence.

Every decision reloads settings and checks that configuration stayed unchanged
during classification. Completion requires a fresh policy callback for either red
acceptance result: a saved label cannot bypass removed settings or changed CI.
