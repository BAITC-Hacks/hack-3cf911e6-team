#!/usr/bin/env python3
"""Автопроверка обоих кейсов: python selftest.py

Зависимостей нет — стандартный unittest. Проверяет три уровня:
  1) правила классификатора на данных задания и на спорных формулировках;
  2) фильтр алертов;
  3) сквозной запуск обоих скриптов как отдельных процессов (как это сделает проверяющий).
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import classify_messages as cm  # noqa: E402
import filter_alerts as fa  # noqa: E402


class TestRules(unittest.TestCase):
    """Категории на пяти обращениях из задания."""

    EXPECTED = [
        ("Как получить справку о месте учёбы?", "справка"),
        ("В столовой очередь, еда холодная.", "жалоба"),
        ("Хочу записаться на консультацию завтра.", "другое"),
        ("Пропал Wi-Fi в корпусе B.", "жалоба"),
        ("Где парковка для гостей?", "справка"),
    ]

    def test_messages_file_matches_task(self):
        texts = cm.read_messages(ROOT / "messages.txt")
        self.assertEqual(texts, [text for text, _ in self.EXPECTED])

    def test_categories(self):
        for text, expected in self.EXPECTED:
            with self.subTest(text=text):
                self.assertEqual(cm.classify(text)[0], expected)

    def test_confidence_is_high_on_task_data(self):
        # Регрессия: правка правил не должна превращать эталонные обращения в спорные.
        for text, _ in self.EXPECTED:
            with self.subTest(text=text):
                self.assertEqual(cm.classify(text)[1], "высокая")

    def test_every_message_gets_nonempty_draft(self):
        for text, _ in self.EXPECTED:
            with self.subTest(text=text):
                reply = cm.draft_reply(cm.classify(text)[0], cm.detect_topic(text))
                self.assertTrue(reply.startswith("Здравствуйте"))
                self.assertGreater(len(reply), 80)

    def test_drafts_are_topic_specific(self):
        # Жалоба про еду и жалоба про сеть не должны получать один и тот же шаблон.
        food = cm.draft_reply("жалоба", cm.detect_topic("В столовой очередь, еда холодная."))
        net = cm.draft_reply("жалоба", cm.detect_topic("Пропал Wi-Fi в корпусе B."))
        self.assertNotEqual(food, net)


class TestRulesEdgeCases(unittest.TestCase):
    def test_no_markers_goes_to_other_with_low_confidence(self):
        category, confidence, signals = cm.classify("Абракадабра шмабракадабра")
        self.assertEqual(category, "другое")
        self.assertEqual(confidence, "низкая")
        self.assertEqual(signals, [])

    def test_uppercase_and_yo_are_normalized(self):
        self.assertEqual(cm.classify("ГДЕ ПАРКОВКА ДЛЯ ГОСТЕЙ?")[0], "справка")
        self.assertEqual(cm.classify("Пропала СВЯЗЬ, ничего не работает")[0], "жалоба")

    def test_nonbreaking_hyphen_in_wifi(self):
        # В тексте задания Wi‑Fi набран неразрывным дефисом U+2011.
        self.assertEqual(cm.detect_topic("Пропал Wi‑Fi в корпусе B."), "ит")

    def test_complaint_verb_forms(self):
        for text in ("Хочу пожаловаться на грубость", "Жалуюсь на шум в аудитории"):
            with self.subTest(text=text):
                self.assertEqual(cm.classify(text)[0], "жалоба")

    def test_question_shaped_complaint_stays_complaint(self):
        self.assertEqual(cm.classify("Почему не работает Wi-Fi?")[0], "жалоба")

    def test_mixed_intent_prefers_dominant_category(self):
        # Ради этого случая правила взвешенные, а не «первое совпадение»: тут просят
        # справку, хотя в тексте есть и маркер жалобы («очередь»).
        self.assertEqual(
            cm.classify("Подскажите, где получить справку, а то в деканате очередь")[0],
            "справка",
        )

    def test_signals_have_no_regex_syntax(self):
        signals = cm.classify("Предлагаю поставить кулер на 3 этаже")[2]
        self.assertTrue(signals)
        for signal in signals:
            for syntax in ("|", "\\b", "[", "]"):
                self.assertNotIn(syntax, signal)


class TestAlerts(unittest.TestCase):
    def test_task_data_has_three_critical(self):
        events = json.loads((ROOT / "events.json").read_text(encoding="utf-8"))
        self.assertEqual(len(events), 8)
        self.assertEqual([e["id"] for e in events if fa.is_critical(e)], [1, 4, 6])

    def test_level_is_case_insensitive(self):
        self.assertTrue(fa.is_critical({"level": "CRITICAL"}))
        self.assertTrue(fa.is_critical({"level": " critical "}))
        self.assertFalse(fa.is_critical({"level": "warn"}))
        self.assertFalse(fa.is_critical({"message": "уровня нет"}))
        self.assertFalse(fa.is_critical("строка вместо объекта"))


class TestBadInput(unittest.TestCase):
    """Файлы, которые может подсунуть проверяющий: BOM от Блокнота, битый JSON, урезанные поля.

    Требование ко всем случаям: понятное сообщение и код возврата, а не трейсбек.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def run_script(self, script, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / script), *args],
            cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace",
        )

    def write(self, name, text, encoding="utf-8"):
        path = self.dir / name
        path.write_text(text, encoding=encoding)
        return str(path)

    def test_messages_with_bom(self):
        path = self.write("m.txt", "Пропал Wi-Fi в корпусе B.\n", encoding="utf-8-sig")
        result = self.run_script("classify_messages.py", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("﻿", result.stdout)  # BOM не должен попасть в текст обращения
        self.assertIn("жалоба", result.stdout)

    def test_events_with_bom(self):
        path = self.write("e.json", '[{"level": "critical", "service": "db", "message": "x"}]',
                          encoding="utf-8-sig")
        result = self.run_script("filter_alerts.py", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("критичных 1", result.stdout)

    def test_event_without_optional_fields(self):
        path = self.write("e.json", '[{"level": "critical", "message": "диск"}]')
        result = self.run_script("filter_alerts.py", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("критичных 1", result.stdout)

    def test_directory_instead_of_file_is_reported(self):
        for script in ("classify_messages.py", "filter_alerts.py"):
            with self.subTest(script=script):
                result = self.run_script(script, str(self.dir))
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr)

    def test_broken_json_reports_instead_of_crashing(self):
        for content in ("{ сломано", '{"events": []}'):
            with self.subTest(content=content):
                path = self.write("e.json", content)
                result = self.run_script("filter_alerts.py", path)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("Не удалось прочитать", result.stderr)


class TestEndToEnd(unittest.TestCase):
    """Запуск скриптов процессами — проверка того, что увидит проверяющий."""

    def run_script(self, *args, expect_code=0):
        result = subprocess.run(
            [sys.executable, *args],
            cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, expect_code, result.stderr)
        return result.stdout

    def test_filter_prints_only_critical_and_one_summary_line(self):
        # Буква задания: «печатает только critical и одну фразу summary».
        lines = self.run_script("filter_alerts.py").strip().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[-1], "критичных 3")
        for noise in ("user login", "cpu 40%", "heartbeat", "cache miss", "deploy ok"):
            self.assertNotIn(noise, "\n".join(lines))

    def test_classify_prints_category_and_draft_for_each(self):
        out = self.run_script("classify_messages.py")
        self.assertEqual(out.count("категория:"), 5)
        self.assertEqual(out.count("черновик ответа:"), 5)

    def test_missing_file_exits_with_code_1(self):
        self.run_script("classify_messages.py", "нет-такого.txt", expect_code=1)
        self.run_script("filter_alerts.py", "нет-такого.json", expect_code=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
