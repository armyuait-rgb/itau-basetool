from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
START_PATH = REPO_ROOT / "modules/basetool/upstream/mhddos/start.py"
REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"

IMPORT_TO_PACKAGE = {
    "PyRoxy": "pyroxy",
    "cloudscraper": "cloudscraper",
    "dns": "dnspython",
    "icmplib": "icmplib",
    "impacket": "impacket",
    "psutil": "psutil",
    "requests": "requests",
    "yarl": "yarl",
    "certifi": "certifi",
}


def _parse_requirements(path: Path) -> set[str]:
    packages: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[<>=!\s@]", line, maxsplit=1)[0].lower()
        if name:
            packages.add(name)
    return packages


def _collect_import_roots(source: str) -> set[str]:
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_upstream_imports_are_declared_in_requirements():
    declared = _parse_requirements(REQUIREMENTS_PATH)
    imports = _collect_import_roots(START_PATH.read_text(encoding="utf-8"))
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    local = {"base64", "contextlib", "itertools", "logging", "math", "multiprocessing", "os", "pathlib", "re", "random", "socket", "ssl", "struct", "subprocess", "sys", "threading", "time", "typing", "urllib", "uuid", "json", "concurrent", "datetime"}

    missing = []
    for root in sorted(imports):
        if root in stdlib or root in local:
            continue
        package = IMPORT_TO_PACKAGE.get(root, root).lower()
        if package not in declared and root.lower() not in declared:
            missing.append(root)

    assert not missing, f"requirements.txt missing packages for imports: {missing}"
