# BaseTool runner architecture

This document tracks the MHDDoS upstream integration on branch
`feature/mhddos-upstream-integration`. **Phase 1 is complete**: the vendor
tree, patch layer, patch-test gates, and CI workflow are in place. Runtime
behaviour of the root [basetool.py](../basetool.py) entrypoint is unchanged.

See also [docs/testing.md](testing.md) for commands and [docs/sprints/2026-05-24-mhddos-upstream-integration.md](sprints/2026-05-24-mhddos-upstream-integration.md) for the full sprint plan.

## Current layout

```
itau-basetool/
├── basetool.py                         ← monolithic entrypoint (Phase 2 refactor)
├── config.json
├── proxy.json
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── THIRD_PARTY_NOTICES.md
├── modules/
│   └── basetool/
│       └── upstream/
│           ├── mhddos/                 # git subtree, tag 2.4.4 + patches applied
│           └── patches/
│               ├── 0001-stats-hook.patch
│               └── 0002-guard-main.patch
├── tests/
│   ├── conftest.py
│   └── patches/
├── scripts/
│   └── dev/
│       └── generate-upstream-patches.py
├── .github/workflows/
│   └── ci.yml                          # Phase 1: patch tests only
└── docs/
    ├── architecture.md                 ← this file
    └── testing.md
```

Phase 2 adds `modules/basetool/adapter/`, `modules/basetool/runner/`,
`modules/basetool/UPSTREAM.json`, and smoke scripts under `scripts/smoke/`.

## Upstream vendor policy

1. `modules/basetool/upstream/mhddos/` is vendored from
   [MatrixTM/MHDDoS](https://github.com/MatrixTM/MHDDoS) tag **`2.4.4`**
   (upstream publishes the tag without a `v` prefix).
2. The squash-subtree base matches upstream commit
   `fa57712c70071b8a79d49398fc80b7259ff1a68b`.
3. BaseTool-specific behaviour lives **outside** `upstream/`.
4. Local customisation is limited to the patch files under
   `modules/basetool/upstream/patches/`.
5. The vendored tree in the repo carries both patches applied. Patch files
   remain the source of truth for future syncs; regenerate with
   `scripts/dev/generate-upstream-patches.py` after upstream layout changes.

## Patch invariants

| Patch | Purpose |
|-------|---------|
| `0001-stats-hook.patch` | Adds `_raw_send` / `_raw_sendto` on upstream `Layer4` and `HttpFlood`. Attack methods route `Tools.send` / `Tools.sendto` through these hooks so the Phase 2 adapter can record per-target stats without forking attack logic. |
| `0002-guard-main.patch` | Defers `config.json` load and `__ip__` discovery to `_ensure_runtime_config()`, called from the CLI entry block, so importing upstream classes is side-effect free. |

**Implementation notes (Phase 1):**

- Upstream Layer 7 attacks live on class `HttpFlood`, not `Layer7`. Phase 2
  adapter imports must use the actual upstream class names.
- Upstream sends go through `Tools.send` / `Tools.sendto`; the stats-hook
  patch redirects those calls inside attack classes to instance hooks.

Patch integrity is enforced by `tests/patches/test_patches_apply.py`, which
shallow-fetches tag `2.4.4` and verifies every patch applies against upstream
`start.py` at the repository root.

To re-apply patches onto the vendored tree after a subtree sync:

```bash
git apply --directory=modules/basetool/upstream/mhddos modules/basetool/upstream/patches/*.patch
```

Import safety is enforced by `tests/patches/test_import_side_effects.py`, which
asserts that importing `modules.basetool.upstream.mhddos.start` does not install
non-default signal handlers, mutate `sys.argv`, or open sockets (after
pre-loading urllib3, which probes IPv6 support at import time).

## CI (Phase 1 scope)

Workflow: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

| Trigger | Branches |
|---------|----------|
| `push` | `main`, `feature/mhddos-upstream-integration` |
| `pull_request` | `main` |
| `workflow_dispatch` | any |

Matrix: **Ubuntu + Windows** × **Python 3.11 + 3.12**.

Jobs run `pytest tests/patches/` only. Unit and smoke steps from the full W8
spec land in Phase 2 when `tests/unit/` and `scripts/smoke/` exist.

Required checks before merge (Phase 1): all four matrix cells green on the
feature branch.

## Phase gates

| Phase | Status | Gate |
|-------|--------|------|
| 1 — Foundation | **Complete** on `feature/mhddos-upstream-integration` | `pytest tests/patches/` green; CI green on branch; `git diff main -- basetool.py` empty |
| 2 — Cutover | Planned | All Phase 1 gates plus unit and smoke parity |
| 3 — Release | Planned | Verified tarball and downstream stage simulation |

Phase 1 verification:

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/patches/
git diff --quiet -- basetool.py
```

## Deferred (Phase 2+)

- `modules/basetool/adapter/` and `METHOD_REGISTRY`
- `modules/basetool/runner/` extraction from `basetool.py`
- `modules/basetool/UPSTREAM.json` manifest and `scripts/sync-mhddos-upstream.py`
- Smoke/regression/stability harnesses under `scripts/smoke/`
- Full W8 CI matrix (unit + smoke jobs)
- `nightly.yml` stability workflow
- Release workflows and downstream stager contract documentation
- Full README and `docs/testing.md` pyramid (unit/smoke/release layers)

## Bumping upstream (Phase 2)

Once the sync script lands, upstream updates follow:

```bash
python scripts/sync-mhddos-upstream.py --tag <tag>
```

Until then, manual steps are: subtree pull at the new tag, re-apply patches
with `--directory` as above, run `pytest tests/patches/`, and resolve any
patch conflicts in `modules/basetool/upstream/patches/`.

If patches fail to apply or import-safety tests regress, resolve conflicts
before merging the bump.
