import unittest

from mindtriage.backend.app.crisis_detector import detect_crisis


class CrisisDetectorTests(unittest.TestCase):
    def test_explicit_intent_triggers_high(self):
        result = detect_crisis(texts=["I want to kill myself tonight."], structured={})
        self.assertTrue(result["is_crisis"])
        self.assertEqual(result["level"], "high")

    def test_neutral_text_triggers_none(self):
        result = detect_crisis(texts=["Today was okay. I went for a walk."], structured={})
        self.assertFalse(result["is_crisis"])
        self.assertEqual(result["level"], "none")

    def test_hopeless_with_hint_triggers_elevated(self):
        result = detect_crisis(
            texts=["I feel hopeless and I hurt myself before."],
            structured={"hopelessness_score": 9},
        )
        self.assertTrue(result["is_crisis"])
        self.assertEqual(result["level"], "elevated")


if __name__ == "__main__":
    unittest.main()
