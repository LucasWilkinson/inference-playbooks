# Model storage by platform

The standalone [vLLM Deployment](../manifests/vllm-v0.24.0/deployment.yaml)
passes `zai-org/GLM-5.2-FP8` to vLLM. It downloads the checkpoint at startup
using the existing `hf-token` Secret and stores its Hugging Face cache in
pod-local `emptyDir` storage. It does not need a pre-populated weights PVC, but
it does need Hugging Face access, outbound network access and enough node
ephemeral storage. A replacement pod downloads the model again.

The [RHOAI service](../manifests/rhoai-3.5/llminferenceservice.yaml) still
requires a pre-populated PVC. Neither path creates a downloader, PVC, namespace
or token secret. Both remain day-zero and needs-verification in
[recipe.yaml](../recipe.yaml). Memory-backed `/dev/shm` is separate from model
storage.

## RHOAI PVC example and exact directory layout

`glm52-weights` is a **concrete example name**, not a mandated infrastructure
identity. It must be an existing Bound PVC in the namespace selected by the
deployer. Storage class, access mode and capacity are deployment-specific.
Single-node use does not inherently require RWX. Capacity must fit the complete
**zai-org/GLM-5.2-FP8** checkpoint at the exact revision selected for deployment,
plus preparation headroom. Choose capacity from the actual checkpoint files
and storage requirements rather than a fixed recipe-wide size.

The RHOAI example stores the checkpoint directory **at the PVC root**, not inside a
Hugging Face hub cache tree. That root must contain its `config.json`, tokenizer
assets, safetensors index and every shard referenced by that index, plus all
other files required by the selected exact checkpoint revision. Do not substitute
`THUDM/GLM-5.2` base weights or another quantization. Record the checkpoint,
resolved immutable revision, file inventory and integrity/completion evidence
outside mutable serving summaries. Serving must not consume a partial download
or a symlink tree whose targets lie outside the mounted PVC.

For RHOAI, the local JSON6902 overlay mounts this existing PVC read-only at **/mnt/models**.
The inherited downstream runtime command serves `/mnt/models`, and the model URI
is `pvc://glm52-weights`. `spec.model.name` is the API alias
`glm-5.2-fp8`, while `serving.model` retains the quantization-specific checkpoint
identity. Do not confuse the API alias with the on-disk checkpoint.

The standalone platform renders from the same `serving` configuration and
[vLLM override](../platforms/vllm-v0.24.0.yaml). Its
[runtime patch](../config/vllm-runtime.yaml) keeps the model ID as the vLLM
argument, sets the API alias and startup budget, and does not add a weights
PVC. It does not inherit the RHOAI controller command or TLS settings; patches
are targeted by resource kind.

## Why an explicit RHOAI mount is necessary

