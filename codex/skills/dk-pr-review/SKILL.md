---
name: dk-pr-review
description: Review another person's PR, independently verify candidate findings and post confirmed issues, with optional policy-authorized approval or merging.
---

Resolve PLUGIN_ROOT from this installation. Follow [the incoming-review recipe](../../../runtime/pr-review.md), [task state and handoffs](../../../docs/task-state.md) and effective `dk-config` settings. Use `dk-pr-reviewers-run` for discovery and a separate native verification pass before publication. Preserve original reviewer outputs, source-backed dispositions, head/target pins, native handles and publication receipts across resume. This workflow does not authorize modifying the author's code, description or commit series. Posting verified findings is the normal invoked workflow; honor analysis-only instructions and saved policy. Approval and merging require separate explicit invocation or saved policy authorization and fresh completion checks.
