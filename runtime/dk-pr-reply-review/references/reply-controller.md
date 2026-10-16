# Script-controlled reply passes

Use `scripts/reply_passes.py` to control both passes. The reply agent follows its `action` and prepares request mappings and text; it does not select the next phase or publish directly. The controller uses the complete shared GitHub fetchers, retains all resolved conversations, and publishes through the existing `reply_batch.py` guard. Existing guard commands and batch schema are unchanged.

When called by `dk-pr-iter`, use the existing run record and canonical reply agent ID. For standalone reply work, initialize a JSON record outside the repository containing `{"pr": NUMBER}`. The controller stores history under `reply_workflow` in this record and saves immutable evidence/batch/verdict/journal artifacts beside it in `RECORD_STEM-replies/`. Use the same record through completion, interruptions, and head changes.

## Get the next instruction

```sh
python3 PLUGIN_ROOT/runtime/dk-pr-reply-review/scripts/reply_passes.py \
  --record RUN_RECORD.json --pr PR_URL --cwd REPO --agent REPLY_AGENT_ID next
```

`next` fetches both complete live snapshots and checks saved reply URLs/bodies. It returns their paths, pinned head, retained request mappings, `unreplied_threads`, `unreplied_comments` (including reviews), `unclassified_sources`, and outstanding requests. These worklists omit verified answers and previously classified informational feedback. Unknown or edited sources require the agent to read and classify their full conversations; the script does not guess whether differently worded answers are equivalent. Retain matching reply URLs in skipped requests' `reason` so the controller can verify previously posted answers too. All snapshots remain available for resolved-thread matches and cross-source duplicate checks, even when worklists are empty.

Follow the returned action:

- `inline`: match requests from all sources to inline threads; draft only missing inline replies. Hold unmatched requests for the top-level pass, and retain separate pending-decision holds.
- `top_level`: reconcile mappings and live answers; draft only remaining requests without a suitable thread. If an unanswered request belongs inline, map it with `disposition: "reply"` in the draft: submission returns `inline` without posting it. Run `next` again and prepare the inline batch first.
- `correct_batch`: correct the active rejection and ask the same validator for pass 2. Do not reset the limit or start another batch.
- `done`: no new feedback needs classification. Report any outstanding holds; this does not mean held requests or completion cleanup are finished. To recheck after review, CI, or completion, call `next` on the same record.
- `reconcile` or `stop`: halt reply publication and report the reason. `reconcile` requires the recovery command below; `stop` after a final rejection prohibits later publication in this run.

Any new, edited, or removed feedback encountered before top-level work sends the agent through inline matching first. A changed head stops for fresh preflight and decision context. A saved answer remains duplicate evidence across heads. Pending user decisions stay held until the user answers; after an answer, use recovery/reassessment to revisit those requests with current evidence.

## Submit each batch and verdict

Prepare the batch with the existing `reply_batch.py prepare`, using **only the latest controller snapshot paths**, and obtain independent validation following [the batch procedure](reply-batch.md). Then submit it to the controller:

```sh
python3 PLUGIN_ROOT/runtime/dk-pr-reply-review/scripts/reply_passes.py \
  --record RUN_RECORD.json --pr PR_URL --cwd REPO --agent REPLY_AGENT_ID \
  publish --batch BATCH.json --verdict VERDICT.json
```

Submit rejected verdicts too: the controller records the first rejection and permits one correction with the same validator; a second rejection stops further reply publication, including completion or restart. Each new batch starts at verdict pass 1 and needs its own digest/approval. The controller rejects a verdict from the drafting agent, a verdict for a different batch, a reply destination from the wrong phase, and a repeated request with an unchanged source and a retained verified answer.

For an empty phase, submit its batch with **no `--verdict`**. The controller skips validation/publication, retains held/skipped mappings, verifies freshness, and advances. An active rejection still requires the same validator's pass-2 verdict on its correction, even if all proposed replies were removed; an empty draft cannot reset that limit. Always call `next` after submission, including after an empty inline phase: it refreshes both complete snapshots before top-level preparation. Keep all requests accounted for in each draft, including mapped duplicates and requests held for another phase.

The controller copies each submitted batch and verdict without changing them, records publication intent before invoking the guard, and owns the new journal path. Do not publish with direct GitHub mutations or the low-level guard CLI: that bypasses phase ordering and recovery state. Existing low-level commands remain available for preparing/checking batches and testing the guard.

## Recover without replaying

