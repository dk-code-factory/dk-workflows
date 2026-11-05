# Commit cleanup

The inspection helper requires Git 2.45 or newer. Its explicit `--no-lazy-fetch`
option makes older Git reject the command before repository inspection, rather
than ignore an unsupported environment safeguard.

Use `python3 PACKAGE_ROOT/scripts/commit_series.py --checkout REPO --target TARGET
--head HEAD` for pinned read-only inspection. It returns resolved commit pins,
a unique merge base, reviewed tree and ordered commit metadata, parents, complete
messages and changed paths as JSON. It never fetches, creates recovery refs or
rewrites. Missing or ambiguous history blocks rewriting. Read every relevant
patch and the final diff; metadata cannot replace content assessment.

For cleanup, first resolve or verify [the effective commit policy](../commit-policy.md), including on direct invocation. Inspect every commit from merge base to head against the final reviewed diff. Before rewriting, save original head and tree, target tip, remote branch lease, ownership and a recovery ref. Require a clean worktree and safe ownership by the same author. Stop if authorship or branch ownership is ambiguous. Apply explicit grouping instructions within those safeguards. Without a configured policy, preserve logical boundaries, split mixed commits, fold fixup commits into the commits they repair, and reorder only when useful. One logical commit is valid. Without explicit grouping instructions, do not rewrite just to change the count.

Draft messages through `dk-commit-message` with the policy's message instructions, required repository templates and current user constraints. Explicit policies replace built-in stylistic defaults. Without explicit message instructions, use recent human conventions and limit each line to 80 characters. Review boundaries and messages together. Stop at the first unchanged pass or after five total passes. Report concerns left after the limit. Run relevant tests and verify that the final tree exactly matches the reviewed tree.

Before pushing, recheck target tip, published source head, remote lease and
governing context. Changed files require content review. History-only changes
require fresh CI and publication checks for the new head, plus refreshed tasks
that use history. Use `scripts/publish.py` for authorized pushes with exact leases.
A changed remote lease or target tip blocks publication until reassessed.

Return an assessment with outcome `no_op` or `published`, PR identity, reviewed
and final heads and trees, target tip, commit count, boundary reasons, validation,
recovery refs and publication receipts. A no-op must explain the whole series;
an unchanged SHA is insufficient. Return this result to the caller so it can
check coverage and CI within its saved budget. Do not claim the caller's
iteration is complete.
