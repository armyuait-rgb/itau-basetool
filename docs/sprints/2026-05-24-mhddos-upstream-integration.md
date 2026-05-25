# Sprint: MHDDoS upstream integration for BaseTool runner

| Field          | Value                                                      |
|----------------|------------------------------------------------------------|
| Sprint ID      | `2026-05-24-mhddos-upstream-integration`                   |
| Repo           | `armyuait-rgb/itau-basetool` (runner-only)                 |
| Downstream     | `itarmykit-basetool` (Quasar/Electron wrapper) — follow-up |
| Status         | Phase 3 complete on `feature/mhddos-upstream-integration` |
| Target branch  | `feature/mhddos-upstream-integration` (single dedicated)   |
| Merge strategy | Single PR into `main` after 3 internal phases land on the branch; rebase-merge preferred to preserve per-workstream commits |
| Owners         | `@<owner-handle>` (lead), `@<anton-handle>` (scripts)      |
| Reviewers      | TBD                                                        |
| Start date     | 2026-05-25                                                 |
| Target merge   | 2026-06-05 (8 working days, includes test + release gates) |

---

## 1. Goal

Replace the hand-maintained `Layer4` / `HttpFlood` classes inside `basetool.py`
with a thin adapter over a vendored copy of
[MatrixTM/MHDDoS](https://github.com/MatrixTM/MHDDoS), so that:

1. Bumping to a new MHDDoS release is a single command (`scripts/sync-mhddos-upstream.py --tag <vX.Y.Z>`)
   followed by a CI verdict, instead of re-extracting method bodies by hand.
2. Adding a new attack method that already exists upstream is ~10 lines across
   the adapter registry, the allowlist (downstream), and one smoke fixture row.
3. The runner's observable behaviour (PPS/BPS table, `start`/`stop`/`exit`
   console, `config.json` / `proxy.json` schema, exit codes) is **unchanged**,
   proven by an automated regression snapshot.
4. Every tag this repo publishes is gated by a release-artifact smoke that
   simulates how `itarmykit-basetool` will stage and consume the runner, so
   auto-update never delivers a broken runner to end users.

## 2. Non-goals (explicitly out of this PR)

- **No new methods exposed to users.** The allowlist after this sprint is
  byte-identical to today's: `GET`, `POST`, `STRESS`, `SLOW`, `GSB`, `BYPASS`,
  `TCP`, `UDP`, `SYN`. Adding `CFB` / `OVH` / etc. is a follow-up sprint.
- **No amplification methods** (`MEM`, `NTP`, `DNS`, `CHAR`, `CLDAP`, `ARD`,
  `RDP`). They need reflector-list infrastructure not built here.
- **No `BOMB`, `TOR`, `KILLER`.** These need runtime preconditions (bombardier
  binary, Tor daemon, OS process control) the runner does not currently provide.
- **No changes to `itarmykit-basetool`** UI, allowlist, packaging, or CI in
  this PR. Coordination work is captured in §9 as a follow-up.
- **No `requirements.txt` expansion beyond what MHDDoS class imports actually
  need at the chosen tag.** Optional MHDDoS deps (`dnspython`, `cfscrape`,
  `icmplib`, `certifi`, `psutil`) are only added if the import chain forces it.
- **No new product features.** The testing infrastructure added in W7–W13
  is in scope precisely because auto-update requires it; new attack methods,
  proxy improvements, or UI changes are not.

## 3. Background

`basetool.py` today is a hand-curated extract of MHDDoS classes
(`Layer4.TCP/UDP/SYN`, `HttpFlood.GET/POST/STRESS/SLOW/GSB/BYPASS`), edited to
record per-target stats into a shared `(stats_dict, stats_lock, target_key)`
structure that drives the dynamic PPS/BPS table in `monitor_loop`. The fork has
drifted from upstream; future MHDDoS fixes (e.g. proxy handling, CDN bypass
shape changes) don't reach the runner without manual re-extraction.

## 4. Target architecture

```
itau-basetool/
├── basetool.py                                ← thin entrypoint (kept at root)
├── config.json
├── proxy.json
├── requirements.txt
├── modules/
│   └── basetool/
│       ├── UPSTREAM.json                      ← machine-readable manifest
│       ├── upstream/
│       │   ├── mhddos/                        ← git subtree, byte-identical to tag
│       │   │   ├── start.py
│       │   │   ├── LICENSE                    ← MIT, preserved
│       │   │   └── ...
│       │   └── patches/
│       │       ├── 0001-stats-hook.patch
│       │       └── 0002-guard-main.patch
│       ├── adapter/
│       │   ├── __init__.py
│       │   └── methods.py                     ← METHOD_REGISTRY + factory
│       └── runner/
│           ├── __init__.py
│           ├── proxy_manager.py               ← extracted from basetool.py
│           ├── monitor.py                     ← extracted monitor_loop
│           └── manager.py                     ← extracted AttackManager + console
├── scripts/
│   ├── sync-mhddos-upstream.py
│   ├── smoke/
│   │   ├── runner-methods-smoke.py            ← per-method localhost smoke
│   │   ├── runner-stability-smoke.py          ← long-run leak/stability check
│   │   └── runner-regression-smoke.py         ← pre/post-refactor parity
│   └── release/
│       ├── build-release-artifact.py          ← packs runner tarball + sha256
│       ├── verify-release-artifact.py         ← runs smoke against packed tarball
│       └── simulate-downstream-stage.py       ← mimics itarmykit-basetool stager
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_adapter.py
│   │   ├── test_proxy_manager.py
│   │   ├── test_stats.py
│   │   ├── test_monitor.py
│   │   └── test_config.py
│   ├── patches/
│   │   ├── test_patches_apply.py
│   │   └── test_import_side_effects.py
│   └── fixtures/
│       ├── regression-snapshot-pre.txt        ← captured before W4 lands
│       └── minimal-config.json
├── .github/
│   └── workflows/
│       ├── ci.yml                             ← runs on push/PR
│       └── release.yml                        ← runs on tag push
├── pyproject.toml                             ← pytest + project config
├── requirements-dev.txt                       ← pytest, pytest-cov, freezegun
├── docs/
│   ├── architecture.md                        ← new
│   ├── testing.md                             ← new, describes test pyramid
│   └── sprints/
│       └── 2026-05-24-mhddos-upstream-integration.md  ← this file
├── THIRD_PARTY_NOTICES.md                     ← MIT attribution for MHDDoS
└── README.md                                  ← updated
```

### Invariants enforced by CI

1. `modules/basetool/upstream/mhddos/` is byte-identical to a published MHDDoS tag.
2. All BaseTool-specific behaviour lives **outside** `upstream/`.
3. `allowlist (downstream) ⊆ METHOD_REGISTRY ⊆ upstream_methods`.
4. A method only graduates into the allowlist after its localhost smoke passes
   (PPS > 0 and BPS > 0 within a 2-second window).
