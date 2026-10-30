# Script-controlled reply phases

Evidence-change stops include a retained `diagnostic` with `changes` and a safe
`next_action`. It identifies expected/actual heads, changed scope fields, added,
removed and changed source/thread IDs, or a missing/edited receipt's URL,
provenance category and content hashes. Full text remains in the observation
artifacts. An evidence digest also captures ordering or metadata changes.

Refresh the identified evidence and reconcile before retrying publication.
Retain verified mappings and journals, and reassess affected requests from the
complete current snapshots. A diagnostic never restores approval, authorizes a
retry or weakens the full-evidence equality check. Uncertain publications still
use reconciliation; never repeat an observed reply.

Prefer the stable `dk.py replies` route with common context before the route and
controller arguments after it. It returns a compact artifact receipt. Load the
saved artifact's `data` from disk to read the controller action and mappings;
do not JSON-parse a truncated tool response. Use `dk.py result --artifact FILE
--section /FIELD` for bounded inspection and pass artifact paths to validators.
Direct `reply_passes.py` examples below show the native arguments and remain
compatible; `dk.py --full ... replies` explicitly requests their native output.

Use `scripts/reply_passes.py` to control both phases. The reply agent follows its `action` and prepares request mappings and text; it does not select the next phase or publish directly. The controller uses the complete shared GitHub fetchers, retains all resolved conversations, and publishes through the existing `reply_batch.py` guard. Existing guard commands and batch schema are unchanged.

When called by `dk-pr-iter`, use the existing run record and canonical reply agent ID. For standalone reply work, initialize a JSON record outside the repository containing `{"pr": NUMBER}`. The controller stores history under `reply_workflow` in this record and saves immutable evidence/batch/verdict/journal artifacts beside it in `RECORD_STEM-replies/`. Use the same record through completion, interruptions, and head changes.

## Get the next instruction

```sh
python3 PLUGIN_ROOT/runtime/dk-pr-reply-review/scripts/reply_passes.py \
  --record RUN_RECORD.json --pr PR_URL --cwd REPO --agent REPLY_AGENT_ID next
```

`next` fetches both complete live snapshots and strictly checks controller publication receipts, publication journals, and resolution reply URLs/bodies. It returns their paths, pinned head, retained request mappings, `unreplied_threads`, `unreplied_comments` (including reviews), `unclassified_sources`, and outstanding requests. These worklists omit verified answers and previously classified informational feedback. Unknown or edited sources require the agent to read and classify their full conversations; the script does not guess whether differently worded answers are equivalent. Retain matching reply URLs in skipped requests' `reason` so the controller can verify previously posted answers too. All snapshots remain available for resolved-thread matches and cross-source duplicate checks, even when worklists are empty.

Follow the returned action:

- `inline`: match requests from all sources to inline threads; draft only missing inline replies. Hold unmatched requests for the top-level phase, and retain separate pending-decision holds.
- `resolve`: assess `resolution_candidates` using complete conversations and published code. Resolve fully answered authorized threads through the guarded command, then call `next`. No new reply is needed for an adequate existing answer. Do this independently of overall review/CI completion. Keep partial or disputed work held with reasons so it is not treated as fully addressed.
- `top_level`: reconcile mappings and live answers; draft only remaining requests without a suitable thread. If an unanswered request belongs inline, map it with `disposition: "reply"` in the draft: submission returns `inline` without posting it. Run `next` again and prepare the inline batch first.
- `correct_batch`: correct the active rejection and ask the same validator to review the corrected batch once and return a verdict with `pass: 2`. Do not reset the limit or start another batch.
- `done`: no new feedback needs classification. Report any outstanding holds; this does not mean held requests or completion cleanup are finished. To recheck after review, CI, or completion, call `next` on the same record.
- `reconcile` or `stop`: halt reply publication and report the reason. An evidence-only block can be retried with `next`, which fetches complete snapshots and clears it only after strict verification succeeds. Other `reconcile` actions require the recovery command below; `stop` after a final rejection prohibits later publication in this run.

Any new, edited, or removed feedback encountered before top-level work sends the agent through inline matching first. A changed head stops for fresh preflight and decision context. A saved answer remains duplicate evidence across heads. Pending user decisions stay held until the user answers; after an answer, use recovery/reassessment to revisit those requests with current evidence.

## Submit each batch and verdict

