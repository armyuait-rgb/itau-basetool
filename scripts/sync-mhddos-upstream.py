#!/usr/bin/env python3
"""Sync vendored MHDDoS upstream, apply patches, and refresh UPSTREAM.json."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_DIR = REPO_ROOT / "modules/basetool/upstream/mhddos"
PATCH_DIR = REPO_ROOT / "modules/basetool/upstream/patches"
START_PATH = UPSTREAM_DIR / "start.py"
MANIFEST_PATH = REPO_ROOT / "modules/basetool/UPSTREAM.json"
ADAPTER_PATH = REPO_ROOT / "modules/basetool/adapter/methods.py"
UPSTREAM_REPO = "https://github.com/MatrixTM/MHDDoS.git"
DEFAULT_TAG = "2.4.4"
DEFAULT_SHA = "fa57712c70071b8a79d49398fc80b7259ff1a68b"


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd or REPO_ROOT, check=False, text=True, capture_output=True)


def parse_upstream_methods(source: str) -> list[str]:
    methods: set[str] = set()
    class_names = ("Layer4", "HttpFlood")
    for class_name in class_names:
        match = re.search(rf"class {class_name}\(.*?\n(?P<body>.*?)(?=\nclass |\Z)", source, re.S)
        if not match:
            continue
        body = match.group("body")
        for name in re.findall(r"^\s+def ([A-Z][A-Z0-9_-]*)\(", body, re.M):
            methods.add(name)
        dict_match = re.search(r"self\.methods\s*=\s*\{(?P<entries>.*?)\}", body, re.S)
        if dict_match:
            for key in re.findall(r'"([A-Z][A-Z0-9_-]*)"', dict_match.group("entries")):
                methods.add(key)
    return sorted(methods)


def parse_registry_methods(source: str) -> list[str]:
    tree = ast.parse(source)
    for node in tree.body:
        registry = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "METHOD_REGISTRY":
                    registry = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "METHOD_REGISTRY":
                registry = node.value
        if registry is None:
            continue
        if isinstance(registry, ast.Dict):
            names = []
            for key in registry.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    names.append(key.value)
            return sorted(names)
    raise RuntimeError("METHOD_REGISTRY not found in adapter/methods.py")


def read_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {}


def compute_drift(previous: dict, upstream_methods: list[str], registry_methods: list[str]) -> dict:
    prev_upstream = set(previous.get("upstream_methods", []))
    curr_upstream = set(upstream_methods)
    registry = set(registry_methods)
    return {
        "upstream_added": sorted(curr_upstream - prev_upstream),
        "upstream_removed": sorted(prev_upstream - curr_upstream),
        "registry_orphans": sorted(name for name in registry if name not in curr_upstream),
        "allowlist_orphans": [],
    }


def write_manifest(tag: str, sha: str, upstream_methods: list[str], registry_methods: list[str]) -> dict:
    previous = read_manifest()
    manifest = {
        "repo": "https://github.com/MatrixTM/MHDDoS",
        "tag": tag,
        "sha": sha,
        "sync_date": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "patches": sorted(path.name for path in PATCH_DIR.glob("*.patch")),
        "upstream_methods": upstream_methods,
        "registry_methods": registry_methods,
        "drift": compute_drift(previous, upstream_methods, registry_methods),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def resolve_tag_sha(tag: str) -> str:
    result = _run(
        ["git", "ls-remote", UPSTREAM_REPO, f"refs/tags/{tag}"],
    )
    if result.returncode != 0 or not result.stdout.strip():
        return DEFAULT_SHA if tag == DEFAULT_TAG else ""
    return result.stdout.split()[0]


def apply_patches() -> None:
    for patch in sorted(PATCH_DIR.glob("*.patch")):
        result = _run(
            ["git", "apply", "--3way", f"--directory={UPSTREAM_DIR.relative_to(REPO_ROOT).as_posix()}", str(patch)],
        )
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            raise SystemExit(
                f"Patch {patch.name} failed to apply. Resolve conflicts in the vendored tree "
                f"or regenerate patches under {PATCH_DIR}."
            )


def subtree_pull(tag: str) -> None:
    prefix = UPSTREAM_DIR.relative_to(REPO_ROOT).as_posix()
    result = _run(
        ["git", "subtree", "pull", f"--prefix={prefix}", UPSTREAM_REPO, tag, "--squash"],
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"git subtree pull failed for tag {tag}")


def validate_registry_subset(upstream_methods: list[str], registry_methods: list[str]) -> None:
    upstream = set(upstream_methods)
    orphans = sorted(method for method in registry_methods if method not in upstream)
    if orphans:
        raise SystemExit(f"METHOD_REGISTRY contains methods missing upstream: {orphans}")


def run_methods_smoke() -> int:
    smoke = REPO_ROOT / "scripts/smoke/runner-methods-smoke.py"
    if not smoke.exists():
        print("runner-methods-smoke.py not found; skipping smoke step", file=sys.stderr)
        return 0
    result = _run([sys.executable, str(smoke)])
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=DEFAULT_TAG, help="MHDDoS tag to sync (default: 2.4.4)")
    parser.add_argument("--no-smoke", action="store_true", help="Skip per-method localhost smoke")
    parser.add_argument("--skip-subtree", action="store_true", help="Refresh manifest only")
    args = parser.parse_args()

    if not args.skip_subtree:
        subtree_pull(args.tag)
        apply_patches()

    upstream_methods = parse_upstream_methods(START_PATH.read_text(encoding="utf-8"))
    registry_methods = parse_registry_methods(ADAPTER_PATH.read_text(encoding="utf-8"))
    validate_registry_subset(upstream_methods, registry_methods)

    sha = resolve_tag_sha(args.tag) or DEFAULT_SHA
    manifest = write_manifest(args.tag, sha, upstream_methods, registry_methods)
    print(json.dumps(manifest, indent=2))

    if args.no_smoke:
        return 0
    return run_methods_smoke()


if __name__ == "__main__":
    raise SystemExit(main())
