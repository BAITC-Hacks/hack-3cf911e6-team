#!/usr/bin/env python3
"""Классификатор обращений: категория + черновик ответа для каждого обращения.

Запуск:
    python classify_messages.py                 # правила, читает messages.txt
    python classify_messages.py other.txt       # другой файл (одно обращение на строку)
    python classify_messages.py --json          # машиночитаемый вывод
    python classify_messages.py --llm           # категории одним запросом к Claude

Категории: справка / жалоба / другое.
    справка — просят информацию или документ («как получить», «где», «сколько»);
    жалоба  — сообщают о проблеме или недовольстве («пропал», «очередь», «холодная»);
    другое  — всё остальное: запись на приём, предложения, заявки.

По умолчанию зависимостей нет — только стандартная библиотека Python 3.8+.
Режим --llm требует `pip install anthropic` и ключ ANTHROPIC_API_KEY; при любой
ошибке скрипт не падает, а возвращается к правилам.
"""

import json
import re
import sys
import textwrap
from pathlib import Path

# Кириллица в stdout на Windows: без этого вывод ломается при перенаправлении в файл/пайп.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # Python < 3.7 или подменённый поток
        pass

CATEGORIES = ("справка", "жалоба", "другое")
FALLBACK_CATEGORY = "другое"
MODEL = "claude-opus-5"

# --- Правила категоризации -------------------------------------------------
# (regex, вес). Веса подобраны так, чтобы явный маркер (3) перевешивал слабый (1).
RULES = {
    "жалоба": [
        (r"не работает", 3), (r"перестал", 2), (r"пропал", 3), (r"слома", 3),
        (r"очеред", 2), (r"холодн", 2), (r"грязн", 2), (r"жалоб", 3), (r"хамств", 3),
        (r"груб", 2), (r"долго жд", 2), (r"не могу", 2), (r"отсутствует", 2),
        (r"шум", 2), (r"протека", 2), (r"плохо", 2), (r"ужасн", 2),
        (r"недоволен|недовольна", 2), (r"сбой", 2), (r"не открыва", 2),
    ],
    "справка": [
        (r"справк", 3), (r"как получить", 3), (r"как оформить", 3),
        (r"\bгде\b", 2), (r"\bкуда\b", 2), (r"\bкогда\b", 2), (r"\bсколько\b", 2),
        (r"документ", 2), (r"режим работы", 2), (r"расписан", 2),
        (r"подскажите", 1), (r"уточнить", 1), (r"\bкакие\b", 1),
    ],
    "другое": [
        (r"записаться|запишите", 3), (r"\bзапис", 2), (r"консультац", 2),
        (r"предлага|предложение", 2), (r"заявк", 1), (r"встреч", 2),
        (r"\bприем\b", 1),
    ],
}

# Тема нужна только для выбора шаблона ответа, на категорию она не влияет.
# Порядок важен: при равном числе совпадений выигрывает тема, указанная выше.
TOPICS = [
    ("документы", [r"справк", r"документ", r"диплом", r"выписк", r"зачетк"]),
    ("питание", [r"столов", r"буфет", r"\bед[аыу]\b", r"обед", r"\bмен[юя]\b", r"кухн"]),
    ("ит", [r"wi-?fi", r"вайфай", r"интернет", r"\bсет[ьи]\b", r"компьютер",
            r"принтер", r"пароль", r"личн\w+ кабинет"]),
    ("парковка", [r"парковк", r"стоянк", r"автомобил", r"\bмашин"]),
    ("запись", [r"запис", r"консультац", r"\bприем\b", r"встреч"]),
    ("помещение", [r"корпус", r"аудитор", r"лифт", r"отоплен", r"туалет", r"общежит"]),
]