The downstream controller's
[disabled-initializer path](https://github.com/red-hat-data-services/kserve/blob/881aa93dc885f9bbb57fe020defafab48bd93fd7/pkg/controller/v1alpha2/llmisvc/workload_storage.go#L97-L104)
returns early when `storageInitializer.enabled=false`, **before** its automatic
PVC attachment. The TP template disables that
initializer, so `model.uri: pvc://...` alone does not mount the weights.

The recipe keeps downloads disabled and supplies the explicit root mount.
This is consistent with the controller's
normal PVC mount semantics: read-only, chosen PVC/subpath and model mount path
([workload_storage.go:253-267](https://github.com/red-hat-data-services/kserve/blob/881aa93dc885f9bbb57fe020defafab48bd93fd7/pkg/controller/v1alpha2/llmisvc/workload_storage.go#L253-L267)).

The inherited default template uses separate ephemeral `/models` for
`HF_HUB_CACHE` and `/home` for HOME, not the durable weight PVC
([config-llm-template.yaml:184-200,232-255](https://github.com/red-hat-data-services/kserve/blob/881aa93dc885f9bbb57fe020defafab48bd93fd7/config/llmisvcconfig/config-llm-template.yaml#L184-L255)).
Installed presets must provide writable runtime/cache directories under the
deployment's security policy. Ensure model files are readable and runtime/cache
directories are writable by the allocated UID and groups.

## Deployment adaptation and preparation checklist

1. For standalone vLLM, verify the existing `hf-token` Secret can read the
   gated checkpoint, outbound access to Hugging Face works, and the selected
   node has enough ephemeral storage for the download and model cache.
2. For RHOAI, choose a PVC using your environment's approved storage
   preparation process. Populate the exact revision completely and establish
   file readability under the runtime's approved UID/group before serving.
3. Verify the RHOAI claim is Bound, its node attachment/access policy fits this
   single-node deployment, and its available capacity fits the checkpoint.
   The generic hardware profile is not PVC inventory.
4. To select another RHOAI PVC, change `deployment.storage.pvc.name` in
   [recipe.yaml](../recipe.yaml) and `claimName` in
   [config/runtime.yaml](../config/runtime.yaml). Storage class and access mode
   belong to that existing claim; this recipe does not create or resize it.
5. If the RHOAI checkpoint occupies a subdirectory rather than the PVC root,
   adapt its `pvc://` model URI and read-only mount `subPath` to the same
   directory. Do not rely on an external hub-cache symlink. The checked example
   intentionally uses the root.
6. Both targets reference the **existing** secret `hf-token`, key
   `HF_TOKEN`, in the selected namespace. Supply it through your approved secret
   process or adapt the RHOAI reference in a recipe-local JSON6902 patch and the
   standalone manifest's secret reference, then regenerate. No credentials
   belong in Git.
7. Keep namespace omitted in the manifest and choose it at deployment time.
   No recipe-owned namespace, ServiceAccount, SCC or PVC creation is implied.
8. Regenerate after deployment-specific adaptation rather than hand-edit outputs:

   ```bash
   python3 tools/validate.py --current --require-converted-raw
   python3 tools/render.py models/glm-5.2/recipes/h200-x8-tp8-mtp-agentic-131k/recipe.yaml
   python3 tools/check_manifests.py models/glm-5.2/recipes/h200-x8-tp8-mtp-agentic-131k
   ```

Before deployment, verify Hugging Face access and node ephemeral capacity for
standalone vLLM, or PVC population and controller presets for RHOAI. Rendering
does not verify storage, schedulability, GLM/MTP correctness or AgentX
performance.

## API alias and probes

The RHOAI alias and probes are set by [config/runtime.yaml](../config/runtime.yaml)
and described by `serving.served_model_name` and `serving.probes` in
[recipe.yaml](../recipe.yaml). [config/vllm-runtime.yaml](../config/vllm-runtime.yaml)
patches the standalone alias and probe settings not honored by the current
template. Keep these patches synchronized with the recipe when adapting either configuration.
Cold-load duration and serving behavior remain unvalidated.

**RHOAI probe scheme follows the controller:** the downstream default
runtime adds SSL args and HTTPS probe schemes conditionally on
`GlobalConfig.EnableTLS`. The overlay omits the probe scheme so the controller's
strategic merge retains its preset scheme while overriding paths, ports and
budgets. Do not disable controller TLS or force HTTP merely to adapt probes.
Verify transport-consistent server and probe behavior with the installed
controller presets, particularly after upgrades.

## Standalone rendering and deployment

- [recipe.yaml](../recipe.yaml) owns the shared image/engine, serving flags, TP
  and resources. [platforms/vllm-v0.24.0.yaml](../platforms/vllm-v0.24.0.yaml)
  identifies the standalone runtime; it does not inherit RHOAI's shell-prequoted
  speculative-config override. The template passes raw JSON as one argv token.
- [config/kustomization.yaml](../config/kustomization.yaml) applies each runtime
  patch only to its matching resource kind. The Deployment patch sets the model
  startup budget, alias, probes and shared memory, and removes the template's
  undeclared CPU limit. The pod downloads into ephemeral cache storage; a pod
  replacement downloads the checkpoint again.
- Render after changing inputs. The entire
  [vLLM Deployment](../manifests/vllm-v0.24.0/deployment.yaml) is generated;
  edit recipe inputs and runtime patches rather than the generated manifest.
- The standalone manifest uses HTTP probes and has no Service, Route, TLS
  termination or authentication resource. Use namespace-scoped port-forwarding
  for initial checks or separately reviewed networking for exposure; RHOAI
  routing/security policy does not apply automatically.
- Its non-root UID and security context must be accepted by the namespace's
  approved SCC policy, with writable cache/temp mounts. The HF token must be
  authorized to access the checkpoint.
  The template's fixed UID is not guaranteed to fit an OpenShift namespace's allocated UID
  range. Adapt security through a reviewed Deployment-targeted patch; no SCC grant is provided here.
- Rendering and drift checks prove reproducible generation, not upstream model
  support or serving correctness. Independently verify
  checkpoint loading, probes, required and automatic tool calls with MTP enabled,
  and benchmark behavior on each platform before claiming validation.
