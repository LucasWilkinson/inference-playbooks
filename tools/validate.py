#!/usr/bin/env python3
"""Validate playbook schemas, references, and corrigible hardware profiles."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from recipe_evidence import check_hardware_profiles, load_unique_yaml, load_unique_yaml_all


SCHEMAS = {
    "recipe": "recipe.schema.json",
    "recipe-notes": "recipe-notes.schema.json",
    "model": "model.schema.json",
    "hardware-profile": "hardware-profile.schema.json",
    "benchmark-run": "benchmark-run.schema.json",
    "benchmark-result": "benchmark-result.schema.json",
}


def load_yaml(path: Path) -> dict:
    """Load a YAML mapping from disk."""
    value = load_unique_yaml(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("expected a YAML object")
    return value


def load_schema(repo: Path, name: str) -> dict:
    """Load one named JSON Schema from the repository schema directory."""
    return json.loads((repo / "schema" / SCHEMAS[name]).read_text())


def load_schema_registry(repo: Path) -> Registry:
    """Register repository schemas locally so external $refs never need network access."""
    resources = []
    for path in sorted((repo / "schema").glob("*.schema.json")):
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def validate_document(path: Path, value: dict, schema: dict, registry: Registry | None = None) -> list[str]:
    """Return JSON Schema validation errors for one document."""
    validator = Draft202012Validator(schema, format_checker=FormatChecker(), registry=registry or Registry())
    return [
        f"{path}: {error.json_path or '$'}: {error.message}"
        for error in sorted(validator.iter_errors(value), key=lambda error: str(error.json_path))
    ]


def contained_path(root: Path, relative_path: object) -> Path | None:
    """Resolve a path only when it stays within the supplied root directory."""
    if not isinstance(relative_path, str):
        return None
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def nested_mapping(value: object, *keys: str) -> dict:
    """Return a nested mapping, or an empty mapping when a path is absent."""
    for key in keys:
        if not isinstance(value, dict):
            return {}
        value = value.get(key)
    return value if isinstance(value, dict) else {}


def validate_raw_manifest_intake(repo: Path, recipe_schema: dict, require_converted: bool = False) -> list[str]:
    """Check initial-release raw submissions without requiring a recipe yet."""
    errors = []
    properties = recipe_schema["properties"]
    for directory in sorted(repo.glob("models/**/raw-manifest")):
        if not directory.is_dir():
            continue
        parts = directory.relative_to(repo).parts
        if (
            len(parts) != 9 or parts[0] != "models" or parts[4] != "recipes"
            or parts[8] != "raw-manifest"
            or not re.fullmatch(properties["model_id"]["pattern"], parts[1])
            or parts[2] not in properties["platform"]["properties"]["stack"]["enum"]
            or not re.fullmatch(properties["platform"]["properties"]["version"]["pattern"], parts[3])
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]+", parts[5])
            or parts[6] not in properties["workload_profile"]["enum"]
            or not re.fullmatch(properties["deployment_mode"]["pattern"], parts[7])
        ):
            errors.append(f"{directory}: raw-manifest must be under a Recipe v3 leaf directory")
            continue
        if require_converted and not (directory.parent / "recipe.yaml").is_file():
            errors.append(f"{directory}: maintainer conversion required before merge: recipe.yaml is missing")
        manifest_count = 0
        for path in sorted(directory.rglob("*")):
            if path.is_dir():
                continue
            if not path.resolve().is_relative_to(repo.resolve()):
                errors.append(f"{path}: raw-manifest file escapes the repository")
                continue
            if path.suffix.lower() not in {".yaml", ".yml", ".json"}:
                if path.name != "README.md":
                    errors.append(f"{path}: raw-manifest accepts YAML/JSON and optional README.md only")
                continue
            manifest_count += 1
            try:
                text = path.read_text()
                if path.suffix.lower() == ".json":
                    documents = [json.loads(text)]
                    if not isinstance(documents[0], dict):
                        raise ValueError("JSON file must contain an object")
                else:
                    documents = [document for document in load_unique_yaml_all(text) if document is not None]
            except (OSError, UnicodeError, ValueError, yaml.YAMLError, json.JSONDecodeError) as error:
                errors.append(f"{path}: cannot parse raw manifest: {error}")
                continue
            if not documents or any(not isinstance(document, dict) for document in documents):
                errors.append(f"{path}: raw manifest must contain at least one YAML object document")
        if not manifest_count:
            errors.append(f"{directory}: raw-manifest needs at least one YAML or JSON file")
    return errors


def validate_component_containers(recipe_path: Path, name: str, component: dict, source: dict) -> list[str]:
    """Check that each labelled override names a container in its source pod."""
    kind = component["kind"]
    if kind == "Deployment":
        pod_paths = [(component, ("spec", "template", "spec"), "")]
    elif kind == "LLMInferenceService":
        pod_paths = [
            (component.get("template", {}), ("spec", "template"), "template"),
            (component.get("worker", {}), ("spec", "worker"), "worker"),
        ]
        prefill = component.get("prefill", {})
        pod_paths.extend([
            (prefill.get("template", {}), ("spec", "prefill", "template"), "prefill.template"),
            (prefill.get("worker", {}), ("spec", "prefill", "worker"), "prefill.worker"),
        ])
    elif kind == "LeaderWorkerSet":
        worker_path = ("spec", "leaderWorkerTemplate", "workerTemplate", "spec")
        leader_path = ("spec", "leaderWorkerTemplate", "leaderTemplate", "spec")
        if not nested_mapping(source, *leader_path):
            leader_path = worker_path  # LWS uses workerTemplate for the leader by default.
        pod_paths = [
            (component.get("leader", {}), leader_path, "leader"),
            (component.get("worker", {}), worker_path, "worker"),
        ]
    else:
        return []
    errors = []
    for override, pod_path, role in pod_paths:
        source_pod = nested_mapping(source, *pod_path)
        for field, source_field in (("containers", "containers"), ("init_containers", "initContainers")):
            for container_name in override.get(field, {}):
                names = {
                    container.get("name")
                    for container in source_pod.get(source_field, [])
                    if isinstance(container, dict)
                }
                if container_name not in names:
                    location = f"{role}.{field}" if role else field
                    errors.append(
                        f"{recipe_path}: component {name} {location}.{container_name} "
                        "is not in its source manifest"
                    )
    return errors


def resolve_spec_source(repo: Path, recipe_path: Path, recipe: dict, reference: str) -> bool:
    """Check a reader spec's file and JSON Pointer without copying its value."""
    source_name, separator, pointer = reference.partition("#")
    if not separator or not pointer.startswith("/"):
        return False
    if source_name == "recipe.yaml":
        value = recipe
    else:
        if source_name == "hardware_profile":
            source_path = contained_path(repo, recipe.get("hardware_profile"))
        elif source_name == "model.yaml":
            source_path = repo / "models" / recipe.get("model_id", "") / "model.yaml"
        elif source_name.startswith("config/"):
            source_path = contained_path(recipe_path.parent, source_name)
            if source_path and not source_path.is_relative_to((recipe_path.parent / "config").resolve()):
                return False
        else:
            return False
        if not source_path or not source_path.is_file():
            return False
        try:
            value = load_yaml(source_path)
        except (OSError, ValueError, yaml.YAMLError):
            return False
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return False
    return True


