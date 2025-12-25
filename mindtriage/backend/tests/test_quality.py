import os
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mindtriage.backend.app import main


class QualityTests(unittest.TestCase):
    def test_short_text_flags(self):
        result = main.assess_input_quality("hi", [], 0)
        self.assertIn("too_short", result["flags"])
        self.assertIn("low_word_count", result["flags"])
        self.assertLess(result["quality_score"], 100)

    def test_keyboard_smash_flag(self):
        result = main.assess_input_quality("asdfghjkl", [], 0)
        self.assertIn("keyboard_smash", result["flags"])

    def test_structured_quality_duplicate(self):
        result = main.assess_structured_quality(["same answer", "same answer"], [], 0)
        self.assertIn("repeated_across_fields", result["flags"])

    def test_retry_after_window(self):
        oldest = datetime.utcnow() - timedelta(seconds=1800)
        retry_after = main.calculate_retry_after(oldest)
        self.assertGreaterEqual(retry_after, 1700)
        self.assertLessEqual(retry_after, 1900)


if __name__ == "__main__":
    unittest.main()
