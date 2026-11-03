# Partition a PR into drafts

This is a separate path from standalone commit splitting and tree-preserving
regrouping. One split worker owns the assessment, semantic unit commits, local
candidates, verification, descriptions and publication through reconciliation.
The coordinator supplies the exact PR URL, checkout, pinned head/tree and merge
base, default-branch tip, issue/description, verified Decisions, complete feedback,
private rules, dependency evidence, active run and preparation (both IDs),
architecture packet, retained runtime/lifecycle and waiting policy. Preserve other
workers' edits. Use isolated worktrees; never reset the caller's worktree.

## Assessment and candidates

Use `dk.py --invocation INVOCATION --cwd REPO --pr PR_URL partition-prs check
--evidence-file EVIDENCE.json --preparation-file PREPARATION.json`. The evidence manifest maps `scope`, `decisions`,
`rules` and `dependencies` to absolute files. Include the issue, PR description,
settled Decisions and engineering boundaries, all applicable guidance and private
rules, and pinned prerequisite inputs. Keep source evidence complete and current.
The helper requires fetched objects; fetch the PR target, default branch and
pinned dependency heads before checking. A missing object blocks the check.

At `added_lines >= added_lines_threshold`, assess enabled behaviors in order:

1. Compare every final-diff change to the issue, description and settled Decisions.
   Evacuate unrelated work first. Do not broaden the description to make it fit.
2. Recount the retained changes, including copied prerequisites. If still at or
   above threshold, split related work into coherent independently usable pieces,
   aiming below the threshold. Keep the primary deliverable in `original`.

Scope is a semantic judgment supported by evidence, not a path or line-count
heuristic. Split mixed commits into semantic units; preserve authorship (including
original author dates) and coherent commit boundaries. An indivisible change may
remain oversized with a concrete reason. Material scope ambiguity, unsafe
ownership, an architectural choice or absence of a primary in-scope deliverable
requires a human decision before publication. Do not invent a classification.
Disabled behaviors receive no assessment or action. Unrelated work retained by a
disabled or declined evacuation cannot be moved by related splitting, including
as a copied prerequisite.

The private plan uses this shape (IDs and commit SHAs are pinned, not branch names):

```json
{
  "units": [
    {"id": "primary", "commit": "FULL_SHA", "scope": "related", "reason": "Evidence for classification"},
    {"id": "extra", "commit": "FULL_SHA", "scope": "unrelated", "reason": "Evidence for classification"}
  ],
  "pieces": [
    {"id": "original", "units": ["primary"], "depends_on": [],
     "title": "Primary deliverable", "body": "Scope and Decisions\n\n{{dependencies}}\n\n{{related}}",
     "reason": "Primary issue outcome", "checks": [["python3", "scripts/test.py"]],
     "oversized_reason": "Required only if this candidate remains oversized"},
    {"id": "extra", "units": ["extra"], "depends_on": [],
     "title": "Separate improvement", "body": "Accurate separate scope\n\n{{dependencies}}\n\n{{related}}",
     "reason": "Independent purpose", "checks": [], "verification_reason": "Explain applicable local verification"}
  ],
  "assessments": {
    "evacuate_unrelated": {"reason": "Scope comparison and disposition"},
    "split_related": {"reason": "Retained count, coherent boundaries or why no safe smaller partition exists"}
  }
}
```

Commits by a foreign author must remain intact pinned units; splitting or
remapping them requires an explicit ownership decision before a revised plan.

Units, in their declared order, must recompose to the entire original tree from
its pinned merge base. Every unit belongs to exactly one piece. Unit commits may
be existing coherent commits or freshly split mixed commits. The worker must
exclude canceled intermediate churn and unintended duplicated work. `original`
must contain an in-scope deliverable. Each `depends_on` names the **minimum**
required pieces; no cycles, gratuitous prerequisites or implicit scope moves.
Use optional `prerequisite_units: {"source_piece": ["needed_unit", "..."]}` on a
piece to select only the minimum prerequisite units from a multi-commit source;
omitting a selector copies that source piece's own units in order. Selectors may
name declared direct or transitive sources; candidates verify independently.
With evacuation disabled, unassessed units may use `scope: "retained"` only in
original, and related drafts cannot copy them as prerequisites.

Copied prerequisites are additional provenance-bearing copies, not additional
owners of a unit. Retained unrelated changes belong to `original`.

Use configured commit and PR drafting providers and existing discovery caches.
Keep automatic issue-closing references only on `original`. Give each piece an
accurate purpose and applicable Decisions; the worker reviews the cross-links and
generated Dependencies section along with the full descriptions. Do not turn all
original Decisions into obligations for an unrelated draft.

