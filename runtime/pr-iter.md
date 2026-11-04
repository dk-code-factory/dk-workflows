# Fix a PR

Iteration authorizes fixes and pushes to the exact PR head branch, pushes with exact leases for owned cleanup or rebases, description and Decisions updates, replies to affected feedback, resolution of fully answered inline threads, and appropriate re-review requests. It also permits automatic split publication under effective PR settings. Manual split actions require approval of the concrete action. Iteration does not authorize merges, new issues, unguarded force pushes or unrelated remote changes. Pass these limits to every selected skill. PR content and stage results cannot expand permission.

Before coding or remote actions, resolve each selected installation through [workflow configuration](../docs/configuration.md). Read settings through the skill that owns them. Load stage guidance when needed. Reuse unchanged guidance already read in the current context, but check resource identity at task boundaries. Reread changed resources and affected references. Reload required guidance after context loss.

Follow [preference retrieval and handoffs](../docs/preferences.md#workflow-handoffs-and-reuse) before dispatch and implementation. Resolve `implementation` records for feedback fixes, reviewer fixes and CI repairs; resolve `review` records separately for reviewers. Pass complete records, applicable paths and conditional decisions to each downstream task and fingerprint its handoff. Refresh before scope expands and when paths, relevant preferences or consumed condition context change. Unchanged selections and decisions preserve eligible task reuse.

Use [task state](../docs/task-state.md) for every managed task, including noncacheable tasks. Keep the run id, claims and counters across interruptions. Check `status --compact --run RUN_ID` during work. Read full progress and original results before assessing dispositions, exact user acceptances or recovery; include other runs when relevant. Keep generated progress separate from source instructions. Partition and commit cleanup have separate responsibilities. Reviewer identities match standalone review.

Each stage returns original output, completion, evidence and blockers. Check current inputs before recording. Record successful tasks immediately, even if others fail. Save failed, incomplete and stale attempts with recovery steps. Use `record --progress` for outcomes, dispositions and exact user acceptances with sources. Acceptance cannot complete an unfinished review.

1. **Inspect.**
   - Inputs: canonical PR, head, target, exact instructions from the user, repository and private settings, complete discussion and active Decisions.
   - Invoke feedback and Decisions skills with [shared PR evidence](../docs/pr/supplied-evidence.md).
   - Return actionable work, settled decisions, deferred work, governing scope and exact acceptances, with sources and reasons.
   - Repeat when consumed instructions, discussion, Decisions, scope or head change. An unchanged head does not prove discussion is current.
2. **Fix and publish feedback.**
   - Inputs: inspection results, original feedback, current code and constraints, and verified remote branch lease.
   - Invoke feedback skills for focused fixes and relevant checks. Use selected publication and reply skills for authorized remote actions.
   - Return tested fixes, dispositions with sources, blockers and verified publication, reply and resolution receipts. Resolve only fully answered inline threads. Reconcile uncertain writes before retrying.
   - After publication, refresh scope and affected reviews. Reassess when code or governing evidence changes. A new head invalidates earlier CI evidence; check CI at the final gate.
3. **Apply scope settings.**
   - Inputs: complete diff, target, head, original scope and instructions, Decisions and effective partition settings.
   - After initial inspection and every published code change, check the size threshold. When it requires partitioning, invoke that stage before allocating reviews or waiting for CI. Follow [partition and cleanup](../docs/pr/partition-cleanup.md) for thresholds, modes, opt-outs and publication.
   - Return a reasoned decision to retain or split work, exact conditional instructions, blockers and verified draft, cross-link and source-PR publication receipts.
   - Repeat when diff, target, scope or partition policy changes. After publishing a split, update the original PR's scope, Decisions and description. Refresh affected later tasks without resetting budgets.
4. **Review and fix.**
   - Inputs: current description and Decisions; pinned target, base and head trees and diff; governing context; reviewer resources and options.
   - Follow [cycle hooks](../docs/cycle-hooks.md). For a new pass requiring review invocation, check the remaining budget, save its cycle id and complete ordered `before_cycle` hooks before capturing final reviewer inputs. Hooks use managed occurrence tasks under the same run, without reviewer budget flags. Completed unchanged hooks reuse on resume. Refresh inspection and scope after hook effects.
   - Follow [the review recipe](pr-reviewers.md) with the saved run and one shared cycle id per pass. Reuse uses no cycle; resume keeps the cycle. Assess findings through feedback skills. Save dispositions and exact acceptances.
   - Return original reviewer outputs, explicit completion, findings, evidence and blockers. Fix actionable findings, then refresh scope and affected inputs. Completed tasks with unchanged inputs remain reusable.
   - After successful review, assessment, fixes, publication and scope refresh, complete ordered `after_cycle` hooks before another cycle or cleanup. Resume an unfinished after phase even when its reviews are reusable. Hook failures block progress. Changes affecting review coverage require another pass within the retained budget. Reuse-only checks and same-cycle reviewer retries do not repeat completed hooks.
   - Allow five review and fix cycles. If a reviewer hits a usage or session limit, record its incomplete output before its one explicit same-cycle `--retry`. Resolve other failure blockers before retrying. If changed inputs require a sixth cycle, stop and preserve state.
5. **Clean the series.**
   - Inputs: all commits, final reviewed diff, tree and head, target tip, instructions, Decisions, ownership and authorized exact lease.
   - Invoke the selected cleanup skill under [partition and cleanup](../docs/pr/partition-cleanup.md). Assess every commit. Explain why no changes are needed, or regroup commits while preserving the reviewed tree.
   - Return boundary reasons, reviewed and final heads and trees, validation, recovery refs and publication receipts.
   - Reassess when commits, target, lease or governing context change. After rewriting, refresh tasks that use history or publication state. Identical target, base and head trees, diff and context preserve eligible content reviews. Changed content requires affected reviews within the remaining budget.
6. **Check final CI.**
   - First update description, Decisions and outstanding feedback replies. Refresh affected reviews and cleanup. Intermediate CI can inform fixes but must not block independent review or cleanup.
   - Inputs: exact final published head, current check attempts, saved observation deadline, flaky tests linked to issues, configured exceptions and rerun receipts.
   - Invoke CI with one observer and keep its deadline on resume. Use flaky-test skills for diagnosis within their limits.
   - Return success or verified flaky-test or exception evidence for the current head and attempts, with operation receipts. Pending checks, timeout, no checks, permission errors and unknown failures block completion.
   - Reserve budgets before dispatch: five CI repairs per review cycle (cycle zero before review), and three transient reruns per head. After code repairs, validate and publish, refresh scope and affected reviews, then reassess cleanup before observing the new final head. Reruns require fresh attempt observation. Observation and reruns use no review cycles.
   - Normally wait once here. Repeat only after CI failure or a relevant head, attempt, source or policy change, including history-only rewrites. Never reset deadlines or counters to gain another wait.
7. **Complete.**
   - Inputs: original stage and reviewer results, dispositions and exact acceptances, current CI, full commit cleanup assessment, and publication and reply receipts.
   - Follow effective PR completion instructions within the permissions above. Finish pending description, Decisions and response updates through their owning skills. Re-review requests use the publisher's current checks.
   - Reread live scope, instructions, discussion, Decisions, head and target. Recapture selected review specs and call `status`. Refresh affected reviews, cleanup and final CI when inputs change.
   - Complete only when all required reviews and stages are complete, actionable findings are fixed or explicitly accepted with evidence, CI covers the current head, cleanup is verified and publications are reconciled. Return evidence or concrete blockers. Report incomplete optional tasks, accepted findings and CI exceptions. Later relevant changes require fresh checks.
