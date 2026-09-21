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
    return isinstance(event, dict) and str(event.get("level", "")).strip().lower() == "critical"


def load_events(path):
    """Читает JSON-массив событий. utf-8-sig — файл мог быть сохранён Блокнотом с BOM."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("ожидался JSON-массив событий")
    return data


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else Path(__file__).with_name("events.json")
    if not path.is_file():  # is_file, а не exists: каталог в аргументе давал трейсбек
        print(f"Файл не найден: {path}", file=sys.stderr)
        return 1
    try:
        events = load_events(path)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
        # Понятное сообщение вместо трейсбека: файл читает проверяющий, а не автор.
        print(f"Не удалось прочитать {path.name}: {exc}", file=sys.stderr)
        return 1

    critical = [event for event in events if is_critical(event)]
    for event in critical:
        print(f"[{event.get('level')}] {event.get('service', '?')}: {event.get('message', '')}")
    print(f"критичных {len(critical)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
