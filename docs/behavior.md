# Bundled role behavior

Every bundled skill has a `default` role. Internal native assignments have named
roles in the owning skill's `prompts/` directory. These maintained Codex resources
are synchronized into Claude's distribution. External plugins and their internal
agents remain owned by their plugin.

Resolve behavior before executing a direct invocation or assigning native work.
Use the helper's effective prompt as the role methodology. Replaceable methodology
is separate from mandatory entry, repository/host, permission, output, checklist,
evidence, budget, historical alternatives, publication and CI contracts. Those
contracts remain governing even with a replacement. Do not reinsert shipped
methodology or specialized behavioral options after selecting a replacement.
Original invocation `instructions` remain separate additional assignment text.
Helpers construct handoffs; the running native session executes them. Missing
native execution, delegation or evidence leaves the affected work incomplete.

Shared invocation settings accept `behavior`, keyed by role:

```json
{
  "skill_defaults": {
    "dk-workflows:dk-pr-review": {
      "behavior": {
        "finding-verification": {
          "override": "Challenge candidates using the supplied evidence.",
          "append": "Explain disagreements precisely."
        }
      }
    }
  }
}
```

Each field resolves independently through user, optional upstream, repository and
invocation precedence. A higher scope replaces text. Omission inherits; `null`
clears an inherited field. Compose replacement or shipped default, then addition.
Both fields may be used together. Blank text, NUL bytes, unknown roles and fields
are rejected. Existing specialized prompt settings remain supported in default
construction; a full replacement suppresses their behavioral text without deleting
those settings. Selection modes, scope and numeric guards remain contracts.

Inspect roles or one prompt without loading other role bodies:

```sh
python3 PLUGIN_ROOT/scripts/config.py --repository HOST/OWNER/REPO \
  behavior-show --skill dk-workflows:dk-pr-review --roles
python3 PLUGIN_ROOT/scripts/config.py --repository HOST/OWNER/REPO \
  behavior-show --skill dk-workflows:dk-pr-review --role finding-verification
python3 PLUGIN_ROOT/scripts/config.py --repository HOST/OWNER/REPO \
  behavior-show --skill dk-workflows:dk-config --role configuration-agent --section default
```

`behavior-show` returns the shipped `default`, `effective` prompt and field
`sources`. `--section default|effective|sources` restricts the response. `--options`
supplies invocation-local skill options and `--behavior` supplies an invocation-local
role map. These reads save nothing. The shipped default remains inspectable while
an override is active. Follow the same resolution when using internal roles such
as `language-review` or `failure-diagnosis`, including supporting assignments that
do not create managed tasks.

`behavior-set --skill SKILL --role ROLE --scope user|repo --file FIELDS.json
--request-id ID` updates only supplied fields of that scoped role. The file is an
object containing `override` and/or `append`. `behavior-reset` with the same
selectors and request ID removes only that scoped role, restoring inheritance.
Both use the existing [request lifecycle](configuration-requests.md), with a typed
`behavior` operation containing `skill`, `role`, `scope`, `action` and (for set)
`value`. Unrelated settings, roles and invocation controls are preserved.

The configuration skill and its `configuration-agent` role obey this same contract.
Resolve that role before delegation, including transient inspection. Its assigned
prompt stays fixed during execution; changes to its own behavior take effect on
its next assignment. Inspecting a default never adopts it or resets an override.

Managed definitions retain role selectors and original invocation edits through
existing task freshness, with a digest of consumed behavior. No rendered effective
prompt, settings snapshot or additional registry is saved for recovery. Existing
task identities, original results, claims, completed siblings, budgets and
publication receipts keep their lifecycle. Changed behavior refreshes affected
work; unrelated role edits preserve completed siblings. Historical alternatives
still require their existing explicit reassessment boundary. Reconcile uncertain
configuration writes under the original request and intent before another write.

Targeted inspection and deterministic handoffs avoid repeated prompt reconstruction
in this run and a fresh session. Freshness ends when consumed behavior, governing
contracts or relevant inputs change. Native API dollar savings remain unmeasured.
