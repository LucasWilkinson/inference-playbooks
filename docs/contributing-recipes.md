# Contributing a Recipe

A recipe connects a tested serving configuration to a model, hardware profile,
and workload. Benchmark evidence is welcome but is not required for a
`day-zero` recipe.

## Recipe v4 (recommended)

Recipe v4 separates the serving configuration from deployment manifests.
Contributors define what to serve; templates generate Kubernetes YAML.

### 1. Choose the recipe identity

Use this path:

```text
models/<model-id>/<stack>/<stack-version>/recipes/
  <hardware-selector>/<workload-profile>/<deployment-mode>[--<suffix>]/
```

Choose `rhoai`, `llm-d`, or `vllm` for the stack. Use one of the current
workloads: `guidellm-8k1k`, `aiperf-agentx-128k`, or
`aiperf-agentx-unlimited-context`. Adding a new workload profile requires
a schema change to `schema/recipe.schema.json`.

The deployment mode names the pattern (for example, `tp8-aggregated` or
`pp2-tp8`), not a latency/throughput category. Use a suffix only if
another recipe already has that mode.

Set `optimization_intent` separately; `latency`, `throughput`, and a
concise custom description are allowed. Set `deployment.scope` to
`single-node` or `multi-node`.

The directory's hardware selector must match either the selected profile's
file stem or its `accelerator_key`.

### 2. Check model metadata

Every model needs `models/<model-id>/model.yaml` with `schema_version: 2`.
Required fields: `huggingface_id`, `model_type`, `architectures`, `source`.

For public models, run `tools/resolve-model.py` to populate from HuggingFace:

```bash
python3 tools/resolve-model.py google/gemma-4-26B-A4B-it --update models/gemma-4/model.yaml
```

For gated models (`source: manual`), fill `model_type` and `architectures`
from your local copy of the model's `config.json`.

### 3. Write `recipe.yaml`

Set `schema_version: 4` and fill the `serving` block:

```yaml
schema_version: 4
recipe_id: gemma-4-tp1-tool-calling
model_id: gemma-4
platform: {stack: vllm, version: v0.24.0}
hardware_profile: hardware-profiles/nvidia-h200-sxm-8x-nvlink-r1.yaml
workload_profile: guidellm-8k1k
deployment_mode: tp1-tool-calling

serving:
  image: docker.io/vllm/vllm-openai:v0.24.0
  model: RedHatAI/gemma-4-26B-A4B-it-FP8-dynamic
  parallelism:
    mode: tp
    tp: 1
  args:
    - flag: --enable-auto-tool-choice
      required: true
      why: Enables tool calling detection in chat completions.
    - flag: --max-model-len
      value: "16384"
      required: true
      why: Memory-bound on single GPU at FP8.

maturity: day-zero
optimization_intent: single-GPU agentic serving
deployment:
  scope: single-node
```

No `config/` directory needed. Templates handle Kubernetes boilerplate
(security context, probes, labels, GPU resources, shared memory).

For multi-node or P/D disaggregated deployments, add role blocks:

```yaml
serving:
  decode:
    leader_args:
      - flag: --rank
        value: "0"
        required: true
        why: Leader rank assignment.
    exclude:
      - flag: --enable-prefix-caching
        reason: Prefix cache lives on prefill pods only.
  prefill:
    args:
      - flag: --enable-chunked-prefill
        required: true
        why: Optimized prefill throughput.
```

### 4. Override path (optional)

When templates do not cover a requirement (custom sidecars, init containers,
non-standard volumes), add `config/` with a `kustomization.yaml` for
Kustomize patches over the generated base. Set `config_overrides: true`.

### 5. Validate and render

```bash
python3 -m pip install -r tools/requirements.txt
python3 tools/validate.py --current
python3 tools/render.py models/.../recipe.yaml --dry-run
```

The validator checks schema, flag constraints, role block consistency, and
layout. The renderer generates manifests from the serving block.

### 6. Add benchmark evidence when available

Same as before: put harness inputs and trace provenance in `benchmarks/`,
commit `results/<run-id>/run.yaml` and `result.json`. Link from
`benchmark_runs`. Use `maturity: day-zero` if no benchmark run is available.

### 7. Open a PR

Do not push directly to `main`. CI validates and computes affected recipes.

## Recipe v3 migration

The v3 schema is no longer supported. Recipes with `schema_version: 3` or
`deployment.components` must be migrated to v4 before they can pass validation.
See the [design document](design-recipe-v4.md) for migration details.

## Quick path: submit raw manifests

If you have a working deployment but not the bandwidth for a full v4 recipe,
submit your manifests directly. A maintainer will convert the submission to
a v4 recipe before merge.

### What to submit

Create a directory under the target model and open a PR:

```text
models/<model-id>/recipes/<recipe-id>/
  raw-manifest/
    deployment.yaml          # your working K8s manifest(s)
    service.yaml             # optional supporting manifests
    README.md                # optional notes
```

Use the same `<recipe-id>` convention as full recipes (e.g.,
`h200-x8-tp8-aggregated-8k1k`). If unsure, use a descriptive name and
a maintainer will adjust it.

### Required information

Include the following in your PR description or in
`raw-manifest/README.md`:

- **Model**: HuggingFace model ID or name
- **Stack and version**: which serving stack and version (e.g., vLLM v0.24.0,
  RHOAI 3.5, llm-d 0.8)
- **Hardware**: GPU type, count, and any topology details
  (e.g., 8x H200 SXM, NVLink)
- **Workload**: which benchmark workload this targets, if any
  (e.g., `guidellm-8k1k`, `aiperf-agentx-128k`)
- **Deployment pattern**: parallelism mode (TP, PP, TP+PP, DP) and node
  scope (single-node or multi-node)
- **Container image**: fully qualified image reference
- **Known prerequisites**: secrets, PVCs, operators, or cluster
  configuration needed to deploy

Mark anything uncertain with "needs review" — maintainers will verify
during conversion.

### What CI checks

CI validates raw-manifest submissions for:

- YAML/JSON syntax (no parse errors)
- No duplicate keys in YAML documents
- Directory is under `models/<model-id>/recipes/<recipe-id>/raw-manifest/`

CI does **not** require `recipe.yaml` or schema validation for raw-only
PRs. Those checks run after a maintainer adds the converted recipe.

### Example PR description

```
## Raw manifest submission

**Model**: RedHatAI/GLM-5.2-FP8-dynamic
**Stack**: vLLM v0.23.0
**Hardware**: 8x NVIDIA H200 SXM (IBMCloud gx3d-160x1792x8h200)
**Workload**: aiperf-agentx-128k
**Deployment**: PP2+TP8, multi-node (LeaderWorkerSet)
**Image**: docker.io/vllm/vllm-openai:v0.23.0

### Prerequisites
- `hf-token` Secret with HuggingFace token
- PVC with model weights mounted at /mnt/models
- 2 nodes with 8 GPUs each

### Notes
- Startup takes ~12 min for cold model load
- Needs `--trust-remote-code` for model loading
```

### What happens next

1. A maintainer converts the raw manifest to a v4 `recipe.yaml` with
   `serving` block and `platforms` entry in the same PR.
2. CI runs full schema validation and template rendering.
3. The raw manifest is retained in `raw-manifest/` for reference.
4. After conversion, `recipe.yaml` and `manifests/` are authoritative.
