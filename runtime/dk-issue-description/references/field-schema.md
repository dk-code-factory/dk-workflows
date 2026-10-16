# Issue field schema

Use the selected repository template's names and layout. Preserve these meanings.

## Required meanings

| Field | Question answered |
|---|---|
| Problem | What is wrong, missing, or slow, and why does it matter? |
| Expected outcome | What observable behavior should occur instead? |

Both must be specific. `It is broken` and `It should work` are insufficient.

## Repository-conditional meanings

Include only when the template or consistent same-kind issues ask for them:

- Context: exact version, component, platform, or configuration.
- Reproduction: minimal numbered steps actually performed.
- Evidence: relevant logs, traces, screenshots, or measurements.
- Proposal: solution or design sketch, separate from the problem.
- Workaround: a verified way around the problem.
- Acceptance criteria: observable completion conditions.

## Conservative fallback

When no template or style evidence exists:

- Title: name the affected behavior and failure or desired outcome.
- Body: `Problem`, `Expected behavior`, and only supported context/evidence.
- Bug reports may include reproduction steps only when known.
- Feature requests describe the unavailable capability before proposing implementation.

## Reject

- Template comments, placeholder text, empty optional sections, or repeated summary.
- Suspected causes stated as facts.
- Invented versions, logs, reproduction, benchmarks, labels, or workarounds.
- Generated-by banners or tool attribution.
- Browser-dead `path:line` references; use a SHA-pinned raw permalink.
