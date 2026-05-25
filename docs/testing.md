# BaseTool runner testing

Test infrastructure for the MHDDoS upstream integration. **Phase 3** adds
stability smoke, release artifact verification, and downstream staging
simulation on top of the Phase 2 cutover gates.

Architecture context: [docs/architecture.md](architecture.md).

## Workability status

Keep this table current when the ship gate is re-run locally or on CI.

| Gate | Last result | Platform | Commit |
|---|---|---|---|
| Pytest (`tests/patches` … `tests/upstream`) | 61 passed, 1 skipped | Windows | `5889ff3` |
| Regression snapshot smoke | parity OK | Windows | `5889ff3` |
| Methods smoke | 8 PASS, SYN skip | Windows | `5889ff3` |
| Stability smoke (`--duration 15`) | all PASS, 0% RSS | Windows | `5889ff3` |
| Release verify | tarball + sha256 OK | Windows | `5889ff3` |
| Downstream stage simulation | OK | Windows | `5889ff3` |
| CI matrix (ubuntu/windows/macos) | pending | GitHub Actions | — |

Expected skips on Windows: `SYN` (raw sockets), unwritable runtime dir
(`tests/integration/test_config_matrix.py`).

What each layer guards:

- **Pytest packs** — adapter/runner units, config matrices, script CLI contracts,
  tarball layout, JSON telemetry schema, upstream manifest/requirements/canaries.
- **Methods smoke** — live localhost traffic for every registered attack method.
- **Regression snapshot** — normalized console output matches the golden fixture.
- **Stability smoke** — RSS and thread count stay flat over a timed run.
- **Release verify** — built tarball matches the golden manifest and runs method smoke
  from the extracted tree.
- **Downstream simulation** — extract, configure, run method matrix, graceful
  shutdown (SIGTERM on Linux/macOS, `terminate()` on Windows).

## Setup

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

## Extended test packs

Module orchestration, release contract, and upstream safety checks added beyond
the Phase 3 pyramid:

```bash
pytest tests/integration/ -q
pytest tests/orchestration/ -q
pytest tests/release/ -q
pytest tests/upstream/ -q
pytest tests/integration tests/orchestration tests/release tests/upstream -q
```

Integration subprocess coverage lives in `tests/integration/` for config
matrices, unreachable targets, and concurrent runners.

Release contract tests build a tarball locally before layout/schema checks:

```bash
python scripts/release/build-release-artifact.py
pytest tests/release/ -q
```

Weekly upstream drift monitoring runs via
[`.github/workflows/upstream-drift.yml`](../.github/workflows/upstream-drift.yml).

## Ship gate (full local release check)

Run this before tagging or merging release-hardening work:

```bash
python -m pytest tests/patches tests/unit tests/integration tests/orchestration tests/release tests/upstream -q
python scripts/smoke/runner-regression-smoke.py
python scripts/smoke/runner-methods-smoke.py
python scripts/smoke/runner-stability-smoke.py --duration 15
python scripts/release/build-release-artifact.py
python scripts/release/verify-release-artifact.py dist/basetool-runner-dev-<sha>.tar.gz
python scripts/release/simulate-downstream-stage.py dist/basetool-runner-dev-<sha>.tar.gz
```

Pass criteria:

- all commands exit `0`
- `SYN` is the only expected skip on Windows / non-root Linux
- stability reports `PASS` for all non-skipped methods
- downstream simulation prints `downstream stage simulation OK`

Notes:

- Pass an explicit archive path after `build-release-artifact.py`. When omitted,
  verify/simulate pick the newest `dist/basetool-runner-*.tar.gz` by mtime.
- Subprocess smokes that launch `basetool.py` require
  `BASETOOL_DEV_PLAINTEXT_CONFIGS=1` when staging plaintext `config.json`.
- Windows downstream simulation uses `psutil.Process.terminate()` instead of
  SIGTERM; exit code `0` is not required on Windows for the terminate scenario.

Local green run: Windows dev box, 2026-05-25, commit `5889ff3` — full ship gate
above exited `0` (61 pytest passed, 1 skipped; all smokes and release steps OK).
Update the [Workability status](#workability-status) table when re-running.

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

Verify the packed artifact and run per-method smoke against the extracted tree.
Prefer an explicit archive path for the tarball you just built:

```bash
python scripts/release/verify-release-artifact.py
python scripts/release/verify-release-artifact.py dist/basetool-runner-dev-<sha>.tar.gz
```

When no path is given, verify picks the newest `dist/basetool-runner-*.tar.gz`
by modification time.

Simulate downstream staging and auto-update consumption:

```bash
python scripts/release/simulate-downstream-stage.py
python scripts/release/simulate-downstream-stage.py dist/basetool-runner-dev-<sha>.tar.gz
```

On Linux/macOS the simulator sends SIGTERM to the runner process. On Windows it
uses `psutil.Process.terminate()` and only requires a timely exit with no orphan
children; release CI still runs the Linux SIGTERM path on `ubuntu-latest`.

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
| Integration packs | `tests/integration/` | Every push/PR (CI) | **extended** |
| Orchestration CLI | `tests/orchestration/` | Every push/PR (CI) | **extended** |
| Release contract | `tests/release/` | Every push/PR + tag push | **extended** |
| Upstream safety | `tests/upstream/` | Every push/PR (CI) | **extended** |
| Upstream drift | `.github/workflows/upstream-drift.yml` | Weekly | **extended** |
| Supply-chain audit | `pip-audit` in nightly | Daily (informational) | **extended** |

## CI

Workflows:

- [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) — patch, unit, and smoke on every push/PR
- [`.github/workflows/nightly.yml`](../.github/workflows/nightly.yml) — daily stability smoke
- [`.github/workflows/upstream-drift.yml`](../.github/workflows/upstream-drift.yml) — weekly MHDDoS release drift check
- [`.github/workflows/release.yml`](../.github/workflows/release.yml) — tag push release gate

`ci.yml` matrix: Ubuntu + Windows + macOS × Python 3.11 + 3.12. Each cell runs patch
tests, unit tests, methods smoke, regression smoke, and extended test packs
(`tests/integration`, `tests/orchestration`, `tests/release`, `tests/upstream`).

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
