import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from tools.recipe_evidence import load_unique_yaml

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "recipe_evidence.py"
VALIDATOR = REPO / "tools" / "validate.py"


def git(directory, *arguments):
    return subprocess.run(
        ["git", "-C", str(directory), *arguments],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )


class RecipeEvidenceTests(unittest.TestCase):
    profile_text = """\
schema_version: 1
profile_id: h200-r1
profile_revision: {revision}
accelerator_key: nvidia-h200-x8
accelerators:
  vendor: nvidia
  model: H200
  count_per_node: 8
correction_log:
  - revision: 1
    summary: Initial profile.
{correction}"""

    def make_repo(self):
        directory = Path(tempfile.mkdtemp())
        git(directory, "init", "-q")
        git(directory, "config", "user.email", "test@example.com")
        git(directory, "config", "user.name", "Test User")
        profile = directory / "hardware-profiles" / "h200-r1.yaml"
        profile.parent.mkdir(parents=True)
        profile.write_text(self.profile_text.format(revision=1, correction=""))
        model = directory / "models" / "glm" / "model.yaml"
        model.parent.mkdir(parents=True)
        model.write_text("schema_version: 1\nmodel_id: glm\nname: GLM\nfamily: GLM\nquantizations:\n  - name: FP8\n")
        recipe = directory / "models" / "glm" / "rhoai" / "3.5" / "recipes" / "h200-r1" / "guidellm-8k1k" / "tp8-aggregated" / "recipe.yaml"
        recipe.parent.mkdir(parents=True)
        recipe.write_text("recipe_id: guidellm-tp8\nhardware_profile: hardware-profiles/h200-r1.yaml\n")
        git(directory, "add", ".")
        git(directory, "commit", "-qm", "initial")
        return directory, profile, recipe

    def run_tool(self, directory, *arguments):
        return subprocess.run(
            ["python3", str(TOOL), "--repo", str(directory), *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def run_validator(self, directory, *arguments):
        return subprocess.run(
            ["python3", str(VALIDATOR), "--repo", str(directory), *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def recipe_validator(self):
        schemas = [json.loads(path.read_text()) for path in (REPO / "schema").glob("*.schema.json")]
        registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema)) for schema in schemas
        )
        recipe = next(schema for schema in schemas if schema["$id"].endswith("/recipe.schema.json"))
        return Draft202012Validator(recipe, registry=registry)

    def sample_recipe(self):
        return {
            "schema_version": 3,
            "recipe_id": "glm-guidellm-tp8",
            "model_id": "glm",
            "platform": {"stack": "rhoai", "version": "3.5"},
            "hardware_profile": "hardware-profiles/h200-r1.yaml",
            "workload_profile": "guidellm-8k1k",
            "deployment_mode": "tp8-aggregated",
            "optimization_intent": "latency",
            "maturity": "day-zero",
            "deployment": {
                "scope": "single-node",
                "components": {"modelserver": {"kind": "Deployment", "source": "config/modelserver.yaml"}},
            },
        }

    def test_profile_correction_is_allowed_with_a_revision_and_log(self):
        directory, profile, _ = self.make_repo()
        profile.write_text(self.profile_text.format(
            revision=2,
            correction="  - revision: 2\n    summary: Corrected host memory documentation.\n",
        ))
        git(directory, "add", ".")
        result = self.run_tool(directory, "--cached", "check-hardware-profiles")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_profile_identity_change_requires_a_new_file(self):
        directory, profile, _ = self.make_repo()
        changed = self.profile_text.format(revision=2, correction="  - revision: 2\n    summary: Incorrect change.\n")
        profile.write_text(changed.replace("nvidia-h200-x8", "nvidia-h200-x4").replace("count_per_node: 8", "count_per_node: 4"))
        git(directory, "add", ".")
        result = self.run_tool(directory, "--cached", "check-hardware-profiles")
        self.assertEqual(result.returncode, 1)
        self.assertIn("accelerator_key", result.stderr)

    def test_profile_metadata_correction_is_allowed(self):
        directory, profile, _ = self.make_repo()
        corrected = self.profile_text.format(
            revision=2,
            correction="  - revision: 2\n    summary: Added the recorded RoCE bandwidth.\n",
        )
        profile.write_text(corrected + "network:\n  inter_node_bandwidth_gbe: 400\n")
        git(directory, "add", ".")
        result = self.run_tool(directory, "--cached", "check-hardware-profiles")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_profile_is_allowed(self):
        directory, _, _ = self.make_repo()
        profile = directory / "hardware-profiles" / "h200-r2.yaml"
        profile.write_text(self.profile_text.format(revision=1, correction="").replace("h200-r1", "h200-r2"))
        git(directory, "add", ".")
        result = self.run_tool(directory, "--cached", "check-hardware-profiles")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_profile_requires_a_log_for_its_current_revision(self):
        directory, _, _ = self.make_repo()
        profile = directory / "hardware-profiles" / "h200-r2.yaml"
        profile.write_text(self.profile_text.format(revision=2, correction="").replace("h200-r1", "h200-r2"))
        git(directory, "add", ".")
        result = self.run_tool(directory, "--cached", "check-hardware-profiles")
        self.assertEqual(result.returncode, 1)
        self.assertIn("current revision", result.stderr)

    def test_duplicate_profile_fields_are_rejected(self):
        directory, profile, _ = self.make_repo()
        profile.write_text(self.profile_text.format(revision=1, correction="") + "profile_revision: 1\n")
        git(directory, "add", ".")
        result = self.run_tool(directory, "--cached", "check-hardware-profiles")
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate key", result.stderr)

    def test_recipe_change_selects_only_that_recipe(self):
        directory, _, recipe = self.make_repo()
        recipe.write_text("recipe_id: guidellm-tp8\nmaturity: validated\n")
        git(directory, "add", ".")
        result = self.run_tool(directory, "--cached", "affected-recipes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"all": False, "recipes": ["models/glm/rhoai/3.5/recipes/h200-r1/guidellm-8k1k/tp8-aggregated"]},
        )

    def test_profile_correction_selects_referencing_recipe(self):
        directory, profile, _ = self.make_repo()
        profile.write_text(self.profile_text.format(
            revision=2,
            correction="  - revision: 2\n    summary: Corrected host memory documentation.\n",
        ))
        git(directory, "add", ".")
        result = self.run_tool(directory, "--cached", "affected-recipes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"all": False, "recipes": ["models/glm/rhoai/3.5/recipes/h200-r1/guidellm-8k1k/tp8-aggregated"]},
        )

    def test_staged_profile_correction_ignores_unstaged_recipe_edits(self):
        directory, profile, recipe = self.make_repo()
        profile.write_text(self.profile_text.format(
            revision=2,
            correction="  - revision: 2\n    summary: Corrected host memory documentation.\n",
        ))
        git(directory, "add", "hardware-profiles/h200-r1.yaml")
        recipe.write_text("recipe_id: guidellm-tp8\n")
        result = self.run_tool(directory, "--cached", "affected-recipes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"all": False, "recipes": ["models/glm/rhoai/3.5/recipes/h200-r1/guidellm-8k1k/tp8-aggregated"]},
        )

    def test_validator_reports_invalid_document_shapes_without_a_traceback(self):
        directory, profile, _ = self.make_repo()
        shutil.copytree(REPO / "schema", directory / "schema")
        profile.write_text("profile_id: h200-r1\n")
        result_path = directory / "models" / "glm" / "results" / "run-1" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("[]\n")
        result = self.run_validator(directory, "--current")
        self.assertEqual(result.returncode, 1)
        self.assertIn("profile_revision", result.stderr)
        self.assertIn("is not of type 'object'", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_recipe_schema_requires_canonical_deployment_scope(self):
        recipe = self.sample_recipe()
        validator = self.recipe_validator()
        self.assertFalse(list(validator.iter_errors(recipe)))

        missing_scope = {**recipe, "deployment": {"components": recipe["deployment"]["components"]}}
        self.assertTrue(list(validator.iter_errors(missing_scope)))

        missing_components = {**recipe, "deployment": {"scope": "single-node"}}
        self.assertTrue(list(validator.iter_errors(missing_components)))

        legacy_containers = {**recipe, "deployment": {**recipe["deployment"], "containers": {}}}
        self.assertTrue(list(validator.iter_errors(legacy_containers)))

        legacy_nodes = {**recipe, "match": {"nodes": "single"}}
        self.assertTrue(list(validator.iter_errors(legacy_nodes)))

        custom_intent = {**recipe, "optimization_intent": "lowest cost at 128K context"}
        self.assertFalse(list(validator.iter_errors(custom_intent)))

    def test_recipe_schema_accepts_component_examples(self):
        validator = self.recipe_validator()
        recipe = self.sample_recipe()
        for path in sorted((REPO / "schema" / "examples").glob("*.components.yaml")):
            with self.subTest(example=path.name):
                recipe["deployment"]["components"] = yaml.safe_load(path.read_text())
                self.assertFalse(list(validator.iter_errors(recipe)))

    def test_reader_notes_example_matches_schema(self):
        schemas = [json.loads(path.read_text()) for path in (REPO / "schema").glob("*.schema.json")]
        registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema)) for schema in schemas
        )
        schema = next(schema for schema in schemas if schema["$id"].endswith("/recipe-notes.schema.json"))
        notes = yaml.safe_load((REPO / "schema" / "examples" / "recipe-notes.yaml").read_text())
        self.assertFalse(list(Draft202012Validator(schema, registry=registry).iter_errors(notes)))

    def test_unquoted_status_date_remains_a_schema_string(self):
        notes = load_unique_yaml("schema_version: 1\ndecisions:\n  - subjects: [image]\n    why: Verified in CI.\n    status: {state: verified, date: 2026-08-12, method: CI}\n")
        self.assertEqual(notes["decisions"][0]["status"]["date"], "2026-08-12")

    def test_recipe_schema_rejects_invalid_component_settings(self):
        validator = self.recipe_validator()
        recipe = self.sample_recipe()
        recipe["deployment"]["components"] = yaml.safe_load(
            (REPO / "schema" / "examples" / "lws-pd.components.yaml").read_text()
        )
        self.assertFalse(list(validator.iter_errors(recipe)))

        worker = recipe["deployment"]["components"]["prefill"]["worker"]["containers"]["vllm"]
        worker["args"] = ["serve", 8]
        self.assertTrue(list(validator.iter_errors(recipe)))

        worker["args"] = ["serve"]
        worker["env"] = [{"name": "TOKEN", "value": "inline", "value_from": {"secretKeyRef": {"name": "token", "key": "value"}}}]
        self.assertTrue(list(validator.iter_errors(recipe)))

        worker["env"] = [{"name": "TOKEN", "value": "inline"}]
        worker["resources"] = {"requests": {"nvidia.com/gpu": -1}}
        self.assertTrue(list(validator.iter_errors(recipe)))

        worker["resources"] = {"requests": {"nvidia.com/gpu": "8"}}
        worker["arg_choices"] = [{"flag": "--tensor-parallel-size", "why": "Matches GPU count."}]
        self.assertTrue(list(validator.iter_errors(recipe)))

        worker["arg_choices"][0]["required"] = True
        self.assertFalse(list(validator.iter_errors(recipe)))

        worker["arg_choices"] = []
        self.assertFalse(list(validator.iter_errors(recipe)))

        recipe["deployment"]["components"]["prefill"]["kind"] = "Deployment"
        self.assertTrue(list(validator.iter_errors(recipe)))

    def test_validator_checks_component_source_and_container_names(self):
        directory, profile, recipe_path = self.make_repo()
        shutil.copytree(REPO / "schema", directory / "schema")
        profile.write_text(self.profile_text.format(revision=1, correction="").replace(
            "profile_id: h200-r1", "profile_id: h200-r1\nkind: hardware-profile"
        ))
        recipe = self.sample_recipe()
        recipe["deployment"]["components"] = {
            "modelserver": {
                "kind": "Deployment",
                "source": "config/modelserver.yaml",
                "containers": {"vllm": {"args": ["serve", "model"]}},
            }
        }
        recipe_path.write_text(yaml.safe_dump(recipe))
        source = recipe_path.parent / "config" / "modelserver.yaml"
        source.parent.mkdir()
        source.write_text(yaml.safe_dump({
            "kind": "Deployment",
            "spec": {"template": {"spec": {"containers": [{"name": "vllm"}]}}},
        }))
        result = self.run_validator(directory, "--current")
        self.assertEqual(result.returncode, 0, result.stderr)

        recipe["deployment"]["components"]["modelserver"]["containers"] = {"missing": {"args": ["serve"]}}
        recipe_path.write_text(yaml.safe_dump(recipe))
        result = self.run_validator(directory, "--current")
        self.assertIn("not in its source manifest", result.stderr)

        recipe["deployment"]["components"]["modelserver"]["containers"] = {"vllm": {"args": ["serve"]}}
        recipe["deployment"]["components"]["modelserver"]["source"] = "config/absent.yaml"
        recipe_path.write_text(yaml.safe_dump(recipe))
        result = self.run_validator(directory, "--current")
        self.assertIn("source does not exist", result.stderr)

    def test_validator_resolves_distinct_lws_leader_and_worker_roles(self):
        directory, profile, recipe_path = self.make_repo()
        shutil.copytree(REPO / "schema", directory / "schema")
        profile.write_text(self.profile_text.format(revision=1, correction="").replace(
            "profile_id: h200-r1", "profile_id: h200-r1\nkind: hardware-profile"
        ))
        recipe = self.sample_recipe()
        recipe["deployment"] = {
            "scope": "multi-node",
            "components": {
                "prefill": {
                    "kind": "LeaderWorkerSet",
                    "source": "config/prefill.yaml",
                    "leader": {"containers": {"vllm": {"args": ["serve", "--leader"]}}},
                    "worker": {"containers": {"vllm": {"args": ["serve", "--worker"]}}},
                },
            },
        }
        recipe_path.write_text(yaml.safe_dump(recipe))
        source = recipe_path.parent / "config" / "prefill.yaml"
        source.parent.mkdir()
        source.write_text(yaml.safe_dump({
            "kind": "LeaderWorkerSet",
            "spec": {"leaderWorkerTemplate": {
                "workerTemplate": {"spec": {"containers": [{"name": "vllm"}]}},
            }},
        }))
        result = self.run_validator(directory, "--current")
        self.assertEqual(result.returncode, 0, result.stderr)

        recipe["deployment"]["components"]["prefill"]["leader"]["containers"] = {"missing": {"args": ["serve"]}}
        recipe_path.write_text(yaml.safe_dump(recipe))
        result = self.run_validator(directory, "--current")
        self.assertIn("leader.containers.missing is not in its source manifest", result.stderr)

    def test_validator_checks_auxiliary_sources(self):
        directory, profile, recipe_path = self.make_repo()
        shutil.copytree(REPO / "schema", directory / "schema")
        profile.write_text(self.profile_text.format(revision=1, correction="").replace(
            "profile_id: h200-r1", "profile_id: h200-r1\nkind: hardware-profile"
        ))
        recipe = self.sample_recipe()
        recipe["deployment"]["auxiliary_sources"] = [{"path": "config/service.yaml", "kind": "Service"}]
        recipe_path.write_text(yaml.safe_dump(recipe))
        config = recipe_path.parent / "config"
        config.mkdir()
        (config / "modelserver.yaml").write_text("kind: Deployment\n")
        (config / "service.yaml").write_text("kind: Service\n")
        result = self.run_validator(directory, "--current")
        self.assertEqual(result.returncode, 0, result.stderr)

        (config / "service.yaml").write_text("kind: ConfigMap\n")
        result = self.run_validator(directory, "--current")
        self.assertIn("auxiliary source config/service.yaml kind must be Service", result.stderr)

        (config / "service.yaml").unlink()
        result = self.run_validator(directory, "--current")
        self.assertIn("auxiliary source does not exist", result.stderr)

    def test_raw_only_leaf_accepts_multidoc_yaml_and_rejects_duplicates(self):
        directory, profile, recipe_path = self.make_repo()
        shutil.copytree(REPO / "schema", directory / "schema")
        profile.write_text(self.profile_text.format(revision=1, correction="").replace(
            "profile_id: h200-r1", "profile_id: h200-r1\nkind: hardware-profile"
        ))
        recipe_path.unlink()
        raw = recipe_path.parent / "raw-manifest" / "deployment.yaml"
        raw.parent.mkdir()
        raw.write_text("kind: Deployment\nmetadata: {name: example}\n---\nkind: Service\nmetadata: {name: example}\n")
        result = self.run_validator(directory, "--current")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_validator(directory, "--current", "--require-converted-raw")
        self.assertIn("maintainer conversion required before merge", result.stderr)

        raw.write_text("kind: Deployment\nkind: Service\n")
        result = self.run_validator(directory, "--current")
        self.assertIn("found duplicate key 'kind'", result.stderr)

        raw.write_text("kind: Deployment\n")
        recipe_path.write_text(yaml.safe_dump(self.sample_recipe()))
        config = recipe_path.parent / "config"
        config.mkdir()
        (config / "modelserver.yaml").write_text("kind: Deployment\n")
        result = self.run_validator(directory, "--current", "--require-converted-raw")
        self.assertEqual(result.returncode, 0, result.stderr)

        raw_json = raw.parent / "values.json"
        raw_json.write_text('{"kind": "ConfigMap", "metadata": {"name": "example"}}')
        recipe_path.unlink()
        result = self.run_validator(directory, "--current")
        self.assertEqual(result.returncode, 0, result.stderr)

        raw_json.write_text("kind: ConfigMap\n")
        result = self.run_validator(directory, "--current")
        self.assertIn("cannot parse raw manifest", result.stderr)

        wrong = recipe_path.parent.parent / "not-a-workload" / "tp8-aggregated" / "raw-manifest" / "deployment.yaml"
        wrong.parent.mkdir(parents=True)
        wrong.write_text("kind: Deployment\n")
        result = self.run_validator(directory, "--current")
        self.assertIn("raw-manifest must be under a Recipe v3 leaf directory", result.stderr)

    def test_validator_checks_reader_note_references(self):
        directory, profile, recipe_path = self.make_repo()
        shutil.copytree(REPO / "schema", directory / "schema")
        profile.write_text(self.profile_text.format(revision=1, correction="").replace(
            "profile_id: h200-r1", "profile_id: h200-r1\nkind: hardware-profile"
        ))
        recipe = self.sample_recipe()
        recipe["notes"] = "guides/notes.yaml"
        recipe_path.write_text(yaml.safe_dump(recipe))
        source = recipe_path.parent / "config" / "modelserver.yaml"
        source.parent.mkdir()
        source.write_text(yaml.safe_dump({"kind": "Deployment"}))
        notes_path = recipe_path.parent / "guides" / "notes.yaml"
        notes_path.parent.mkdir()
        notes = {
            "schema_version": 1,
            "profile": {"specs": [{"label": "Scope", "source": "recipe.yaml#/deployment/scope"}]},
            "decisions": [{"subjects": ["GPU count", "--tensor-parallel-size"], "why": "They must agree."}],
        }
        notes_path.write_text(yaml.safe_dump(notes))
        result = self.run_validator(directory, "--current")
        self.assertEqual(result.returncode, 0, result.stderr)

        notes["profile"]["specs"][0]["source"] = "recipe.yaml#/deployment/missing"
        notes_path.write_text(yaml.safe_dump(notes))
        result = self.run_validator(directory, "--current")
        self.assertIn("spec source does not resolve", result.stderr)

        notes["profile"]["specs"][0]["source"] = "recipe.yaml#/deployment/scope"
        notes["features"] = [{"name": "Tool calling", "status": "claimed"}]
        notes_path.write_text(yaml.safe_dump(notes))
        result = self.run_validator(directory, "--current")
        self.assertIn("features must be an object with known keys", result.stderr)
        del notes["features"]

        notes["image_choices"] = [{
            "component": "modelserver", "container": "vllm", "ref": "docker.io/vllm/vllm-openai:v0.24.0",
            "status": {"state": "needs-verification", "note": "Run pending"}, "why": "Model support",
        }]
        notes_path.write_text(yaml.safe_dump(notes))
        recipe["maturity"] = "validated"
        recipe["benchmark_runs"] = ["results/run-1/run.yaml"]
        run_path = recipe_path.parent / "results" / "run-1" / "run.yaml"
        run_path.parent.mkdir(parents=True)
        run_path.write_text(yaml.safe_dump({
            "schema_version": 1,
            "run_id": "run-1",
            "recipe_id": recipe["recipe_id"],
            "deployment_scope": "single-node",
            "hardware_profile": recipe["hardware_profile"],
            "hardware_profile_revision": 1,
            "harness": "guidellm",
            "result": "result.json",
            "artifacts": [{}],
        }))
        (run_path.parent / "result.json").write_text(json.dumps({
            "schema_version": 1,
            "run_id": "run-1",
            "deployment_scope": "single-node",
            "accelerator_key": "nvidia-h200-x8",
            "metrics": {},
        }))
        recipe_path.write_text(yaml.safe_dump(recipe))
        result = self.run_validator(directory, "--current")
        self.assertIn("cannot recommend an unverified image", result.stderr)

    def test_complete_day_zero_examples_preserve_source_manifests(self):
        examples = [
            (
                "models/gemma-4/vllm/v0.24.0/recipes/nvidia-h200-x8/guidellm-8k1k/mtp-single-gpu",
                "models/gemma-4/vllm/v0.24.0/single-node/manifests/gemma-4-26b-a4b-it-fp8-deployment.yaml",
                "config/modelserver.yaml", "Deployment",
            ),
            (
                "models/glm-5.2/rhoai/3.5/recipes/ibmcloud-h200-gx3d-160x1792x8h200/aiperf-agentx-128k/pp2-tp8",
                "models/glm-5.2/rhoai/3.5/multi-node-pp/manifests/pp2-tp8-llmisvc.yaml",
                "config/llmisvc.yaml", "LLMInferenceService",
            ),
            (
                "models/glm-5.2/vllm/v0.23.0/recipes/ibmcloud-h200-gx3d-160x1792x8h200/aiperf-agentx-128k/pp2-tp8",
                "models/glm-5.2/vllm/v0.23.0/multi-node-lws/manifests/pp2-tp8-lws.yaml",
                "config/lws.yaml", "LeaderWorkerSet",
            ),
        ]
        for directory, original, source, kind in examples:
            with self.subTest(kind=kind):
                root = REPO / directory
                recipe = yaml.safe_load((root / "recipe.yaml").read_text())
                component = recipe["deployment"]["components"]["modelserver"]
                self.assertEqual(recipe["maturity"], "day-zero")
                self.assertNotIn("benchmark_runs", recipe)
                self.assertEqual(component["kind"], kind)
                self.assertEqual(component["source"], source)
                if kind == "Deployment":
                    roles = [component["containers"]]
                elif kind == "LLMInferenceService":
                    roles = [component["template"]["containers"], component["worker"]["containers"]]
                else:
                    roles = [component["leader"]["containers"], component["worker"]["containers"]]
                for containers in roles:
                    self.assertTrue(any(runtime.get("arg_choices") for runtime in containers.values()))
                self.assertTrue((root / recipe["notes"]).is_file())
                self.assertTrue((REPO / "models" / recipe["model_id"] / "model.yaml").is_file())
                for auxiliary in recipe["deployment"].get("auxiliary_sources", []):
                    self.assertEqual(yaml.safe_load((root / auxiliary["path"]).read_text())["kind"], auxiliary["kind"])
                original_documents = list(yaml.safe_load_all((REPO / original).read_text()))
                self.assertEqual(yaml.safe_load((root / source).read_text()), original_documents[0])
                if kind == "LeaderWorkerSet":
                    self.assertEqual(yaml.safe_load((root / "config/service.yaml").read_text()), original_documents[1])

        llmisvc_root = REPO / examples[1][0]
        prerequisite = "models/glm-5.2/rhoai/3.5/prerequisites/pp-worker-llmisvcconfig.yaml"
        self.assertEqual(
            yaml.safe_load((llmisvc_root / "config/pp-worker-llmisvcconfig.yaml").read_text()),
            yaml.safe_load((REPO / prerequisite).read_text()),
        )


if __name__ == "__main__":
    unittest.main()
