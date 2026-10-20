# Workflow lifecycle

At entry, run `python3 PLUGIN_ROOT/scripts/update.py entry --host HOST`, replacing HOST with the calling platform. If a DK workflow or another skill supplies a parent token, pass `--parent PARENT_TOKEN`. Retain the returned invocation `token` as UPDATE_TOKEN. Set `DK_HOST=HOST` and `DK_UPDATE_PARENT=UPDATE_TOKEN` on **every** helper or provider process launched during this invocation. Shell exports do not persist between independent tool calls; repeat the environment prefix or use an explicit environment argument. Pass UPDATE_TOKEN as the parent for nested native skill calls, including other plugins.

The script owns update policy. Follow `action`: show `message` once for `notify`; continue for `continue`. An offline or unavailable check never blocks the requested work. Do not install an update yourself based on release information.

After work (including failure), run `python3 PLUGIN_ROOT/scripts/update.py complete --host HOST --token UPDATE_TOKEN`. Follow its action and report failed updates or reload requirements accurately. If entry returned no token, there is nothing to complete. Interrupted sessions may complete retained tokens explicitly; never invent a parent token. Updates remain deferred while any nested invocation is active. Do not finish a parent while its children are still running.

PLUGIN_ROOT is the directory containing this plugin's host manifests. Resolve it from the loaded skill location, or use `${CLAUDE_PLUGIN_ROOT}` in Claude. Keep the reviewed working directory when invoking lifecycle commands so the script can reject state locations inside that repository.

When a DK host executor supplies `DK_UPDATE_MANAGED_RECEIPT` for this plugin identity, that executor has already called this plugin's entry script outside the model's read-only sandbox and will call completion in a finalization step. Use that receipt and token; do not enter or complete it twice. Pass its token as parent to native child skills. For a different plugin identity, perform the normal entry and completion calls. A direct standalone invocation always uses its own script and does not require DK.


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
