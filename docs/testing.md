# BaseTool runner testing

Test infrastructure for the MHDDoS upstream integration. **Phase 3** adds
stability smoke, release artifact verification, and downstream staging
simulation on top of the Phase 2 cutover gates.

Architecture context: [docs/architecture.md](architecture.md).

## Setup

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

## Phase 3 gate (release check)

```bash
pytest tests/patches/
pytest tests/unit/
python scripts/smoke/runner-methods-smoke.py
python scripts/smoke/runner-regression-smoke.py
python scripts/smoke/runner-stability-smoke.py --duration 15
python scripts/release/build-release-artifact.py
python scripts/release/verify-release-artifact.py
python scripts/release/simulate-downstream-stage.py
BASETOOL_JSON=1 python basetool.py --help
```

## Phase 2 gate (full cutover check)

```bash
pytest tests/patches/
pytest tests/unit/
python scripts/smoke/runner-methods-smoke.py
python scripts/smoke/runner-regression-smoke.py
python -c "from modules.basetool.adapter import METHOD_REGISTRY; print(sorted(METHOD_REGISTRY))"
python scripts/sync-mhddos-upstream.py --tag 2.4.4 --no-smoke --skip-subtree
```

## Patch tests

```bash
pytest tests/patches/test_patches_apply.py -q
pytest tests/patches/test_import_side_effects.py -q
```

## Unit tests

```bash
pytest tests/unit/ -q --cov=modules/basetool/adapter --cov=modules/basetool/runner --cov-report=term-missing --cov-fail-under=80
```

Coverage gate (≥ 80 % on adapter + runner) applies to unit tests only; patch
and smoke steps run without the global coverage threshold.

## Smoke tests

Per-method localhost traffic (SYN skipped on Windows / non-root):

```bash
python scripts/smoke/runner-methods-smoke.py
```

Regression snapshot parity against `tests/fixtures/regression-snapshot-pre.txt`:

```bash
python scripts/smoke/runner-regression-smoke.py
```

Regression smoke stages `tests/fixtures/minimal-config.json` into a temp dir
and sets `BASETOOL_RUNTIME_DIR` so the runner under test does not depend on the
repo's default `config.json`.

Stability smoke (long-run leak / thread drift check):

```bash
python scripts/smoke/runner-stability-smoke.py
python scripts/smoke/runner-stability-smoke.py --method GET --duration 15
```

## Release verification

Build a runner tarball and checksum:

```bash
python scripts/release/build-release-artifact.py
python scripts/release/build-release-artifact.py --from-tag
```

Verify the packed artifact and run per-method smoke against the extracted tree:

```bash
python scripts/release/verify-release-artifact.py
python scripts/release/verify-release-artifact.py dist/basetool-runner-dev-<sha>.tar.gz
```

Simulate downstream staging and auto-update consumption:

```bash
python scripts/release/simulate-downstream-stage.py
```

On Linux/macOS the simulator includes a SIGTERM clean-shutdown check. On Windows
that check is skipped because `TerminateProcess` does not invoke Python signal
handlers; release CI runs the full check on `ubuntu-latest`.

## Test pyramid

| Layer | Location | When it runs | Phase |
|-------|----------|--------------|-------|
| Patch integrity | `tests/patches/` | Every push/PR (CI) | **1 — live** |
| Unit | `tests/unit/` | Every push/PR (CI) | **2 — live** |
| Methods smoke | `scripts/smoke/runner-methods-smoke.py` | Every push/PR (CI) | **2 — live** |
| Regression snapshot | `scripts/smoke/runner-regression-smoke.py` | Every push/PR (CI) | **2 — live** |
| Stability / leak | `scripts/smoke/runner-stability-smoke.py` | Nightly + tag push | **3 — live** |
| Release artifact | `scripts/release/verify-release-artifact.py` | Tag push | **3 — live** |
| Downstream stage | `scripts/release/simulate-downstream-stage.py` | Tag push | **3 — live** |

## CI

Workflows:

- [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) — patch, unit, and smoke on every push/PR
- [`.github/workflows/nightly.yml`](../.github/workflows/nightly.yml) — daily stability smoke
- [`.github/workflows/release.yml`](../.github/workflows/release.yml) — tag push release gate

`ci.yml` matrix: Ubuntu + Windows × Python 3.11 + 3.12. Each cell runs patch
tests, unit tests, methods smoke, and regression smoke.

`release.yml` runs the full CI matrix, stability smoke, build, verify,
downstream simulation, and publishes a GitHub release. Tags containing `smoke`
are published as prereleases.

## Maintenance commands

Regenerate upstream patch files:

```bash
python scripts/dev/generate-upstream-patches.py
```

Re-apply patches to the vendored tree:

```bash
git apply --directory=modules/basetool/upstream/mhddos modules/basetool/upstream/patches/*.patch
```

Refresh upstream manifest without subtree pull:

```bash
python scripts/sync-mhddos-upstream.py --tag 2.4.4 --no-smoke --skip-subtree
```

## Fixtures

`tests/conftest.py` provides:

- `repo_root`, `patch_dir`, `upstream_tag`
- `tmp_config` — minimal `config.json` builder
- `localhost_http_server`, `localhost_tcp_echo`, `localhost_udp_echo`
- `mock_proxy_pool` — short-circuits proxy cache for unit tests

Regression fixtures under `tests/fixtures/`:

- `minimal-config.json` — single localhost GET target, 4 threads
- `regression-input.txt` — console commands (`stop`, `exit`)
- `regression-snapshot-pre.txt` — captured pre-merge reference output
