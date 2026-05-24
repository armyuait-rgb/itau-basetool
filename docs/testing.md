# BaseTool runner testing

Test infrastructure for the MHDDoS upstream integration. **Phase 1** covers
patch integrity and import safety only; unit and smoke layers arrive in
Phases 2 and 3.

Architecture context: [docs/architecture.md](architecture.md).

## Setup

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

Runtime deps include upstream import requirements (`cloudscraper`, `certifi`,
`dnspython`, `psutil`, `icmplib`, `pyasn1`) added in Phase 1 so the import
side-effects test can load the vendored `start.py`.

## Phase 1 commands

Run the full Phase 1 gate:

```bash
pytest tests/patches/
```

Individual suites:

```bash
# Patches apply cleanly to a fresh checkout of MHDDoS tag 2.4.4
pytest tests/patches/test_patches_apply.py -q

# Importing upstream start.py has no signal/argv/socket side effects
pytest tests/patches/test_import_side_effects.py -q
```

Verify runtime entrypoint is untouched (Phase 1 invariant):

```bash
git diff main -- basetool.py
```

Regenerate upstream patch files after editing the patch generator or upstream
layout:

```bash
python scripts/dev/generate-upstream-patches.py
```

Re-apply patches to the vendored tree:

```bash
git apply --directory=modules/basetool/upstream/mhddos modules/basetool/upstream/patches/*.patch
```

## Test pyramid (planned)

| Layer | Location | When it runs | Phase |
|-------|----------|--------------|-------|
| Patch integrity | `tests/patches/` | Every push/PR (CI) | **1 — live** |
| Unit | `tests/unit/` | Every push/PR (CI) | 2 |
| Methods smoke | `scripts/smoke/runner-methods-smoke.py` | Every push/PR (CI) | 2 |
| Regression snapshot | `scripts/smoke/runner-regression-smoke.py` | Every push/PR (CI) | 2 |
| Stability / leak | `scripts/smoke/runner-stability-smoke.py` | Nightly | 3 |
| Release artifact | `scripts/release/verify-release-artifact.py` | Tag push | 3 |
| Downstream stage | `scripts/release/simulate-downstream-stage.py` | Tag push | 3 |

## CI

Workflow: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

Phase 1 matrix: Ubuntu + Windows × Python 3.11 + 3.12, running
`pytest tests/patches/` only.

Watch runs:
<https://github.com/armyuait-rgb/itau-basetool/actions?query=branch%3Afeature%2Fmhddos-upstream-integration>

## Coverage

Phase 1 `pyproject.toml` configures pytest discovery only (`addopts = "-q"`).
The `--cov-fail-under=80` gate on `modules/basetool/adapter/` and
`modules/basetool/runner/` activates in Phase 2 when those modules and
`tests/unit/` exist.

## Fixtures (Phase 1)

`tests/conftest.py` provides:

- `repo_root` — repository root path
- `patch_dir` — `modules/basetool/upstream/patches/`
- `upstream_tag` — pinned tag `2.4.4`
- `tmp_config` — minimal `config.json` builder (used by Phase 2 smokes)

Additional fixtures (`localhost_http_server`, `mock_proxy_pool`, etc.) land
with Phase 2 unit and smoke work.
