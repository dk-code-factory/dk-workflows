# Reviewed inline payload

Save one JSON object outside the repository, using the verified PR node ID and
the full reviewed head SHA. The helper owns the mutation document and retains
the complete approved input. Do not send this file directly to GitHub.

```json
{
  "query": "mutation($input:AddPullRequestReviewInput!){addPullRequestReview(input:$input){pullRequestReview{id fullDatabaseId url state body commit{oid}}}}",
  "variables": {
    "input": {
      "pullRequestId": "PR_NODE_ID",
      "commitOID": "REVIEWED_HEAD_SHA",
      "event": "COMMENT",
      "threads": [{"path": "src/example.py", "line": 42, "side": "RIGHT", "body": "Finding and consequence."}]
    }
  }
}
```

Use the user's requested event: COMMENT, APPROVE or REQUEST_CHANGES. Each thread
needs its actual repository-relative path, diff line, side and reviewed body.
For a range, add `startLine` and `startSide` on the same side. Use RIGHT for the
new version and LEFT for the old version. Keep one thread per distinct finding.
Omit the review-level `body` unless a summary was explicitly requested; supply
that reviewed text and `--allow-summary` when requested.
