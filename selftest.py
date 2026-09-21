#!/usr/bin/env python3
"""Автопроверка обоих кейсов: python selftest.py

Зависимостей нет — стандартный unittest. Проверяет три уровня:
  1) правила классификатора на данных задания и на спорных формулировках;
  2) фильтр алертов на штатном и нестандартном входе;
  3) сквозной запуск обоих скриптов как отдельных процессов (как это сделает проверяющий).
"""

import json
import subprocess
import sys
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
                self.assertEqual(cm.classify_by_rules(text)[0], expected)

    def test_confidence_is_high_on_task_data(self):
        # Регрессия: правка правил не должна превращать эталонные обращения в спорные.
        for text, _ in self.EXPECTED:
            with self.subTest(text=text):
                self.assertEqual(cm.classify_by_rules(text)[1], "высокая")

    def test_every_message_gets_nonempty_draft(self):
        for text, _ in self.EXPECTED:
            with self.subTest(text=text):
                category, _, _ = cm.classify_by_rules(text)
                reply = cm.draft_reply(category, cm.detect_topic(text))
                self.assertTrue(reply.startswith("Здравствуйте"))
                self.assertGreater(len(reply), 80)

    def test_drafts_are_topic_specific(self):
        # Жалоба про еду и жалоба про сеть не должны получать один и тот же шаблон.
        food = cm.draft_reply("жалоба", cm.detect_topic("В столовой очередь, еда холодная."))
        net = cm.draft_reply("жалоба", cm.detect_topic("Пропал Wi-Fi в корпусе B."))
        self.assertNotEqual(food, net)


class TestRulesEdgeCases(unittest.TestCase):
    def test_no_markers_goes_to_other_with_low_confidence(self):
        category, confidence, signals = cm.classify_by_rules("Абракадабра шмабракадабра")
        self.assertEqual(category, "другое")
        self.assertEqual(confidence, "низкая")
        self.assertEqual(signals, [])

    def test_uppercase_and_yo_are_normalized(self):
        self.assertEqual(cm.classify_by_rules("ГДЕ ПАРКОВКА ДЛЯ ГОСТЕЙ?")[0], "справка")
        self.assertEqual(cm.classify_by_rules("Пропала СВЯЗЬ, ничего не работает")[0], "жалоба")

    def test_nonbreaking_hyphen_in_wifi(self):
        # В тексте задания Wi‑Fi набран неразрывным дефисом U+2011.
        self.assertEqual(cm.detect_topic("Пропал Wi‑Fi в корпусе B."), "ит")

    def test_complaint_verb_forms(self):
        for text in ("Хочу пожаловаться на грубость", "Жалуюсь на шум в аудитории"):
            with self.subTest(text=text):
                self.assertEqual(cm.classify_by_rules(text)[0], "жалоба")

    def test_question_shaped_complaint_stays_complaint(self):
        self.assertEqual(cm.classify_by_rules("Почему не работает Wi-Fi?")[0], "жалоба")

    def test_signals_have_no_regex_syntax(self):
        _, _, signals = cm.classify_by_rules("Предлагаю поставить кулер на 3 этаже")
        self.assertTrue(signals)
        for signal in signals:
            self.assertNotIn("|", signal)
            self.assertNotIn("\\b", signal)

    def test_llm_disagreement_is_flagged_for_review(self):
        # Категория берётся от LLM, но расхождение с правилами понижает уверенность.
        results = cm.build_results(["В столовой очередь, еда холодная."], ["справка"])
        self.assertEqual(results[0]["category"], "справка")
        self.assertEqual(results[0]["rule_category"], "жалоба")
        self.assertEqual(results[0]["confidence"], "низкая")

    def test_llm_answer_outside_schema_falls_back_to_rules(self):
        # Категория вне трёх разрешённых (или пропущенный номер) не должна ронять скрипт.
        for bad in ("мусор", None, ""):
            with self.subTest(bad=bad):
                results = cm.build_results(["Пропал Wi-Fi в корпусе B."], [bad])
                self.assertEqual(results[0]["category"], "жалоба")
                self.assertEqual(results[0]["source"], "правила")
                self.assertTrue(results[0]["reply"])


class TestAlerts(unittest.TestCase):
    def test_task_data_has_three_critical(self):
        events = fa.load_events(ROOT / "events.json")
        self.assertEqual(len(events), 8)
        self.assertEqual([e["id"] for e in events if fa.is_critical(e)], [1, 4, 6])

    def test_level_is_case_insensitive(self):
        self.assertTrue(fa.is_critical({"level": "CRITICAL"}))
        self.assertTrue(fa.is_critical({"level": " critical "}))
        self.assertFalse(fa.is_critical({"level": "warn"}))

    def test_missing_or_broken_event_is_not_critical(self):
        self.assertFalse(fa.is_critical({"message": "нет уровня"}))
        self.assertFalse(fa.is_critical("строка вместо объекта"))
        self.assertEqual(fa.level_of({}), "unknown")


class TestEndToEnd(unittest.TestCase):
    """Запуск скриптов процессами — проверка того, что увидит проверяющий."""

    def run_script(self, *args):
        result = subprocess.run(
            [sys.executable, *args],
            cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_filter_alerts_prints_summary_and_only_critical(self):
        out = self.run_script("filter_alerts.py")
        self.assertIn("критичных 3", out)
        for noise in ("user login", "cpu 40%", "heartbeat", "cache miss", "deploy ok"):
            self.assertNotIn(noise, out)

    def test_classify_prints_category_and_draft_for_each(self):
        out = self.run_script("classify_messages.py")
        self.assertEqual(out.count("категория:"), 5)
        self.assertEqual(out.count("черновик ответа:"), 5)

    def test_json_mode_is_valid_json(self):
        payload = json.loads(self.run_script("classify_messages.py", "--json"))
        self.assertEqual(len(payload), 5)
        self.assertEqual([item["category"] for item in payload],
                         [category for _, category in TestRules.EXPECTED])

    def test_llm_mode_degrades_to_rules_without_key(self):
        # Без пакета anthropic или ключа скрипт обязан отработать на правилах, а не упасть.
        out = self.run_script("classify_messages.py", "--llm")
        self.assertEqual(out.count("категория:"), 5)

    def test_unknown_flag_exits_with_code_2(self):
        result = subprocess.run(
            [sys.executable, "classify_messages.py", "--oops"],
            cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
