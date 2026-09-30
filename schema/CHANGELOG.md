# Schema changelog

## Model schema v2

- Accepts `schema_version` 1 or 2; existing v1 documents remain valid.
- Adds required fields for v2: `huggingface_id`, `model_type`, `architectures`,
  and `source`. These are enforced only when `schema_version` is 2.
- `source` is `"huggingface"` for public models resolved via
  `tools/resolve-model.py`, or `"manual"` for gated models where the
  contributor fills in metadata.
- `model_type` and `architectures` correspond to the HuggingFace
  `config.json` fields of the same name.
- Adds optional `quantizations[].huggingface_id` for quantized variant
  HuggingFace repository references.
- `huggingface_id` at the top level now refers to the base (unquantized) model;
  quantized variant references move to `quantizations[].huggingface_id`.

## Recipe schema v3

- Makes `deployment.scope` the required, canonical node-scope field with
  values `single-node` or `multi-node`.
- Removes `match.nodes` as a supported catalog selector; catalog tooling derives
  node scope from `deployment.scope`.
- Requires explicit platform, hardware-profile, and workload-profile identity.
- Limits workload profiles to the reusable PR #7 benchmark workloads and adds
  deployment-mode plus free-form optimization-intent metadata. `latency` and
  `throughput` remain the standard catalog values.
- Restricts platform-version identifiers to safe path components for CI matrix
  generation.
- Adds references to benchmark run records for validated and production recipes.

## Supporting schemas v1

- Adds model metadata, corrigible hardware-profile, benchmark-run, and
  normalized benchmark-result schemas.
- Benchmark runs record the hardware-profile revision known when they ran.
- Benchmark runs must reference their normalized result; result run IDs resolve
  to exactly one run record.
