# Agent context

Status: proposal. This document defines the model, addressing and schema
([agent-context.schema.json](../schemas/agent-context.schema.json)). The resolver,
adapters, builder and skill adoption are tracked in
[#246](https://github.com/dk-code-factory/dk-workflows/issues/246)–[#251](https://github.com/dk-code-factory/dk-workflows/issues/251).
Precedence between scopes is an open question in
[#252](https://github.com/dk-code-factory/dk-workflows/issues/252); until it is
settled, conflicts follow existing native judgment and
[decision reconciliation](decision-reconciliation.md).

## Model

Each agent receives one JSON context document. It holds the task, the governing
instructions and decisions, and permissions. Anything the framework knows the
agent needs is inlined. Everything else is a `$ref` the agent reads on demand:

```
dk.py get-item <$ref>
```

There are three standalone item kinds:

| Kind | Holds | Typical owners |
| --- | --- | --- |
| `instruction` | Something the agent is told to do or follow. | Preferences, repository instruction files, rules, role behavior, user asks. |
| `decision` | A settled, sourced commitment. | Decision reconciliation, alternatives selections, CI exceptions. |
| `evidence` | Material the work is based on. | Findings, task results, feedback threads, Git content, CI data, publication receipts. |

The `task` is the assignment itself. Output and execution contracts live inside
`task.output` and `task.execution`; they are not a separate kind. `schema/…`
references name report schemas shipped with skills.

## Item forms

Wherever the document holds an item, it uses one of four forms:

| Form | Meaning |
| --- | --- |
| `{"$ref": "x"}` | Reference only. |
| `{"$ref": "x", ...fields}` | Partial copy. Fetch `x` for the rest. |
| `{"$ref": "x", "text": "…", "truncated": true, ...}` | `text` is a verbatim prefix of the item's text, cut at a sentence boundary. Fetch `x` before relying on it. |
| `{...fields}` without `$ref` | The complete item. |

Siblings of `$ref` are copies, never overrides: each must equal the same field of
the resolved item, except a truncated `text`. This intentionally differs from
OpenAPI 3.1, where such siblings are ignored. Nested `$ref`s are not followed
automatically.

A standalone item, as returned by `get-item`, always has this envelope:

```json
{
  "$id": "decision/pr/retry-migration",
  "kind": "decision",
  "scope": "pr",
  "statement": "Old state files without a consumed count are treated as exhausted, not as zero.",
  "agreed_by": ["user", "reviewer"],
  "source": {"$ref": "evidence/pr/thread-1187"}
}
```

`source` leads one step back to the original material: a user message, an
instruction file section, a PR thread or a task result.

## Scopes and addressing

Scopes are `global`, `repo`, `pr` and `run`. Every address names its kind and
scope. A canonical address starts with `/` and carries its owner. A packed
address has no leading `/` and drops the owner segments, which come from the
document's `context`:

| Scope | Canonical | Packed |
| --- | --- | --- |
| global | `/decision/global/offline-recovery` | `decision/global/offline-recovery` |
| repo | `/decision/repo/github.com/acme/project/retry-storage` | `decision/repo/retry-storage` |
| pr | `/decision/pr/github.com/acme/project/42/retry-migration` | `decision/pr/retry-migration` |
| run | `/instruction/run/github.com/acme/project/42/run-7/focus-recovery` | `instruction/run/focus-recovery` |

- Repository coordinates are the bare lowercase `host/owner/repo` that the task
  store already uses. Repositories without a remote identity have no repository
  address.
- A run id is an opaque caller string that is unique only within one PR, so run
  addresses also carry PR coordinates.
- An item owned by a different repository or PR, such as an upstream settings
  layer, cannot be packed and keeps its canonical address.
- Item names are existing owner identities wherever they exist (`pref-…`,
  `ci-…`, thread node ids, operation ids). An item name may have several
  segments, as in `instruction/repo/files/docs/recovery.md`.
- `#` followed by an RFC 6901 JSON Pointer selects one field of an item:
  `evidence/pr/restart-failure#/events`.

The same name in different scopes identifies different items. Resolution never
falls back from one scope to another.

## Reading items

```
dk.py get-item --key KEY --claim CLAIM 'evidence/pr/restart-failure#/events'
```

```json
[
  {"event": "before_restart", "attempts": 2},
  {"event": "after_restart", "attempts": 0}
]
```

Without a pointer, a large item returns its top-level keys and sizes instead of
its body. Other outcomes:

| Outcome | When |
| --- | --- |
| `not_in_context` | The item's backing source is not among the claim's captured inputs. |
| `recheck` | The backing source changed after the claim captured it. |
| `missing_pointer` | The pointer selects nothing. |
| `wrong_kind` | The address names a kind the item does not have. |
| `too_large` | The selected value exceeds the bound; its keys and sizes are returned instead. |

`get-item` is read-only. It never claims, publishes, grants authority or
launches models.

## Freshness

There are no revisions. Every item is read from its existing owner, and the
claim's captured hashes are the freshness boundary. The builder declares each
referenced item's backing file as a task input before `prepare`, so capture
fingerprints it as usual. `get-item` serves an item only while that file still
matches its captured hash; otherwise the agent gets `recheck` and the
coordinator refreshes the task through the existing path. Changed governing
content still invalidates affected work, and unchanged siblings stay reusable.

## Owner mapping

| Owner | Kind / scope | Address (packed) |
| --- | --- | --- |
| Preferences | instruction / global, repo | `instruction/global/pref-…`, `instruction/repo/pref-…` |
| Repository instruction sources | instruction / repo | `instruction/repo/files/<path>`; Markdown exposes `{sections: {heading: text}}` |
| Rules, drafting, completion prompts | instruction / global, repo | `instruction/repo/rules`, `instruction/global/completion` |
| Role behavior, additional instructions | instruction / run | `instruction/run/behavior-<role>`, `instruction/run/additional` |
| Run-only user asks | instruction / run | `instruction/run/<label>` |
| Commit policy | instruction / repo | `instruction/repo/commit-policy` |
| Decision reconciliation | decision / pr, repo | `decision/pr/…`, `decision/repo/…` |
| Alternatives selections | decision / pr | `decision/pr/<inventory decision id>` |
| CI exceptions | decision / repo, global | `decision/repo/ci-<hash16>` |
| Task results and findings | evidence / pr | `evidence/pr/result-<key12>`, `evidence/pr/finding-<digest8>` |
| Feedback threads and comments | evidence / pr | `evidence/pr/thread-<node-id>`, `evidence/pr/comment-<database-id>` |
| Git subject | evidence / pr | `evidence/pr/diff`, `evidence/pr/files/<path>` |
| CI flakes | evidence / repo | `evidence/repo/flake-<key12>` |
| CI observations | evidence / pr | `evidence/pr/ci-<observation-id>` |
| Publication receipts | evidence / pr | `evidence/pr/publication-<operation-id>` |

Configuration requests, claims, budgets and checklist rounds are bookkeeping, not
agent context, and have no addresses.

## Example: fixer

```json
{
  "context": {"repo": "github.com/acme/project", "pr": 42, "run": "run-7"},
  "agent": "fixer",
  "resolve": "dk.py get-item <$ref>",
  "subject": {"base": "3f1c9e0", "head": "a84d211"},
  "task": {
    "$id": "task/pr/fix-retry-count",
    "instruction": "Fix retry-count restoration after a workflow restart. Restore the consumed count instead of resetting it to zero. Limit edits to retry persistence and recovery.",
    "checks": [
      "Limit 3, 2 consumed, restart → 1 attempt left.",
      "Old state file without a consumed count loads as exhausted."
    ],
    "sources": [
      {"$ref": "evidence/pr/finding-5c1e09ab", "summary": "consumed_attempts reset to 0 on load", "path": "runtime/retry.py", "line": 88},
      {"$ref": "evidence/pr/ci-7d20e4"}
    ],
    "paths": ["runtime/retry.py", "tests/test_retry.py"],
    "output": {"report": {"$ref": "schema/global/fix-report"}, "must_include": ["changed_files", "tests_run", "complete"]},
    "execution": {"native_review": {"required": false}}
  },
  "instructions": {
    "user-ask": {"$ref": "instruction/run/user-ask", "text": "Retries reset after restart, please fix."},
    "offline-tests": {"$ref": "instruction/repo/files/AGENTS.md#/sections/testing", "text": "Tests must not make authenticated model or GitHub calls."},
    "recovery-guide": {
      "$ref": "instruction/repo/files/docs/recovery.md",
      "text": "Recovery code must restore persisted counters before resuming work. Never reset budgets on load.",
      "truncated": true
    },
    "pytest-fixtures": {"$ref": "instruction/global/pref-3k9a", "text": "Use pytest fixtures for shared test setup.", "paths": ["tests/test_retry.py"]}
  },
  "decisions": {
    "offline-recovery": {"$ref": "decision/global/offline-recovery", "statement": "Recovery must work without contacting a remote service."},
    "retry-storage": {"$ref": "decision/repo/retry-storage", "statement": "Retry counters are persisted in the task state file."},
    "retry-migration": {"$ref": "decision/pr/retry-migration", "statement": "Old state files without a consumed count are treated as exhausted, not as zero."}
  },
  "permissions": ["edit_files", "run_offline_tests"],
  "required_reads": [{"$ref": "instruction/repo/files/docs/recovery.md", "reason": "Truncated and covers the task paths."}]
}
```

## Example: reviewer

The reviewer shares the fixer's instructions and decisions, pins the post-fix
head and reads the diff on demand.

```json
{
  "context": {"repo": "github.com/acme/project", "pr": 42, "run": "run-7"},
  "agent": "reviewer",
  "resolve": "dk.py get-item <$ref>",
  "subject": {"base": "3f1c9e0", "head": "c07be95"},
  "task": {
    "$id": "task/pr/content-review",
    "instruction": "Review the complete pinned diff for correctness against the PR purpose, decisions and repository rules.",
    "checks": ["Every changed file was inspected.", "Each finding cites file, line and the violated rule or behavior."],
    "sources": [{"$ref": "evidence/pr/diff"}],
    "output": {"report": {"$ref": "schema/global/review-report"}, "must_include": ["findings", "complete"]},
    "execution": {"native_review": {"required": true, "skill_version": "2.4.1", "passes": ["correctness", "tests"]}}
  },
  "instructions": {
    "offline-tests": {"$ref": "instruction/repo/files/AGENTS.md#/sections/testing", "text": "Tests must not make authenticated model or GitHub calls."}
  },
  "decisions": {
    "retry-migration": {"$ref": "decision/pr/retry-migration", "statement": "Old state files without a consumed count are treated as exhausted, not as zero."},
    "upstream-storage": {"$ref": "/decision/repo/github.com/upstream/project/state-format", "statement": "State files stay JSON."}
  },
  "permissions": ["read_files", "run_offline_tests"]
}
```

`upstream-storage` belongs to a different repository, so its address stays canonical.

## Example: reply writer

Repository agent instructions are excluded from reply tasks, as before. The exact
feedback is inlined; supporting evidence is referenced.

```json
{
  "context": {"repo": "github.com/acme/project", "pr": 42, "run": "run-7"},
  "agent": "reply-writer",
  "resolve": "dk.py get-item <$ref>",
  "subject": {"head": "c07be95"},
  "task": {
    "$id": "task/pr/reply-thread-1187",
    "instruction": "Answer every request in the feedback. Quote each request, then state what changed and where, or why nothing changed.",
    "checks": ["Every request has an answer.", "Each fixed answer links its commit."],
    "sources": [
      {"$ref": "evidence/pr/thread-1187", "author": "reviewer-a", "text": "After a restart the retry budget is back to 3. What happens with old state files?"},
      {"$ref": "evidence/pr/result-5c1e09ab7f21"}
    ],
    "output": {"report": {"$ref": "schema/global/reply-draft"}, "must_include": ["body", "source_thread", "complete"]}
  },
  "instructions": {
    "reply-style": {"$ref": "instruction/global/pref-7q2c", "text": "Keep replies short. Link commits with full SHAs."}
  },
  "decisions": {
    "retry-migration": {"$ref": "decision/pr/retry-migration", "statement": "Old state files without a consumed count are treated as exhausted, not as zero."}
  },
  "permissions": ["draft_reply"]
}
```

## State and cost

This model adds no durable state. Documents are generated in memory from the
existing assignment and regenerated on `resume`. Addresses reuse existing owner
identities. No item copies, catalog, revision store or read log is saved. Task
keys, fingerprints, original results, claims, budgets and publication intents are
unchanged.

The expected saving is that agents stop consuming large evidence bodies and
handoff prose they do not need, in this session and in fresh ones. Inlining
always-needed items avoids extra reads. The cost side is the document structure
and any `get-item` calls. Native API dollar savings are unmeasured until the
skills adopt the document
([#251](https://github.com/dk-code-factory/dk-workflows/issues/251)).
