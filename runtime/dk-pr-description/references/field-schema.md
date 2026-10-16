# Pull request sections

Use the repository's headings and section order when available. Keep the meanings below.

## Main sections

| Section | Question answered |
|---|---|
| Issue | What problem led to this change? |
| Fix | How does this change solve that problem? |

Usually include `Issue` and `Fix`. Leave out sections with nothing to explain.

## Other sections

Add these only when the PR has facts to report:

- Decisions: choices that affect behavior or limit the work, and why they were made.
- Results: outcomes or measurements that are not clear from the fix itself.
- Scope: work left out of this PR, with a link if an existing issue tracks that work.
- Risks or breaking changes: known effects on users, APIs, settings, data formats, or workflows.
- Release, migration, or rollback: steps needed to release, adopt, or undo the change.

Bold issue and PR numbers central to the change, plus names whose exact identity matters. Do not describe tests run or checks performed, even if the repository template asks for them.

## If the template and past PRs do not show a format

- Title: start with a verb and keep it short. If commit titles consistently start with commands such as `Fix` or `Add`, use that form.
- Body sections: `Issue` and `Fix`.
- Add `Decisions`, `Results`, or `Scope` only when the PR has facts for them.
- Mention known risks or breaking changes if they affect users or maintainers.
- Link a real issue with a phrase the repository uses to close issues, such as `Fixes #123`. If you cannot confirm the phrase, leave out the link.

## Leave out

- A summary that repeats the title without explaining the change.
- Lists of changed files, a contents list, emoji headings, or text that does not explain the change.
- Tests or checks, and invented facts, issue links, reviewers, screenshots, or release notes.
- Banners or mentions of the drafting tool.
- `path:line`, code links to branches, or Markdown links to code. Paste GitHub URLs to code at a full commit ID.
