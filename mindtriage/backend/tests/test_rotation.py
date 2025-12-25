import os
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mindtriage.backend.app import main


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.pool = [
            {"id": 1, "category": "mood"},
            {"id": 2, "category": "sleep"},
            {"id": 3, "category": "energy"},
            {"id": 4, "category": "stress"},
        ]

    def test_determinism_same_seed(self):
        seed = main.build_rotation_seed(1, date(2025, 1, 1), "micro")
        first = main.select_questions_with_seed(self.pool, set(), set(), set(), 2, seed)
        second = main.select_questions_with_seed(self.pool, set(), set(), set(), 2, seed)
        self.assertEqual([q["id"] for q in first], [q["id"] for q in second])

    def test_rotation_changes_across_days(self):
        sets = []
        base = date(2025, 1, 1)
        for offset in range(3):
            seed = main.build_rotation_seed(1, base + timedelta(days=offset), "micro")
            selected = main.select_questions_with_seed(self.pool, set(), set(), set(), 2, seed)
            sets.append(tuple(q["id"] for q in selected))
        self.assertGreater(len(set(sets)), 1)

    def test_exclude_answered_today(self):
        seed = main.build_rotation_seed(1, date(2025, 1, 1), "micro")
        selected = main.select_questions_with_seed(self.pool, set(), set(), {1, 2}, 2, seed)
        self.assertTrue(all(q["id"] not in {1, 2} for q in selected))


if __name__ == "__main__":
    unittest.main()
