# BaseTool runner architecture

This document tracks the MHDDoS upstream integration. Phase 1 establishes the
vendor tree, patch layer, and patch-test gates without changing runtime
behaviour of the root [basetool.py](../basetool.py) entrypoint.

## Layout (Phase 1)

```
itau-basetool/
├── basetool.py
├── config.json
├── proxy.json
├── requirements.txt
├── modules/
│   └── basetool/
│       └── upstream/
│           ├── mhddos/                 # git subtree at tag 2.4.4
│           └── patches/
│               ├── 0001-stats-hook.patch
│               └── 0002-guard-main.patch
├── tests/
│   └── patches/
├── scripts/
│   └── dev/
│       └── generate-upstream-patches.py
└── THIRD_PARTY_NOTICES.md
```

## Upstream vendor policy

1. `modules/basetool/upstream/mhddos/` is vendored from
   [MatrixTM/MHDDoS](https://github.com/MatrixTM/MHDDoS) tag `2.4.4`.
2. BaseTool-specific behaviour lives outside `upstream/`.
3. Local customisation is limited to the patch files under
   `modules/basetool/upstream/patches/`.

## Patch invariants

| Patch | Purpose |
|-------|---------|
| `0001-stats-hook.patch` | Adds `_raw_send` / `_raw_sendto` hooks on `Layer4` and `HttpFlood` so the Phase 2 adapter can record per-target stats without forking attack logic. |
| `0002-guard-main.patch` | Defers config and IP discovery until CLI execution so importing upstream classes is side-effect free. |

Patch integrity is enforced by `tests/patches/test_patches_apply.py`, which
clones the pinned upstream tag and verifies every patch applies cleanly against
upstream `start.py` at the repository root.

To re-apply patches onto the vendored tree after a subtree sync:

```bash
git apply --directory=modules/basetool/upstream/mhddos modules/basetool/upstream/patches/*.patch
```

Import safety is enforced by `tests/patches/test_import_side_effects.py`, which
asserts that importing `modules.basetool.upstream.mhddos.start` does not install
signal handlers, consume `sys.argv`, or open network sockets.

## Phase gates

| Phase | Scope | Gate |
|-------|-------|------|
| 1 — Foundation | W1, W2, W7a, W7c, W8, partial docs | `pytest tests/patches/` green; CI green; `git diff main -- basetool.py` empty |
| 2 — Cutover | Adapter, runner refactor, unit/smoke tests | All Phase 1 gates plus unit and smoke parity |
| 3 — Release | Release artifact, downstream contract, full docs | Verified tarball and downstream stage simulation |

## Deferred (Phase 2+)

- `modules/basetool/adapter/` and `METHOD_REGISTRY`
- `modules/basetool/runner/` extraction from `basetool.py`
- `modules/basetool/UPSTREAM.json` manifest and `scripts/sync-mhddos-upstream.py`
- Smoke/regression/stability harnesses under `scripts/smoke/`
- Release workflows and downstream stager contract documentation

## Bumping upstream (future)

Once the sync script lands in Phase 2, upstream updates follow:

```bash
python scripts/sync-mhddos-upstream.py --tag <tag>
```

If patches fail to apply or import-safety tests regress, resolve the patch
conflicts before merging the bump.
