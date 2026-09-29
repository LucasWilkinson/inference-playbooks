# Inference Playbooks

Inference Playbooks is a curated, reproducible collection of deployment
recipes for serving large language models on OpenShift/Kubernetes with GPU
accelerators. Each playbook connects a model, serving stack, hardware
profile, workload, deployment configuration, and — when available —
auditable benchmark evidence. It helps operators select, deploy, validate,
and compare practical inference configurations rather than treat manifests
or benchmark numbers in isolation.

## Supported Accelerators

- NVIDIA B200
- NVIDIA H200
- NVIDIA H100

## Target Workloads

Reusable benchmark Jobs live in
[`benchmarks/manifests/`](benchmarks/manifests/). Set the `ENDPOINT`,
`MODEL`, and `TOKENIZER` environment values in each manifest before
applying it in the same namespace as the target Service.

- [GuideLLM 8K/1K](benchmarks/manifests/guidellm-8k1k-job.yaml) —
  synthetic 8 000-input / 1 000-output requests at concurrent streams
  1, 4, 16, 32, 64, and 128.
- [AIPerf AgentX 128K](benchmarks/manifests/aiperf-agentx-128k-job.yaml)
  — long-context, multi-turn agentic trace replay filtered to
  128K contexts.
- [AIPerf AgentX unlimited context][agentx-unlimited] — the same replay
  without context filtering; requires an endpoint that can accept every
  request in the trace corpus.

[agentx-unlimited]: benchmarks/manifests/aiperf-agentx-unlimited-context-job.yaml

Model playbooks may add benchmark manifests beside a topology only when
the workload is specific to that model, framework, or deployment shape.

## Models

| Model              | Frameworks                                                                                              | Start Here                                                                                               |
| ------------------ | ------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| GLM-5.2-FP8        | [vLLM (v0.23.0)](models/glm-5.2/vllm/v0.23.0/) · [RHOAI (3.5)](models/glm-5.2/rhoai/3.5/)             | [vLLM guides](models/glm-5.2/vllm/v0.23.0/README.md) · [RHOAI guides](models/glm-5.2/rhoai/3.5/README.md) |
| GLM-5 / GLM-5-FP8  | [vLLM (latest)](models/glm-5/vllm/latest/)                                                             | [Model Ops](models/glm-5/model-ops/)                                                                    |
| Gemma-4-26B-A4B-FP8| [vLLM (v0.24.0)](models/gemma-4/vllm/v0.24.0/)                                                         | [Deployment Guides](models/gemma-4/vllm/v0.24.0/README.md)                                              |

## Recipe v3 Layout

```text
hardware-profiles/
  <hardware-profile>.yaml

models/<model-id>/
  model.yaml
  <stack>/<stack-version>/
    model-ops/
    recipes/<hardware-profile>/<workload-profile>/<deployment-mode>[--<suffix>]/
      raw-manifest/        # initial-release intake, when used
      recipe.yaml
      config/
      manifests/
      guides/
      benchmarks/
      results/
```

### Path segments

- **`<stack>`** — a serving stack: `vllm`, `rhoai`, or `llm-d`. The
  version is part of that stack context.
- **`<workload-profile>`** — one of `guidellm-8k1k`,
  `aiperf-agentx-128k`, or `aiperf-agentx-unlimited-context`.
- **`<deployment-mode>`** — the configuration pattern, such as
  `tp8-aggregated`, `tp8-replicas-2`, or `pp2-tp8`. Add `--<suffix>`
  only when more than one recipe shares that deployment mode.

### Key conventions

- `recipe.yaml` explicitly names its root-level hardware profile. The
  path is a navigation selector, not a source of hardware facts.
- `deployment.scope` is always `single-node` or `multi-node`;
  `match.nodes` is retired. `optimization_intent` is catalog metadata,
  not a path level, and may be a custom concise label.
- `deployment.components` uses separate schemas for Deployment,
  LLMInferenceService, LeaderWorkerSet, and the llm-d router. Each
  component references an editable source in `config/`; see
  [component starting examples](schema/examples/README.md).
- `deployment.auxiliary_sources` links supporting `config/` manifests
  such as Services and operator configuration by path and kind.
- Optional `notes: guides/notes.yaml` links reader-facing context and
  reasons for deployment choices; see the
  [notes example](schema/examples/recipe-notes.yaml).

### Day-zero examples

Complete day-zero recipes cover all three component types:

- [Deployment](models/gemma-4/vllm/v0.24.0/recipes/nvidia-h200-x8/guidellm-8k1k/mtp-single-gpu/recipe.yaml)
- [LLMInferenceService](models/glm-5.2/rhoai/3.5/recipes/ibmcloud-h200-gx3d-160x1792x8h200/aiperf-agentx-128k/pp2-tp8/recipe.yaml)
- [LeaderWorkerSet](models/glm-5.2/vllm/v0.23.0/recipes/ibmcloud-h200-gx3d-160x1792x8h200/aiperf-agentx-128k/pp2-tp8/recipe.yaml)

For the initial release, contributors may submit only raw YAML/JSON
files under a leaf's `raw-manifest/`. Thibrahi or Saketh will convert
them in the same PR before merge; see the
[contribution guide](docs/contributing-recipes.md).

### Hardware profiles

Hardware profiles contain accelerator, host-topology, and applicable
network facts together. They are stable but corrigible: a factual
correction increments `profile_revision` and adds a correction-log entry.
Changing accelerator type or count requires a new profile, while
`accelerator_key` supports comparisons across different hosts with the
same accelerator type/count.

See [the recipe-evidence guide](docs/recipe-evidence.md) for evidence
and profile rules, [the recipe contribution guide](docs/contributing-recipes.md)
for the creation and review flow, and [AGENTS.md](AGENTS.md) for the
full contributor contract.

## Validation

Install the local validation dependencies and validate repository
content:

```bash
python3 -m pip install -r tools/requirements.txt
python3 tools/validate.py --current
```

CI validates profile corrections and calculates only the recipes
affected by a change. A corrected hardware profile selects just recipes
that explicitly reference it; schema, validator, renderer, or workflow
changes select all recipes. README rendering and catalog generation will
be enabled when the renderer lands.

## Current Status

- Existing deployment guides and manifests remain in the legacy
  topology-based layout; new playbooks use the Recipe v3 evidence
  structure above.
- Recipe v3 schemas, immutable hardware profiles, evidence validation,
  affected-recipe selection, and reusable benchmark workloads are
  available on `main`.
- Three complete day-zero recipes demonstrate the Deployment, LLMI, and
  LWS schema modes. They are not benchmark-validated; migration of the
  remaining legacy playbooks, README rendering, and catalog generation
  are follow-up work.

## Planned

- **llm-d** — prefix-cache-aware routing, P/D disaggregation, KV tiering

Existing content under these frameworks uses the legacy
`<version>/<topology>/` layout. Add new playbooks using the Recipe v3
layout above.
