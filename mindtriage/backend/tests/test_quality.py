import os
import sys
import unittest
from datetime import datetime, timedelta
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mindtriage.backend.app import main

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class QualityTests(unittest.TestCase):
    def test_short_text_flags(self):
        result = main.assess_input_quality("hi", [], 0)
        self.assertIn("too_short", result["flags"])
        self.assertIn("low_word_count", result["flags"])
        self.assertLess(result["quality_score"], 100)

    def test_keyboard_smash_flag(self):
        result = main.assess_input_quality("asdfghjkl", [], 0)
        self.assertIn("keyboard_smash", result["flags"])

    def test_gibberish_short_repeated_flags(self):
        gibberish = main.assess_input_quality("bcdfghjklmnp", [], 0)
        self.assertIn("keyboard_smash", gibberish["flags"])

        short_text = main.assess_input_quality("hi", [], 0)
        self.assertIn("too_short", short_text["flags"])

        repeated = main.assess_input_quality("same same same same same", [], 0)
        self.assertIn("repeated_tokens", repeated["flags"])

    def test_structured_quality_duplicate(self):
        result = main.assess_structured_quality(["same answer", "same answer"], [], 0)
        self.assertIn("repeated_across_fields", result["flags"])

    def test_retry_after_window(self):
        oldest = datetime.utcnow() - timedelta(seconds=1800)
        retry_after = main.calculate_retry_after(oldest)
        self.assertGreaterEqual(retry_after, 1700)
        self.assertLessEqual(retry_after, 1900)

    def test_low_quality_excluded_by_default(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        main.Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        try:
            question = main.MicroQuestion(
                prompt="How is your mood?",
                question_type="scale",
                options_json="[]",
                category="mood",
                is_active=True,
            )
            db.add(question)
            db.commit()
            db.refresh(question)

            user_id = 1
            today = date.today()
            high_date = today
            low_date = today - timedelta(days=1)
            now = datetime.utcnow()
            later = now + timedelta(seconds=1)

            db.add(main.MicroAnswer(
                user_id=user_id,
                question_id=question.id,
                entry_date=high_date,
                value_json='{"value":"3"}',
                is_low_quality=False,
                created_at=now,
                answered_at=now,
            ))
            db.add(main.MicroAnswer(
                user_id=user_id,
                question_id=question.id,
                entry_date=low_date,
                value_json='{"value":"1"}',
                is_low_quality=True,
                created_at=later,
                answered_at=later,
            ))
            db.commit()

            dates_default = main.fetch_micro_dates(user_id, db, include_low_quality=False)
            self.assertEqual(dates_default, [high_date])

            dates_all = main.fetch_micro_dates(user_id, db, include_low_quality=True)
            self.assertEqual(dates_all, [low_date, high_date])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
