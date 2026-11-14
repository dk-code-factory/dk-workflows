# Prepare local commit scope

Use this procedure before direct commit preparation and before committing fixes
or entering selected commit cleanup during PR finalization. Message-only drafting
uses the same assessment but returns the proposed scope and message without
staging or committing. The native session decides relevance; helpers only capture
evidence. Commit authorization does not authorize pushing or rewriting published
history. Preserve the user's narrower scope and explicit exclusions.

Run `python3 PACKAGE_ROOT/scripts/commit_changes.py --checkout REPO` for a
read-only capture of HEAD, status, staged and unstaged patches, and untracked
paths across the whole repository, including when REPO is a subdirectory or
`diff.relative` is enabled. HEAD is null on an unborn branch; initial staged
changes are compared against the empty tree. The helper requires Git 2.45 or
newer. Content patches disable external diff/textconv; submodules use short
gitlink summaries regardless of `diff.submodule`, avoiding inline child diffs
that can re-enable those drivers. Capture also disables filesystem-monitor hooks,
configured clean/process filters (including initialized submodules), and lazy
fetching. It rejects unresolved index conflicts or changes observed during
capture and returns evidence on stdout without storing a persistent inventory.

`disabled_filter_drivers` lists bypassed configured drivers. The staged patch
shows the exact index content; the unstaged patch compares that content with the
worktree without running those external filters. Git's built-in conversions
(such as end-of-line normalization) still apply. Filtered files can therefore
show normalization-only differences, including large LFS content versus an
indexed pointer; these differences are candidates, not proof of an edit suitable
for committing. Assess the actual worktree content and filter attributes before
selecting hunks. Do not run filters merely to draft a message. Read relevant
untracked files and binary/submodule changes separately; paths and binary patches
alone do not establish relevance or correctness. For initialized submodule content,
run this same helper with that submodule as REPO; parent summaries retain the
candidate path, gitlink revisions and dirty marker without rendering its content.
Recheck live changes before staging or commit.

Assess every existing staged and unstaged hunk against the PR or requested
commit's scope and current code, including comments and functional edits. An
edit's origin or age alone must never exclude it. Include clearly relevant,
correct and suitable edits under the existing authorization to commit the fixes;
improve and verify them when necessary. Do not ask again solely because the edit
predates the task. Seek clarification for materially ambiguous scope while
continuing independent work. A staged edit is a candidate, not automatic consent
to include it in this commit.

Preserve unrelated or explicitly excluded edits, including other hunks in the
same file and existing staged changes. Never blanket-stage a dirty worktree or
commit the entire existing index without assessing it. Stage only the selected
hunks; isolate unrelated staged hunks without losing their original index and
worktree content or staging state, and restore that state after the commit.
If safe separation cannot be established, stop before mutating the index and
report the concrete ambiguity. Review the exact staged patch before committing
and check the remaining index and worktree against the exclusions afterward.
Run relevant validation for the selected change, including edits improved here.

Group each included edit with its corresponding logical change. Fold it into the
appropriate existing commit only when squashing or rewriting is authorized and
safe; otherwise make the appropriate focused commit. Return the included scope,
reasons for any material exclusions or ambiguity, validation and remaining edits
to the caller. Do not persist another selection inventory; use the existing task
result when a managed caller needs this evidence.

Commit preparation precedes [commit cleanup](commit-cleanup.md). Refresh the
reviewed head/tree and affected review coverage after including or improving
local edits. Cleanup still requires a clean worktree and exact reviewed-tree
preservation before rewriting. If preserved unrelated edits prevent that
prerequisite, report cleanup incomplete with those edits intact; never discard
or commit them just to satisfy cleanup. Custom finalizer selection and publication
permissions remain governed by the caller.
