---
name: dk-pr
description: Handle a PR by identifying its author and your accounts, then fixing your PR or reviewing another person's PR under configured publication policies.
---

Resolve PLUGIN_ROOT from this installation. Follow [PR routing](../../../runtime/pr.md). Honor an explicit request to fix or review; otherwise run `scripts/pr_route.py --pr PR_URL`, report the selected workflow and invoke `dk-pr-fix` or `dk-pr-review` with the original instructions, identity evidence and existing run state. Unknown identity blocks automatic routing. Routing grants no additional publication, approval or merge authority.