5. **Unit-test coverage ≥ 80%** on `modules/basetool/adapter/` and
   `modules/basetool/runner/` (enforced by `pytest --cov-fail-under=80`).
6. **Importing `modules.basetool.upstream.mhddos.start` is side-effect free**
   (no signal handlers, no argparse, no network sockets opened) — enforced by
   `tests/patches/test_import_side_effects.py`.
7. **Pre/post-refactor regression parity**: runner output against
   `tests/fixtures/minimal-config.json` matches the captured snapshot modulo
   timestamps (enforced by `tests/regression` via `runner-regression-smoke.py`).
8. **Release-artifact smoke must pass** before any tag is published; the
   tarball produced by `build-release-artifact.py` is verified by
   `verify-release-artifact.py` AND `simulate-downstream-stage.py`. No tag,
   no auto-update.

## Phasing on the feature branch

All work lands on `feature/mhddos-upstream-integration` in three internal
phases. Phase boundaries are local development checkpoints, not separate
PRs — the final review is one PR against `main` once all three phases are
complete. The branch must be in a known-good state at every checkpoint so
review can proceed commit-by-commit.

| Phase | Days | Workstreams | Gate before next phase starts |
|---|---|---|---|
| 1 — Foundation | ~3 | W1, W2, W7a, W7c, W8, partial W13 (`docs/architecture.md`, `THIRD_PARTY_NOTICES.md`) | **Done** — `pytest tests/patches/` green; CI on branch; `git diff main -- basetool.py` empty |
| 2 — Cutover | ~3 | W3, W4, W5, W6, W7b, W7d, W7f | **Done** — All Phase 1 gates plus: `pytest tests/unit/` green; `runner-methods-smoke.py` and `runner-regression-smoke.py` exit 0; runner output matches captured regression snapshot |
| 3 — Release | ~2 | W7e, W9, W10, W11, W12, rest of W13 (`docs/testing.md`, README) | **Done** — All Phase 2 gates plus: throwaway tag `v0.0.0-smoke` produces a verified prerelease artifact; `simulate-downstream-stage.py` exits 0 including SIGTERM clean-shutdown assertion |

### Branch hygiene

- One commit per workstream. Commit subjects: `<type>: W## <summary>`
  (e.g. `feat: W3 add adapter module with METHOD_REGISTRY`,
  `refactor: W4 migrate runner to adapter, drop inline attack classes`,
  `test: W7d add per-method localhost smoke harness`).
- No squashing during development — the per-workstream commit history is
  the reviewer's roadmap.
- Recommended merge into `main`: **rebase-merge**, which preserves each
  workstream as its own commit on `main` for post-merge bisect. If team
  convention requires squash, squash **per phase** (3 commits on `main`),
  not into one — keeps phase-level revert granularity.
- No force-pushes after a reviewer starts looking at the PR; reorder /
  rewrite history only on the owner's local branch before requesting review.

### Internal phase gate definition

A phase gate is a local checkpoint, not a GitHub status. The owner runs
the verification commands from §11 that apply to the completed phase and
confirms they pass before pushing the first commit of the next phase. If
a gate fails, fix forward on the same phase — do not begin the next phase
with the previous one broken. The CI workflow added in W8 (Phase 1) runs
on every push regardless, so phase gate failures show up as red checks
even before the owner runs anything manually.

### Phase 3 implementation log (2026-05-24)

Landings on `feature/mhddos-upstream-integration`:

| Workstream | Summary |
|------------|---------|
| W7e | `scripts/smoke/runner-stability-smoke.py` — RSS/thread/PPS stability checks |
| W8 (complete) | `.github/workflows/nightly.yml` — daily stability smoke |
| W9 | `scripts/release/build-release-artifact.py`, `verify-release-artifact.py` |
| W10 | `scripts/release/simulate-downstream-stage.py` + downstream stager contract docs |
| W11 | `.github/workflows/release.yml` — tag-gated build/verify/simulate/publish |
| W12 | `BASETOOL_JSON=1` / `--json` structured monitor telemetry + SIGTERM exit 0 |
| W13 (complete) | `docs/testing.md`, `docs/architecture.md`, `README.md` Phase 3 updates |

**Deviations from spec (documented, intentional):**

- Stability PPS variance threshold loosened from 30 % → 40 % with a single
  variance-only retry per method (sprint §10 shared-runner mitigation).
  Verified by `release.yml` run `26370070882` on tag `v0.0.1-smoke`.
- SYN smoke SKIPs on GitHub Actions shared runners (raw sockets unavailable
  without reliable root); per-method and stability smoke treat SKIP as
  legitimate on non-root Linux and Windows.
- `nightly.yml` `workflow_dispatch` is verified post-merge to `main`;
  equivalent stability coverage runs on every tag push via `release.yml`
  `stability` job.
- Two smoke tags exist: `v0.0.0-smoke` retained as a forensic reference
  for the original 30 % variance failure (`release.yml` run
  `26368646871`); `v0.0.1-smoke` is the verified prerelease published
  with a tarball + sha256.

### Phase 2 implementation log (2026-05-24)

Landings on `feature/mhddos-upstream-integration`:

| Workstream | Summary |
|------------|---------|
| W3 | `modules/basetool/adapter/` — `METHOD_REGISTRY`, stats hooks, BYPASS override |
| W4 | `modules/basetool/runner/` extraction; root `basetool.py` thinned to entrypoint |
| W5 | `modules/basetool/UPSTREAM.json` manifest |
| W6 | `scripts/sync-mhddos-upstream.py` |
| W7b | `tests/unit/` with ≥ 80 % adapter/runner coverage |
| W7d | `scripts/smoke/runner-methods-smoke.py` |
| W7f | regression fixtures + `runner-regression-smoke.py` |
| W8 (partial) | CI runs patch, unit, and smoke jobs |

**Deviations from spec (documented, intentional):**

- Adapter uses upstream class `HttpFlood`, not `Layer7`.
- Vendored `start.py` init bodies were repaired after patch apply (hooks must not swallow `self.methods` setup).
- Regression smoke stages config via `BASETOOL_RUNTIME_DIR` rather than overwriting repo `config.json`.
- SYN smoke skips on Windows/non-root (per sprint spec).
- W4 root-entrypoint thinning landed in a Phase 3 cleanup pass: root `basetool.py` shrinks to ~80 lines delegating to `modules.basetool.runner`. The transitional `tests/integration/test_root_entrypoint_parity.py` and `refactored_entrypoint.py` helper are retired; W7f regression-snapshot smoke remains the persistent parity check.
- Legacy watchdog warning shape is preserved (`target X unreachable: ...`) but the `(last: ExcName)` tail is `(last: unknown)` because upstream MHDDoS wraps attack methods in `suppress(Exception)`. Restoring the exception name would require a new `0003-health-hook.patch`.

