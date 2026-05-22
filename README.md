# MegaTool

**MegaTool** — багатопотоковий інструмент для тестування навантаження на мережеві сервіси (Layer 4 та Layer 7).  
Підтримує атаки TCP, UDP, SYN (L4) та GET, POST, STRESS, SLOW, GSB, BYPASS (L7) із можливістю використання проксі.

## Можливості

- **Layer 4**: TCP flood, UDP flood, SYN flood.
- **Layer 7**: HTTP‑флуд методами GET, POST, Stress (великі POST‑дані), Slowloris (SLOW), GSB, Bypass (проксі-GET через requests).
- **Проксі**: автоматичне завантаження з віддалених джерел (HTTP, SOCKS4, SOCKS5), перевірка працездатності, кешування у `cache/proxies.json` (формат `host:port`) на 24 години.
- **Динамічна таблиця**: вивід PPS та BPS по кожній цілі в реальному часі (висота підлаштовується під кількість цілей).
- **Консольне керування**: команди `start`, `stop`, `exit`.
- **Гнучка конфігурація**: `config.json` та `proxy.json`.
- **User‑Agent / Referer**: випадковий вибір із заданого списку, або стандартні значення.

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
cd MegaTool
pip install -r requirements.txt
```

## Файли
megatool.py - Основний скрипт
requirements.txt - Cписок залежностей Python.
config.json - Файл конфігурації. Тут зберігаються налаштування за замовчуванням.
proxy.json - База джерел проксі-серверів.
README.md - Документація проекту.
--
# MegaTool

**MegaTool** is a multithreaded tool for load testing network services at Layer 4 and Layer 7.  
It supports TCP, UDP, SYN methods at L4 and GET, POST, STRESS, SLOW, GSB, BYPASS methods at L7, with optional proxy support.

## Features

- **Layer 4**: TCP flood, UDP flood, SYN flood
- **Layer 7**: HTTP flood using GET, POST, Stress large POST data, Slowloris SLOW, GSB, and Bypass proxy GET via requests
- **Proxies**: automatic loading from remote sources HTTP, SOCKS4, SOCKS5, availability checks, and caching in `cache/proxies.json` in `host:port` format for 24 hours
- **Dynamic table**: real-time PPS and BPS output for each target, with table height adjusted to the number of targets
- **Console control**: `start`, `stop`, and `exit` commands
- **Flexible configuration**: `config.json` and `proxy.json`
- **User-Agent / Referer**: random selection from a defined list, or default values

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
cd MegaTool
pip install -r requirements.txt
```

№№ Files
megatool.py — main script
requirements.txt — list of Python dependencies
config.json — configuration file containing default settings
proxy.json — proxy source database
README.md — project documentation
