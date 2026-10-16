# Decision language review

Apply this review when drafting or revising decision text, including local choices, extracted discussion decisions, and code suggestions. Read-only consumers preserve the record. Preserve previously published entries verbatim unless the user authorizes rewriting them.

Write one material choice per entry. Name the affected component and state the chosen behavior directly. Use short sentences and familiar words; remove filler, vague qualifiers, unclear pronouns, and wording with more than one plausible meaning. Keep the rationale, scope, conditions, exceptions, and compatibility constraints needed to understand the choice. Preserve exact source links and user quotations. Shortening must not remove meaning or make a conditional choice unconditional. Resolve ambiguity from the evidence; if the evidence cannot resolve it, surface the uncertainty instead of inventing details or presenting it as settled.

For a deferral, distinguish exclusion from this PR from a temporary pause within
its scope. Preserve the reason for waiting and any expiry or revisit condition when
extracting, publishing, or handing off the choice. A cycle-only deferral keeps the
item pending within PR scope; it is not an exclusion for later cycles or resumed
sessions. Do not invent a temporary deferral to avoid an unresolved behavior or
scope choice. Follow the
[deferral question guidance](../../dk-pr-iter/references/decision-question.md)
when the source question and answer leave this scope unresolved.

After drafting, run `python3 PLUGIN_ROOT/runtime/shared/review_loop.py start --kind decisions`. Review the decision text against the evidence and the criteria above. After each pass, run `python3 PLUGIN_ROOT/runtime/shared/review_loop.py report --state TOKEN --updated yes` if you changed decision text, or `--updated no` otherwise. Use the latest returned token. The script reports `updated` and directs another language check only after a changed pass. Stop at the first unchanged pass or after three total passes, including the first. Do not restart the loop to exceed the cap for the same draft.

Complete the loop before saving a final decision draft, handing it to dependent work, or publishing an authorized update. Keep tokens and review status out of decision text. The script tracks reported edits; the agent judges language and fidelity to evidence. Reaching the cap does not prove clarity: report any remaining ambiguity without changing the settled choice or expanding publication permission.
