#!/usr/bin/env python3
"""Фильтр алертов: печатает только critical и строку «критичных N».

Запуск:
    python filter_alerts.py              # читает events.json рядом со скриптом
    python filter_alerts.py other.json   # другой файл того же формата

Формат: JSON-массив объектов [{"id", "level", "service", "message"}, ...],
уровни info / warn / critical. Зависимостей нет — стандартная библиотека Python 3.8+.
"""

import json
import sys
from pathlib import Path

# Кириллица в stdout на Windows: без этого вывод ломается при перенаправлении в файл/пайп.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # Python < 3.7 или подменённый поток
        pass


def is_critical(event):
    return str(event.get("level", "")).strip().lower() == "critical"


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else Path(__file__).with_name("events.json")
    if not path.exists():
        print(f"Файл не найден: {path}", file=sys.stderr)
        return 1

    events = json.loads(path.read_text(encoding="utf-8"))
    critical = [event for event in events if is_critical(event)]
    for event in critical:
        print(f"[{event['level']}] {event['service']}: {event['message']}")
    print(f"критичных {len(critical)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
