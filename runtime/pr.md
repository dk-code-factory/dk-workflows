# Handle a PR

When effective configuration uses `--upstream HOST/OWNER/REPO`, pass the same option to `pr_route.py`. Otherwise routing reads only user and canonical PR repository settings. The result includes `pr`, `repository`, `head`, `host`, `author`, `actor`, `ownership` (`mine` or `others`) and the selected `skill` identity. Identity failures return blocked JSON and exit 2.

Resolve the canonical PR URL from the user's input or checkout. Read effective settings through `dk-config`. Run `python3 PLUGIN_ROOT/scripts/pr_route.py --pr PR_URL`. It compares the author's numeric account id with the authenticated account and configured personal accounts on that host. Remotes, repository ownership, commit authorship and write access do not prove personal PR ownership. Additional accounts, including bots, count as yours only when explicitly configured. Missing or conflicting identity evidence blocks automatic routing.

Report the author, ownership and selected workflow. Invoke `dk-pr-fix` for your PR or `dk-pr-review` for another author's PR. An explicit request to review your PR or maintain another author's PR overrides automatic selection. It does not change the author or grant branch permissions. The fix workflow checks permission to write or rewrite separately. Never approve the authenticated account's own PR.

Pass original instructions from the user, repository and private settings, current Decisions and discussion, identity evidence and exact permitted actions to the selected skill. Keep the run id, claims, original results and budgets on resume. Recheck identity and head before remote actions. Saved account mappings select a workflow; they grant no permission to publish, approve or merge. Read-only and narrower user instructions override saved completion policies.
