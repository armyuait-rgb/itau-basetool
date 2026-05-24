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

## Встановлення

```bash
git clone <repo-url>
cd BaseTool
pip install -r requirements.txt
```

## Тестування (Phase 3)

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/patches/
pytest tests/unit/ -q --cov=modules/basetool/adapter --cov=modules/basetool/runner --cov-report=term-missing --cov-fail-under=80
python scripts/smoke/runner-methods-smoke.py
python scripts/smoke/runner-regression-smoke.py
python scripts/smoke/runner-stability-smoke.py --duration 15
python scripts/release/build-release-artifact.py
python scripts/release/verify-release-artifact.py
python scripts/release/simulate-downstream-stage.py
```

## Синхронізація upstream

```bash
python scripts/sync-mhddos-upstream.py --tag 2.4.4
python scripts/sync-mhddos-upstream.py --tag 2.4.4 --no-smoke --skip-subtree
```

Докладніше: [docs/testing.md](docs/testing.md).

## Файли
- basetool.py - Основний скрипт
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
- docs/testing.md - Команди тестування.
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

## Installation

```bash
git clone <repo-url>
cd BaseTool
pip install -r requirements.txt
```

## Testing (Phase 3)

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/patches/
pytest tests/unit/ -q --cov=modules/basetool/adapter --cov=modules/basetool/runner --cov-report=term-missing --cov-fail-under=80
python scripts/smoke/runner-methods-smoke.py
python scripts/smoke/runner-regression-smoke.py
python scripts/smoke/runner-stability-smoke.py --duration 15
python scripts/release/build-release-artifact.py
python scripts/release/verify-release-artifact.py
python scripts/release/simulate-downstream-stage.py
```

## Upstream sync

```bash
python scripts/sync-mhddos-upstream.py --tag 2.4.4
python scripts/sync-mhddos-upstream.py --tag 2.4.4 --no-smoke --skip-subtree
```

See [docs/testing.md](docs/testing.md) for the full test pyramid.

## Files
- basetool.py — main script
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
- docs/testing.md — testing commands
- README.md — project documentation
