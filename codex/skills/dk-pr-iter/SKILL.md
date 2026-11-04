---
name: dk-pr-iter
description: Identify the PR author and your accounts, then fix your PR or review another person's PR under saved publication policies.
---

Set PLUGIN_ROOT to this plugin's root directory. Follow [PR routing](../../../runtime/pr.md). Follow an explicit request to fix or review. Otherwise run `scripts/pr_route.py --pr PR_URL`, report the selected workflow and invoke `dk-pr-fix` or `dk-pr-review`. Pass original instructions, identity evidence and saved run state. Unknown identity blocks automatic routing. Routing grants no extra permission to publish, approve or merge.
