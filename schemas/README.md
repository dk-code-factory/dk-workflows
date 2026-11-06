# Configuration schemas

Schemas use [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12/json-schema-core). Relative schema references resolve within this directory. Every configured agent call references [invocation.schema.json](invocation.schema.json); do not duplicate its fields in feature-specific schemas.

| Schema | File or input described |
| --- | --- |
| [config.schema.json](config.schema.json) | Workflow configuration, including partial scope overrides. |
| [invocation.schema.json](invocation.schema.json) | Process entrypoints, finding verification, supporting skills, reviewers, hooks, finalizers, drafting calls and commit policy providers. |
| [drafting.schema.json](drafting.schema.json) | The drafting role-to-invocation object managed by `pr_config.py drafting`. |
| [commit-policy.schema.json](commit-policy.schema.json) | Literal commit policy or shared provider invocation. |
| [preferences.schema.json](preferences.schema.json) | Stored versioned preference catalog. |
| [pr-size.schema.json](pr-size.schema.json) | Stored versioned PR size policy. |
| [ci-exceptions.schema.json](ci-exceptions.schema.json) | Stored explicit CI exceptions and matchers. |
| [pr-partition.schema.json](pr-partition.schema.json) | Stored per-PR partition choices, history and declines. |
| [rules.schema.json](rules.schema.json) | Domain-owned private PR rules; contents intentionally remain open. |

Workflow and drafting files accept `$schema` metadata. Helper-managed policy and preference records retain their existing formats; use editor file associations for those schemas and edit records through their owning helpers. Completion prompts are plain text and have no JSON Schema.

The [compact example](../examples/config.json) shows overrides. The [expanded example](../examples/config.expanded.json) shows all bundled defaults. A schema `default` documents behavior; schema validation does not insert it. The workflow helper performs inheritance and default expansion.

Python additionally checks resolved installations, short-name ambiguity, actual model and reasoning availability, host-scoped account uniqueness, valid routing targets, selector semantics, normalized preference duplicates, ownership, revisions and hash-linked policy history. Native process instructions still govern task completion and permitted actions. A schema-valid file does not establish native availability or execution success.

The offline test suite validates each schema against its metaschema and exercises the same valid and invalid invocations at every call site. It uses a local resource registry; schema-reference tests never fetch remote resources. Install `jsonschema` with the test dependencies, then run `python3 -m pytest -q tests/test_configuration_schema.py`.