Prepare the batch with the existing `reply_batch.py prepare`, using **only the latest controller snapshot paths**, and obtain independent validation following [the batch procedure](reply-batch.md). Then submit it to the controller:

```sh
python3 PLUGIN_ROOT/runtime/dk-pr-reply-review/scripts/reply_passes.py \
  --record RUN_RECORD.json --pr PR_URL --cwd REPO --agent REPLY_AGENT_ID \
  publish --batch BATCH.json --verdict VERDICT.json
```

Submit rejected verdicts too: the controller records the first rejection and permits one correction with the same validator; a second rejection stops further reply publication, including completion or restart. Each new batch starts at verdict pass 1 and needs its own digest/approval. Rejection recording requires a structurally valid batch, an independent verdict bound to its original digest, and exact retained controller snapshots for the same PR, selection, pinned head, and current phase. It can use an earlier retained observation even when the draft repeats a verified answer. It preserves the original artifacts and attempt history without publishing, changing answer mappings, or advancing phases. Approved submissions still require the latest snapshots and pass all duplicate, routing, and wording checks.

For an empty phase that does not restore or dismiss a stale request, submit its batch with **no `--verdict`**. The controller skips publication, retains held/skipped mappings, verifies freshness, and advances. Restoring or dismissing a still-present stale request requires an independent verdict even when there are zero replies; use the same verdict schema and correction limit. An active rejection still requires the same validator's second verdict (`pass: 2`) on its correction, even if all proposed replies were removed; an empty draft cannot reset that limit. Always call `next` after submission, including after an empty inline phase: it refreshes both complete snapshots before top-level preparation. Keep all requests accounted for in each draft, including mapped duplicates and requests held for another phase.

The controller copies each submitted batch and verdict without changing them, records publication intent before invoking the guard, and owns the new journal path. Do not publish with direct GitHub mutations or the low-level guard CLI: that bypasses phase ordering and recovery state. Existing low-level commands remain available for preparing/checking batches and testing the guard.

## Recover without replaying

```sh
python3 PLUGIN_ROOT/runtime/dk-pr-reply-review/scripts/reply_passes.py \
  --record RUN_RECORD.json --pr PR_URL --cwd REPO --agent REPLY_AGENT_ID reconcile
```

After refreshing preflight and decisions for a changed head, or on resume, reconcile the same record with live GitHub before replying. On resume only, a replacement reply agent may supply its canonical ID here; it receives the existing history and owns both remaining phases. Reconciliation verifies recorded receipts and inspects an in-flight journal for an exact new answer at its destination. Multiple candidates or missing/edited controller-published or resolution replies block recovery. Strict receipt failures identify the affected URL and category (`controller_published`, `publication_journal`, or `resolution_reply`). Preserve uncertain journals; never delete them or replay a batch. An unobserved reply remains outstanding and needs fresh drafting from complete evidence. New or edited feedback arriving during interruption remains unclassified. Recovery restarts inline-first assessment while retaining verified answers, pending requests, and original artifacts. A first rejection at the same head resumes only its correction with the same validator; a head change during that rejection remains blocked. Recovery cannot reset an outstanding or final rejection.

If an earlier controller refused to record an externally retained rejection because of a duplicate answer or stale snapshots, reconcile the original record, then submit the unchanged original batch and rejected verdict with `publish --batch BATCH.json --verdict VERDICT.json` above. The original snapshots must still match a retained observation for the current phase and pinned head. This records the rejection through the existing interface; it does not replay publication or reset attempts. Run `next` for fresh snapshots, retain verified answers as skipped mappings, and submit the corrected batch with the same validator's second verdict (`pass: 2`). A correction containing no replies still needs that verdict. Final rejection and uncertain-publication blocks must be reconciled under their existing rules and cannot be replaced by importing a rejection.

## Edited semantic answer evidence

Reply-workflow state version 2 distinguishes `controller_published` answers from `semantic_evidence`. Each mapping retains the request identity, source fingerprint, receipt, and `current` or `stale` status. Publication and resolution provenance takes precedence when later phases reuse a published URL. Comment authorship never establishes provenance. The batch schema and CLI commands remain unchanged.

When semantic evidence is edited or deleted, `next` and `reconcile` mark every dependent mapping stale and retain its original body. `answerRevisions` is append-only: invalidations include the observed change and immutable snapshot paths; repeated observations of the same change do not add invalidations. Complete snapshots include evidence outside the selected review, while assessment and mutation stay within selection.