def validate_recipe_notes(repo: Path, recipe_path: Path, recipe: dict, schema: dict, registry: Registry) -> list[str]:
    """Validate the optional reader notes and their local references."""
    reference = recipe.get("notes")
    if reference is None:
        return []
    notes_path = contained_path(recipe_path.parent, reference)
    if not notes_path or not notes_path.is_relative_to((recipe_path.parent / "guides").resolve()):
        return [f"{recipe_path}: notes path escapes guides/: {reference}"]
    if not notes_path.is_file():
        return [f"{recipe_path}: notes file does not exist: {reference}"]
    try:
        notes = load_yaml(notes_path)
    except (OSError, ValueError, yaml.YAMLError) as error:
        return [f"{recipe_path}: cannot load notes: {error}"]
    if isinstance(notes.get("features"), list):
        return [f"{notes_path}: features must be an object with known keys"
                " (e.g. tool_calling, prefix_caching, reasoning_parser),"
                " not a list; see schema/examples/recipe-notes.yaml"]
    errors = validate_document(notes_path, notes, schema, registry)
    if errors:
        return errors
    for spec in notes.get("profile", {}).get("specs", []):
        if not resolve_spec_source(repo, recipe_path, recipe, spec["source"]):
            errors.append(f"{notes_path}: spec source does not resolve: {spec['source']}")
    for decision in notes.get("decisions", []):
        for evidence in decision.get("evidence", []):
            evidence_path = contained_path(recipe_path.parent, evidence)
            if not evidence_path or not evidence_path.is_relative_to((recipe_path.parent / "results").resolve()) or not evidence_path.is_file():
                errors.append(f"{notes_path}: decision evidence does not exist: {evidence}")
    components = recipe.get("deployment", {}).get("components", {})
    for image_choice in notes.get("image_choices", []):
        if image_choice["component"] not in components:
            errors.append(f"{notes_path}: image choice component does not exist: {image_choice['component']}")
        if recipe.get("maturity") in {"validated", "production"} and image_choice["status"]["state"] == "needs-verification":
            errors.append(f"{notes_path}: validated recipe cannot recommend an unverified image")
    return errors


