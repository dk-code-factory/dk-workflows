# Lifecycle guidance evidence

`docs/task-state.md` is the managed lifecycle authority. Entry skills and recipes
refer to it for preparation, claims, recording, checklists, reuse, recovery and
budgets. Recipes retain stage decisions, applicability, consumed evidence and
acceptance. Existing domain contracts retain native execution, publication,
authorization and CI requirements.

The implementation changes guidance and offline fixtures. Runtime capture,
identity, storage and execution algorithms are unchanged. Complete explicit
resource maps still include indirect governing dependencies before claims;
deferred native prose reads do not change those maps. Helpers do not infer
missing dependencies from Markdown links.

## Instruction consumption

The comparison pins the baseline to
`f431ca6d0534761c789cfd9544e72bb78e2ee416`. It reads the same entry skill, recipe
and lifecycle sources for each route. Initial execution covers the complete
selected path. Fresh-session recovery covers a pending completion check: entry,
recipe introduction, review/verification stage and relevant lifecycle sections,
including the new authority section. Unchanged guidance already in the active
context requires no repeated prose read in either arm. Both arms retain full
resource capture. The script returns source hashes for reproduction.

| Route | Initial baseline bytes | Initial candidate bytes | Fresh-session baseline bytes | Fresh-session candidate bytes |
| --- | ---: | ---: | ---: | ---: |
| Fix | 50,975 | 49,481 | 38,494 | 37,554 |
| Incoming review | 48,772 | 48,024 | 33,701 | 33,697 |

The fix path consumes 2,434 fewer bytes across these two modeled sessions;
incoming review consumes 752 fewer. The incoming recovery difference is only
four bytes. These are core-guidance diagnostics, not observed native reads or
API-dollar savings. Unchanged supporting contracts, host/tool schemas, reasoning,
response inventories and account billing are excluded. No claim is made about
complete default-workflow cost or finding quality; that measurement remains
separate. Changes to the prose or declared read scope require a fresh comparison.

Reproduce against the committed candidate, or omit `--candidate` for the worktree:

```sh
PYTHONPATH=runtime:tests:tests/fixtures python3 tests/test_lifecycle_guidance.py \
  --baseline f431ca6d0534761c789cfd9544e72bb78e2ee416 --candidate CANDIDATE_SHA
```

This offline script requires Git 2.45 or newer, both pinned Git objects and
installed test dependencies. It performs no model or GitHub calls and persists
no measurement state. Missing objects fail the command without lazy fetching; they do not supply
synthetic results.

## Behavior and freshness

`tests/test_lifecycle_guidance.py` exercises both routes using the existing
managed interface and copied original guidance resources. It proves that declared
indirect resources are captured before semantic reads, reading order preserves
capture, unchanged fresh-process resume reuses original findings/results without
writes, pending checks preserve their original rounds, stopped-claim recovery
retains completed siblings, indirect resource changes invalidate affected work
only, and missing deferred resources block claims and in-flight recording.
These stand-ins establish protocol behavior, not native instruction adherence.

Existing offline coverage supplies the remaining workflow boundaries:

- `tests/test_instruction_sources.py` checks selected-source changes, relevant
  invalidation, exclusions and unchanged fresh-process reuse.
- `tests/test_finalizers.py` checks live selection/order, explicit parallel
  execution, partial failure, omitted/empty lists and noncacheable CI.
- `tests/test_next_actions.py` checks partial siblings, pending completion and
  original uncertain-publication references across runs without resending.
- `tests/test_recipe_integration.py` drives complete fix/merge, repair, rerun and
  interruption scenarios through real Git and offline GitHub stand-ins.
- `tests/test_execution_boundary.py` enforces that production helpers and skill
  guidance do not start model sessions.

No claim, result, allowance, journal, fingerprint algorithm or durable format is
reset or migrated. Changed governing instruction bytes still refresh affected
current work; completed historical alternatives keep their existing history
boundary. Current authorization, publication and CI checks remain live.