Controller results include `stale_answers` with each affected request, evidence URL, category, change reason, and `requestPresent`. Still-present requests return to inline-first assessment even when their own source text is unchanged. Stale mappings cannot prevent a missing reply through duplicate detection, satisfy cleanup, or authorize resolution. Read the current full conversation and independently reassess any restoration or dismissal, including an empty batch. Keeping a request held does not restore its answer; the hold stays outstanding. Removed requests keep their stale audit history without supplying answer evidence. Reverting the evidence body does not restore approval.

Version-1 state migrates automatically during observation, retaining unrelated run fields, counters, histories, and artifacts. The controller recovers provenance first from publication/resolution receipts and journals, then from retained successful batches proving pre-existing semantic evidence. Request identities come from retained mappings or batches. Ambiguous or inconsistent evidence stays blocked with the URL and a `legacy_answer` diagnostic; restore the missing retained artifacts or reconcile the inconsistency instead of guessing from authorship or deleting state.

Reconciliation observes publications without replaying them. An interrupted batch cannot reactivate changed semantic evidence or dismiss a newer stale request using an old assessment. Evidence-only blocks retry automatically through fresh complete snapshots. Head changes, uncertain publication, unfinished resolution, and outstanding/final rejection keep their existing recovery requirements and limits.

## Resolve with a verified answer

Resolution has its own guarded mutation and is directed by `next` as soon as an authorized open thread has verified current answer evidence and no pending inline request. It is independent of overall PR review and CI completion. `next` also returns `unresolved_threads` (including answered-but-open threads), `scopeFingerprint`, and each open thread's `conversationFingerprint`. `cleanup_complete` is true only when no selected open threads, outstanding requests, or unclassified sources remain. `done` still means reply processing finished. Full snapshots retain unrelated and already resolved threads as context; a review URL's persisted selection limits mutations. Resume with the same original URL, including its review anchor.

Record **existing** authorization in the run record before resolving. For example, add `resolution_authorization: {"pr": "https://github.com/OWNER/REPO/pull/NUMBER", "selection": "pullrequestreview-ID", "source": "Exact user instruction authorizing resolution"}`. Use `selection: null` for a bare PR URL. An iteration run may cite `dk-pr-iter`'s invoked resolution authorization during initial reply work; it need not wait for final completion. This field records permission already given; it cannot grant permission or broaden the selected review.

For an inline comment link, use `selection: "discussion_rID"` and keep that original URL for every controller call and resume. Only its containing thread can be assessed or mutated, even when other threads share the same review. GitHub Code Quality comments use this ordinary thread workflow; resolving a conversation does not itself prove a Code Quality check has passed.

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

Include a reason for every comment other than the verified reply (including informational responses), explaining how all requests are satisfied. Mere comment presence is insufficient. The verified reply must be a non-root comment. Later responses stop resolution until reassessed: each later comment needs `disposition: "no_objection"` and a reason explaining why it adds no unresolved request (for example, a reviewer acknowledgement). Completion's verified reply reference uses the same entries under `laterResponses`. Do not relabel an objection as an answer. Held, unanswered, or stale controller mappings must be reassessed through the reply phases first. An assessment must reflect published evidence, never local-only fixes or unsettled decisions.

```sh
python3 PLUGIN_ROOT/runtime/dk-pr-reply-review/scripts/reply_passes.py \
  --record RUN_RECORD.json --pr ORIGINAL_PR_OR_REVIEW_URL --cwd REPO --agent REPLY_AGENT_ID \
  resolve --assessment ASSESSMENT.json
```

The command refreshes both snapshots, checks selection/head/scope/conversation and the exact reply URL/text, records intent, calls `resolveReviewThread`, then verifies the live result. It never posts a reply. Already resolved threads need no mutation. Permission failures, changed evidence, missing replies, or uncertain results halt the affected resolution. Top-level sources cannot be resolved.

After interruption or uncertainty, use `reconcile` on the same record. It records the observed resolution state and never replays the mutation or answer. If resolution was not observed, run `next` and create a fresh assessment; the attempted assessment cannot be reused. Retain all intents and receipts. After cleanup, call `next` and inspect the remaining selected work instead of assuming reply `done` means resolution is complete.
