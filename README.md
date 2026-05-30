# BaseTool

**BaseTool** — багатопотоковий інструмент для тестування навантаження на мережеві сервіси (Layer 4 та Layer 7).  
Підтримує атаки TCP, UDP, SYN (L4) та GET, POST, STRESS, SLOW, GSB, BYPASS (L7) із можливістю використання проксі.

## Можливості

- **Layer 4**: TCP flood, UDP flood, SYN flood.
- **Layer 7**: HTTP‑флуд методами GET, POST, Stress (великі POST‑дані), Slowloris (SLOW), GSB, Bypass (проксі-GET через requests).
- **Проксі**: автоматичне завантаження з віддалених джерел (HTTP, SOCKS4, SOCKS5), перевірка працездатності, кешування у `cache/proxies.json` (формат `host:port`) на 24 години.
- **Динамічна таблиця**: вивід PPS та BPS по кожній цілі в реальному часі (висота підлаштовується під кількість цілей).
- **Консольне керування**: команди `start`, `stop`, `exit`.
- **Гнучка конфігурація**: `config.json` та `proxy.json`.
- **User‑Agent / Referer**: випадковий вибір із заданого списку, або стандартні значення.

> **MHDDoS upstream integration (Phase 3):** attacks run through the adapter in
> `modules/basetool/adapter/` over vendored [MatrixTM/MHDDoS](https://github.com/MatrixTM/MHDDoS)
> tag `2.4.4`. Runner orchestration lives in `modules/basetool/runner/`.
> Release tarballs are built with `scripts/release/build-release-artifact.py`.
> Details: [docs/architecture.md](docs/architecture.md).

## Вимоги

- Python 3.9+
- права root для SYN flood (RAW‑сокети)
- залежності з `requirements.txt`:
  - `PyRoxy`
  - `impacket`
  - `requests`
  - `yarl`
  - `cryptography`

## Встановлення

```bash
git clone <repo-url>
cd BaseTool
pip install -r requirements.txt
```

## Перевірка працездатності

Остання локальна перевірка: **2026-05-25**, commit [`5889ff3`](https://github.com/armyuait-rgb/itau-basetool/commit/5889ff3), Windows dev box.

| Перевірка | Статус | Що підтверджує |
|---|---|---|
| Pytest packs | ✅ 61 passed, 1 skipped | модулі, оркестрація, release-контракт, upstream safety |
| Methods smoke | ✅ 8/8 (SYN skip на Windows) | кожен метод атаки генерує трафік |
| Regression snapshot | ✅ parity OK | стабільний вивід консолі |
| Stability smoke | ✅ RSS 0% | без витоків пам’яті / зависань потоків |
| Release artifact | ✅ verified | tarball + sha256, golden manifest |
| Downstream simulation | ✅ OK | розгортання як у itarmykit-basetool |

Повний ship gate (запускати перед тегом або merge release-hardening):

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/patches tests/unit tests/integration tests/orchestration tests/release tests/upstream -q
python scripts/smoke/runner-regression-smoke.py
python scripts/smoke/runner-methods-smoke.py
python scripts/smoke/runner-stability-smoke.py --duration 15
python scripts/release/build-release-artifact.py
python scripts/release/verify-release-artifact.py dist/basetool-runner-dev-<sha>.tar.gz
python scripts/release/simulate-downstream-stage.py dist/basetool-runner-dev-<sha>.tar.gz
```

CI macOS matrix ще не верифікований на GitHub Actions — див. [docs/testing.md](docs/testing.md).

## Синхронізація upstream

```bash
python scripts/sync-mhddos-upstream.py --tag 2.4.4
python scripts/sync-mhddos-upstream.py --tag 2.4.4 --no-smoke --skip-subtree
```

Докладніше: [docs/testing.md](docs/testing.md) — повна піраміда тестів і таблиця статусу.

## Файли
- basetool.py - Основний скрипт
- crypto.py - AES-256-GCM helpers for encrypted runtime configs
- requirements.txt - Cписок залежностей Python.
- requirements-dev.txt - Залежності для тестів (pytest).
- config.json - Файл конфігурації. Тут зберігаються налаштування за замовчуванням.
- proxy.json - База джерел проксі-серверів.
- modules/basetool/adapter/ - Адаптер атак і METHOD_REGISTRY.
- modules/basetool/runner/ - Оркестрація раннера (manager, monitor, proxy).
- modules/basetool/UPSTREAM.json - Зафіксований upstream manifest.
- modules/basetool/upstream/ - Vendored MHDDoS upstream та патчі.
- scripts/sync-mhddos-upstream.py - Синхронізація upstream і manifest.
- scripts/smoke/ - Localhost smoke harnesses.
- scripts/release/ - Release tarball build, verify, and downstream stage simulation.
- docs/architecture.md - Архітектура upstream integration.
- docs/testing.md - Команди тестування та таблиця статусу працездатності.
- docs/engine-boundary.md - Межа публічного engine-репозиторію.
- README.md - Документація проекту.
--
# BaseTool

**BaseTool** is a multithreaded tool for load testing network services at Layer 4 and Layer 7.  
It supports TCP, UDP, SYN methods at L4 and GET, POST, STRESS, SLOW, GSB, BYPASS methods at L7, with optional proxy support.

## Features

- **Layer 4**: TCP flood, UDP flood, SYN flood
- **Layer 7**: HTTP flood using GET, POST, Stress large POST data, Slowloris SLOW, GSB, and Bypass proxy GET via requests
- **Proxies**: automatic loading from remote sources HTTP, SOCKS4, SOCKS5, availability checks, and caching in `cache/proxies.json` in `host:port` format for 24 hours
- **Dynamic table**: real-time PPS and BPS output for each target, with table height adjusted to the number of targets
- **Console control**: `start`, `stop`, and `exit` commands
- **Flexible configuration**: `config.json` and `proxy.json`
- **User-Agent / Referer**: random selection from a defined list, or default values

> **MHDDoS upstream integration (Phase 3):** attacks run through the adapter in
> `modules/basetool/adapter/` over vendored [MatrixTM/MHDDoS](https://github.com/MatrixTM/MHDDoS)
> tag `2.4.4`. Runner orchestration lives in `modules/basetool/runner/`.
> Release tarballs are built with `scripts/release/build-release-artifact.py`.
> See [docs/architecture.md](docs/architecture.md).

## Requirements

- Python 3.9+
- Root privileges for SYN flood RAW sockets
- Dependencies from `requirements.txt`:
  - `PyRoxy`
  - `impacket`
  - `requests`
  - `yarl`
  - `cryptography`

## Installation

```bash
git clone <repo-url>
cd BaseTool
pip install -r requirements.txt
```

## Workability check

Last local verification: **2026-05-25**, commit [`5889ff3`](https://github.com/armyuait-rgb/itau-basetool/commit/5889ff3), Windows dev box.

| Check | Status | What it proves |
|---|---|---|
| Pytest packs | ✅ 61 passed, 1 skipped | modules, orchestration scripts, release contract, upstream safety |
| Methods smoke | ✅ 8/8 (SYN skip on Windows) | every attack method generates traffic |
| Regression snapshot | ✅ parity OK | stable console output |
| Stability smoke | ✅ 0% RSS growth | no memory leaks or thread drift |
| Release artifact | ✅ verified | tarball + sha256, golden manifest |
| Downstream simulation | ✅ OK | staging like itarmykit-basetool auto-update |

Full ship gate (run before tagging or merging release-hardening):

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/patches tests/unit tests/integration tests/orchestration tests/release tests/upstream -q
python scripts/smoke/runner-regression-smoke.py
python scripts/smoke/runner-methods-smoke.py
python scripts/smoke/runner-stability-smoke.py --duration 15
python scripts/release/build-release-artifact.py
python scripts/release/verify-release-artifact.py dist/basetool-runner-dev-<sha>.tar.gz
python scripts/release/simulate-downstream-stage.py dist/basetool-runner-dev-<sha>.tar.gz
```

CI macOS matrix not yet verified on GitHub Actions — see [docs/testing.md](docs/testing.md).

## Upstream sync

```bash
python scripts/sync-mhddos-upstream.py --tag 2.4.4
python scripts/sync-mhddos-upstream.py --tag 2.4.4 --no-smoke --skip-subtree
```

See [docs/testing.md](docs/testing.md) for the full test pyramid and workability status table.

## Files
- basetool.py — main script
- crypto.py — AES-256-GCM helpers for encrypted runtime configs
- requirements.txt — list of Python dependencies
- requirements-dev.txt — test dependencies (pytest)
- config.json — configuration file containing default settings
- proxy.json — proxy source database
- modules/basetool/adapter/ — attack adapter and METHOD_REGISTRY
- modules/basetool/runner/ — runner orchestration (manager, monitor, proxy)
- modules/basetool/UPSTREAM.json — pinned upstream manifest
- modules/basetool/upstream/ — vendored MHDDoS upstream and patches
- scripts/sync-mhddos-upstream.py — upstream sync and manifest refresh
- scripts/smoke/ — localhost smoke harnesses
- scripts/release/ — release tarball build, verify, and downstream stage simulation
- docs/architecture.md — upstream integration architecture
- docs/testing.md — testing commands and workability status
- docs/engine-boundary.md — public engine repository boundary
- README.md — project documentation

## Scope

This repository is the **public core engine** only. Application packaging, UI integration, and deployment tooling live in separate downstream projects.

See [docs/engine-boundary.md](docs/engine-boundary.md) for what belongs in this repo.
