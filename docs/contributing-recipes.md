# Contributing a Recipe v3 playbook

A recipe connects a tested serving configuration to a model, hardware profile,
and workload. Benchmark evidence is welcome but is not required for a
`day-zero` recipe. Start with a working manifest whenever possible; the
[component examples](../schema/examples/README.md) illustrate schema shape,
not deployable configurations. In particular, do not copy their shortened
argument lists over a working manifest's complete command.

For complete day-zero starting points, compare the
[Deployment](../models/gemma-4/vllm/v0.24.0/recipes/nvidia-h200-x8/guidellm-8k1k/mtp-single-gpu/recipe.yaml),
[LLMInferenceService](../models/glm-5.2/rhoai/3.5/recipes/ibmcloud-h200-gx3d-160x1792x8h200/aiperf-agentx-128k/pp2-tp8/recipe.yaml),
and [LeaderWorkerSet](../models/glm-5.2/vllm/v0.23.0/recipes/ibmcloud-h200-gx3d-160x1792x8h200/aiperf-agentx-128k/pp2-tp8/recipe.yaml)
recipes. They include full source manifests and reader notes, but no benchmark
runs or generated output yet.

## Initial-release option: submit raw manifests

For the initial release, a contributor may open a PR with only raw YAML/JSON
inputs at the same recipe leaf, without authoring `recipe.yaml`:

```text
models/<model-id>/<stack>/<stack-version>/recipes/
  <hardware-selector>/<workload-profile>/<deployment-mode>[--<suffix>]/
    raw-manifest/
      deployment.yaml
```

Multi-document Kubernetes YAML and Helm values are accepted. Put supporting
YAML/JSON inputs in the same folder; an optional `README.md` may explain apply
order or prerequisites. The PR description should state the model, stack and
version, hardware, intended workload, deployment pattern, source/provenance,
what has actually been tested, and any missing information. Do not include
credentials, private prompts, model weights, or sensitive logs. A contributor
does **not** need to create a model file, hardware profile, recipe notes,
benchmark results, or generated manifests for this intake path.

`python3 tools/validate.py --current` checks the folder layout and raw file
syntax, including duplicate YAML keys, without requiring a recipe. The PR
merge check intentionally remains red while `recipe.yaml` is absent. Thibrahi
or Saketh will convert the files to `config/` and Recipe v3 **in that same PR**,
fill in missing metadata with the contributor, and run the full checks. The
raw files remain as the submitted snapshot; `config/` and `recipe.yaml` become
authoritative after conversion. If there are no benchmark runs, the converted
recipe is `day-zero`. This intake exception is temporary and ends after the
initial release.

## 1. Choose the recipe identity

Use this path:

```text
models/<model-id>/<stack>/<stack-version>/recipes/
  <hardware-selector>/<workload-profile>/<deployment-mode>[--<suffix>]/
```

Choose `rhoai`, `llm-d`, or `vllm` for the stack. Use one of the current
workloads: `guidellm-8k1k`, `aiperf-agentx-128k`, or
`aiperf-agentx-unlimited-context`. The deployment mode names the pattern (for
example, `tp8-aggregated` or `pp2-tp8`), not a latency/throughput category.
Use a suffix only if another recipe already has that mode. Set
`optimization_intent` separately; `latency`, `throughput`, and a concise custom
description are allowed. Set `deployment.scope` to `single-node` or
`multi-node`; do not use the retired `match.nodes` field.

The directory's hardware selector must match either the selected profile's
file stem or its `accelerator_key`. The explicit `hardware_profile` path in
`recipe.yaml` is the source of truth.

## 2. Reuse or add model and hardware metadata

Use `models/<model-id>/model.yaml` for model identity, access, and
quantizations. Reuse a root-level `hardware-profiles/*.yaml` file if its
accelerator and host/network facts match. A new accelerator type or count
needs a new profile. To correct missing or mistaken stable facts in an
existing profile, increment `profile_revision` and add a correction-log entry;
do not put recipe or benchmark links in the profile. A run records the
profile revision known when it ran.

## 3. Keep complete, tested deployment inputs in `config/`

