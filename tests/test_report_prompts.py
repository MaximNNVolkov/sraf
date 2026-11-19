import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {
    "monitor": ROOT / "prompts" / "moex_monitor.md",
    "morning": ROOT / "prompts" / "moex_morning.md",
    "evening": ROOT / "prompts" / "moex_evening.md",
}
CANONICAL = ROOT / "prompts" / "moex_elliott.md"
CLAUDE_SKILLS = (
    ROOT / ".claude" / "skills" / "moex-elliott" / "SKILL.md",
    ROOT / ".claude" / "skills" / "usdrubf-morning" / "SKILL.md",
)


class ReportPromptFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prompt_text = {
            name: path.read_text(encoding="utf-8")
            for name, path in PROMPTS.items()
        }

    def assert_report_block(self, prompt_name, report_name, expected_labels):
        text = self.prompt_text[prompt_name]
        header = f"MOEX · {report_name} · <время МСК>"
        matches = re.findall(
            rf"```text\n({re.escape(header)}\n.*?)\n```",
            text,
            flags=re.DOTALL,
        )
        self.assertEqual(len(matches), 1)

        nonempty_lines = [
            line.strip()
            for line in matches[0].splitlines()
            if line.strip()
        ]
        self.assertEqual(nonempty_lines[0], header)
        self.assertEqual(nonempty_lines[1], "<ТИКЕР>")
        self.assertEqual(
            [line.partition(":")[0] + ":" for line in nonempty_lines[2:-1]],
            expected_labels,
        )
        self.assertEqual(
            nonempty_lines[-1],
            "Не является инвестиционной рекомендацией.",
        )

    def test_all_prompts_require_no_tables(self):
        for name, text in self.prompt_text.items():
            with self.subTest(prompt=name):
                self.assertIn("без таблиц", text.lower())

    def test_monitor_report_block_contract(self):
        self.assert_report_block(
            "monitor",
            "контроль 4ч",
            [
                "Цена:",
                "Среднесрочный тренд:",
                "Действие сейчас:",
                "Ожидается:",
            ],
        )

    def test_morning_report_block_contract(self):
        self.assert_report_block(
            "morning",
            "утренний отчёт",
            [
                "Цена:",
                "Среднесрочный тренд:",
                "Сценарий / позиция брокера:",
                "Действие сейчас:",
                "Ожидается:",
            ],
        )

    def test_evening_report_block_contract(self):
        self.assert_report_block(
            "evening",
            "вечерний отчёт",
            [
                "Цена:",
                "Среднесрочный тренд:",
                "Сценарий / позиция брокера:",
                "Действие сейчас:",
                "Ожидается:",
            ],
        )

    def test_monitor_has_exact_waiting_condition(self):
        text = self.prompt_text["monitor"]
        self.assertIn("ровно четыре короткие строки", text)
        self.assertIn("точное крупное условие", text)
        self.assertIn("ТФ, уровня и события", text)

    def test_monitor_preserves_safety_contract(self):
        text = self.prompt_text["monitor"]
        self.assertIn("текущая цена не подтверждена", text)
        self.assertIn("Разделяй рыночный сценарий", text)
        self.assertIn("фактическую позицию", text)
        self.assertIn("При `error/history_issues`", text)
        self.assertIn("не создавай решение", text)

    def test_morning_uses_five_short_lines(self):
        self.assertIn("пять коротких строк", self.prompt_text["morning"])

    def test_evening_uses_five_short_lines(self):
        self.assertIn("ровно пять коротких строк", self.prompt_text["evening"])

    def test_all_reports_are_medium_term_and_answer_core_questions(self):
        for name, text in self.prompt_text.items():
            with self.subTest(prompt=name):
                self.assertIn("Среднесрочный тренд:", text)
                self.assertIn("Действие сейчас:", text)
                self.assertIn("Ожидается:", text)
                self.assertIn("W1", text)
                self.assertIn("D1", text)
                self.assertIn("H4", text)

    def test_h1_is_restricted_to_breakout_retest(self):
        for name, text in self.prompt_text.items():
            with self.subTest(prompt=name):
                self.assertRegex(text, r"H1.*(пробо|breakout)")

    def test_all_prompts_forbid_broker_order_mutation(self):
        for name, text in self.prompt_text.items():
            with self.subTest(prompt=name):
                self.assertIn("Никогда не выставляй, не изменяй и не отменяй поручения брокера", text)

    def test_canonical_prompt_and_claude_skills_share_medium_term_regime(self):
        paths = (CANONICAL, *CLAUDE_SKILLS)
        for path in paths:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertIn("W1", text)
                self.assertIn("D1", text)
                self.assertIn("H4", text)
                self.assertIn("без таблиц", text.lower())
                self.assertRegex(text, r"(?s)H1.{0,200}пробо")
                self.assertRegex(text, r"(?s)H1.{0,300}ретест")
                self.assertNotRegex(text, r"(?m)^\s*\|.*\|\s*$")

    def test_canonical_guidance_cannot_restore_intraday_direction_mechanics(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in (CANONICAL, *CLAUDE_SKILLS)
        )
        for stale_guidance in ("15m", "15м", "подтверждение 1ч", "волны на 1h"):
            with self.subTest(stale_guidance=stale_guidance):
                self.assertNotIn(stale_guidance, combined)
        self.assertIn("H1 не выбирает направление", combined)
        self.assertIn("конкретный числовой уровень", combined)
        self.assertIn("KEEP_PLAN", combined)


if __name__ == "__main__":
    unittest.main()
