---
name: dk-pr-review-architect
description: Run and record an architecture-only PR review on demand, including a fresh review after an earlier completed run.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Preserve host instruction priority.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PLUGIN_ROOT to this installed plugin root. Follow [manual architecture review](../../../docs/architecture-review.md#manual-architecture-review) and the shared architecture contract. Resolve this skill's [defaults](config.json) and [schema](config.schema.json) through [skill-owned configuration](../../../docs/skill-configuration.md). Use the incoming workflow's resolved architecture reviewer, scoring, selection policy, limits and restricted publication permissions; enable architecture for this invocation without changing saved settings.

A new explicit invocation requests fresh architecture assessment and comparison, even for an unchanged PR or previously completed review. Bind its original request source as described in the manual contract; an interruption resumes that same request and preserves completed stages. Use managed `prepare`/`finish`, original native evidence and mandatory checklists. Helpers construct assignments and retain results; use only the already running native session and supported native delegation. Unavailable required execution or evidence leaves the affected work incomplete.

Inspect and pin the complete PR, discussion, requirements, preferences and existing Decisions. Run assessment, the configured architecture comparison, selection and current implementation alignment. A manual request requires comparison rather than an automatic simple-change skip. Ask for an actual choice when selection remains pending. Publish the settled Architecture decision through the existing bounded editor and publication journal when permitted; independently verify a material mismatch before an authorized architecture-only `REQUEST_CHANGES` review.

Stop after the architecture stages. Do not invoke ordinary reviewers, incoming-review task/finalizer lists, code fixes, fork CI approval, PR approval or merging. Analysis-only instructions retain private results and suppress external writes. Report the architecture outcome, selected approach, saved result references and any decision/review publication links or blockers. Never represent architecture-only completion as complete code review or merge readiness.