# --- Шаблоны черновиков ----------------------------------------------------
# Сроки и подразделения в шаблонах — заглушки: подставьте регламент своего вуза.
REPLIES = {
    ("справка", "документы"): (
        "Здравствуйте! Справки об учёбе выдаёт учебный отдел (деканат): заявку можно "
        "оставить лично или через личный кабинет, срок подготовки — 1–2 рабочих дня. "
        "Напишите, пожалуйста, ФИО, группу и нужное количество экземпляров — подготовим "
        "документ и сообщим, когда он будет готов."
    ),
    ("справка", "парковка"): (
        "Здравствуйте! Для гостей предусмотрены места на территории кампуса, въезд — по "
        "заранее оформленному пропуску на посту охраны. Сообщите дату визита, марку и "
        "номер автомобиля — оформим пропуск и пришлём схему проезда."
    ),
    ("жалоба", "питание"): (
        "Здравствуйте! Спасибо, что сообщили, и извините за неудобства. Передали "
        "обращение в службу питания: проверим график раздачи и температурный режим на "
        "линии, по итогам вернёмся с ответом в течение 3 рабочих дней. Если подскажете "
        "точное время и корпус, проверка пойдёт быстрее."
    ),
    ("жалоба", "ит"): (
        "Здравствуйте! Спасибо за сигнал — зарегистрировали заявку в ИТ-службу. "
        "Специалисты проверят точки доступа в указанном корпусе, ориентировочный срок "
        "диагностики — до конца рабочего дня. Уточните, пожалуйста, этаж или номер "
        "аудитории и время, когда связь пропала."
    ),
    ("другое", "запись"): (
        "Здравствуйте! Записать вас на консультацию можно — подтвердите, пожалуйста, "
        "удобный интервал (например, до или после 14:00) и тему обращения. После "
        "подтверждения пришлём точное время, место и имя специалиста."
    ),
}
REPLIES_BY_CATEGORY = {
    "справка": (
        "Здравствуйте! Спасибо за вопрос. Уточните, пожалуйста, детали — что именно "
        "нужно и к какому сроку, — и мы пришлём точную информацию со ссылкой на регламент."
    ),
    "жалоба": (
        "Здравствуйте! Спасибо за обращение — зафиксировали проблему и передали в "
        "ответственное подразделение. Вернёмся с решением в течение 3 рабочих дней, при "
        "необходимости уточним детали. Извините за неудобства."
    ),
    "другое": (
        "Здравствуйте! Обращение получили и передали профильному специалисту. Ответим в "
        "течение 3 рабочих дней; если вопрос срочный, напишите об этом в ответном сообщении."
    ),
}


def normalize(text):
    """Приводит текст к виду, на котором работают правила."""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[‐-―−]", "-", text)  # типографские дефисы и минус
    return re.sub(r"\s+", " ", text).strip()


def score_categories(text):
    """Считает баллы по каждой категории. Возвращает (баллы, сработавшие маркеры)."""
    norm = normalize(text)
    scores = {category: 0 for category in CATEGORIES}
    signals = {category: [] for category in CATEGORIES}
    for category, markers in RULES.items():
        for pattern, weight in markers:
            if re.search(pattern, norm):
                scores[category] += weight
                signals[category].append(pattern.replace(r"\b", ""))
    if norm.endswith("?"):  # слабый сигнал: вопрос чаще запрос информации
        scores["справка"] += 1
        signals["справка"].append("вопросительный знак")
    return scores, signals


def classify_by_rules(text):
    """Категория по правилам + уверенность и сработавшие маркеры."""
    scores, signals = score_categories(text)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, best_score = ranked[0]
    runner_up_score = ranked[1][1]
    if best_score == 0:  # ни одного маркера — честно отправляем в «другое»
        return FALLBACK_CATEGORY, "низкая", []
    # Отрыв меньше 2 баллов — спорный случай, такие стоит посмотреть руками.
    confidence = "высокая" if best_score - runner_up_score >= 2 else "низкая"
    return best, confidence, signals[best]


def detect_topic(text):
    """Тема обращения — для выбора шаблона ответа."""
    norm = normalize(text)
    best_topic, best_hits = None, 0
    for topic, patterns in TOPICS:
        hits = sum(1 for pattern in patterns if re.search(pattern, norm))
        if hits > best_hits:  # строго больше => при равенстве выигрывает порядок в TOPICS
            best_topic, best_hits = topic, hits
    return best_topic


def draft_reply(category, topic):
    """Один черновик ответа: шаблон по паре (категория, тема), иначе по категории."""
    return REPLIES.get((category, topic)) or REPLIES_BY_CATEGORY[category]


