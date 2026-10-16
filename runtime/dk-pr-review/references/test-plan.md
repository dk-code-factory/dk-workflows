# Validate portable workflows

Run `python3 scripts/check.py` and `python3 scripts/test.py` from the plugin root. The runner isolates each migrated suite to prevent duplicate module names. Fixture tests reject unmocked model calls.

Validate both host manifests and skill directories. Install DK alone into temporary Claude and Codex profiles. Confirm unrelated configuration/drafting workflows use bundled defaults with no reviewers, and only selected workflow bindings affect prerequisites. Exercise native and cross-host installed skills, third-party Claude commands, command argument vectors, per-role drafting precedence, and legacy reads without migration writes.

Review integration must preserve exact Decisions, pinned PR/base/head, complete engineering preparation, provider source fingerprints, and tailored reviewer addenda. Reject stale patches, metadata, procedures, malformed output, interrupted preparation and all-exhausted reviewers. Verify publication tokens and exact force-push leases remain enforced. Keep credentials, private state and retained workflow artifacts outside the plugin and reviewed checkout.

Updater fixtures cover caching, deduplicated notices, notify/auto/off and overrides, all active outer/nested invocation deferral, completion failures after partial success, pins, offline checks, policy changes, verified installation results and reload requirements. Authenticated smoke reviews use copied credentials in isolated profiles and tiny offline Git fixtures after fixture suites pass. Never run remote paid reviews from regression tests.