def validate_recipe_layout(repo: Path, recipe_path: Path, recipe: dict, runs_by_path: dict[Path, dict]) -> list[str]:
    """Validate recipe layout, local references, and linked benchmark runs."""
    errors = []
    parts = recipe_path.relative_to(repo).parts
    # models/<model>/<stack>/<version>/recipes/<hardware>/<workload>/<mode>/recipe.yaml
    if len(parts) != 9 or parts[0] != "models" or parts[4] != "recipes":
        return [f"{recipe_path}: does not follow the model/stack/version/recipes layout"]
    model_id, stack, version = parts[1:4]
    hardware_selector, workload, deployment_mode = parts[5:8]
    if recipe.get("model_id") != model_id:
        errors.append(f"{recipe_path}: model_id '{recipe.get('model_id')}' must match its model directory '{model_id}'")
    model_yaml = repo / "models" / model_id / "model.yaml"
    if not model_yaml.is_file():
        errors.append(f"{recipe_path}: models/{model_id}/model.yaml does not exist")
    platform = recipe.get("platform", {})
    if not isinstance(platform, dict):
        platform = {}
    if platform.get("stack") != stack or platform.get("version") != version:
        errors.append(f"{recipe_path}: platform.stack/version '{platform.get('stack')}/{platform.get('version')}' must match its directory '{stack}/{version}'")
    if recipe.get("workload_profile") != workload:
        errors.append(f"{recipe_path}: workload_profile '{recipe.get('workload_profile')}' must match its directory segment '{workload}'")
    if recipe.get("deployment_mode") != deployment_mode:
        errors.append(f"{recipe_path}: deployment_mode '{recipe.get('deployment_mode')}' must match its directory segment '{deployment_mode}'")
    profile_path = contained_path(repo, recipe.get("hardware_profile"))
    if not profile_path or not profile_path.is_file():
        errors.append(f"{recipe_path}: hardware_profile does not exist: {recipe.get('hardware_profile')}")
    else:
        try:
            profile = load_yaml(profile_path)
            if hardware_selector not in {profile_path.stem, profile.get("accelerator_key")}:
                errors.append(f"{recipe_path}: hardware selector must match the profile ID or accelerator_key")
        except (OSError, ValueError, yaml.YAMLError) as error:
            errors.append(f"{recipe_path}: cannot load hardware_profile: {error}")
    run_references = recipe.get("benchmark_runs", [])
    if not isinstance(run_references, list):
        run_references = []
    for run_reference in run_references:
        run_path = contained_path(recipe_path.parent, run_reference)
        if not run_path:
            errors.append(f"{recipe_path}: benchmark run escapes the recipe directory: {run_reference}")
            continue
        if not run_path.is_file():
            errors.append(f"{recipe_path}: benchmark run does not exist: {run_reference}")
        elif run := runs_by_path.get(run_path):
            if run.get("recipe_id") != recipe.get("recipe_id"):
                errors.append(f"{recipe_path}: benchmark run recipe_id does not match the recipe")
            deployment = recipe.get("deployment", {})
            if isinstance(deployment, dict) and run.get("deployment_scope") != deployment.get("scope"):
                errors.append(f"{recipe_path}: benchmark run deployment_scope does not match deployment.scope")
        else:
            errors.append(f"{recipe_path}: benchmark run was not indexed: {run_reference}")
    deployment = recipe.get("deployment", {})
    if recipe.get("maturity") in {"validated", "production"} and deployment.get("status", {}).get("state") == "needs-verification":
        errors.append(f"{recipe_path}: validated recipe cannot have an unverified deployment")
    manifests = deployment.get("manifests", []) if isinstance(deployment, dict) else []
    if not isinstance(manifests, list):
        manifests = []
    for manifest in manifests:
        manifest_path = contained_path(recipe_path.parent, manifest.get("path") if isinstance(manifest, dict) else None)
        if not manifest_path:
            errors.append(f"{recipe_path}: manifest escapes the recipe directory")
            continue
        if not manifest_path.is_file():
            errors.append(f"{recipe_path}: manifest does not exist: {manifest.get('path')}")
    components = deployment.get("components", {}) if isinstance(deployment, dict) else {}
    if not isinstance(components, dict):
        components = {}
    for name, component in components.items():
        source_ref = component.get("values") if component.get("kind") == "llmd-router" else component.get("source")
        source_path = contained_path(recipe_path.parent, source_ref)
        if not source_path or not source_path.is_relative_to((recipe_path.parent / "config").resolve()):
            errors.append(f"{recipe_path}: component {name} source escapes config/: {source_ref}")
            continue
        if not source_path.is_file():
            errors.append(f"{recipe_path}: component {name} source does not exist: {source_ref}")
            continue
        try:
            source = load_yaml(source_path)
        except (OSError, ValueError, yaml.YAMLError) as error:
            errors.append(f"{recipe_path}: cannot load component {name} source: {error}")
            continue
        if component.get("kind") != "llmd-router" and source.get("kind") != component.get("kind"):
            errors.append(f"{recipe_path}: component {name} source kind must be {component.get('kind')}")
        elif component.get("kind") != "llmd-router":
            errors.extend(validate_component_containers(recipe_path, name, component, source))
    for auxiliary in deployment.get("auxiliary_sources", []):
        source_ref = auxiliary["path"]
        source_path = contained_path(recipe_path.parent, source_ref)
        if not source_path or not source_path.is_relative_to((recipe_path.parent / "config").resolve()):
            errors.append(f"{recipe_path}: auxiliary source escapes config/: {source_ref}")
            continue
        if not source_path.is_file():
            errors.append(f"{recipe_path}: auxiliary source does not exist: {source_ref}")
            continue
        try:
            source = load_yaml(source_path)
        except (OSError, ValueError, yaml.YAMLError) as error:
            errors.append(f"{recipe_path}: cannot load auxiliary source {source_ref}: {error}")
            continue
        if source.get("kind") != auxiliary["kind"]:
            errors.append(f"{recipe_path}: auxiliary source {source_ref} kind must be {auxiliary['kind']}")
    return errors


