# Waiting and communication

Apply this policy to the caller, workflow coordinator and every nested worker.
Include this reference's retained runtime path in every self-contained handoff;
each recipient reads it before waiting or reporting progress. The coordinator
consolidates nested progress into updates useful to its caller and the user.

## User-facing updates

Follow the host's communication requirements and intervals. Report substantive
stage changes, useful results, failures, scope changes, required decisions and
completion promptly. State what changed and its consequence or next action;
use only verified evidence. A newly reported failure or decision must not wait
for the next routine update interval or be hidden behind an unchanged-wait message.

During an unchanged wait, remain quiet when the host permits it. When the host
requires an update, use one brief sentence naming the pending stage, such as
“Engineering preparation is pending.” Keep that description stable while the
state is unchanged. Do not repeat the remaining workflow, paraphrase the same
status into a new progress narrative, or imply progress without evidence. Do not
repeat elapsed time, speculative completion estimates or unchanged CI output.
Answer a user status request from the latest verified state, noting any relevant
uncertainty; collect fresh evidence only when the request needs it.

User-update timing is independent of worker polling, GitHub reads, provider calls
and CI observation. Produce required updates from the latest verified state;
an update interval or wait timeout never justifies extra evidence queries or
model calls. A tool yield alone is not a stage change or a result.

## Worker communication and waits

While an agent works, do independent work or block on `wait_agent`. Prefer a
300000 ms timeout when tool limits and higher-priority instructions permit it.
If runtime or host update requirements limit waits to 60000 ms, use that limit,
send any required brief user update, then quietly wait again. Messages and
completion notifications wake the wait early. A timeout means no message arrived;
it is not evidence that the worker stalled. Do not call `list_agents`, request
status, inspect files or Git state, poll GitHub, or interrupt/restart a worker
merely because a wait expired. Check only for a concrete reason, such as a missed
agreed milestone, reported failure, conflicting evidence or user status request.
Use `wait_agent` for agent notifications, not `sleep` or short polling loops.

Send parent–child messages for completion, blockers, required decisions, failures
or material progress that changes the parent's next action. Do not send periodic
heartbeats, “still working” messages, repeated unchanged CI output, or routine
acknowledgements. Consolidate nested changes instead of relaying every update.
Completion is delivered automatically; do not duplicate a final result with a
separate `send_message`. Send necessary corrections or new instructions promptly.
A user update or timeout must not start a round of worker status messages.

## CI observation

Keep the single-process CI waiter and its observation cadence in
[review and CI](iteration-review-ci.md). Reuse its verified state for user updates;
its progress output does not require a user message for every observation.
Do not start a second waiter or query checks to supply commentary. A terminal
result or newly reported failure is substantive and must be reported promptly.

## Behavioral acceptance examples

Evaluate these scenarios against the policy; example sentences are illustrative,
not exact output requirements. No extra model calls are needed to evaluate or
compose individual updates.

| Scenario | Expected behavior | Reject |
| --- | --- | --- |
| Preparation runs across several host communication intervals without a result. | Meet each required interval with one brief sentence naming preparation as pending; remain quiet if permitted. | Repeated descriptions of reconciliation, publication and completion steps, or invented progress. |
| Several `wait_agent` calls time out with no message. | Continue waiting; send only a host-required brief pending-stage update. | Status requests, file/Git inspection, GitHub reads or restarts caused by the timeouts. |
| A nested worker sends routine progress, then a result that enables the next stage. | Consolidate the useful result into one stage-change update; routine nested messages do not each become user updates. | Forwarding every nested message or duplicating automatic completion notifications. |
| The CI waiter reports the same pending checks at successive observations. | Keep that waiter running; use its latest verified state for any required brief CI-pending update. | Repeating check output, adding observations or launching another waiter for commentary. |
| A worker reports a new failure during an otherwise unchanged wait. | Promptly name the failure and its consequence or needed decision, using the reported evidence. | Another generic pending message, delayed reporting until the next routine interval, or unsupported claims of recovery. |

Across all scenarios, preserve host intervals, consolidate nested updates, report
material changes promptly, and perform no extra evidence queries, provider calls
or CI observations solely to produce user-facing messages.