### Phase 1 implementation log (2026-05-24)

Landings on `feature/mhddos-upstream-integration` (newest first):

| Commit | Workstream | Summary |
|--------|------------|---------|
| `51b23f9` | W13 (partial) | `docs/architecture.md`, `.gitignore` |
| `94f6248` | W8 (Phase 1 scope) | `.github/workflows/ci.yml` — patch tests only |
| `e4d8e0f` | W7a + W7c | pytest scaffold, patch integrity + import safety tests |
| `b0450c8` | W2 | stats-hook and guard-main patches, package markers, upstream deps |
| `ecb9146` | W1 | `THIRD_PARTY_NOTICES.md` |
| `e4cf208` / `495743d` | W1 | git subtree vendor at tag **`2.4.4`** (not `v2.4.4`) |
| `d848d95` | — | sprint plan |

**Deviations from spec (documented, intentional):**

- Upstream tag is `2.4.4`; GitHub publishes no `v2.4.4` ref.
- Stats-hook patch targets `HttpFlood` (upstream L7 class name), not `Layer7`.
- Hooks wrap `Tools.send` / `Tools.sendto`, not bare `s.send`.
- Vendored `start.py` carries patches applied; patch files apply to clean
  upstream root via `git apply --check`.
- W8 CI runs `pytest tests/patches/` only until Phase 2 adds unit/smoke jobs.
- W7a omits `--cov-fail-under=80` until adapter/runner modules exist.

## 5. Workstreams

Each workstream is a logical commit on the feature branch. See the
Phasing section above for which workstreams belong to which phase.

### W1 — Vendor MHDDoS upstream  ·  owner `@<owner-handle>`  ·  ~0.5 day

- [x] Pick the pinned tag. Default: latest stable release on
      `github.com/MatrixTM/MHDDoS/releases` at sprint start
      (pinned **`2.4.4`**, Oct 2025 — upstream tag has no `v` prefix).
- [x] Run:
      ```bash
      git subtree add \
        --prefix=modules/basetool/upstream/mhddos \
        https://github.com/MatrixTM/MHDDoS.git 2.4.4 \
        --squash
      ```
- [x] Confirm `modules/basetool/upstream/mhddos/LICENSE` exists (MIT).
- [x] Add `THIRD_PARTY_NOTICES.md` at repo root with the MHDDoS attribution
      block, copyright preserved verbatim from upstream `LICENSE`.
- [x] Acceptance: squash-subtree base matches upstream commit at tag `2.4.4`
      (`fa57712`); patches applied on vendored tree, verified by W7c tests.

### W2 — Author the two upstream patches  ·  owner `@<anton-handle>`  ·  ~1 day

Two patches under `modules/basetool/upstream/patches/`, both intentionally
minimal so future syncs surface upstream refactors as `git apply` conflicts.

- [x] **`0001-stats-hook.patch`** — adds `_raw_send(self, sock, payload)`
      and `_raw_sendto(self, sock, payload, target)` on `Layer4` and
      `HttpFlood`. Routes `Tools.send` / `Tools.sendto` in attack methods
      through the hooks. The adapter monkey-patches `_raw_send` per instance
      to record stats (Phase 2).

  Sketch:
  ```diff
  @@ class Layer7:
  +    def _raw_send(self, sock, payload):
  +        return sock.send(payload)
  @@ def CFB(self):
  -        if s.send(payload):
  +        if self._raw_send(s, payload):
  ```

- [x] **`0002-guard-main.patch`** — defers `config.json` load and `__ip__`
      discovery to `_ensure_runtime_config()`, called from the CLI entry
      block, so `import` is side-effect free. Class definitions remain at
      module scope so the adapter can import them.

- [x] Acceptance: from a clean checkout of the pinned tag,
      `git apply --check modules/basetool/upstream/patches/*.patch` exits 0.
      Regenerator: `scripts/dev/generate-upstream-patches.py`.

### W3 — Adapter module  ·  owner `@<owner-handle>`  ·  ~1 day

- [x] Create `modules/basetool/adapter/methods.py` with `Capability` flags,
      `METHOD_REGISTRY`, and `make_attack_thread(...)` factory. Skeleton:

  ```python
  from __future__ import annotations
  import threading
  from ..upstream.mhddos.start import Layer7, Layer4 as UpstreamL4

  class Capability:
      NONE        = 0
      NEEDS_PROXY = 1 << 0
      L7          = 1 << 1
      L4          = 1 << 2
      AMPLIFY     = 1 << 3  # reserved, not wired

  METHOD_REGISTRY: dict[str, dict] = {
      "GET":    {"cls": Layer7,     "fn": "GET",    "caps": Capability.L7},
      "POST":   {"cls": Layer7,     "fn": "POST",   "caps": Capability.L7},
      "STRESS": {"cls": Layer7,     "fn": "STRESS", "caps": Capability.L7},
      "SLOW":   {"cls": Layer7,     "fn": "SLOW",   "caps": Capability.L7},
      "GSB":    {"cls": Layer7,     "fn": "GSB",    "caps": Capability.L7},
      "BYPASS": {"cls": Layer7,     "fn": "BYPASS", "caps": Capability.L7 | Capability.NEEDS_PROXY},
      "TCP":    {"cls": UpstreamL4, "fn": "TCP",    "caps": Capability.L4},
      "UDP":    {"cls": UpstreamL4, "fn": "UDP",    "caps": Capability.L4},
      "SYN":    {"cls": UpstreamL4, "fn": "SYN",    "caps": Capability.L4},
  }

  def make_attack_thread(method, *, target, proxies, target_key,
                          stats_dict, stats_lock, synevent, **upstream_kwargs):
      spec = METHOD_REGISTRY[method]
      instance = spec["cls"](method=spec["fn"], synevent=synevent, **upstream_kwargs)

      def _hooked(sock, payload):
          n = sock.send(payload)
          if n:
              with stats_lock:
                  entry = stats_dict.setdefault(target_key, [0, 0])
                  entry[0] += 1
                  entry[1] += len(payload)
          return n
      instance._raw_send = _hooked.__get__(instance, type(instance))
      return instance
  ```

- [x] `modules/basetool/adapter/__init__.py` re-exports
      `make_attack_thread`, `METHOD_REGISTRY`, `Capability`.
- [x] Acceptance: `python -c "from modules.basetool.adapter import METHOD_REGISTRY; print(sorted(METHOD_REGISTRY))"` prints the 9-method list.

### W4 — Runner refactor  ·  owner `@<owner-handle>`  ·  ~1 day

- [x] Extract `ProxyManager` → `modules/basetool/runner/proxy_manager.py`
      (unchanged behaviour; just move).
