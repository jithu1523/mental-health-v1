import unittest

from mindtriage.backend.app import baseline_engine


class BaselineDriftTests(unittest.TestCase):
    def test_compute_signal_stats_with_coverage(self):
        values = [4, 5, 6, 5, 4, 5, 6]
        stats = baseline_engine.compute_signal_stats(values, total_days=10)
        self.assertIsNotNone(stats.get("mean"))
        self.assertEqual(stats.get("samples"), 7)
        self.assertEqual(stats.get("coverage_percent"), 70.0)

    def test_drift_classification(self):
        baseline_signals = {
            "mood_score": {"mean": 6.0, "std": 1.0, "coverage_percent": 80.0},
            "anxiety_score": {"mean": 4.0, "std": 1.0, "coverage_percent": 80.0},
        }
        signals_today = {"mood_score": 4.0, "anxiety_score": 6.5}
        drift, top_changes, confidence, _recommendations = baseline_engine.compute_drift(
            signals_today,
            baseline_signals,
        )
        self.assertEqual(drift["mood_score"]["status"], "down")
        self.assertEqual(drift["anxiety_score"]["status"], "up")
        self.assertTrue(confidence > 0)
        self.assertTrue(any(item["signal"] == "mood_score" for item in top_changes))


if __name__ == "__main__":
    unittest.main()
