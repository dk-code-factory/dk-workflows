# Commit message fields

Use the repository's names and layout. Keep these meanings.

## Main fields

| Field | Question answered |
|---|---|
| Summary | What changed, in one specific line? |
| Motivation | Why was the change needed? Which constraint shaped it? |
| References | Which issue, ticket or PR relates to it? Omit if none exists. |

Put motivation in the subject only when repository history permits a one-line message for a change this small. Otherwise the body must add information beyond the diff.

## Conditional fields

Include only when supported:

- Testing: repository commits consistently record validation.
- Sign-off: contributor rules, DCO or hooks require `Signed-off-by`.
- Breaking change: the repository uses Conventional Commits and the change is breaking.
- Human co-authors: actual joint authorship exists. Never credit a tool as an author.

## Fallback

Without repository style evidence:

- Use an imperative, sentence-case subject without a period, preferably within 72 characters.
- Add a short body only for motivation or an important consequence.
- Wrap prose near 72 characters.
- End with a real issue reference; omit it when unknown.
- Use plain text without Markdown headings.

## Leave out

- Generic subjects such as `fix bug`, `update code` or `misc changes`.
- Diff narration, unsupported claims, invented issues or test results.
- Generated-by banners, robot emoji or tool co-author trailers.
- Web links when a terminal `path:line` reference is enough.
