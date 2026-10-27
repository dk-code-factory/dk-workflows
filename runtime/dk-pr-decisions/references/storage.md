# Decisions storage and recovery

Set `DK_PR_DECISIONS_HOME` to an absolute private directory accessible to every
run that should share agreements. This is the root of the complete Decisions
cache, not its parent. Records use
`<root>/v3/<host>/<base-owner>/<base-repo>/pr-<number>.json`. The PR URL supplies the
base identity, including when the checkout and head belong to a fork. Hosts,
repositories and PRs have independent records. The comments report, suggestion
evidence and publication receipts use the same store.

When the variable is unset, the root remains
`${XDG_DATA_HOME:-~/.local/share}/dk-pr-decisions`. An explicitly empty, relative,
inaccessible or repository-local store fails with a diagnostic. A corrupt record
also blocks use; restore a valid backup or reconcile it explicitly. There is no
silent fallback or merging with another store. Hosts must provide persistent
storage, sandbox access and a filesystem supporting process locks, atomic rename
and synchronization. No Code Factory configuration is changed by this plugin.

Every modifying command reads a monotonic revision before analysis and checks it
under a per-PR process lock at commit. GitHub and model calls run outside the lock.
Stale writers return `metadata_conflict` with their retained proposal and request;
reconcile explicitly without automatic merging or retries. PRs proceed independently. New directories are private (0700); records and locks use 0600.
Atomic replacement synchronizes the file and containing directory. Interruption
leaves the previous complete record or its committed replacement. Temporary files
from a killed process are not read as records. Do not delete lock files while
workflows may be active. `list` reads offline without creating directories, locks
or records, and makes no freshness claim.

Recovery automatically retains recorded choices, exact agreement evidence and
replacement chains across independent runs, even with disposable `XDG_DATA_HOME`
directories. Repeated recording does not overwrite the original evidence.
Mutable source edits or deletion do not silently remove an agreement: retained
evidence and current sources must be reconciled through fresh context and the
existing engineering preparation process. Material conflicts block dependent
work. Use explicit `replace` for a changed agreement; a superseded ID cannot be
reactivated. Legacy records retain the evidence they already contain; exact text
that was never cached cannot be recovered.

Recovery grants no publication authority, preparation readiness, review coverage
or iteration budget. Fetch live scope/head/discussions, refresh verified context
and run preparation before dependent work. Fresh review and iteration requests
keep their existing session and budget rules. Iteration records remain operational
history. `--local-decisions-file` is a temporary context import and never persists
its choices. Suggestions and unresolved questions remain unagreed until selected
through `add` or `replace`.

Saving and recovering cache records never mutates GitHub. Publication remains an
explicit separate action (`publish` or `scan --publish`). A pending publication
receipt survives interruption and blocks competing refreshes and writers. Use `publish --reconcile` to observe
live state before any retry; do not discard uncertain receipts or replay writes.

## Migration

Existing Decisions JSON formats and API v2 records remain readable. Fresh writes use
the versioned `v3/` namespace. A validated snapshot from the same configured root
may seed it without altering the legacy file. Pending publication remains blocking. With the
default root, the former `dk-pr-read-comments` cache is still migrated on refresh.
An explicit root never searches the default or former report store.

Before workflows start, stop writers and seed an **empty** persistent root by
copying the contents of the existing `dk-pr-decisions` root, retaining the
host/owner/repository hierarchy and private permissions. Include all companion
evidence files and publication bookkeeping. Configure the same absolute
`DK_PR_DECISIONS_HOME` for every independent run and verify recovery with `list`,
then refresh context and preparation. Keep a backup of the original store.

Do not overlay or merge divergent JSON snapshots. Select one authoritative seed,
reconcile uncertain publications against live GitHub, and replay other agreed
choices with `add` or `replace`, supplying the exact original instruction or source
evidence. Reconcile changed or missing mutable sources through preparation before
dependent work. This preserves replacement history and prevents an old snapshot
from reactivating superseded choices.