Place each editable source manifest in the recipe's `config/` directory.
Use one labelled `deployment.components` entry per Deployment,
LLMInferenceService, or LeaderWorkerSet; llm-d router entries point to a
values file. The [component examples](../schema/examples/README.md) show the
available shapes, including separate LWS leader and worker settings.
List supporting Kubernetes manifests, such as an LWS Service or LLMI worker
configuration, in `deployment.auxiliary_sources` with their path and kind.

The source file retains the full deployment: image, replicas, probes,
volumes, storage, networking, and other operator-specific fields. Container
`command` and `args` in the recipe are **full-list replacements**, not
individual flag patches. Omit an override to keep the source value; when
supplying one, copy every required token in its original order and verify the
result against the tested deployment. Container `env` entries and resource
request/limit keys are labelled overrides. Do not infer a working deployment
from a schema-valid fragment. This is especially important for LLMI
`VLLM_ADDITIONAL_ARGS`, P/D role settings, and LWS startup/rank logic.

Use optional `arg_choices` to explain selected flags. Each entry has `flag`,
optional `value`, `required: true` or `false`, and `why`. Here `required`
means essential to *this recipe's intended behavior*, not merely present in
the command. Leave the list empty when importing a manifest without verified
rationale; the exact source command/`args` remain authoritative.

## 4. Write `recipe.yaml` and optional reader notes

Fill the fields required by [Recipe v3](../schema/recipe.schema.json): model,
platform, profile, workload, deployment mode, intent, maturity, scope, and at
least one component. The path identity must agree with those fields. Use
`maturity: day-zero` if no benchmark run is available; `validated` and
`production` require a nonempty `benchmark_runs` list and cannot recommend an
image or deployment marked `needs-verification`.

For explanations, add `notes: guides/notes.yaml` and use the
[reader-notes example](../schema/examples/recipe-notes.yaml). Notes can cover
the headline, linked display specs, decisions across settings, omitted
options, image compatibility, feature claims, quickstart, insights, known
issues, and sizing. A display spec points to a source field with a JSON
Pointer rather than copying a mutable value. Mark unverified claims honestly.

## 5. Add benchmark evidence when available

Put reproducible harness inputs and trace provenance in `benchmarks/`. For
each completed run, commit `results/<run-id>/run.yaml`, its normalized
`result.json`, and sanitized raw artifacts or their durable location and
checksum. Link the run from `benchmark_runs`; keep the run ID, recipe ID,
deployment scope, profile path/revision, workload, command, environment, and
artifact provenance consistent. Do not copy numbers manually into notes or
README text. Do not commit secrets, private prompts, model weights, or
sensitive logs. See the [evidence guide](recipe-evidence.md).

The benchmark-output parser and generated reader summaries are not available
yet. A benchmarked contribution currently needs a valid `result.json` and
manual verification of its fidelity to the raw run; the validator checks
schema and references, not metric derivation.

## 6. Validate, test, and open a PR

From the repository root:

```bash
python3 -m pip install -r tools/requirements.txt
python3 tools/validate.py --current
python3 -m unittest discover -s tests -p 'test_recipe_evidence.py'
```

Maintainers also run
`python3 tools/validate.py --current --require-converted-raw` before merge.
CI runs the same conversion gate on
PRs. A red conversion gate on a raw-only intake PR is expected until the
maintainer adds its recipe.

Before requesting review, inspect the complete source manifest and deploy or
otherwise test it on the stated stack and hardware when claiming it works.
The local command checks schemas, paths, source kinds, labelled container
names, notes references, and evidence links; CI additionally checks
hardware-profile corrections against the PR base. Validation does **not**
prove that a manifest deploys, that feature claims follow from flags, or that
benchmark metrics were parsed correctly.

Open a pull request; do not push directly to `main`. CI validates the repository
and computes the affected recipe set. A recipe-local edit selects that recipe;
a corrected hardware profile selects only recipes that explicitly reference
it; schema/tool/workflow changes select all recipes. The current CI render
job is a placeholder: `tools/render.py`, generated manifest/README drift
checks, and catalog generation are follow-up work, not merge gates today.
