# Synthetic workflow observations

These files are authored policy examples, never an actual host recording.
`representative.json` is schema-v1 normalized input. `synthetic-capture.jsonl`
retains the corresponding synthetic event records; its exact bytes are hashed by
the trace. The same synthetic capture stands in for private pinned artifacts to
keep this plumbing fixture small. Real traces must reference the actual artifacts.

`entrypoints.json` selects applicable synthetic worker/CI observations and explicit
unknown coverage for each shared entrypoint.

`negative-controls.json` describes isolated event changes and the expected derived
violation. Tests regenerate the synthetic capture/hash after making each change.
See [the evaluation guide](../../../docs/workflow-behavior-evaluation.md) for real
capture normalization, unavailable observations, limits and version comparisons.
