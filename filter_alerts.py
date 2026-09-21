#!/usr/bin/env python3
"""Фильтр алертов: из потока событий оставляет только critical.

Запуск:
    python filter_alerts.py              # читает events.json рядом со скриптом
    python filter_alerts.py other.json   # другой файл того же формата

Формат файла: JSON-массив объектов [{"id", "level", "service", "message"}, ...]
или объект {"events": [...]}. Уровни: info / warn / critical.
Зависимостей нет — только стандартная библиотека Python 3.8+.
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

CRITICAL = "critical"
KNOWN_LEVELS = {"info", "warn", "critical"}


def load_events(path):
    """Читает файл и возвращает список событий (поддерживает оба формата)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("events", [])
    if not isinstance(data, list):
        raise ValueError("ожидался JSON-массив событий или объект с ключом 'events'")
    return data


def level_of(event):
    """Уровень события в нижнем регистре; 'unknown', если поля нет."""
    if not isinstance(event, dict):
        return "unknown"
    return str(event.get("level", "unknown")).strip().lower()


def is_critical(event):
    return level_of(event) == CRITICAL


def format_event(event):
    parts = [f"[{level_of(event).upper()}]"]
    if event.get("id") is not None:
        parts.append(f"#{event['id']}")
    if event.get("service"):
        parts.append(f"{event['service']}:")
    parts.append(str(event.get("message", "")).strip() or "(без текста)")
    return " ".join(parts)


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else Path(__file__).with_name("events.json")
    if not path.exists():
        print(f"Файл не найден: {path}", file=sys.stderr)
        return 1
    try:
        events = load_events(path)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Не удалось разобрать {path}: {exc}", file=sys.stderr)
        return 1

    critical = [e for e in events if is_critical(e)]

    print(f"Источник: {path.name} — всего событий: {len(events)}")
    print("-" * 60)
    for event in critical:
        print(format_event(event))
    if not critical:
        print("(критичных событий нет)")
    print("-" * 60)
    print(f"критичных {len(critical)}")

    # Диагностика: события с нераспознанным уровнем не теряются молча.
    unknown = [level_of(e) for e in events if level_of(e) not in KNOWN_LEVELS]
    if unknown:
        print(f"Предупреждение: нераспознанные уровни: {sorted(set(unknown))}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
