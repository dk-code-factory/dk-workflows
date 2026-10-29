# Independent architecture exceptions

Read [architecture delivery](../dk-architect/procedure.md). Workers must stop
before breaching delivered rules. Create a request with `architecture request
--packet FILE --file REQUEST.json`; REQUEST contains rule_id, deviation, paths,
evidence (source references), and rationale. Evaluate as a separate role from
worker, architect and reviewer with `architecture enforce --request FILE`.

Approve automatically only if the current rule enables auto_approve and evidence
establishes approval_when unambiguously. Disabled, unmet or ambiguous conditions
produce a retained rejection requiring explicit user approval. A reviewer or
worker's assertion alone cannot establish a condition. Never edit a rule, enable
autoapproval or broaden approval_when to grant an exception. Only the user may
authorize those policy changes through dk-config. Preserve exact user approval
and its source with --user-file when explicitly granted; elapsed time is no answer.

Approval covers one task/PR, rule revision, deviation, paths, scope/Decisions and
code evidence, with rationale. Revalidate after governing evidence changes,
including proposed code becoming committed code. Do not silently carry an
exception to another task, revision, path or changed scope. Pin both approvals and
rejections; neither grants publication authority. Return approval reference and
fingerprint to the worker, coordinator and architecture reviewer.
