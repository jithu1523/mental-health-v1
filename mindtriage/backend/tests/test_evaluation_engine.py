import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mindtriage.backend.app import evaluation_engine


class EvaluationEngineTests(unittest.TestCase):
    def test_low_mood_high_risk(self):
        result = evaluation_engine.evaluate(daily_answers={"daily_mood": "2", "daily_hopeless": "yes"})
        self.assertGreaterEqual(result.risk_score, 3)
        self.assertIn("Low mood rating", result.signals)

    def test_quality_flags_short(self):
        result = evaluation_engine.evaluate(journal_text="hi")
        self.assertTrue(result.quality.flags)

    def test_followups_when_low_confidence(self):
        result = evaluation_engine.evaluate(journal_text="x", duration_seconds=5)
        self.assertTrue(result.recommended_followups)


if __name__ == "__main__":
    unittest.main()