def validate_benchmark_run(repo: Path, path: Path, run: dict) -> tuple[list[str], Path | None]:
    """Validate one run's profile revision and normalized result reference."""
    errors = []
    profile_path = contained_path(repo, run.get("hardware_profile"))
    if not profile_path or not profile_path.is_file():
        return [f"{path}: hardware_profile does not exist"], None
    try:
        profile = load_yaml(profile_path)
    except (OSError, ValueError, yaml.YAMLError) as error:
        return [f"{path}: cannot load hardware_profile: {error}"], None
    profile_revision = profile.get("profile_revision")
    if isinstance(profile_revision, int) and run.get("hardware_profile_revision", 0) > profile_revision:
        errors.append(f"{path}: hardware_profile_revision is newer than the referenced profile")
    result_path = contained_path(path.parent, run.get("result"))
    if not result_path:
        errors.append(f"{path}: result escapes the run directory")
    elif not result_path.is_file():
        errors.append(f"{path}: normalized result does not exist: {run.get('result')}")
    return errors, result_path


def main() -> int:
    """Validate repository documents, references, and optional Git-diff rules."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--cached", action="store_true")
    parser.add_argument("--current", action="store_true", help="validate current files without a profile-diff comparison")
    parser.add_argument("--require-converted-raw", action="store_true", help="fail if a raw-manifest submission lacks recipe.yaml")
    arguments = parser.parse_args()
    if arguments.cached and arguments.current:
        parser.error("--cached and --current cannot be combined")
    if not arguments.cached and not arguments.current and (not arguments.base or not arguments.head):
        parser.error("pass --current, --cached, or both --base and --head")
    repo = arguments.repo.resolve()
    errors = []
    schemas = {name: load_schema(repo, name) for name in SCHEMAS}
    registry = load_schema_registry(repo)
    errors.extend(validate_raw_manifest_intake(repo, schemas["recipe"], arguments.require_converted_raw))

    if not arguments.current:
        base = arguments.base or "HEAD"
        head = arguments.head or "HEAD"
        errors.extend(
            [] if check_hardware_profiles(repo, base, head, arguments.cached) == 0
            else ["hardware profile correction validation failed"]
        )
    for path in sorted((repo / "hardware-profiles").glob("*.yaml")):
        try:
            profile = load_yaml(path)
        except (OSError, ValueError, yaml.YAMLError) as error:
            errors.append(f"{path}: cannot load YAML: {error}")
            continue
        errors.extend(validate_document(path, profile, schemas["hardware-profile"], registry))
    for path in sorted(repo.glob("models/**/model.yaml")):
        try:
            model = load_yaml(path)
        except (OSError, ValueError, yaml.YAMLError) as error:
            errors.append(f"{path}: cannot load YAML: {error}")
            continue
        errors.extend(validate_document(path, model, schemas["model"], registry))
    runs_by_id: dict[str, tuple[Path, dict]] = {}
    runs_by_path: dict[Path, dict] = {}
    expected_results: dict[Path, tuple[Path, dict]] = {}
    for path in sorted(repo.glob("models/**/results/**/run.yaml")):
        try:
            run = load_yaml(path)
        except (OSError, ValueError, yaml.YAMLError) as error:
            errors.append(f"{path}: cannot load YAML: {error}")
            continue
        document_errors = validate_document(path, run, schemas["benchmark-run"], registry)
        errors.extend(document_errors)
        if document_errors:
            continue
        run_id = run.get("run_id")
        if isinstance(run_id, str):
            if run_id in runs_by_id:
                errors.append(f"{path}: duplicate run_id also used by {runs_by_id[run_id][0]}")
            else:
                runs_by_id[run_id] = (path, run)
        runs_by_path[path.resolve()] = run
        run_errors, result_path = validate_benchmark_run(repo, path, run)
        errors.extend(run_errors)
        if result_path:
            if result_path in expected_results:
                errors.append(f"{path}: normalized result is already referenced by {expected_results[result_path][0]}")
            else:
                expected_results[result_path] = (path, run)
    recipe_ids: dict[str, Path] = {}
    for path in sorted(repo.glob("models/**/recipes/*/*/*/recipe.yaml")):
        try:
            recipe = load_yaml(path)
        except (OSError, ValueError, yaml.YAMLError) as error:
            errors.append(f"{path}: cannot load YAML: {error}")
            continue
        document_errors = validate_document(path, recipe, schemas["recipe"], registry)
        errors.extend(document_errors)
        if document_errors:
            continue
        rid = recipe.get("recipe_id")
        if isinstance(rid, str):
            if rid in recipe_ids:
                errors.append(f"{path}: duplicate recipe_id '{rid}' also used by {recipe_ids[rid]}")
            else:
                recipe_ids[rid] = path
        errors.extend(validate_recipe_layout(repo, path, recipe, runs_by_path))
        errors.extend(validate_recipe_notes(repo, path, recipe, schemas["recipe-notes"], registry))
    for path in sorted(repo.glob("models/**/results/**/result.json")):
        try:
            result = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"{path}: cannot load JSON: {error}")
            continue
        document_errors = validate_document(path, result, schemas["benchmark-result"], registry)
        errors.extend(document_errors)
        if document_errors:
            continue
        expected = expected_results.get(path.resolve())
        if not expected:
            errors.append(f"{path}: normalized result is not referenced by a benchmark run")
        elif result.get("run_id") != expected[1].get("run_id"):
            errors.append(f"{path}: run_id does not match its referencing benchmark run")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Validated schemas, references, and hardware-profile corrections.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