```sh
python3 PLUGIN_ROOT/runtime/dk-pr-reply-review/scripts/reply_passes.py \
  --record RUN_RECORD.json --pr PR_URL --cwd REPO --agent REPLY_AGENT_ID reconcile
```

After refreshing preflight and decisions for a changed head, or on resume, reconcile the same record with live GitHub before replying. On resume only, a replacement reply agent may supply its canonical ID here; it receives the existing history and owns both remaining passes. Reconciliation verifies recorded receipts and inspects an in-flight journal for an exact new answer at its destination. Multiple candidates or missing/edited saved replies block recovery. Preserve uncertain journals; never delete them or replay a batch. An unobserved reply remains outstanding and needs fresh drafting from complete evidence. New or edited feedback arriving during interruption remains unclassified. Recovery restarts inline-first assessment while retaining verified answers, pending requests, and original artifacts. A first rejection at the same head resumes only its correction with the same validator; a head change during that rejection remains blocked. Recovery cannot reset an outstanding or final rejection.

## Resolve with a verified answer

Resolution is separate from reply processing. `next` also returns `unresolved_threads` (including answered-but-open threads), `scopeFingerprint`, and each open thread's `conversationFingerprint`. `cleanup_complete` is true only when no selected open threads, outstanding requests, or unclassified sources remain. `done` still means reply processing finished. Full snapshots retain unrelated and already resolved threads as context; a review URL's persisted selection limits mutations. Resume with the same original URL, including its review anchor.

Record **existing** authorization in the run record before resolving. For example, add `resolution_authorization: {"pr": "https://github.com/OWNER/REPO/pull/NUMBER", "selection": "pullrequestreview-ID", "source": "Exact user instruction authorizing resolution"}`. Use `selection: null` for a bare PR URL. An iteration run may cite `dk-pr-iter`'s invoked completion authorization. This field records permission already given; it cannot grant permission or broaden the selected review.

Read the entire selected thread and current published code. If an adequate answer already exists, do not post another acknowledgement. Otherwise use the existing independently validated publication path, verify the receipt, and call `next` again. Prepare one JSON assessment outside the repository:

```json
{
  "schemaVersion": 1,
  "snapshot": "/absolute/path/from/latest/next/inline.json",
  "threadReference": "THREAD_NODE_ID",
  "head": "PINNED_REMOTE_HEAD",
  "scopeFingerprint": "FROM_NEXT",
  "conversationFingerprint": "FROM_UNRESOLVED_THREADS",
  "reply": {"url": "VERIFIED_INLINE_REPLY_URL", "text": "Exact normalized reply text"},
  "fullyAddressed": true,
  "pendingDecision": false,
  "unpublishedFix": false,
  "unresolvedObjections": false,
  "requests": [{"source": "COMMENT_REFERENCE", "reason": "Why the request is fully addressed on the published head"}],
  "evidence": ["Pinned code permalink or retained verification artifact and relevant behavior"]
}
```

Include a reason for every comment other than the verified reply (including informational responses), explaining how all requests are satisfied. Mere comment presence is insufficient. The verified reply must be a non-root comment. Later responses stop resolution until reassessed: each later comment needs `disposition: "no_objection"` and a reason explaining why it adds no unresolved request (for example, a reviewer acknowledgement). Completion's verified reply reference uses the same entries under `laterResponses`. Do not relabel an objection as an answer. Held or unanswered controller mappings must be reassessed through the reply passes first. An assessment must reflect published evidence, never local-only fixes or unsettled decisions.

```sh
python3 PLUGIN_ROOT/runtime/dk-pr-reply-review/scripts/reply_passes.py \
  --record RUN_RECORD.json --pr ORIGINAL_PR_OR_REVIEW_URL --cwd REPO --agent REPLY_AGENT_ID \
  resolve --assessment ASSESSMENT.json
```

The command refreshes both snapshots, checks selection/head/scope/conversation and the exact reply URL/text, records intent, calls `resolveReviewThread`, then verifies the live result. It never posts a reply. Already resolved threads need no mutation. Permission failures, changed evidence, missing replies, or uncertain results halt the affected resolution. Top-level sources cannot be resolved.

After interruption or uncertainty, use `reconcile` on the same record. It records the observed resolution state and never replays the mutation or answer. If resolution was not observed, run `next` and create a fresh assessment; the attempted assessment cannot be reused. Retain all intents and receipts. After cleanup, call `next` and inspect the remaining selected work instead of assuming reply `done` means resolution is complete.