# --- Опциональный режим LLM ------------------------------------------------
LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                },
                "required": ["id", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

LLM_SYSTEM = (
    "Ты классифицируешь обращения студентов в службу поддержки вуза. "
    "Категории строго три:\n"
    "справка — просят информацию или документ (как получить, где, сколько, режим работы);\n"
    "жалоба — сообщают о проблеме, поломке или недовольстве сервисом;\n"
    "другое — всё остальное: запись на приём, предложения, заявки.\n"
    "Верни категорию для каждого обращения по его номеру, без пояснений."
)


def classify_with_llm(texts):
    """Одним запросом получает категории для всех обращений.

    Возвращает список категорий или None — тогда вызывающий код работает по правилам.
    """
    try:
        import anthropic
    except ImportError:
        print("режим --llm: пакет anthropic не установлен (pip install anthropic) — "
              "категории по правилам", file=sys.stderr)
        return None

    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(texts, 1))
    try:
        client = anthropic.Anthropic()  # ключ из ANTHROPIC_API_KEY или профиля ant auth login
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=LLM_SYSTEM,
            messages=[{"role": "user", "content": f"Обращения:\n{numbered}"}],
            # Задача простая — низкий effort дешевле и быстрее; схема гарантирует валидный JSON.
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": LLM_SCHEMA},
            },
            # Если модель откажется отвечать, запрос доигрывается на резервной модели.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            print("режим --llm: модель отклонила запрос — категории по правилам", file=sys.stderr)
            return None
        payload = json.loads(next(b.text for b in response.content if b.type == "text"))
    except Exception as exc:  # сеть, ключ, лимиты, формат — любой сбой не должен ронять скрипт
        print(f"режим --llm: {type(exc).__name__}: {exc} — категории по правилам", file=sys.stderr)
        return None

    by_id = {item["id"]: item["category"] for item in payload.get("items", [])}
    result = []
    for index in range(1, len(texts) + 1):
        category = by_id.get(index)
        if category not in CATEGORIES:  # ответ не по схеме — добираем правилами
            category = classify_by_rules(texts[index - 1])[0]
        result.append(category)
    return result


# --- Разбор входа и вывод --------------------------------------------------
def read_messages(path):
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def build_results(texts, llm_categories):
    """Собирает по каждому обращению категорию, тему, уверенность и черновик."""
    results = []
    for index, text in enumerate(texts, 1):
        category, confidence, signals = classify_by_rules(text)
        source = "правила"
        if llm_categories:
            llm_category = llm_categories[index - 1]
            source = "llm"
            if llm_category != category:  # расхождение с правилами — повод проверить руками
                confidence = "низкая"
            category = llm_category
        topic = detect_topic(text)
        results.append({
            "id": index,
            "text": text,
            "category": category,
            "confidence": confidence,
            "source": source,
            "signals": signals,
            "topic": topic,
            "reply": draft_reply(category, topic),
        })
    return results


def print_report(results, source_label):
    print(f"Обращений: {len(results)} | категории: {source_label}")
    print("=" * 78)
    for item in results:
        print(f"[{item['id']}] {item['text']}")
        details = [f"уверенность: {item['confidence']}"]
        if item["topic"]:
            details.append(f"тема: {item['topic']}")
        if item["signals"]:
            details.append("сигналы: " + ", ".join(item["signals"]))
        print(f"    категория: {item['category']}  ({'; '.join(details)})")
        print("    черновик ответа:")
        for line in textwrap.wrap(item["reply"], width=74):
            print(f"      {line}")
        print()
    counts = {c: sum(1 for i in results if i["category"] == c) for c in CATEGORIES}
    print("-" * 78)
    print("Итого: " + ", ".join(f"{c} — {n}" for c, n in counts.items()))
    unsure = [str(i["id"]) for i in results if i["confidence"] == "низкая"]
    if unsure:
        print(f"На ручную проверку (низкая уверенность): {', '.join(unsure)}")


def main(argv):
    flags = {a for a in argv[1:] if a.startswith("--")}
    args = [a for a in argv[1:] if not a.startswith("--")]
    unknown = flags - {"--llm", "--json"}
    if unknown:
        print(f"Неизвестные флаги: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    path = Path(args[0]) if args else Path(__file__).with_name("messages.txt")
    if not path.exists():
        print(f"Файл не найден: {path}", file=sys.stderr)
        return 1
    texts = read_messages(path)
    if not texts:
        print(f"В файле {path} нет обращений", file=sys.stderr)
        return 1

    llm_categories = classify_with_llm(texts) if "--llm" in flags else None
    results = build_results(texts, llm_categories)

    if "--json" in flags:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_report(results, f"LLM ({MODEL})" if llm_categories else "правила")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
