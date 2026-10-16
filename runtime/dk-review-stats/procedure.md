Use the local versioned API at `../dk-pr-iter/scripts/review_stats.py`:

```text
python3 PLUGIN_ROOT/runtime/dk-pr-iter/scripts/review_stats.py summary
python3 PLUGIN_ROOT/runtime/dk-pr-iter/scripts/review_stats.py summary --repo HOST/OWNER/REPO [--reviewer SKILL]
python3 PLUGIN_ROOT/runtime/dk-pr-iter/scripts/review_stats.py repositories
python3 PLUGIN_ROOT/runtime/dk-pr-iter/scripts/review_stats.py details --repo HOST/OWNER/REPO --run RUN_ID
```

`summary` also accepts `--reviewer` without `--repo`. Queries read local files only;
no reviewer, model, network, rendering or configuration commands are needed.
Use the normalized **PR base repository** identity. User-wide means this local user's
recorded repositories. Read `details` only when the request needs individual runs,
findings, assessment history or retained evidence. See [the API contract](../dk-pr-iter/references/review-stats.md)
for definitions and schema.

Return a compact comparison with Major, Minor, Nit and total where relevant, precision
and its adjudicated sample size, shared/exclusive contributions, and incomplete review
counts. Link `data.reports.user` and the relevant repository report paths. Distinguish
observation counts from deduplicated confirmed issues. Unknown precision or exclusive
eligibility must remain unknown; a missing repository/run is not a zero-quality score.
Show collection warnings. A real excluded, deferred, pre-existing or subsequently fixed
issue still receives confirmed credit. Use reported versus validated severity to explain
severity inflation when requested. Do not claim recall or invent a combined quality score.
Only future iteration reviews are collected; do not reconstruct historical semantic
verdicts, launch reviewers, or remove/reconfigure a reviewer to answer a stats request.
