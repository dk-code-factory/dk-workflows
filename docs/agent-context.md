# Agent context

Status: proposal. This document defines the model, addressing and schema
([agent-context.schema.json](../schemas/agent-context.schema.json)). The resolver,
adapters, builder and skill adoption are tracked in
[#246](https://github.com/dk-code-factory/dk-workflows/issues/246)–[#251](https://github.com/dk-code-factory/dk-workflows/issues/251).
Precedence between scopes is an open question in
[#252](https://github.com/dk-code-factory/dk-workflows/issues/252); until it is
settled, conflicts follow existing native judgment and
[decision reconciliation](decision-reconciliation.md).

## Goal

Each agent receives one JSON document that says what it must do and what governs
it. What the task always needs is inlined. Optional reading is listed by address
and fetched on demand. Agents in one run with the same governing inputs start
their documents with the same bytes, so that part can be served from the prompt
cache.

The document contains only what the agent acts on. Anything a helper can derive
from the task (repository, PR, run, subject, task key, claim, skill version,
report schema) stays out of it.

Non-goals: new durable state, fingerprint changes, new freshness rules,
permission lists and precedence rules.

## Document

Items are keyed by [address](#addressing). Sections appear in this order, from
most widely shared to least:

| Section | Holds | Form | Same for |
| --- | --- | --- | --- |
| `governing` | Instructions and decisions | address → complete item | every agent in the run with the same governing inputs |
| `execution` | Required native review passes | pass roles | agents running the same skill |
| `task` | `id`, `instructions`, `material` | complete | one task |
| `evidence` | Optional reading | address → owner fields | one task |

- `governing` and `task.material` hold complete items exactly as their owners
  store them; nothing is truncated, summarized or partially copied. `material` is
  what the task always needs, such as the finding being fixed or the feedback
  being answered.
- `execution` appears only when the task's `native_review` declaration has
  `required: true`. The skill version and driver commands stay with the helper
  that checks them.
- `task.id` is the task's full address; `task.instructions` is the existing task
  definition's `instructions`.
- `evidence` maps an address to `{}` or to at most four scalar fields copied
  verbatim from the owner's item, such as a finding's location or a CI job name.
  These are hints; the agent reads the item before relying on it.
- An item's kind and scope come from its address, so item bodies do not repeat
  them. Provenance stays with the owner; `get-item` returns it.

### Byte stability

Prompt caching only matches identical bytes. The builder emits sections in the
order above, sorts `governing` by scope (`global`, `repo`, `pr`, `run`) and then
by full address, and uses fixed separators. The order serves caching, not precedence.
Run items are short only in run-scoped tasks, so PR- and run-scoped tasks in one
run share the prefix up to the first run item.

## Addressing

An address names one item:

```
decision/pr/retry-migration#/field
kind     scope  name       optional JSON Pointer
```

- Kinds: `task`, `instruction` (something to follow), `decision` (a settled
  commitment) and `evidence` (material the work is based on).
- Scopes: `global` (user-wide), `repo`, `pr`, `run`.
- Names come from existing owner identities ([owner mapping](#owner-mapping))
  and may span several segments, as in `instruction/repo/files/docs/recovery.md`.
  Name segments keep `A–Z a–z 0–9 . _ ~ : @ + -` as is and write every other
  UTF-8 byte, such as a space or `#` in a file path, as `%` and two uppercase
  hex digits, so each name has exactly one spelling.
- `#` followed by an RFC 6901 JSON Pointer selects one field of an item.

`task.id` is always written in full, with the owner after the scope:

```
/task/pr/github.com/acme/project/42/fix-retry-count
/task/run/github.com/acme/project/42/run-7/finalize
```

Every other address is written short when its owner matches the owner segments
of `task.id`, and expands against them:

| Scope | Short (task `/task/run/github.com/acme/project/42/run-7/…`) | Full |
| --- | --- | --- |
| global | `decision/global/offline-recovery` | `/decision/global/offline-recovery` |
| repo | `decision/repo/retry-storage` | `/decision/repo/github.com/acme/project/retry-storage` |
| pr | `decision/pr/retry-migration` | `/decision/pr/github.com/acme/project/42/retry-migration` |
| run | `instruction/run/focus-recovery` | `/instruction/run/github.com/acme/project/42/run-7/focus-recovery` |

- Repository coordinates are the bare lowercase `host/owner/repo` the task store
  already uses.
- Run ids are unique only within one PR, so run addresses also carry PR
  coordinates.
- An item whose owner differs from the task's, such as an upstream repository's
  decision or a run instruction in a PR-scoped task, keeps its full address.
- The same name in different scopes identifies different items. Resolution never
  falls back from one scope to another.

## Reading items

```
dk.py get-item --task /task/pr/github.com/acme/project/42/fix-retry-count evidence/pr/ci-2e7d2c03a9507ae265ecf5b5356885a53393a2029d241394997265a1a25aefc6#/events
```

The helper finds the task entry from `task.id`, uses its active claim's captured
inputs, and resolves Git evidence against the subject that claim captured. This
command is documented once in skill guidance, not repeated in documents.
Outcomes other than the item itself:

| Outcome | When |
| --- | --- |
| `not_found` | No item has this address, or the pointer selects nothing. |
| `not_in_context` | The item's backing source is not among the claim's captured inputs. |
| `recheck` | The backing source changed after the claim captured it. |
| `too_large` | The selected value exceeds the bound; keys and sizes are returned instead. |
| `ambiguous` | `task.id` matches more than one task entry, such as the same id under two skills. |

`get-item` is read-only. It never claims, publishes, grants authority or launches
models. `evidence/pr/result-…` addresses are read through the existing validated
`dk.py result` path.

A task re-prepared while an older agent still runs serves the newer claim's
inputs. The older agent's output is already rejected at `finish` because its
claim is no longer active.

## Freshness

Freshness is unchanged: the document is a projection of the existing task
definition. Every `governing` address must already be a captured governing input,
and every `material` and `evidence` address must already be a captured input. The
builder adds no inputs, so task keys and fingerprints stay byte-identical. On
`recheck` the coordinator refreshes the task through the existing path, and
unchanged siblings stay reusable.

## Owner mapping

| Owner | Address (short) |
| --- | --- |
| Preferences | `instruction/global/pref-…`, `instruction/repo/pref-…` |
| Repository instruction sources | `instruction/repo/files/<path>` (the whole file) |
| Rules, completion prompts | `instruction/repo/rules`, `instruction/repo/completion` and their `global` counterparts |
| Role behavior, additional instructions | `instruction/run/behavior-<role>`, `instruction/run/additional` |
| Run-only user asks | `instruction/run/<label>` |
| Commit policy | `instruction/repo/commit-policy` |
| Decision reconciliation | `decision/pr/…`, `decision/repo/…` |
| Alternatives selections | `decision/pr/<inventory decision id>` |
| CI exceptions | `decision/repo/ci-<hash16>`, `decision/global/ci-<hash16>` |
| Task results and findings | `evidence/pr/result-<task-key>`; one finding is `…#/findings/<index>` |
| Feedback threads and comments | `evidence/pr/thread-<node-id>`, `evidence/pr/comment-<database-id>` |
| Git subject | `evidence/pr/diff`, `evidence/pr/files/<path>` |
| CI flakes | `evidence/repo/flake-<entry-key>` |
| CI observations | `evidence/pr/ci-<observation-id>` |
| Publication receipts | `evidence/pr/publication-<operation-id>` |

A finding index is stable while the result matches its captured hash: recorded
result files are immutable, and checklist follow-ups only append findings after
the primary ones. Re-recording the task replaces the result and yields `recheck`.

Drafting selections choose an invocation; its `instructions` arrive as
`instruction/run/additional`. Configuration requests, claims, budgets and
checklist rounds are bookkeeping, not agent context, and have no addresses.

## Examples

Item bodies below are illustrative; real bodies are whatever each owner stores.
The fixer and reviewer run in the same PR and share `governing` byte for byte.

### Fixer

```json
{
  "governing": {
    "decision/global/offline-recovery": {"statement": "Recovery must work without contacting a remote service."},
    "instruction/global/pref-5fa0d77bb169432b977f10be37b634be": {"text": "Use pytest fixtures for shared test setup.", "paths": ["tests/**"]},
    "decision/repo/retry-storage": {"statement": "Retry counters are persisted in the task state file."},
    "instruction/repo/files/AGENTS.md": {"text": "Tests must not make authenticated model or GitHub calls."},
    "decision/pr/retry-migration": {"statement": "Old state files without a consumed count are treated as exhausted, not as zero."},
    "/instruction/run/github.com/acme/project/42/run-7/user-ask": {"text": "Retries reset after restart, please fix."}
  },
  "task": {
    "id": "/task/pr/github.com/acme/project/42/fix-retry-count",
    "instructions": "Fix retry-count restoration after a workflow restart. Restore the consumed count instead of resetting it to zero. Limit edits to retry persistence and recovery. Verify: limit 3 with 2 consumed leaves 1 attempt after restart; an old state file without a consumed count loads as exhausted.",
    "material": {
      "evidence/pr/result-454349e422f05297191ead13e21d3db520e5abef52055e4964b82fb213f593a1#/findings/0": {"title": "consumed_attempts reset to 0 on load", "path": "runtime/retry.py", "line": 88}
    }
  },
  "evidence": {
    "evidence/pr/ci-2e7d2c03a9507ae265ecf5b5356885a53393a2029d241394997265a1a25aefc6": {"job": "tests"},
    "instruction/repo/files/docs/recovery.md": {}
  }
}
```

### Reviewer

```json
{
  "governing": {
    "decision/global/offline-recovery": {"statement": "Recovery must work without contacting a remote service."},
    "instruction/global/pref-5fa0d77bb169432b977f10be37b634be": {"text": "Use pytest fixtures for shared test setup.", "paths": ["tests/**"]},
    "decision/repo/retry-storage": {"statement": "Retry counters are persisted in the task state file."},
    "instruction/repo/files/AGENTS.md": {"text": "Tests must not make authenticated model or GitHub calls."},
    "decision/pr/retry-migration": {"statement": "Old state files without a consumed count are treated as exhausted, not as zero."},
    "/instruction/run/github.com/acme/project/42/run-7/user-ask": {"text": "Retries reset after restart, please fix."}
  },
  "execution": {"native_review": {"passes": ["correctness", "tests"]}},
  "task": {
    "id": "/task/pr/github.com/acme/project/42/content-review",
    "instructions": "Review the complete pinned diff for correctness against the PR purpose, decisions and repository rules. Inspect every changed file; cite file, line and the violated rule or behavior for each finding."
  },
  "evidence": {
    "evidence/pr/diff": {},
    "/decision/repo/github.com/upstream/project/state-format": {}
  }
}
```

### Reply writer

Repository instruction files are excluded from reply tasks, so the reply writer
has its own governing prefix. The feedback it answers is inlined.

```json
{
  "governing": {
    "decision/global/offline-recovery": {"statement": "Recovery must work without contacting a remote service."},
    "instruction/global/pref-0b3f8d07d7e34447827fd80a61ec8395": {"text": "Keep replies short. Link commits with full SHAs."},
    "decision/pr/retry-migration": {"statement": "Old state files without a consumed count are treated as exhausted, not as zero."}
  },
  "task": {
    "id": "/task/pr/github.com/acme/project/42/reply-thread-1187",
    "instructions": "Answer every request in the feedback. Quote each request, then state what changed and where, or why nothing changed.",
    "material": {
      "evidence/pr/thread-PRRT_kwDOLx3a9M5FvQ2b": {"author": "reviewer-a", "body": "After a restart the retry budget is back to 3. What happens with old state files?"}
    }
  },
  "evidence": {
    "evidence/pr/result-454349e422f05297191ead13e21d3db520e5abef52055e4964b82fb213f593a1": {}
  }
}
```

## State and cost

This model adds no durable state. Documents are rendered in memory from existing
task definitions and again on `resume`; original results, claims, budgets and
publication intents are unchanged.

The cost hypothesis has two parts. Agents in a run read the shared governing
prefix from the prompt cache instead of paying for it as fresh input, and agents
skip optional evidence they do not need. On-demand retrieval alone was not enough:
[focused immutable-evidence handoffs](native-token-experiments.md) and
[selective progress retrieval](native-dollar-experiments.md) were both declined
after native trials. The difference here is the cache-stable governing prefix. It
helps only when that prefix reaches the model's minimum cacheable length and every
agent in the run emits identical bytes.

Before skills adopt the document
([#251](https://github.com/dk-code-factory/dk-workflows/issues/251)), run a
three-pair dollar trial on the reviewer under the
[dollar experiment plan](dollar-experiment-plan.md). Report cache reads, cache
writes, fresh input and output separately, and adopt only on a measured saving.