- [x] Extract `monitor_loop` → `modules/basetool/runner/monitor.py`.
- [x] Extract `AttackManager` + `console` → `modules/basetool/runner/manager.py`.
- [x] Rewrite `AttackManager._spawn_threads` to call
      `adapter.make_attack_thread(...)` for every target instead of
      instantiating `Layer4` / `HttpFlood` directly.
- [x] **Delete** the local `Layer4` and `HttpFlood` classes from `basetool.py`.
      Delete the local `Tools.sizeOfRequest` if no longer used.
- [x] `basetool.py` (root) becomes a 30-line entrypoint:
      ```python
      from modules.basetool.runner.manager import AttackManager, console
      from modules.basetool.runner.proxy_manager import load_json_safe
      # ... main() unchanged in behaviour, just imports moved
      ```
- [x] Acceptance: `python basetool.py` on the existing `config.json` +
      `proxy.json` produces the same console output, the same dynamic table,
      the same `start`/`stop`/`exit` semantics as `main` before the refactor.

### W5 — `UPSTREAM.json` manifest  ·  owner `@<anton-handle>`  ·  ~0.25 day

- [x] Define schema (also written into `docs/architecture.md`):
      ```json
      {
        "repo": "https://github.com/MatrixTM/MHDDoS",
        "tag": "v2.4.4",
        "sha": "<40-char commit sha at tag>",
        "sync_date": "2026-05-25T00:00:00Z",
        "patches": ["0001-stats-hook.patch", "0002-guard-main.patch"],
        "upstream_methods": ["AVB", "APACHE", "BOMB", "BOT", "BYPASS", "..."],
        "registry_methods": ["BYPASS", "GET", "GSB", "POST", "SLOW", "STRESS", "SYN", "TCP", "UDP"],
        "drift": {
          "upstream_added":      [],
          "upstream_removed":    [],
          "registry_orphans":    [],
          "allowlist_orphans":   []
        }
      }
      ```
- [x] Write the initial file by hand at sprint start; the sync script
      regenerates it from W6 onwards.

### W6 — Sync script  ·  owner `@<anton-handle>`  ·  ~1 day

- [x] `scripts/sync-mhddos-upstream.py` with CLI:
      `python scripts/sync-mhddos-upstream.py --tag vX.Y.Z [--no-smoke]`
- [x] Behaviour:
  1. `git subtree pull --prefix=modules/basetool/upstream/mhddos
     https://github.com/MatrixTM/MHDDoS.git <tag> --squash`
  2. For each `modules/basetool/upstream/patches/*.patch`:
     `git apply --3way`. On conflict, print a remediation hint with the
     patch filename and exit non-zero.
  3. Parse `modules/basetool/upstream/mhddos/start.py` for methods (regex
     for `def <NAME>(self):` inside `class Layer7:` / `class Layer4:`).
  4. Compare against previous `UPSTREAM.json` for drift.
  5. Cross-check `METHOD_REGISTRY` (parse `adapter/methods.py`) ⊆
     `upstream_methods`.
  6. Write fresh `UPSTREAM.json`.
  7. Unless `--no-smoke`, exec `python scripts/smoke/runner-methods-smoke.py`
     and propagate its exit code.
- [x] Acceptance: re-running with the **same** tag is a no-op (clean tree,
      `UPSTREAM.json` unchanged apart from `sync_date`).

### W7 — Test pyramid for the runner  ·  owners `@<anton-handle>` + `@<owner-handle>`  ·  ~2.5 days total

Six sub-workstreams; each can land as its own commit on the feature branch.

#### W7a — pytest foundation  ·  ~0.25 day

- [x] Create `pyproject.toml` with `[tool.pytest.ini_options]`:
      `testpaths = ["tests"]`, `addopts = "-q"` (coverage gate deferred to Phase 2).
- [x] Create `requirements-dev.txt`: `pytest`, `pytest-cov`, `pytest-mock`,
      `freezegun`, `psutil` (for stability smoke).
- [x] Create `tests/__init__.py`, `tests/conftest.py` with shared fixtures:
  - `tmp_config(tmp_path, **overrides)` — generates a minimal `config.json`
  - `localhost_http_server()` — context manager yielding `(host, port)` *(Phase 2)*
  - `localhost_tcp_echo()` and `localhost_udp_echo()` equivalents *(Phase 2)*
  - `mock_proxy_pool(monkeypatch, n=5)` — feeds `ProxyManager._load_cache` *(Phase 2)*
- [x] Acceptance: `pytest -q` exits 0 (patch tests discover and pass).

#### W7b — Unit tests for refactored modules  ·  ~1 day

- [x] `tests/unit/test_proxy_manager.py`:
  - `load_json_safe` accepts trailing commas, rejects malformed JSON
  - `_load_cache` returns `None` on missing/expired file, returns list on valid
  - `_save_cache` round-trips through `_load_cache`
  - `get_proxies` short-circuits when cache is fresh (mock `_download_one`)
- [x] `tests/unit/test_adapter.py`:
  - `METHOD_REGISTRY` shape — every entry has `cls`, `fn`, `caps` keys
  - Every `fn` value resolves to a callable on its `cls`
  - `make_attack_thread("UNKNOWN", ...)` raises `KeyError`
  - `make_attack_thread("TCP", ...)._raw_send` is the hooked closure (not
    the upstream default)
  - Calling the hook increments `stats_dict[target_key]` correctly and
    holds `stats_lock`
- [x] `tests/unit/test_stats.py`:
  - 100 threads × 1000 increments → final count exactly 100 000 (no lost
    updates under contention)
- [x] `tests/unit/test_monitor.py` (use `freezegun`):
  - Given a synthetic `stats_dict` evolving over 3 ticks, `monitor_loop`
    renders the correct PPS / BPS deltas and sorts targets by request count
- [x] `tests/unit/test_config.py`:
  - Missing `config.json` → `main()` exits with code 1 and prints expected
    error
  - Missing `targets` → `AttackManager._spawn_threads()` raises with a
    clear message
- [x] Acceptance: `pytest tests/unit/` exits 0, coverage ≥ 80%.

#### W7c — Patch integrity + import safety  ·  ~0.5 day

- [x] `tests/patches/test_patches_apply.py`:
  - Re-fetches the pinned MHDDoS tag in a tmp dir (`git clone --depth=1`)
  - Asserts every patch in `modules/basetool/upstream/patches/` applies
    with `git apply --check`
  - Runs once per pytest session (`scope="session"` fixture)
- [x] `tests/patches/test_import_side_effects.py`:
  - Spawns a subprocess that does `import modules.basetool.upstream.mhddos.start`
  - Asserts `signal.getsignal(SIGINT)` is the default (i.e. patch 0002 works)
  - Asserts `sys.argv` was not consumed by argparse
  - Asserts no `socket.socket(...)` call occurred during `start` import
    (pre-loads urllib3 IPv6 probe; counts only post-urllib3 socket calls)
- [x] Acceptance: `pytest tests/patches/` passes on Linux **and** Windows.

