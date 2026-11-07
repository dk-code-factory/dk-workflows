# Issue fields

Use the selected repository template's names and layout. Keep these meanings.

## Main fields

| Field | Question answered |
|---|---|
| Problem | What is wrong, missing or slow? Why does it matter? |
| Expected outcome | What observable behavior should occur? |

Be specific. `It is broken` and `It should work` are insufficient.

## Conditional fields

Include only when the template or consistent issues of the same type request them:

- Context: exact version, component, platform or configuration.
- Reproduction: minimal numbered steps actually performed.
- Evidence: relevant logs, traces, screenshots or measurements.
- Proposal: solution or design, separate from the problem.
- Workaround: a verified way to avoid the problem.
- Acceptance criteria: observable completion conditions.

## Fallback

Without a template or style evidence:

- Title: name the affected behavior and failure or desired outcome.
- Body: the configured `fallback_sections` (defaults `Problem` and `Expected behavior`) and supported context or evidence.
- Include reproduction steps for bugs only when known.
- For features, describe the missing capability before proposing implementation.

## Leave out

- Template comments, placeholders, empty optional sections or repeated summaries.
- Suspected causes presented as facts.
- Invented versions, logs, reproduction steps, benchmarks, labels or workarounds.
- Generated-by banners or tool attribution.
- Browser-unusable `path:line` references. Use a raw permalink pinned to a commit SHA.
