# Decision-question format

Use this format when a PR requires a human choice. Write for someone who knows the repository but has not followed the investigation. Explain technical terms needed to compare the options; prefer observable behavior over implementation shorthand.

Start with a standalone heading, not a bullet containing a heading. In two or three short sentences, identify the component, the current behavior or verified problem, and the choice that remains unsettled. Link the relevant feedback and code. Distinguish confirmed findings from assumptions; do not invent evidence links.

Give two or three viable options when supported by the evidence. Use a numbered, bold option title and separate labeled paragraphs for each option. State concrete benefits and costs, including implementation effort, compatibility, failure behavior, and maintenance where relevant. Do not invent a disadvantage just to fill the format; say when none is known and identify any remaining uncertainty. Do not describe a partial mitigation as a complete fix.

Ask about the unresolved behavior or PR scope, not whether to postpone deciding.
Use existing scope and settled decisions without asking again. For an unsettled
compatibility tradeoff, compare concrete outcomes such as **Include the repair in
this PR** and **Preserve existing behavior; leave repair for separate work**.
Explain the compatibility cost of the repair and the defect retained by exclusion.
Exclusion applies to later cycles and resumed sessions of this PR; it does not mean
that a follow-up issue or fix has been scheduled.

Offer **Defer for this review cycle only** only when waiting has a concrete purpose,
such as pending evidence or a dependency, or the user explicitly requests it.
Name what is awaited and the event that triggers reconsideration. Another cycle
alone does not resolve a compatibility or scope choice. Keep the item pending in
PR scope and revisit it when that event occurs, or before PR completion if still
unresolved. This choice neither resets review limits nor authorizes PR completion
with the item excluded. Routine evidence gathering needs no deferral question.

For any deferral option, state its scope in both the option title and structured
selection label; never use an unqualified "Defer repair" label.

Preserve the selected duration and revisit condition in the decision, run record,
handoffs, and final report. A cycle-only choice must not become a PR-wide scope
exclusion. If an answer says only "defer", use the exact option it answers to
resolve the scope; ask a focused clarification if that option was also ambiguous.
Do not ask again when the original question and answer already settle the duration.

Mark the recommended option only when evidence supports it. Its **Why recommended** paragraph must explain how it fits the PR's scope and priorities, why it is preferable to the alternatives, and which costs or remaining limitations are acceptable. The label “recommended” alone is insufficient. If evidence does not support a recommendation, say what is missing.

Use this template, replacing the placeholders with evidence:

```markdown
## Decision needed — PR #NUMBER

[Short problem statement and the behavior to decide, with source links.]

1. **[Plain-language option name] — Recommended**

   **What changes:** [The resulting behavior and necessary implementation.]

   **Pros:** [Concrete benefits.]

   **Cons:** [Concrete costs, risks, or remaining limitations.]

   **Why recommended:** [Why this best fits the PR and why its costs are
   preferable to the alternatives.]

2. **[Alternative option name]**

   **What changes:** [The resulting behavior and necessary implementation.]

   **Pros:** [Concrete benefits.]

   **Cons:** [Concrete costs, risks, or remaining limitations.]

[Repeat the same labels for a third option only when useful.]

Which option do you prefer? Reply with its number, or describe another choice.
```

Keep a blank line between labeled paragraphs. Use short sentences; do not merge the pros and cons into an inline semicolon list. Do not wrap the whole question in a bullet, blockquote, or code fence in the actual user-facing response.

If operational status matters, place a short **Status:** paragraph after the question. Keep CI details, cycle counts, and publication state out of the option descriptions unless they affect the choice.

When a structured user-input tool is available and appropriate, preserve the full comparison in the question text. If the tool cannot display it, provide the full comparison in the user-facing decision request and use the tool for concise option selection. Respect tool mode restrictions. Repeat the full format in a final response if the answer is still pending; do not leave only a compressed option list. Pass the comparison unchanged to a delegated agent that must ask or retain the same question.
