# Recipe component starting points

These files are fragments for `deployment.components` in `recipe.yaml`. Use
one as a structural starting point, then place tested manifest inputs or
router values at the matching `config/` paths. The fragments demonstrate
schema shape; their shortened arguments and environment values do not
reproduce the referenced upstream deployments. They are not complete
deployments or benchmark evidence.

For complete, validator-passing day-zero recipes with full source configs, see:

- [Gemma Deployment](../../models/gemma-4/vllm/v0.24.0/recipes/nvidia-h200-x8/guidellm-8k1k/mtp-single-gpu/recipe.yaml)
- [GLM-5.2 RHOAI LLMI](../../models/glm-5.2/rhoai/3.5/recipes/ibmcloud-h200-gx3d-160x1792x8h200/aiperf-agentx-128k/pp2-tp8/recipe.yaml)
- [GLM-5.2 vLLM LWS](../../models/glm-5.2/vllm/v0.23.0/recipes/ibmcloud-h200-gx3d-160x1792x8h200/aiperf-agentx-128k/pp2-tp8/recipe.yaml)

## Available fragments

| Starting point                                                    | Based on                                                                                                                                                                                                                     |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Deployment](deployment.components.yaml)                         | [Gemma vLLM Deployment](https://github.com/openshift-psap/inference-playbooks/blob/main/models/gemma-4/vllm/v0.24.0/single-node/manifests/gemma-4-26b-a4b-it-fp8-deployment.yaml)                                           |
| [LLMInferenceService](llmisvc.components.yaml)                   | [KServe DeepSeek P/D example](https://github.com/red-hat-data-services/kserve/blob/main/docs/samples/llmisvc/dp-ep/deepseek-r1-gpu-rdma-roce/llm-inference-service-dp-ep-deepseek-r1-pd-gpu-p-deepep-ht-d-pplx.yaml); `rhoai` |
| [llm-d optimized baseline](llmd-optimized.components.yaml)       | [llm-d optimized-baseline guide](https://github.com/llm-d/llm-d/tree/main/guides/optimized-baseline)                                                                                                                        |
| [llm-d P/D Deployments and router](llmd-pd.components.yaml)      | [llm-d P/D guide](https://github.com/llm-d/llm-d/tree/main/guides/pd-disaggregation)                                                                                                                                       |
| [P/D LeaderWorkerSets and router](lws-pd.components.yaml)        | [llm-d `release-0.8` wide EP guide](https://github.com/llm-d/llm-d/tree/release-0.8/guides/wide-ep-lws)                                                                                                                    |

## LWS leader and worker roles

The LWS fragment shows different leader and worker arguments. In the upstream
wide EP source, `workerTemplate` supplies both roles; its shell startup also
calculates ranks at runtime. Replace the illustrative fixed ranks with the
tested startup logic for your deployment.

The `source` file retains fields such as replicas, group size, resources,
volumes, probes, and network settings. Container fields in the recipe are
explicit overrides for that source:

- **Resource** requests and limits may remain in the source; when specified in
  the recipe, only the named resource keys change. The same applies to
  environment entries by name.
- **`command`** and **`args`** each replace the full source list when supplied.
- **`arg_choices`** documents selected flags with `flag`, optional `value`, a
  `required` boolean, and `why`. Here `required: true` means the flag is
  essential to this recipe's intended behavior; `false` means it is a
  deliberate but optional choice. It does not construct the command. Leave it
  empty when importing a manifest whose rationale is not yet known.

## Reader notes

Optional [recipe notes](recipe-notes.yaml) hold the authored headline,
decision rationale across settings, omitted options, image compatibility,
feature claims, quickstart, insights, known issues, and sizing pointer. A
recipe links them with `notes: guides/notes.yaml`.

Display specs point to source fields with `file#/JSON/Pointer` syntax so
their values can be generated. Supported file names are `recipe.yaml`,
`hardware_profile`, `model.yaml`, and recipe-local `config/*.yaml` files.

## Validation

Validate a complete recipe with `python3 tools/validate.py --current`. The
validator checks schema shape and that each `config/` reference exists and has
the expected manifest kind. Rendering these inputs to `manifests/` is
follow-up work.