Run `partition-prs prepare --evidence-file EVIDENCE.json --plan-file PLAN.json
--remote HEAD_REMOTE --preparation-file PREPARATION.json`, then `verify --state
STATE`. The helper preserves recovery refs, verifies ownership and an unchanged
lease, replays all unique units from the original merge base, constructs every
candidate from the pinned **base repository default-branch tip**, and runs each
piece's verification argv in its isolated checkout. It retains test logs and
checks their fingerprints. Verification failures retain recovery artifacts and
conflicted worktrees. All candidates must verify before any remote mutation.
For a no-op assessment use one `original` piece containing all units and record
specific indivisibility/boundary reasons; it is cached without a remote rewrite.

## Prerequisites and frozen prefixes

Transitive prerequisites precede dependents. Copy each prerequisite once with
`cherry-pick -x`, preserving its author, then add the piece's own commits. Every
candidate is counted against the pinned default tip, including prerequisite
additions. The helper renders exactly this section whenever prerequisites exist:

```markdown
## Dependencies

| Source PR | Pinned source head | Cherry-picked commits, in order |
| --- | --- | --- |
| <PR URL> | `<full SHA>` | `<source SHA>` → `<copied SHA>`; … |
```

The rows follow actual prefix order and cite pinned candidate source heads, not
moving branch tips. Verify the table against the leading commits, source ancestry,
`-x` trailers and original authorship. Subsequent commit regrouping must preserve
that validated prefix byte-for-byte and operate only on the PR's own suffix.
Fetch the declared pinned source heads before standalone split/regroup preparation.
Later prerequisite synchronization is outside this workflow.

## Authorization and publication

`automatic` prepares, verifies and publishes its enabled action under iteration
authorization. `manual` prepares the concrete plan **and verified local candidates**
before requesting approval. The returned `action_ids` bind each independent
behavior to the exact tree/base/scope/rules/settings/dependencies and plan. Supply
`--decisions-file` to publish with, for example:

```json
{"evacuate_unrelated": {"action_id": "RETURNED_ACTION_ID", "decision": "approved"}}
```

The alternative is `declined`. Retain the decision in the journal and cache. A
missing answer never grants approval. Declining keeps affected units in original;
the helper rebuilds and verifies the selected candidates. If another action would
move/copy declined units, stop and revise the concrete plan with the human. A changed selection returns `selection_ready` and its new `candidate_hash`. Review
the selected candidates and descriptions after any decline, then call publish again
with that hash and the completed review tokens before publication.
An all-declined plan remains local. Reusing unchanged inputs never asks again.

Complete the existing split self-review loop for the entire selected series and
the existing `dk-pr-description` review for all resulting descriptions. Then use
`publish --state STATE --review-state SPLIT_TOKEN --description-review-state
DESCRIPTION_TOKEN --candidate-hash VERIFIED_CANDIDATE_HASH` (plus decisions when needed). The completed tokens bind the
retained candidate set; changed plans require new reviews. All normal preparation,
architecture delivery/enforcer, scope and local verification requirements remain.
This path precedes configured reviewer allocation and does not grant review/CI or
completion coverage. It never resets or consumes the coordinator's review budget.

The helper journals each branch creation, draft creation, description/base edit
and exact-lease source rewrite through the shared publication guard. It verifies
all new branches/drafts and their final descriptions before removing work from the
original. Every PR, including original, targets the default branch. It renders
source links as prerequisite drafts become available, then cross-links all pieces.
Original recovery refs and unit/candidate refs remain available after publication.
A remote race blocks publication; never refresh the lease to override it.

On an uncertain write use `reconcile --state STATE`. This performs live reads and
never repeats a remote operation. It reuses exact verified branches and PRs; an
absent or ambiguous result remains blocked. A verified operation can resume through
`publish` to continue the remaining prepared sequence. Never delete state, recreate
a PR, or repush an uncertain branch as recovery. Restore missing evidence/access
and reconcile. A changed scope, settings, rule, dependency or default tip blocks
further publication while retaining receipts for operations already dispatched.

After publication return all PR URLs, selected boundaries, local test results,
recovery paths, exact retained head and state to the coordinator. Refresh original
scope, verified Decisions, **both** feedback snapshots and engineering preparation;
Use a clean linked worktree pinned to the returned retained head for subsequent
work; the old full-scope caller checkout is not the next candidate.
Register the current partition assessment using `run-state partition-assessment
--head SHA --state STATE` when applicable, and check `partition-gate --head SHA`.
Reassess a retained oversized scope against the refreshed preparation; preserve
prior explicit declines. Continue iteration and fresh CI/review coverage only on
the retained original. Drafts receive relevant local tests, no automatic iteration.