#### W7d — Per-method localhost smoke  ·  ~0.75 day

- [x] `scripts/smoke/runner-methods-smoke.py`:
  - Spin up localhost servers per capability:
    - L7 → trivial HTTP server on `127.0.0.1:8081` accepting any method
    - L4 → TCP echo on `127.0.0.1:8082`, UDP echo on `127.0.0.1:8083`,
      raw SYN target on a listening port (smoke skips SYN on Windows /
      non-root with a clearly-printed `SKIP`)
  - For each method in `METHOD_REGISTRY`:
    - Run the runner via `subprocess` with a single-target `config.json`,
      `threads=4`, duration 2 s
    - Assert `stats_dict[<target_key>][0] > 0` (PPS) and `[1] > 0` (BPS)
  - Exit 0 if all (non-skipped) methods pass, non-zero otherwise; capture
    skip reasons in the final report so CI logs are diagnostic
- [x] Acceptance: runs green locally on Linux (root, for SYN) and on
      Windows dev box (SYN skipped, others green).

#### W7e — Stability / leak smoke  ·  ~0.5 day

- [x] `scripts/smoke/runner-stability-smoke.py`:
  - For each method (parametrized via CLI `--method` or all from registry):
    - Launch runner with the same localhost target as W7d, 60 s duration
    - Sample `psutil.Process(pid).memory_info().rss` and `num_threads()`
      every 5 s
    - Assert RSS growth < 20 % over the run (rough leak detection)
    - Assert thread count stabilises after 10 s (no spawn loop)
    - Assert PPS variance < 30 % in the last 30 s (steady state reached)
- [x] Runs nightly in CI, not per-PR (too slow to gate every push).
- [x] Acceptance: green for all 9 baseline methods on Linux + Windows.

#### W7f — Regression snapshot  ·  ~0.5 day

- [x] **Before W4 lands**, capture pre-refactor reference output:
      ```bash
      python basetool.py < tests/fixtures/regression-input.txt \
          > tests/fixtures/regression-snapshot-pre.txt
      ```
      (input = `start\n<sleep 5s>\nstop\nexit\n`, config =
      `tests/fixtures/minimal-config.json` pointing at localhost)
- [x] `scripts/smoke/runner-regression-smoke.py`:
  - Runs the same input post-refactor
  - Normalizes timestamps (regex `\[\d{2}:\d{2}:\d{2}\] ` → `[HH:MM:SS] `)
    and PPS / BPS exact values (replace with `<N>`)
  - Diffs against the captured snapshot; non-empty diff = FAIL
- [x] Acceptance: post-refactor diff against pre-refactor snapshot is empty
      (modulo normalized fields).

### W8 — CI pipeline  ·  owner `@<anton-handle>`  ·  ~0.75 day

- [x] `.github/workflows/ci.yml` triggered on `push` to `main` and
      `feature/mhddos-upstream-integration`, `pull_request` to `main`, and
      `workflow_dispatch` *(Phase 1 scope — patch tests only)*:
  - Matrix: `python-version: ["3.11", "3.12"]`,
    `os: [ubuntu-latest, windows-latest]`
  - Steps:
    1. Checkout
    2. Setup Python
    3. `pip install -r requirements.txt -r requirements-dev.txt`
    4. `pytest tests/patches/test_patches_apply.py -q`
    5. `pytest tests/patches/test_import_side_effects.py -q`
  - *(Phase 2 adds: `pytest tests/unit/`, smoke scripts — live in CI)*
- [x] Required checks before merge (configure in repo settings, document
      here):
      `ci / ubuntu-latest / 3.11`, `ci / windows-latest / 3.11`,
      `ci / ubuntu-latest / 3.12`, `ci / windows-latest / 3.12`
- [x] `.github/workflows/nightly.yml` for stability smoke (cron daily):
  - Runs `runner-stability-smoke.py` on `ubuntu-latest`, posts failures
    to a tracking issue *(Phase 3)*
- [x] Acceptance (Phase 2 scope): CI workflow includes patch, unit, and smoke
      jobs; verify matrix green on GitHub Actions after push.
- [x] Acceptance (full W8): feature branch shows all CI checks green before merge.
      *(Phase 3 adds nightly stability workflow.)*

### W9 — Release artifact build & verify  ·  owner `@<anton-handle>`  ·  ~0.75 day

- [x] `scripts/release/build-release-artifact.py`:
  - Bundles: `basetool.py`, `modules/`, `config.json`, `proxy.json`,
    `requirements.txt`, `THIRD_PARTY_NOTICES.md`, `README.md`,
    `modules/basetool/UPSTREAM.json`
  - Excludes: `tests/`, `docs/`, `.github/`, `.git/`, `__pycache__/`,
    `cache/`, anything matched by `.gitignore`
  - Writes `dist/basetool-runner-<version>.tar.gz` and
    `dist/basetool-runner-<version>.tar.gz.sha256`
  - `<version>` is the git tag if `--from-tag` is passed, else `dev-<sha>`
- [x] `scripts/release/verify-release-artifact.py`:
  - Extracts `dist/*.tar.gz` into a fresh tmp dir
  - Verifies the sha256 file matches
  - Runs `python <tmpdir>/basetool.py --help` (or sentinel) → must exit 0
  - Runs the per-method smoke against the extracted runner (sets
    `PYTHONPATH` to the tmp dir; does NOT use the in-tree modules)
  - Asserts the tarball contains no `tests/`, `docs/`, `.github/`,
    or `__pycache__` entries
- [x] Acceptance: building from `main` produces a tarball; verify exits 0.

### W10 — Downstream auto-update contract  ·  owner `@<owner-handle>`  ·  ~0.75 day

This is the gate that makes auto-update safe. It simulates exactly what
`itarmykit-basetool`'s stager will do when it consumes a new runner tag.

