# Shared PR evidence

Two or more bundled feedback, Decisions or read-comments consumers sharing an analysis boundary use one complete handoff collected by the coordinator. Pass its compact receipt to each consumer and use its analysis view instead of separate initial collections. Consumers at different boundaries need fresh evidence. Standalone consumers without a handoff collect complete stable snapshots with `fetch_unresolved_review_threads.py` and `fetch_pr_conversation.py` under `runtime/dk-pr-address-reviews/scripts/`.

## Collect

Run `runtime/dk-pr-address-reviews/scripts/pr_evidence.py collect --pr PR_URL`. The canonical URL includes the upstream hostname. Optional `--threads-file FILE` and `--conversation-file FILE` accept full snapshot JSON or artifact receipts. Pass `--instructions-file FILE` for each original governing instruction file; the handoff preserves its exact bytes, path and hash. Preserve direct user instructions separately and reread them when checking freshness.

Snapshots must be complete and match each other, the requested canonical PR and the assigned head. Missing, corrupt, incomplete or mismatched supplied evidence requires coordinator recollection and reassessment of affected consumers; never accept partial evidence or silently collect separate initial snapshots at the shared boundary.

Both fetch helpers support `--artifact` for private storage and compact receipts; otherwise they return full JSON. The conversation helper’s `--snapshot-file` accepts either full thread JSON or a thread receipt. Receipts retain PR/repository, head, completeness, counts, source references, byte count, SHA-256 and the original absolute artifact path. Full thread evidence includes resolved threads, every paginated comment and the PR description containing Decisions.

## Analyze

Use `pr_evidence.py view --handoff PATH --sha256 HASH --pr PR_URL --head HEAD --role feedback` or `--role decisions`; read-comments uses the feedback view. Retain the expected hash from the compact receipt separately. The helper verifies it before loading manifest fields or instruction references.

The view contains complete threads/discussion, original PR metadata and instruction references; feedback also receives unresolved thread references. A selected comment/review retains its selection alongside the full discussion. Read required original evidence from its artifact paths. Preserve work items, source links, settled/deferred choices, exact instructions and dispositions. Unresolved comments are not automatically actionable, and suggestions are not settled decisions.

## Verify before acceptance

Each consumer independently runs `pr_evidence.py verify --handoff PATH --sha256 HASH --pr PR_URL --head HEAD` immediately before accepting or recording its result, and rereads current direct instructions. Verification repeats complete paginated, consecutive stable reads and compares full sources, including description, resolved discussion and original instruction files.

Integrity and an unchanged head do not establish freshness: discussion, Decisions and instructions can change without commits. Drift requires recollection and reassessment; preserve prior dispositions only when still source-backed. Repeat verification at every later required boundary and after intervening changes. A saved success receipt grants no freshness lease.

Evidence and verification grant no publication authority. Publishers perform fresh validation/reconciliation; completion recollects live scope, instructions, feedback, Decisions, head and target. Domain consumers verify immediately before workflow `record`; task-state bookkeeping remains domain-independent.
