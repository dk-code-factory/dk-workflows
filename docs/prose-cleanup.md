# Prose cleanup

The default fix finalizer is `dk-workflows:dk-plain-english`. Replace that entry
with `dk-workflows:dk-asd-ste100` or an installed `plugin:skill` through the shared
[invocation contract](invocations.md), including instructions, options, required
status and native agent controls. Scoped lists replace the entire inherited
list; existing custom lists and `finalizers: []` remain authoritative. Incoming
review does not run this pass.

Run one full pass after the review/fix loop converges, before selected commit
cleanup and final CI. Keep it outside the repeating reviewer list. If its edits
require review, finish and accept the prose task, then refresh affected coverage
under the same run and budgets. Before continuing finalization, use the helper
again: unchanged accepted output reuses the pass; later fixes supply only a delta.
Do not repeat complete prose analysis just because review or CI repairs ran.

Supply this shared editing and recovery contract to every selected prose provider,
including an external replacement. The provider retains its invocation-local
instructions, options and native controls.

The candidate diff bounds editing to PR changes, including docstrings, comments
and documentation, with minimal adjacent edits necessary for coherence. The
native skill identifies prose; the helper does not classify file extensions or
assume that all changed text is natural language. Preserve meaning, requirements,
conditions, exceptions, terminology, executable examples, identifiers, commands,
links, machine directives, markup and docstring structure. Leave ambiguous text
unchanged and report it privately. Validate protected syntax and source meaning,
then run applicable existing checks. No eligible prose is a completed no-op with
scope evidence. Ambiguity is reported without inventing meaning.

Plain English favors short sentences, familiar words, active voice and clear
references. ASD-STE100 uses independently written guidance and available
[authoritative references](https://www.asd-ste100.org/about_STE.html). Report
unavailable dictionary entries, rule checks or reference access explicitly;
continue the best-effort pass. Never claim full compliance. Capture consumed
reference files as ordinary managed resources/inputs. If a reference becomes
available or changes, update the consumed sources and refresh the full pass.
Do not silently retain the earlier limited-verification result as current.

## Deterministic handoff

Build one ordinary managed descriptor, normally the selected `kind:"skill"`
finalizer occurrence from [finalizers](finalizers.md). Include the retained run,
owner selection/index/options/upstream, exact assignment, installed resources,
instructions, consumed context and immutable Git `checkout`, `target`, original
PR `base` and current `head` commit IDs. Use the same assignment on subsequent
calls; the changing candidate patch is data, not rewritten instructions. Include
reference availability evidence and any consumed reference contents among the
ordinary inputs/resources. Missing references can be recorded in the native
report; lack of reference verification does not block editing.

```sh
python3 PLUGIN_ROOT/scripts/prose.py --repository HOST/OWNER/REPO --pr PR_URL \
  --checkout CHECKOUT --descriptor /private/prose-descriptor.json
```

After compact managed `resume`, use the saved key and current immutable head to
recover the descriptor without rebuilding its instructions or reading a manifest:

```sh
python3 PLUGIN_ROOT/scripts/prose.py --repository HOST/OWNER/REPO --pr PR_URL \
  --checkout CHECKOUT --key TASK_KEY --head CURRENT_COMMIT
```

A changed selection, original base/target or consumed source map needs an updated
explicit descriptor instead. Retained sources still undergo live capture.

The helper returns a transient `patch` and a managed `request` for `prepare`.
Save only that request privately as required by the normal managed interface;
pass the patch directly to the native skill. Binary changes appear as compact
Git summaries, without encoded binary-patch bodies. Inspect pinned binary sources
separately when needed; a summary alone does not establish prose eligibility.
The initial baseline is the original PR base. An accepted previous result supplies the delta baseline from its output
commit. Its immutable result path is added to existing managed inputs. No
separate registry, transcript, completion marker or saved patch is needed.
The helper binds its contract/resources and input references deterministically.
Keep referenced Git objects and result artifacts available while reusing them.

Use the returned action:

- `prepare`: prepare the returned request, execute the selected native skill with
  the patch and checklist, then finish its original output and completion report.
- `reuse`: prepare the returned saved request for normal live acceptance checks;
  inspect its result only as needed. Verify the current tree and all required
  review, publication and CI evidence independently.
- `recover_required`: reconcile the original claim and any side effects with the
  saved request before selecting new candidates. Recover that occurrence with the original claim token through
  the normal managed interface; do not launch duplicate work.
- `recheck`: finish the retained checklist without repeating the primary pass,
  then call the helper again with current pins.

A successful report includes explicit completion, changed locations, validation,
unresolved ambiguities, reference-verification limits and these exact references:

```json
{
  "complete": true,
  "findings": [],
  "changed_locations": [],
  "validation": ["No eligible prose in the candidate diff."],
  "unresolved_ambiguities": [],
  "reference_verification_limits": [],
  "prose_cleanup": {
    "baseline": "BASELINE_COMMIT_FROM_HANDOFF",
    "head": "INPUT_HEAD_FROM_HANDOFF",
    "output": "RESULTING_COMMIT"
  }
}
```

Use real immutable commit IDs. The output must resolve to the current committed
tree. Include all prose edits in that commit through the existing authorized
commit stages and [local commit preparation](pr/commit-preparation.md), preserving
unrelated edits and their staging state. If edits cannot be committed under
current authorization, keep the task incomplete with recovery evidence. The helper
never creates commits or starts model sessions. No-op passes use the input head
as output. Publication remains with existing authorized publication stages.

The starting inputs stay immutable while the task edits prose. Finish validates
input/output references and the normal checklist; the native skill supplies
semantic and validation evidence. Uncommitted changes are never covered by the
output reference: settle authorized scope and refresh coverage before final CI.
Current cache opt-outs remain authoritative. Changed checklist requirements use
completion-only checking and retain the original analysis. Fresh explicit
descriptors can refresh adopted repository guidance without replaying stale
instruction bindings. Changed instructions, provider, options, consumed
references, original base or target require a full pass. Damaged/missing evidence or objects block reuse and
require restoration; they cannot become an empty delta. The same lifecycle
preserves completed siblings, original results, claims and budgets.