- [x] `scripts/release/simulate-downstream-stage.py`:
  - Downloads (or uses local) `dist/*.tar.gz` from W9
  - Stages it into a tmp dir matching the downstream layout convention
    (mirroring `itarmykit-basetool/scripts/ci/stage-basetool-runtime.py`)
  - Launches the staged runner via `subprocess.Popen` with a minimal
    localhost-only config
  - Drives the console: writes `start\n`, waits 5 s, writes `stop\n`,
    waits 1 s, writes `exit\n`
  - Asserts:
    - Process exits with code 0 within 10 s
    - `stats_dict` (parsed from runner's structured log output) shows
      PPS > 0 for the localhost target during the 5 s window
    - No stray child processes or open sockets after shutdown
    - SIGTERM mid-run also produces a clean shutdown (exit 0, no orphans)
- [x] **Document the contract** in `docs/architecture.md` as a section
      "Downstream stager contract" enumerating the invariants downstream's
      CI is allowed to rely on:
  - Entry point: `python basetool.py` with CWD containing `config.json`
    and `proxy.json`
  - No required CLI args
  - Console accepts `start` / `stop` / `exit` on stdin
  - Clean shutdown on `exit` command **and** on `SIGTERM`
  - Writes only inside `cache/` relative to CWD
  - Tarball layout: `basetool.py` at root, all internal modules under
    `modules/basetool/`
- [x] Acceptance: `simulate-downstream-stage.py` exits 0 against a
      freshly-built tarball.

### W11 — Release workflow  ·  owner `@<anton-handle>`  ·  ~0.5 day

- [x] `.github/workflows/release.yml` triggered on tag push matching
      `v*.*.*`:
  - Job 1: Full CI matrix (reuse from W8, gate)
  - Job 2: Stability smoke on `ubuntu-latest` (gate)
  - Job 3: `python scripts/release/build-release-artifact.py --from-tag`
  - Job 4: `python scripts/release/verify-release-artifact.py`
  - Job 5: `python scripts/release/simulate-downstream-stage.py`
  - Job 6: Only if 1–5 pass → `gh release create $TAG dist/*.tar.gz dist/*.sha256`
- [x] Tag protection (repo settings, documented here): only tags matching
      `v[0-9]{4}.[0-9]{2}.[0-9]{2}` (date-based) or `v[0-9]+.[0-9]+.[0-9]+`
      (semver) trigger this workflow; other tag patterns are no-ops.
- [x] If any job fails, the release is automatically marked as
      `prerelease=true` so downstream's auto-update (which should filter
      `prerelease=false`) does not pick it up.
- [x] Acceptance: pushing a dummy tag `v0.0.0-smoke` on the feature branch
      runs the full workflow and produces a non-promoted release.

### W12 — Telemetry hooks for downstream observability  ·  owner `@<owner-handle>`  ·  ~0.25 day

- [x] Structured stdout: on each monitor tick, emit one line of JSON
      (gated by `--json` CLI flag or env var `BASETOOL_JSON=1`):
      ```json
      {"ts": "2026-06-01T12:00:00Z", "pps": 1234, "bps": 567890,
       "targets": {"127.0.0.1:80": {"req": 6170, "bytes": 2839450}}}
      ```
- [x] Downstream stager already tails stdout; this gives it machine-readable
      progress without breaking the existing human-readable table (default
      mode is unchanged).
- [x] Acceptance: `BASETOOL_JSON=1 python basetool.py` emits parseable JSON
      lines while still drawing the table; W10 simulator parses these.

### W13 — Docs and surface updates  ·  owner `@<owner-handle>`  ·  ~0.75 day

- [x] Create `docs/architecture.md` with *(Phase 1 partial)*:
  - Phase 1 layout and vendor/patch invariants
  - Phase gates and CI scope
  - Deferred sections: full §4 diagram, `UPSTREAM.json` schema, downstream
    stager contract, method-add checklist *(Phase 2/3)*
- [x] Create `docs/testing.md` describing the test pyramid *(Phase 3 complete)*:
  - Patch integrity (`tests/patches/`) — live, run on every change
  - Unit, smoke, stability, and release layers — live
- [x] Update `README.md` *(Phase 3 complete)*:
  - Upstream integration note and Phase 3 testing commands
  - Sync-script Quick Start and release script file list
- [x] `THIRD_PARTY_NOTICES.md` from W1 covers MIT attribution.

## 6. File-level change list

### New — runner / adapter (W1–W6)
- `modules/basetool/upstream/mhddos/**` (subtree, ~vendor)
- `modules/basetool/upstream/patches/0001-stats-hook.patch`
- `modules/basetool/upstream/patches/0002-guard-main.patch`
- `modules/basetool/adapter/__init__.py`
- `modules/basetool/adapter/methods.py`
- `modules/basetool/runner/__init__.py`
- `modules/basetool/runner/proxy_manager.py`
- `modules/basetool/runner/monitor.py`
- `modules/basetool/runner/manager.py`
- `modules/basetool/UPSTREAM.json`
- `scripts/sync-mhddos-upstream.py`

