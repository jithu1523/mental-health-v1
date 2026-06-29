import json
import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mindtriage.backend.app import clinician_report, main


class ClassifyClusterStatusTests(unittest.TestCase):
    def test_no_rates_is_insufficient_data(self):
        self.assertEqual(clinician_report.classify_cluster_status([]), "insufficient_data")

    def test_high_rate_is_frequently_elevated(self):
        self.assertEqual(clinician_report.classify_cluster_status([0.8, 0.6]), "frequently_elevated")

    def test_mid_rate_is_occasionally_elevated(self):
        self.assertEqual(clinician_report.classify_cluster_status([0.2, 0.1]), "occasionally_elevated")

    def test_low_rate_is_stable(self):
        self.assertEqual(clinician_report.classify_cluster_status([0.0, 0.05]), "stable")


class ConcernRateTests(unittest.TestCase):
    def test_is_concerning_thresholds(self):
        self.assertTrue(clinician_report.is_concerning("mood_score", 2.0))
        self.assertFalse(clinician_report.is_concerning("mood_score", 7.0))
        self.assertTrue(clinician_report.is_concerning("anxiety_score", 8.0))
        self.assertTrue(clinician_report.is_concerning("sleep_hours", 4.5))

    def test_compute_signal_concern_rate_empty(self):
        self.assertIsNone(clinician_report.compute_signal_concern_rate("mood_score", []))

    def test_compute_signal_concern_rate(self):
        rate = clinician_report.compute_signal_concern_rate("mood_score", [2.0, 2.0, 8.0, 8.0])
        self.assertEqual(rate, 0.5)

    def test_compute_eval_hit_rate(self):
        rate = clinician_report.compute_eval_hit_rate(
            ["Reported hopelessness"],
            [["Low mood rating"], ["Reported hopelessness"], []],
        )
        self.assertAlmostEqual(rate, 1 / 3)

    def test_compute_eval_hit_rate_no_evaluations(self):
        self.assertIsNone(clinician_report.compute_eval_hit_rate(["Reported hopelessness"], []))


class BuildClusterSummaryTests(unittest.TestCase):
    def test_insufficient_data_when_no_signals_or_evaluations(self):
        cluster_def = clinician_report.CLUSTERS[1]  # Anxiety
        result = clinician_report.build_cluster_summary(cluster_def, {}, [])
        self.assertEqual(result["status"], "insufficient_data")

    def test_frequently_elevated_when_signals_consistently_high(self):
        cluster_def = clinician_report.CLUSTERS[1]  # Anxiety
        signals_by_date = {
            date(2025, 1, 1): {"anxiety_score": 9.0},
            date(2025, 1, 2): {"anxiety_score": 8.0},
            date(2025, 1, 3): {"anxiety_score": 9.5},
        }
        result = clinician_report.build_cluster_summary(
            cluster_def,
            signals_by_date,
            [["High anxiety rating"], ["High anxiety rating"]],
        )
        self.assertEqual(result["status"], "frequently_elevated")


class BuildClinicianSummaryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        main.Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.user = main.User(email="patient@example.com", hashed_password="x")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        self.today = date.today()

    def tearDown(self):
        self.db.close()

    def _seed_answer(self, days_ago: int, category: str, answer_text: str):
        answer = main.Answer(
            user_id=self.user.id,
            question_id=1,
            kind="daily",
            category=category,
            answer_text=answer_text,
            entry_date=self.today - timedelta(days=days_ago),
            is_low_quality=False,
        )
        self.db.add(answer)

    def test_privacy_journal_text_never_leaks_into_summary(self):
        marker = "MARKER_TEXT_SHOULD_NOT_APPEAR_xyz123"
        journal = main.JournalEntry(
            user_id=self.user.id,
            content=f"Today I felt fine. {marker}",
            entry_date=self.today,
            is_low_quality=False,
        )
        crisis_event = main.CrisisEvent(
            user_id=self.user.id,
            entry_date=self.today,
            source="journal",
            level="elevated",
            matched_terms_json="[]",
            snippet=marker,
        )
        self.db.add_all([journal, crisis_event])
        self.db.commit()

        summary = clinician_report.build_clinician_summary(self.user.id, self.db, days=30)
        summary_text = json.dumps(summary)
        self.assertNotIn(marker, summary_text)

        html_output = clinician_report.render_clinician_summary_html(summary)
        self.assertNotIn(marker, html_output)
        self.assertEqual(summary["safety_summary"]["crisis_event_count"], 1)

    def test_summary_shape_and_completeness(self):
        for i in range(5):
            self._seed_answer(i, "mood", "8")
        self.db.commit()

        summary = clinician_report.build_clinician_summary(self.user.id, self.db, days=30)
        self.assertEqual(summary["data_completeness"]["daily_checkin_days_logged"], 5)
        self.assertEqual(len(summary["clusters"]), len(clinician_report.CLUSTERS))
        cluster_keys = {c["key"] for c in summary["clusters"]}
        self.assertEqual(cluster_keys, {c["key"] for c in clinician_report.CLUSTERS})


if __name__ == "__main__":
    unittest.main()
