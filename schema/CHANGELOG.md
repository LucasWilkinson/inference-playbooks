# Schema changelog

## Recipe schema v3

### Deployment scope and identity

- `deployment.scope` is the required, canonical node-scope field with values
  `single-node` or `multi-node`.
- `match.nodes` is removed as a supported catalog selector; catalog tooling
  derives node scope from `deployment.scope`.
- Requires explicit platform, hardware-profile, and workload-profile identity.
- Limits workload profiles to the reusable PR #7 benchmark workloads and adds
  deployment-mode plus free-form optimization-intent metadata. `latency` and
  `throughput` remain the standard catalog values.
- Restricts platform-version identifiers to safe path components for CI matrix
  generation.

### Components and container overrides

- Adds manifest-specific component schemas for Deployment,
  LLMInferenceService, LeaderWorkerSet, and the llm-d router. Components
  reference editable `config/` inputs; container settings reuse ordered
  `command`/`args` tokens, `env` values, and optional resource overrides.
- Adds optional per-flag `arg_choices` notes with a required essentiality
  boolean and explanation; an imported manifest may leave the list empty.
- Adds `deployment.auxiliary_sources` for validated references to supporting
  recipe-local manifests such as Services and operator configurations.
- Moves role-specific configuration out of the common deployment object.
- Requires at least one typed deployment component; benchmark runs remain
  optional for day-zero recipes.

### Benchmark evidence and reader notes

- Adds references to benchmark run records for validated and production
  recipes.
- Adds optional, separately validated reader notes for decision rationale,
  image choices, feature claims, omissions, quickstart, known issues, sizing,
  and generated catalog spec references.

## Supporting schemas v1

- Adds model metadata, corrigible hardware-profile, benchmark-run, and
  normalized benchmark-result schemas.
- Benchmark runs record the hardware-profile revision known when they ran.
- Benchmark runs must reference their normalized result; result run IDs
  resolve to exactly one run record.
