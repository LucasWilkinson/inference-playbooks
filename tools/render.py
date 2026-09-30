#!/usr/bin/env python3
"""Render Kubernetes manifests from Recipe v4 serving blocks.

Pipeline:
  recipe.yaml + model.yaml + hardware-profile + flag-constraints
    -> resolve constraints
    -> resolve role args
    -> select template (platform.stack, parallelism.mode)
    -> render Jinja2
    -> optional kustomize overlay
    -> manifests/
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / "templates"

sys.path.insert(0, str(REPO_ROOT / "tools"))
from constraints import load_constraints, validate_recipe_against_constraints
from recipe_evidence import load_unique_yaml
from validate import resolve_role_args


TEMPLATE_MAP: dict[tuple[str, str], str] = {
    ("vllm", "tp"): "vllm/deployment.yaml.j2",
    ("vllm", "dp"): "vllm/deployment.yaml.j2",
    ("vllm", "tp+dp"): "vllm/deployment.yaml.j2",
    ("vllm", "pp"): "vllm/lws.yaml.j2",
    ("vllm", "tp+pp"): "vllm/lws.yaml.j2",
    ("rhoai", "tp"): "rhoai/llmisvc.yaml.j2",
    ("rhoai", "pp"): "rhoai/llmisvc-pp.yaml.j2",
    ("rhoai", "tp+pp"): "rhoai/llmisvc-pp.yaml.j2",
}

COMPONENT_KIND: dict[tuple[str, str], str] = {
    ("vllm", "tp"): "Deployment",
    ("vllm", "dp"): "Deployment",
    ("vllm", "tp+dp"): "Deployment",
    ("vllm", "pp"): "LeaderWorkerSet",
    ("vllm", "tp+pp"): "LeaderWorkerSet",
    ("rhoai", "tp"): "LLMInferenceService",
    ("rhoai", "pp"): "LLMInferenceService",
    ("rhoai", "tp+pp"): "LLMInferenceService",
}


def load_yaml_file(path: Path) -> dict:
    text = path.read_text()
    data = load_unique_yaml(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected YAML mapping")
    return data


def sanitize_name(recipe_id: str) -> str:
    """Convert recipe_id to a valid Kubernetes resource name."""
    return re.sub(r"[^a-z0-9-]", "-", recipe_id.lower())[:63]


def select_template(stack: str, mode: str) -> str:
    """Select template path from (platform.stack, parallelism.mode)."""
    key = (stack, mode)
    if key not in TEMPLATE_MAP:
        raise ValueError(
            f"No template for platform={stack}, mode={mode}. "
            f"Supported: {sorted(TEMPLATE_MAP.keys())}"
        )
    return TEMPLATE_MAP[key]


def build_template_context(
    recipe: dict,
    model: dict,
    constraints: dict | None,
) -> dict:
    """Build template rendering context from recipe + model + constraints."""
    serving = recipe["serving"]
    parallelism = serving["parallelism"]
    stack = recipe["platform"]["stack"]
    mode = parallelism["mode"]

    tp = parallelism.get("tp", 1)
    pp = parallelism.get("pp", 1)
    dp = parallelism.get("dp", 1)

    resources = serving.get("resources", {})
    if "requests" not in resources:
        resources["requests"] = {}
    if "limits" not in resources:
        resources["limits"] = {}

    env = serving.get("env", [])
    port = serving.get("port", 8000)
    name = sanitize_name(recipe["recipe_id"])

    role_name = "decode"
    kind = COMPONENT_KIND.get((stack, mode), "Deployment")

    if kind == "LeaderWorkerSet" or (stack == "rhoai" and pp > 1):
        leader_args = resolve_role_args(serving, role_name, "leader")
        worker_args = resolve_role_args(serving, role_name, "worker")
        args = resolve_role_args(serving, role_name, "leader")
    else:
        args = resolve_role_args(serving, role_name, "leader")
        leader_args = args
        worker_args = args

    replicas = dp if dp > 1 else 1

    return {
        "name": name,
        "image": serving["image"],
        "model": serving["model"],
        "tp": tp,
        "pp": pp,
        "dp": dp,
        "gpu_count": tp,
        "replicas": replicas,
        "args": args,
        "leader_args": leader_args,
        "worker_args": worker_args,
        "env": env,
        "port": port,
        "resources": resources,
        "recipe_id": recipe["recipe_id"],
        "kind": kind,
        "stack": stack,
        "mode": mode,
        "router": serving.get("router", {}),
    }


def render_template(template_path: str, context: dict) -> str:
    """Render a Jinja2 template with the given context."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(template_path)
    return template.render(**context)