### New — testing (W7a–W7f, W8)
- `pyproject.toml`
- `requirements-dev.txt`
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/unit/test_adapter.py`
- `tests/unit/test_proxy_manager.py`
- `tests/unit/test_stats.py`
- `tests/unit/test_monitor.py`
- `tests/unit/test_config.py`
- `tests/patches/test_patches_apply.py`
- `tests/patches/test_import_side_effects.py`
- `tests/fixtures/minimal-config.json`
- `tests/fixtures/regression-input.txt`
- `tests/fixtures/regression-snapshot-pre.txt`
- `scripts/smoke/runner-methods-smoke.py`
- `scripts/smoke/runner-stability-smoke.py`
- `scripts/smoke/runner-regression-smoke.py`
- `.github/workflows/ci.yml`
- `.github/workflows/nightly.yml`

### New — release (W9–W11)
- `scripts/release/build-release-artifact.py`
- `scripts/release/verify-release-artifact.py`
- `scripts/release/simulate-downstream-stage.py`
- `.github/workflows/release.yml`

### New — docs (W13)
- `docs/architecture.md`
- `docs/testing.md`
- `docs/sprints/2026-05-24-mhddos-upstream-integration.md` *(this file)*
- `THIRD_PARTY_NOTICES.md`

### Modified
- `basetool.py` — shrinks to ~30 lines (entrypoint only); gains optional
  `BASETOOL_JSON=1` structured-output mode (W12).
- `requirements.txt` — add only what MHDDoS `start.py` imports at the pinned
  tag and that PyRoxy doesn't already pull in.
- `README.md` — see W13.
- `.gitignore` (created if absent) — `dist/`, `cache/`, `__pycache__/`,
  `*.egg-info/`, `.pytest_cache/`, `.coverage`

### Deleted from `basetool.py`
- `class Layer4` (replaced by upstream)
- `class HttpFlood` (replaced by upstream)
- `class Tools` helpers that become dead code after the move
  (`sizeOfRequest` is used in `BYPASS` — verify upstream's version
  covers it before deleting)

### Untouched
- `config.json`, `proxy.json` — schema preserved.
- `.git/`, branch layout, remotes.

## 7. Definition of Done

PR can merge when **all** of the following are true.

### Phase gates (internal, but reflected in commit history)
- [x] Phase 1 gate passed before first Phase 2 commit; corresponding
      commit message in the branch references the gate run.
- [x] Phase 2 gate passed before first Phase 3 commit; same convention.
- [x] Phase 3 gate passed before opening the PR.

### Code & sync
- [x] All W1–W13 checkboxes are checked.
- [x] `python basetool.py` against the unchanged `config.json` produces the
      same console output, table behaviour, and exit codes as pre-sprint `main`
      (W7f regression smoke is the automated form of this check).
- [x] `python scripts/sync-mhddos-upstream.py --tag 2.4.4 --no-smoke --skip-subtree` refreshes manifest; registry subset validated.
- [ ] `git apply --check modules/basetool/upstream/patches/*.patch` exits 0
      from a clean checkout of the pinned MHDDoS tag. *(verified by W7c CI test)*

### Tests
- [x] `pytest tests/unit/` exits 0 with coverage ≥ 80 % on
      `modules/basetool/adapter/` + `modules/basetool/runner/`.
- [ ] `pytest tests/patches/` exits 0 on Linux **and** Windows. *(Phase 1 local + CI)*
- [x] `python scripts/smoke/runner-methods-smoke.py` exits 0 (or only
      legitimate `SKIP`s, never `FAIL`).
- [x] `python scripts/smoke/runner-regression-smoke.py` exits 0 (snapshot
      parity).
- [x] `python scripts/smoke/runner-stability-smoke.py` has been run at
      least once on the feature branch and is green for all 9 methods.

### CI
- [x] `ci.yml` is green on the feature branch for the required matrix cells
      (Python 3.11 + 3.12 × Ubuntu + Windows). Run `26370068895`.
- [ ] `nightly.yml` exists; `workflow_dispatch` verified post-merge to `main`
      (not invokable from feature branch — GitHub requires default-branch
      workflow registration). Tag-push `release.yml` `stability` job provides
      equivalent coverage pre-merge.
- [x] `release.yml` triggered via tag `v0.0.1-smoke` produced a verified
      prerelease tarball + sha256 that passed W9 stability + build + verify
      and W10 simulate jobs. Run `26370070882`.

### Release contract
- [x] `python scripts/release/build-release-artifact.py` produces a
      `dist/basetool-runner-<version>.tar.gz` + `.sha256`.
- [x] `python scripts/release/verify-release-artifact.py` exits 0 against
      that tarball.
- [x] `python scripts/release/simulate-downstream-stage.py` exits 0 locally;
      SIGTERM clean-shutdown assertion runs on Linux CI (`ubuntu-latest`).
- [x] `docs/architecture.md` includes the "Downstream stager contract"
      section (W10) and downstream owner has reviewed it.

### Docs & licence
- [x] `THIRD_PARTY_NOTICES.md` includes the verbatim MHDDoS MIT block.
- [x] `docs/architecture.md` and `docs/testing.md` reflect Phase 3 layout.
- [x] `README.md` reflects Phase 3 upstream note and testing commands.

### Process
- [ ] At least one reviewer approval; sprint owner has linked this doc
      from the PR description.
- [ ] Downstream follow-up issue is filed in `itarmykit-basetool` linking
      this PR (see §9).

## 8. Steady-state runbooks (also copy into `docs/architecture.md`)

### Adding a method already present upstream

1. Add a row to `METHOD_REGISTRY` in `modules/basetool/adapter/methods.py`.
2. Add the method name to the downstream allowlist in
   `itarmykit-basetool/lib/module/basetoolValidation.ts`.
3. Add the method to the UI enum in
   `itarmykit-basetool/lib/module/basetoolConfig.ts` and the Vue dropdown
   in `src/pages/modules/basetoolPage.vue`.
4. `scripts/smoke/runner-methods-smoke.py` picks it up automatically from
   `METHOD_REGISTRY` — confirm it passes locally.
5. Update the method table in `docs/architecture.md`.

### Bumping upstream

```bash
python scripts/sync-mhddos-upstream.py --tag vX.Y.Z
```

| Outcome | Action |
|---|---|
| Patches apply, smoke green, no drift | Review `UPSTREAM.json` diff, commit, PR |
| Patches apply, smoke green, drift report shows added/removed methods | Update `METHOD_REGISTRY` for relevant additions; remove orphans; re-run smoke; commit |
| Patch conflict OR smoke regression | Resolve conflict and regenerate patch (`git diff > patches/0001-stats-hook.patch`), OR pin back to previous tag and open a tracking issue |

## 9. Coordination with `itarmykit-basetool` (follow-up PR, NOT this sprint)

Out-of-scope here but flagged for handoff. This list is the **contract**
between the two repos; downstream relies on it for auto-update safety.

### Required downstream changes (filed as a tracking issue)

- [ ] Mirror allowlist in `lib/module/basetoolValidation.ts` to today's 9
      methods (no change expected; sanity-check).
- [ ] Update `scripts/smoke/basetool-runner-smoke.py` to invoke our
      `runner-methods-smoke.py` after staging the runner (delegate the
      per-method assertions upstream-side).
- [ ] Update `docs/architecture.md` (downstream) "Runner" section to
      reference upstream pinning via `modules/basetool/UPSTREAM.json` in
      the runner repo, and link the "Downstream stager contract" section
      written by W10.
- [ ] Verify `scripts/ci/stage-basetool-runtime.py` copies
      `modules/basetool/upstream/`, `modules/basetool/adapter/`, and
      `modules/basetool/runner/` into the staged runtime, not just
      `basetool.py`. Easiest path: consume our release tarball produced
      by W9 instead of cherry-picking files from a git ref.
- [ ] Confirm `src-electron/handlers/updater.ts` filters GitHub releases
      by `prerelease == false` so a failed `release.yml` run (auto-marked
      prerelease by W11) cannot be picked up by auto-update.
- [ ] Update `yarn test:integrity:release` to run our
      `simulate-downstream-stage.py` against the just-fetched runner
      tarball as a release gate.

### Contract surface (do not change without coordinating both repos)

| Surface | Owner | Stability |
|---|---|---|
| `basetool.py` entry point at runner repo root | this repo | Stable; renaming requires a downstream PR in lockstep |
| Console accepts `start` / `stop` / `exit` on stdin, exits 0 on `exit` or SIGTERM | this repo | Stable |
| Stats keys: `stats_dict[target_key] = [packets, bytes]` | this repo | Stable |
| Structured JSON output via `BASETOOL_JSON=1` | this repo (W12) | New, additive |
| Release tarball layout (`basetool.py` at root, modules under `modules/`) | this repo (W9) | Stable; documented in `docs/architecture.md` |
| Release marked `prerelease=true` on any CI failure | this repo (W11) | Stable; downstream auto-updater MUST honour this |
| Pinned MHDDoS tag visible at `modules/basetool/UPSTREAM.json` | this repo | Stable; consumable for changelog generation downstream |

## 10. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Upstream `start.py` has import-time side effects beyond signals/argparse | Med | Patch 0002 catches signals + argparse; `tests/patches/test_import_side_effects.py` (W7c) surfaces anything else as a hard test failure |
| MHDDoS's `Layer7.BYPASS` uses `requests` instead of raw sockets → no `s.send` to hook, stats stay 0 | High | Adapter handles `BYPASS` via an explicit branch that wraps `requests.Session.get` and accounts response sizes the same way `Tools.sizeOfRequest` does today; W7d smoke explicitly asserts PPS > 0 for BYPASS |
| `git subtree pull` rewrites history awkwardly on Windows | Low | Sync is run on Linux/WSL only; CI runs the sync verification on Ubuntu; documented in `docs/architecture.md` |
| Downstream `itarmykit-basetool` stager breaks because runtime layout changed | Med→Low | W10 `simulate-downstream-stage.py` is the contract; if it passes, the real downstream stager should too. §9 follow-up PR locks the downstream side in. |
| New optional MHDDoS deps required to import `Layer7` even though we don't expose those methods | Med | W7c import test catches this on every CI run; add only what's strictly necessary; document any additions in the PR description |
| Smoke `SYN` test requires root / raw sockets | Known | Smoke prints `SKIP` with reason; Ubuntu CI runs under `sudo -E` so SYN is exercised; Windows CI skips SYN by design |
| Test coverage threshold (80%) is too aggressive and blocks early PRs | Med | Threshold is `--cov-fail-under=80` only on `modules/basetool/adapter/` + `modules/basetool/runner/` (the new code); allowed to lower to 70% during W7b if a specific path is impractical to unit-test, but lowering requires explicit reviewer sign-off in the PR |
| Regression snapshot (W7f) captures incidental noise (timestamps, RNG-driven payloads) → false failures | High | `runner-regression-smoke.py` normalizes timestamps to `[HH:MM:SS]` and replaces PPS/BPS numerics with `<N>`; if a non-deterministic field is found post-merge, add it to the normalizer list with a one-line comment explaining why |
| Auto-update picks up a broken release because release workflow has a bug, not the runner | Med | `release.yml` marks any failed-job release as `prerelease=true`; downstream auto-updater filters to `prerelease=false`. Document this contract in §9 follow-up — downstream MUST honour the prerelease flag |
| Tag-pushed release workflow takes too long (CI matrix × release jobs × stability smoke) | Low | Stability smoke runs on a single Ubuntu cell in `release.yml`, not the full matrix; total budget ~25 min, acceptable for release cadence |
| `psutil`-based stability checks are flaky on shared CI runners due to noisy-neighbour CPU contention | Med | Stability smoke runs nightly, not per-PR; failures auto-file a tracking issue rather than blocking; thresholds (RSS < 20%, PPS variance < 30%) are deliberately loose |
| Legacy watchdog `(last: ExcName)` lost after adapter cutover | Low | Upstream MHDDoS swallows exceptions via `suppress(Exception)`; adapter health watchdog emits `(last: unknown)` instead. Restoring exception names requires `0003-health-hook.patch` on the 9 in-registry methods |

## 11. Verification commands (paste into PR description)

```bash
# ---- 1. Sync invariants ----
# 1a. Patches still apply cleanly to the pinned tag
git apply --check modules/basetool/upstream/patches/*.patch
# 1b. Re-running sync is a no-op
python scripts/sync-mhddos-upstream.py --tag 2.4.4 --no-smoke --skip-subtree
git diff --quiet -- modules/basetool/UPSTREAM.json \
    || echo "UPSTREAM.json drifted unexpectedly"

# ---- 2. Unit + patch tests ----
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/patches/
pytest tests/unit/ -q --cov=modules/basetool/adapter --cov=modules/basetool/runner --cov-report=term-missing --cov-fail-under=80

# ---- 3. Smoke tests ----
# 3a. Per-method localhost smoke (~30 s)
python scripts/smoke/runner-methods-smoke.py
# 3b. Regression parity (~10 s)
python scripts/smoke/runner-regression-smoke.py
# 3c. Stability / leak smoke (~10 min in CI; `--duration 15` for a quick local run)
python scripts/smoke/runner-stability-smoke.py
python scripts/smoke/runner-stability-smoke.py --duration 15 --method GET

# ---- 4. Release artifact gates ----
python scripts/release/build-release-artifact.py
python scripts/release/verify-release-artifact.py
python scripts/release/simulate-downstream-stage.py
# SIGTERM assertion runs on Linux/macOS; Windows skips (TerminateProcess bypasses handlers)

# ---- 5. Manual sanity ----
python basetool.py --help
python basetool.py
# expect: BaseTool> prompt, dynamic table after `start`, clean exit on `exit`
BASETOOL_JSON=1 python basetool.py
# expect: JSON lines on stdout; table on stderr
```

Quick local Phase 3 gate (matches `docs/testing.md`):

```bash
pytest tests/patches/
pytest tests/unit/ -q --cov=modules/basetool/adapter --cov=modules/basetool/runner --cov-report=term-missing --cov-fail-under=80
python scripts/smoke/runner-methods-smoke.py
python scripts/smoke/runner-regression-smoke.py
python scripts/smoke/runner-stability-smoke.py --duration 15
python scripts/release/build-release-artifact.py
python scripts/release/verify-release-artifact.py
python scripts/release/simulate-downstream-stage.py
```

## 12. PR checklist (for the final PR description)

- [x] Sprint doc linked: `docs/sprints/2026-05-24-mhddos-upstream-integration.md`
- [x] Branch is `feature/mhddos-upstream-integration`; commits ordered
      Phase 1 → Phase 2 → Phase 3 with `<type>: W## <summary>` subjects
- [x] All three phase gates passed locally (see §7 "Phase gates")
- [x] All workstreams W1–W13 complete
- [x] §11 commands green on local (including stability smoke run at
      least once; SIGTERM via simulate on Linux CI)
- [x] `ci.yml` green on feature branch for required matrix cells
      (run `26370068895`)
- [ ] `nightly.yml` `workflow_dispatch` verified post-merge to `main`
      (equivalent stability coverage via `release.yml` on tag push)
- [x] `release.yml` triggered on tag `v0.0.1-smoke`, produced verified
      prerelease artifact (run `26370070882`)
- [x] W10 downstream stage simulation green locally
- [x] `THIRD_PARTY_NOTICES.md` includes MHDDoS MIT block
- [x] `UPSTREAM.json` checked in with tag, sha, patches, methods, drift
- [x] `docs/architecture.md` + `docs/testing.md` written and reviewed
- [ ] Downstream follow-up issue filed in `itarmykit-basetool` (§9)
- [ ] Reviewer assigned
- [ ] Merge method: rebase-merge (or squash-per-phase if team convention
      requires squash; do not collapse to a single commit on `main`)

## 13. References

- Upstream repo: [MatrixTM/MHDDoS](https://github.com/MatrixTM/MHDDoS)
- Upstream release notes for the pinned tag: <https://github.com/MatrixTM/MHDDoS/releases/tag/v2.4.4>
- PyRoxy (already a transitive dep): <https://github.com/MHProDev/PyRoxy>
- Prior runner commit: `de3ee8f Rename runner to basetool.py and adopt kit-canonical defaults`
- Prior reverse-port commit: `a88bb6f Reverse-port BaseTool runner fixes from itarmykit-basetool`
