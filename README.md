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