def run_kustomize(base_manifest: str, recipe_dir: Path) -> str:
    """Run kustomize build with config/ as overlay over generated base."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir) / "base"
        base_dir.mkdir()
        base_file = base_dir / "manifest.yaml"
        base_file.write_text(base_manifest)

        overlay_dir = Path(tmpdir) / "overlay"
        overlay_dir.mkdir()

        config_dir = recipe_dir / "config"
        for item in config_dir.iterdir():
            if item.name == "kustomization.yaml":
                kustomization = yaml.safe_load(item.read_text()) or {}
                resources = kustomization.get("resources", [])
                new_resources = []
                for res in resources:
                    if "../manifests/" in res or res.startswith("../manifests"):
                        new_resources.append(str(base_file))
                    else:
                        src = config_dir / res
                        if src.is_file():
                            dest = overlay_dir / res
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, dest)
                            new_resources.append(res)
                        else:
                            new_resources.append(res)
                kustomization["resources"] = new_resources
                if not new_resources:
                    kustomization["resources"] = [str(base_file)]
                (overlay_dir / "kustomization.yaml").write_text(
                    yaml.dump(kustomization, default_flow_style=False)
                )
            else:
                dest = overlay_dir / item.name
                shutil.copy2(item, dest)

        if not (overlay_dir / "kustomization.yaml").is_file():
            kustomization = {"resources": [str(base_file)]}
            (overlay_dir / "kustomization.yaml").write_text(
                yaml.dump(kustomization, default_flow_style=False)
            )

        result = subprocess.run(
            ["kustomize", "build", str(overlay_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"kustomize build failed:\n{result.stderr}"
            )
        return result.stdout


def render_recipe(
    repo: Path,
    recipe_path: Path,
    dry_run: bool = False,
) -> tuple[str, list[str]]:
    """Render a single v4 recipe.

    Returns (rendered_yaml, errors).
    Errors are non-empty if rendering cannot proceed.
    """
    errors: list[str] = []
    recipe = load_yaml_file(recipe_path)

    if recipe.get("schema_version") != 4:
        return "", [f"{recipe_path}: not a v4 recipe (schema_version={recipe.get('schema_version')})"]

    serving = recipe.get("serving")
    if not isinstance(serving, dict):
        return "", [f"{recipe_path}: missing serving block"]

    model_path = repo / "models" / recipe.get("model_id", "") / "model.yaml"
    if model_path.is_file():
        model = load_yaml_file(model_path)
    else:
        model = {}

    try:
        constraints = load_constraints(repo)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"flag-constraints: {exc}")
        constraints = None

    if constraints:
        constraint_errors = validate_recipe_against_constraints(
            constraints, recipe, model
        )
        if constraint_errors:
            errors.extend(f"{recipe_path}: {e}" for e in constraint_errors)
            return "", errors

    stack = recipe.get("platform", {}).get("stack", "")
    mode = serving.get("parallelism", {}).get("mode", "")

    try:
        template_path = select_template(stack, mode)
    except ValueError as exc:
        return "", [str(exc)]

    context = build_template_context(recipe, model, constraints)
    rendered = render_template(template_path, context)

    if serving.get("config_overrides") is True:
        config_dir = recipe_path.parent / "config"
        kustomization = config_dir / "kustomization.yaml"
        if kustomization.is_file():
            try:
                rendered = run_kustomize(rendered, recipe_path.parent)
            except (RuntimeError, OSError) as exc:
                errors.append(f"{recipe_path}: kustomize failed: {exc}")
                return "", errors
        else:
            errors.append(
                f"{recipe_path}: config_overrides is true but config/kustomization.yaml missing"
            )
            return "", errors

    if not dry_run:
        manifest_dir = recipe_path.parent / "manifests"
        manifest_dir.mkdir(exist_ok=True)
        kind = context["kind"]
        filename = f"{kind.lower()}.yaml"
        output_path = manifest_dir / filename
        output_path.write_text(rendered)
        print(f"  wrote {output_path.relative_to(repo)}")

    return rendered, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "recipes",
        nargs="*",
        type=Path,
        help="Recipe paths to render (default: all v4 recipes)",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render to stdout without writing files",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()

    if args.recipes:
        recipe_paths = [p.resolve() for p in args.recipes]
    else:
        recipe_paths = sorted(
            repo.glob("models/**/recipes/*/*/*/recipe.yaml")
        )

    all_errors: list[str] = []
    rendered_count = 0

    for recipe_path in recipe_paths:
        try:
            recipe = load_yaml_file(recipe_path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            all_errors.append(f"{recipe_path}: {exc}")
            continue

        if recipe.get("schema_version") != 4:
            continue

        print(f"Rendering {recipe_path.relative_to(repo)}...")
        rendered, errors = render_recipe(repo, recipe_path, dry_run=args.dry_run)

        if errors:
            all_errors.extend(errors)
        elif rendered:
            rendered_count += 1
            if args.dry_run:
                print(rendered)

    if all_errors:
        print("\nErrors:", file=sys.stderr)
        for err in all_errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    print(f"\nRendered {rendered_count} recipe(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
