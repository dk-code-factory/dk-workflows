---
name: dk-pr-feedback-reply
description: Reply to affected PR feedback and resolve fully answered inline threads when authorized.
---

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Read the original thread, comment or review, plus current code and validation. Reply only with a supported answer. Select the destination from the structured original source in the complete PR evidence, following [publication and recovery](../../../docs/pr/publication.md):

- For an actual inline review comment, use `reply-inline` inside its originating thread. Set `parent` to that thread's root comment database id, even when answering a later reply. Preserve the answered comment's own id and exact URL separately from this routing parent.
- For a review body or top-level comment without an inline parent, use `reply-comment`. Begin the exact body with `In Reply To: [descriptive source label](https://github.com/owner/repo/pull/7#pullrequestreview-789)`, then a blank line and the substantive response. Use the original review or comment URL, including its fragment. A file/line reference, quoted inline finding or code link embedded in a review body does not create an inline parent. Other inline comments attached to the same review do not change the review body's destination.

Use `scripts/publish.py` for both reply kinds. Include the structured `source` record described in the publication contract. Verify source identity and thread membership from the original evidence; if missing or ambiguous, refresh the evidence before choosing a destination. Save the source kind, original id and exact URL, routing parent/thread where applicable, actor, head, exact body and returned operation path. On resume, reuse that operation and reconcile pending writes before retrying; do not reconstruct the source or reformat the saved attribution.

Resolve only fully answered inline threads. Use the `resolve-thread` publisher with the thread node id and the database id of your verified posted reply. Top-level comments cannot be resolved. Return the original source, reply URL and resolution evidence.
