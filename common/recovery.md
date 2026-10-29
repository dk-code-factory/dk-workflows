# Workflow recovery diagnostics

Load this document only when a typed diagnostic points here. Preserve original evidence and use the returned exact action. Reconciliation reads remote state; it never repeats a mutation.

Use the direct lifecycle interface below only to inspect or recover an existing
invocation. Do not run a second entry during recovery. New invocations use
`begin`/`end` from the short lifecycle procedure.

# Workflow lifecycle

Reuse `DK_CLI_REFERENCE` when supplied by the parent. Otherwise, after entry and
runtime retention, create one CLI reference for this root workflow with
`python3 PLUGIN_ROOT/scripts/dk.py --host HOST --parent UPDATE_TOKEN reference`.
Retain its `reference` path and pass it as `DK_CLI_REFERENCE` on helper processes
and in child handoffs. The reference contains the command map; a helper help query
with that environment retains its text once and returns a reference section.
Read only the needed section and reuse it in this agent's context. Version or
helper changes invalidate it; create a fresh reference instead of using stale
help. If reference creation is unavailable, inspect the current helper directly.
The reference never supplies workflow results, review evidence or authorization.

At entry, run `python3 PLUGIN_ROOT/scripts/update.py entry --host HOST`, replacing HOST with the calling platform. If a DK workflow or another skill supplies a parent token, pass `--parent PARENT_TOKEN`. Retain the returned invocation `token` as UPDATE_TOKEN. Set `DK_HOST=HOST` and `DK_UPDATE_PARENT=UPDATE_TOKEN` on **every** helper or provider process launched during this invocation. Shell exports do not persist between independent tool calls; repeat the environment prefix or use an explicit environment argument. Pass UPDATE_TOKEN as the parent for nested native skill calls, including other plugins.

After entry, require `runtime_root` and `completion_script` in its receipt. Set
PLUGIN_ROOT to that retained root for all subsequent helpers and linked resources,
and use the returned completion script with the token. Pass the retained root
alongside the parent token in child handoffs. A missing retained root blocks runtime
use; an offline release check still permits work when retention succeeded.

The script owns update policy. Follow `action`: show `message` once for `notify`; continue for `continue`. An offline or unavailable check never blocks the requested work. Do not install an update yourself based on release information.

After work (including failure), run `python3 PLUGIN_ROOT/scripts/update.py complete --host HOST --token UPDATE_TOKEN`. Follow its action and report failed updates or reload requirements accurately. If entry returned no token, there is nothing to complete. Interrupted sessions may complete retained tokens explicitly; never invent a parent token. Updates remain deferred while any nested invocation is active. Do not finish a parent while its children are still running.

PLUGIN_ROOT is the directory containing this plugin's host manifests. Resolve it from the loaded skill location, or use `${CLAUDE_PLUGIN_ROOT}` in Claude. Keep the reviewed working directory when invoking lifecycle commands so the script can reject state locations inside that repository.

When a DK host executor supplies `DK_UPDATE_MANAGED_RECEIPT` for this plugin identity, that executor has already called this plugin's entry script outside the model's read-only sandbox and will call completion in a finalization step. Use that receipt, its retained runtime root and token; do not enter or complete it twice. Pass its token as parent to native child skills. For a different plugin identity, perform the normal entry and completion calls. A direct standalone invocation always uses its own script and does not require DK.


Inspect retained lifecycle records with `python3 PLUGIN_ROOT/scripts/update.py inspect --host HOST`.
This reads all incomplete records across plugins and hosts, their owner metadata,
creation times and pending updates. Add `--token TOKEN` to inspect one completed
or incomplete record. Older records may lack ownership or timestamps; inspection
preserves them unchanged. A recorded parent PID identifies the launcher and does
not prove current liveness. An old record is not evidence that its work stopped.

After verifying that an interrupted invocation and all its nested work have
stopped, use the normal `complete --host HOST --token TOKEN` command. Optionally
add `--reason TEXT` to retain the recovery reason with its completion timestamp.
Completion remains idempotent and updates stay deferred while any other work is
active. Never expire tokens automatically or delete lifecycle records to unblock
an update.


Prefer the router for listed helpers: `python3 PLUGIN_ROOT/scripts/dk.py --host HOST --parent UPDATE_TOKEN
--cwd REPO --pr PR_URL HELPER ...` supplies the workflow environment and routes
to the packaged script. Keep that command on one shell line or use explicit
shell continuations. Run `dk.py commands` for helper names and context options;
use `--dry-run` before the helper name to inspect routing. Common context flags
precede the helper; native helper arguments follow it. Existing direct helpers
remain supported. The router does not enter or complete the lifecycle again.


Runtime copies live in private content-addressed storage under the lifecycle
state directory's `runtimes/` folder. They preserve package resources, internal
links and executable permissions, reject links outside the package and verify
content hashes before reuse. Copies remain available for interrupted work and
historical review evidence; completion does not delete them. Never switch an
active invocation back to the mutable installed cache or a newly installed version.

## Publication conditions

| Code | Required response |
| --- | --- |
| publication_uncertain | Run the exact returned read-only reconciliation argv and retain the operation, response and recovery refs. A missing or ambiguous match is a block. |
| publication_not_ready | Refresh the changed governing evidence and prepare/review a fresh operation. Never refresh a lease on the old operation. |
| metadata_conflict | Inspect the retained proposal and current revision. Reconcile a pending intent first, then explicitly reapply the intended choice; do not merge proposals automatically. |
| artifact_storage / invocation_storage | Restore writable private storage. The receipt distinguishes whether dispatch began; inspect pending operation records before any new mutation. |
| local_changes / rebase_conflicts | Perform the returned engineering assessment or conflict resolution against scope and Decisions. Preserve original, target and candidate recovery refs. |
| reply_stop | Preserve the independent validator's final rejection and report it. Do not reset rejection budgets. |
| reply_reconcile | Use the existing controller's exact reconciliation command, including its original PR selection, record and agent ID. |
| model_validation_failed | Inspect the private exchange in `action.artifacts`, including both rejected responses and exact validation fields. Correct governing evidence before preparing again in the existing run; unchanged or interrupted exchanges cannot reset the correction budget. See [preparation recovery](../runtime/dk-pr-decisions/references/preparation.md#rejected-model-output-and-recovery). |

PR edits retain their original head/title/body and reviewed field hashes. Review
posting retains the authenticated author, pinned head, complete pre-submit reviews
and all expected inline locations. Reruns require the new attempt to be observed;
waiting on the previous result cannot confirm submission. Review-request attempts
are retained by final head and reviewer, including rejected/uncertain attempts.

A reconciliation that cannot prove application never authorizes a second mutation.
Read the retained artifacts and obtain fresh preparation only after the earlier
operation has been accounted for. Repeated publication of a verified operation
still checks current head, scope, readiness and live receipts.

For review-specific response or matching diagnostics, inspect the retained IDs
and [publication details](../runtime/dk-pr-post-review/references/publication.md).

Standalone split recovery uses its retained Git state and original-head ref.
Regroup recovery additionally requires the original cleanup precheck and unchanged
target tip. Completion still requires fresh final-head preparation, engineering
verification, configured-reviewer coverage, series assessment and current CI policy.
