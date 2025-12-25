from __future__ import annotations

import csv
import io
import json
import os
import random
import re
import shutil
import uuid
import zipfile
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
import statistics
from typing import List, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from dotenv import load_dotenv
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, create_engine, func, text, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship, sessionmaker

from .evaluation_engine import evaluate as run_evaluation
from .crisis_detector import detect_crisis
from .baseline_engine import (
    compute_baseline_snapshot,
    compute_drift,
    collect_signals_for_window,
)

APP_VERSION = "1.4.0"
REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPO_ROOT / ".env")


def resolve_db_path() -> str:
    db_env = (os.getenv("MINDTRIAGE_DB_PATH") or os.getenv("DB_PATH") or "").strip()
    db_path = Path(db_env) if db_env else (REPO_ROOT / "mindtriage.db")
    if not db_path.is_absolute():
        db_path = REPO_ROOT / db_path
    return str(db_path)


def maybe_copy_legacy_db(canonical_path: str) -> None:
    canonical = Path(canonical_path)
    legacy = REPO_ROOT / "mindtriage" / "backend" / "mindtriage.db"
    if canonical.exists() and legacy.exists():
        print(f"Using canonical DB at {canonical}. Legacy DB still at {legacy}.")
        return
    if not canonical.exists() and legacy.exists():
        canonical.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, canonical)
        print(f"Copied legacy DB from {legacy} to {canonical}.")


DB_PATH = resolve_db_path()
maybe_copy_legacy_db(DB_PATH)
DATABASE_URL = f"sqlite:///{DB_PATH}"
SECRET_KEY = "CHANGE_ME"
EXPORT_SALT = "LOCAL_EXPORT_SALT_CHANGE_ME"
ROTATION_SALT = "LOCAL_ROTATION_SALT_CHANGE_ME"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    journal_entries = relationship("JournalEntry", back_populates="user")
    answers = relationship("Answer", back_populates="user")
    onboarding_answers = relationship("OnboardingAnswer", back_populates="user")
    baseline = relationship("UserBaseline", uselist=False, back_populates="user")


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    entry_date = Column(Date, default=date.today, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)
    input_quality_score = Column(Integer, nullable=True)
    input_quality_flags_json = Column(String, nullable=False, default="[]")
    is_low_quality = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="journal_entries")


class RapidEvaluation(Base):
    __tablename__ = "rapid_evaluations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    entry_date = Column(Date, default=date.today, nullable=True)
    started_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    answers_json = Column(String, nullable=False)
    score = Column(Integer, nullable=False)
    level = Column(String, nullable=False)
    signals_json = Column(String, nullable=False)
    confidence_score = Column(Float, nullable=True)
    explainability_json = Column(String, nullable=False, default="[]")
    time_taken_seconds = Column(Float, nullable=True)
    is_valid = Column(Boolean, default=True, nullable=False)
    quality_flags_json = Column(String, nullable=False, default="[]")
    is_demo = Column(Boolean, default=False, nullable=False)
    input_quality_score = Column(Integer, nullable=True)
    input_quality_flags_json = Column(String, nullable=False, default="[]")
    is_low_quality = Column(Boolean, default=False, nullable=False)


class OnboardingQuestion(Base):
    __tablename__ = "onboarding_questions"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(String, nullable=False)
    options_json = Column(String, nullable=False, default="[]")
    category = Column(String, nullable=False)
    weight = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, default=True, nullable=False)


class OnboardingAnswer(Base):
    __tablename__ = "onboarding_answers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("onboarding_questions.id"), nullable=False)
    selected_option = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="onboarding_answers")
    question = relationship("OnboardingQuestion")


class UserBaseline(Base):
    __tablename__ = "user_baseline"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    baseline_score_mean = Column(Float, nullable=True)
    baseline_score_std = Column(Float, nullable=True)
    baseline_response_time_mean = Column(Float, nullable=True)
    baseline_response_time_std = Column(Float, nullable=True)
    baseline_confidence_mean = Column(Float, nullable=True)
    baseline_confidence_std = Column(Float, nullable=True)
    sample_count = Column(Integer, nullable=False, default=0)
    last_updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="baseline")


class BaselineSnapshot(Base):
    __tablename__ = "baseline_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    computed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    window_days = Column(Integer, nullable=False, default=14)
    json_payload = Column(String, nullable=False)


class CrisisEvent(Base):
    __tablename__ = "crisis_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    entry_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    source = Column(String, nullable=False)
    level = Column(String, nullable=False)
    matched_terms_json = Column(String, nullable=False, default="[]")
    snippet = Column(String, nullable=True)
    risk_score_at_time = Column(Integer, nullable=True)


class MicroQuestion(Base):
    __tablename__ = "micro_questions"

    id = Column(Integer, primary_key=True, index=True)
    prompt = Column(String, nullable=False)
    question_type = Column(String, nullable=False)
    options_json = Column(String, nullable=False, default="[]")
    category = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class MicroAnswer(Base):
    __tablename__ = "micro_answers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("micro_questions.id"), nullable=False)
    entry_date = Column(Date, default=date.today, nullable=False)
    kind = Column(String, nullable=True)
    category = Column(String, nullable=True)
    value_json = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    answered_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    input_quality_score = Column(Integer, nullable=True)
    input_quality_flags_json = Column(String, nullable=False, default="[]")
    is_low_quality = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "question_id", "answered_at", name="uq_micro_user_question_time"),
    )


class EvaluationSession(Base):
    __tablename__ = "evaluation_sessions"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    inputs_json = Column(String, nullable=False)
    result_json = Column(String, nullable=False)
    followups_json = Column(String, nullable=False, default="[]")


class EvaluationFollowup(Base):
    __tablename__ = "evaluation_followups"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("evaluation_sessions.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_key = Column(String, nullable=False)
    question_prompt = Column(String, nullable=False)
    answer_text = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, index=True, nullable=False)
    slug = Column(String, unique=True, index=True, nullable=False)
    text = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Answer(Base):
    __tablename__ = "answers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    kind = Column(String, nullable=True)
    category = Column(String, nullable=True)
    answer_text = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    entry_date = Column(Date, default=date.today, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)
    input_quality_score = Column(Integer, nullable=True)
    input_quality_flags_json = Column(String, nullable=False, default="[]")
    is_low_quality = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="answers")
    question = relationship("Question")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str


class RegisterRequest(BaseModel):
    email: str
    password: str


class QuestionResponse(BaseModel):
    id: int
    kind: str
    slug: str
    text: str


class NextQuestion(BaseModel):
    id: int
    text: str
    category: str
    kind: str
    slug: Optional[str] = None
    question_type: Optional[str] = None
    options: Optional[List[str]] = None


class NextQuestionsResponse(BaseModel):
    kind: str
    date: str
    questions: List[NextQuestion]


class AnswerCreate(BaseModel):
    question_id: int
    answer_text: str
    entry_date: Optional[date] = None


class AnswerBatch(BaseModel):
    answers: List[AnswerCreate]
    override_datetime: Optional[datetime] = None


class JournalCreate(BaseModel):
    content: str
    entry_date: Optional[date] = None
    override_datetime: Optional[datetime] = None


class JournalResponse(BaseModel):
    id: int
    content: str
    created_at: datetime
    input_quality_score: Optional[int] = None
    input_quality_flags: Optional[List[str]] = None
    is_low_quality: Optional[bool] = None
    reason_summary: Optional[str] = None
    crisis: Optional[dict] = None


class RiskResponse(BaseModel):
    risk_level: str
    score: int
    reasons: List[str]
    last_journal_excerpt: Optional[str]


class RiskHistoryEntry(BaseModel):
    date: str
    score: int
    level: str


class OnboardingQuestionResponse(BaseModel):
    id: int
    question: str
    options: List[str]
    category: str
    weight: int


class OnboardingAnswerCreate(BaseModel):
    question_id: int
    selected_option: Optional[str] = None


class OnboardingAnswerBatch(BaseModel):
    answers: List[OnboardingAnswerCreate]


class MicroQuestionResponse(BaseModel):
    id: int
    prompt: str
    question_type: str
    options: List[str]
    category: str


class MicroAnswerCreate(BaseModel):
    question_id: int
    value: str
    entry_date: Optional[date] = None
    override_entry_date: Optional[date] = None


class ActionPlanItem(BaseModel):
    title: str
    why: str
    duration_min: Optional[int] = None
    timeframe: Optional[str] = None


class ActionPlanResource(BaseModel):
    label: str
    type: str
    note: str


class ActionPlanOutput(BaseModel):
    next_15_min: List[ActionPlanItem]
    next_24_hours: List[ActionPlanItem]
    resources: List[ActionPlanResource]
    safety_note: str


class ActionPlanRequest(BaseModel):
    risk_level: str
    confidence: str
    baseline_deviation_z: Optional[float] = None
    micro_streak_days: int = 0
    answered_last_7_days: int = 0
    self_harm_flag: bool = False


class EvaluationRequest(BaseModel):
    journal_text: Optional[str] = None
    daily_answers: Optional[dict] = None
    rapid_answers: Optional[dict] = None
    duration_seconds: Optional[float] = None


class EvaluationFollowupRequest(BaseModel):
    session_id: str
    answers: dict


class RapidQuestion(BaseModel):
    id: int
    slug: str
    text: str
    kind: str
    format: str
    choices: Optional[List[str]] = None


class RapidAnswer(BaseModel):
    question_id: int
    answer_text: str


class RapidSubmitRequest(BaseModel):
    entry_date: Optional[date] = None
    started_at: Optional[datetime] = None
    session_id: Optional[int] = None
    override_datetime: Optional[datetime] = None
    answers: List[RapidAnswer]


class RapidExplainabilityItem(BaseModel):
    signal: str
    weight: float
    reason: str


class RapidSubmitResponse(BaseModel):
    level: str
    score: int
    signals: List[str]
    recommended_actions: List[str]
    crisis_guidance: Optional[List[str]] = None
    crisis: Optional[dict] = None
    confidence_score: float
    explanations: List[RapidExplainabilityItem]
    is_valid: bool
    quality_flags: List[str]
    time_taken_seconds: float
    micro_signal: dict
    entry_date: str
    input_quality_score: Optional[int] = None
    input_quality_flags: Optional[List[str]] = None
    is_low_quality: Optional[bool] = None
    reason_summary: Optional[str] = None


class RapidStartRequest(BaseModel):
    entry_date: Optional[date] = None


class RapidStartResponse(BaseModel):
    session_id: int
    started_at: str
    entry_date: str


app = FastAPI(title="MindTriage API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


ONBOARDING_QUESTIONS = [
    {"kind": "onboarding", "slug": "onb_goals", "text": "What brings you here today?"},
    {"kind": "onboarding", "slug": "onb_sleep", "text": "How has your sleep been lately?"},
    {"kind": "onboarding", "slug": "onb_stress", "text": "What are your biggest stressors right now?"},
    {"kind": "onboarding", "slug": "onb_support", "text": "Who do you lean on for support?"},
    {"kind": "onboarding", "slug": "onb_coping", "text": "What coping strategies have helped you?"},
    {"kind": "onboarding", "slug": "onb_work", "text": "How is work or school impacting you?"},
    {"kind": "onboarding", "slug": "onb_relationships", "text": "How are your relationships lately?"},
    {"kind": "onboarding", "slug": "onb_activity", "text": "What activities give you energy?"},
    {"kind": "onboarding", "slug": "onb_anxiety", "text": "When do you feel most anxious?"},
    {"kind": "onboarding", "slug": "onb_mood", "text": "How would you describe your mood this month?"},
]

DAILY_CORE = [
    {"kind": "daily", "slug": "daily_mood", "text": "Rate your mood today (1-10).", "category": "mood"},
    {"kind": "daily", "slug": "daily_anxiety", "text": "Rate your anxiety today (1-10).", "category": "anxiety"},
    {"kind": "daily", "slug": "daily_sleep", "text": "How many hours did you sleep?", "category": "sleep"},
    {"kind": "daily", "slug": "daily_energy", "text": "How is your energy level today?", "category": "energy"},
    {"kind": "daily", "slug": "daily_stress", "text": "How stressed do you feel today?", "category": "stress"},
    {"kind": "daily", "slug": "daily_focus", "text": "How is your focus today?", "category": "focus"},
]

DAILY_ROTATING_POOL = [
    {"kind": "daily", "slug": "daily_isolation", "text": "Do you feel isolated today?", "category": "isolation"},
    {"kind": "daily", "slug": "daily_hopeless", "text": "Have you felt hopeless today?", "category": "hopelessness"},
    {"kind": "daily", "slug": "daily_irritability", "text": "How irritable do you feel today?", "category": "irritability"},
    {"kind": "daily", "slug": "daily_appetite", "text": "How is your appetite today?", "category": "appetite"},
    {"kind": "daily", "slug": "daily_motivation", "text": "How motivated do you feel today?", "category": "motivation"},
    {"kind": "daily", "slug": "daily_support", "text": "Did you feel supported today?", "category": "support"},
    {"kind": "daily", "slug": "daily_activity", "text": "Did you get any movement or activity today?", "category": "activity"},
    {"kind": "daily", "slug": "daily_overwhelm", "text": "How overwhelmed did you feel today?", "category": "overwhelm"},
    {"kind": "daily", "slug": "daily_confidence", "text": "How confident do you feel today?", "category": "confidence"},
    {"kind": "daily", "slug": "daily_gratitude", "text": "Name one thing that felt okay today.", "category": "gratitude"},
]

DAILY_QUESTIONS = DAILY_CORE + DAILY_ROTATING_POOL

ONBOARDING_PROFILE_QUESTIONS = [
    {
        "question": "Which area would you most like support with right now?",
        "options": ["Mood", "Anxiety", "Stress", "Sleep", "Motivation", "Focus"],
        "category": "goals",
        "weight": 2,
    },
    {
        "question": "How often have you felt overwhelmed lately?",
        "options": ["Rarely", "Sometimes", "Often", "Almost always"],
        "category": "stress",
        "weight": 2,
    },
    {
        "question": "How supported do you feel by people in your life?",
        "options": ["Very supported", "Somewhat supported", "Not very supported", "Not at all supported"],
        "category": "support",
        "weight": 2,
    },
    {
        "question": "How would you describe your sleep quality?",
        "options": ["Good", "Okay", "Poor", "Very poor"],
        "category": "sleep",
        "weight": 1,
    },
    {
        "question": "How is your energy most days?",
        "options": ["High", "Moderate", "Low", "Very low"],
        "category": "energy",
        "weight": 1,
    },
    {
        "question": "How connected do you feel to your routines?",
        "options": ["Very connected", "Somewhat connected", "Barely connected", "Not connected"],
        "category": "routine",
        "weight": 1,
    },
    {
        "question": "How often do you feel anxious in a typical week?",
        "options": ["Rarely", "Some days", "Most days", "Nearly every day"],
        "category": "anxiety",
        "weight": 2,
    },
    {
        "question": "How often do you feel down in a typical week?",
        "options": ["Rarely", "Some days", "Most days", "Nearly every day"],
        "category": "mood",
        "weight": 2,
    },
    {
        "question": "How much are you using coping tools right now?",
        "options": ["A lot", "Some", "A little", "Not at all"],
        "category": "coping",
        "weight": 1,
    },
    {
        "question": "How safe do you feel day to day?",
        "options": ["Safe", "Mostly safe", "Sometimes unsafe", "Often unsafe"],
        "category": "safety",
        "weight": 2,
    },
]

MICRO_POOL = [
    {
        "prompt": "How is your mood right now?",
        "question_type": "scale",
        "options": [str(i) for i in range(1, 6)],
        "category": "mood",
    },
    {
        "prompt": "How anxious do you feel right now?",
        "question_type": "scale",
        "options": [str(i) for i in range(1, 6)],
        "category": "anxiety",
    },
    {
        "prompt": "How stressed do you feel right now?",
        "question_type": "scale",
        "options": [str(i) for i in range(1, 6)],
        "category": "stress",
    },
    {
        "prompt": "How is your energy right now?",
        "question_type": "scale",
        "options": [str(i) for i in range(1, 6)],
        "category": "energy",
    },
    {
        "prompt": "How connected do you feel today?",
        "question_type": "choice",
        "options": ["Connected", "Neutral", "Isolated"],
        "category": "connection",
    },
    {
        "prompt": "How hopeful do you feel right now?",
        "question_type": "scale",
        "options": [str(i) for i in range(1, 6)],
        "category": "hopelessness",
    },
    {
        "prompt": "How irritable do you feel right now?",
        "question_type": "scale",
        "options": [str(i) for i in range(1, 6)],
        "category": "irritability",
    },
    {
        "prompt": "How focused do you feel right now?",
        "question_type": "scale",
        "options": [str(i) for i in range(1, 6)],
        "category": "focus",
    },
    {
        "prompt": "How is your appetite right now?",
        "question_type": "scale",
        "options": [str(i) for i in range(1, 6)],
        "category": "appetite",
    },
    {
        "prompt": "Did you take a short pause or break today?",
        "question_type": "choice",
        "options": ["Yes", "No"],
        "category": "recovery",
    },
]

MICRO_QUESTIONS = MICRO_POOL

RAPID_QUESTIONS = [
    {
        "id": 1,
        "slug": "rapid_mood",
        "text": "Rate your mood right now (1-10).",
        "kind": "rapid",
        "format": "scale",
    },
    {
        "id": 2,
        "slug": "rapid_anxiety",
        "text": "Rate your anxiety right now (1-10).",
        "kind": "rapid",
        "format": "scale",
    },
    {
        "id": 3,
        "slug": "rapid_hopeless",
        "text": "Are you feeling hopeless right now?",
        "kind": "rapid",
        "format": "yesno",
    },
    {
        "id": 4,
        "slug": "rapid_isolation",
        "text": "Do you feel isolated right now?",
        "kind": "rapid",
        "format": "yesno",
    },
    {
        "id": 5,
        "slug": "rapid_sleep",
        "text": "How was your sleep last night?",
        "kind": "rapid",
        "format": "choice",
        "choices": ["Good", "Okay", "Poor"],
    },
    {
        "id": 6,
        "slug": "rapid_appetite",
        "text": "How is your appetite today?",
        "kind": "rapid",
        "format": "choice",
        "choices": ["Good", "Okay", "Poor"],
    },
    {
        "id": 7,
        "slug": "rapid_support",
        "text": "Do you have someone you can reach out to right now?",
        "kind": "rapid",
        "format": "yesno",
    },
    {
        "id": 8,
        "slug": "rapid_self_harm_thoughts",
        "text": "Are you having thoughts of self-harm?",
        "kind": "rapid",
        "format": "yesno",
    },
    {
        "id": 9,
        "slug": "rapid_self_harm_plan",
        "text": "Do you have intent or a plan to act on those thoughts?",
        "kind": "rapid",
        "format": "yesno",
    },
    {
        "id": 10,
        "slug": "rapid_substance",
        "text": "Have you used alcohol or substances to cope today?",
        "kind": "rapid",
        "format": "yesno",
    },
    {
        "id": 11,
        "slug": "rapid_attention_check",
        "text": "Attention check: select 'Sometimes' for this item.",
        "kind": "rapid",
        "format": "choice",
        "choices": ["Never", "Sometimes", "Often"],
    },
]


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_entry_date_columns()
    ensure_rapid_columns()
    ensure_onboarding_tables()
    ensure_quality_columns()
    ensure_micro_schema()
    seed_questions()
    seed_onboarding_profile_questions()
    seed_micro_questions()


def seed_questions() -> None:
    session = SessionLocal()
    try:
        existing = {q.slug for q in session.query(Question).all()}
        to_add = []
        for item in ONBOARDING_QUESTIONS + DAILY_QUESTIONS:
            if item["slug"] not in existing:
                to_add.append(Question(
                    kind=item["kind"],
                    slug=item["slug"],
                    text=item["text"],
                ))
        if to_add:
            session.add_all(to_add)
            session.commit()
    finally:
        session.close()


def ensure_onboarding_tables() -> None:
    Base.metadata.create_all(bind=engine)


def seed_onboarding_profile_questions() -> None:
    session = SessionLocal()
    try:
        existing = {q.question for q in session.query(OnboardingQuestion).all()}
        to_add = []
        for item in ONBOARDING_PROFILE_QUESTIONS:
            if item["question"] not in existing:
                to_add.append(OnboardingQuestion(
                    question=item["question"],
                    options_json=json.dumps(item["options"]),
                    category=item["category"],
                    weight=item["weight"],
                    is_active=True,
                ))
        if to_add:
            session.add_all(to_add)
            session.commit()
    finally:
        session.close()


def seed_micro_questions() -> None:
    session = SessionLocal()
    try:
        existing = {q.prompt for q in session.query(MicroQuestion).all()}
        to_add = []
        for item in MICRO_QUESTIONS:
            if item["prompt"] not in existing:
                to_add.append(MicroQuestion(
                    prompt=item["prompt"],
                    question_type=item["question_type"],
                    options_json=json.dumps(item["options"]),
                    category=item["category"],
                    is_active=True,
                ))
        if to_add:
            session.add_all(to_add)
            session.commit()
    finally:
        session.close()


def ensure_entry_date_columns() -> None:
    with engine.connect() as connection:
        answer_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(answers)"))}
        if "entry_date" not in answer_columns:
            connection.execute(text("ALTER TABLE answers ADD COLUMN entry_date DATE"))
        if "is_demo" not in answer_columns:
            connection.execute(text("ALTER TABLE answers ADD COLUMN is_demo BOOLEAN DEFAULT 0"))
        journal_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(journal_entries)"))}
        if "entry_date" not in journal_columns:
            connection.execute(text("ALTER TABLE journal_entries ADD COLUMN entry_date DATE"))
        if "is_demo" not in journal_columns:
            connection.execute(text("ALTER TABLE journal_entries ADD COLUMN is_demo BOOLEAN DEFAULT 0"))
        connection.commit()


def ensure_rapid_columns() -> None:
    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(rapid_evaluations)"))}
        if columns:
            if "started_at" not in columns:
                connection.execute(text("ALTER TABLE rapid_evaluations ADD COLUMN started_at DATETIME"))
            if "submitted_at" not in columns:
                connection.execute(text("ALTER TABLE rapid_evaluations ADD COLUMN submitted_at DATETIME"))
            if "is_valid" not in columns:
                connection.execute(text("ALTER TABLE rapid_evaluations ADD COLUMN is_valid BOOLEAN DEFAULT 1"))
            if "quality_flags_json" not in columns:
                connection.execute(text("ALTER TABLE rapid_evaluations ADD COLUMN quality_flags_json TEXT DEFAULT '[]'"))
            if "confidence_score" not in columns:
                connection.execute(text("ALTER TABLE rapid_evaluations ADD COLUMN confidence_score FLOAT"))
            if "explainability_json" not in columns:
                connection.execute(text("ALTER TABLE rapid_evaluations ADD COLUMN explainability_json TEXT DEFAULT '[]'"))
            if "time_taken_seconds" not in columns:
                connection.execute(text("ALTER TABLE rapid_evaluations ADD COLUMN time_taken_seconds FLOAT"))
            if "is_demo" not in columns:
                connection.execute(text("ALTER TABLE rapid_evaluations ADD COLUMN is_demo BOOLEAN DEFAULT 0"))
            connection.commit()


def ensure_quality_columns() -> None:
    with engine.connect() as connection:
        journal_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(journal_entries)"))}
        if "input_quality_score" not in journal_columns:
            connection.execute(text("ALTER TABLE journal_entries ADD COLUMN input_quality_score INTEGER"))
        if "input_quality_flags_json" not in journal_columns:
            connection.execute(text("ALTER TABLE journal_entries ADD COLUMN input_quality_flags_json TEXT DEFAULT '[]'"))
        if "is_low_quality" not in journal_columns:
            connection.execute(text("ALTER TABLE journal_entries ADD COLUMN is_low_quality BOOLEAN DEFAULT 0"))

        rapid_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(rapid_evaluations)"))}
        if "input_quality_score" not in rapid_columns:
            connection.execute(text("ALTER TABLE rapid_evaluations ADD COLUMN input_quality_score INTEGER"))
        if "input_quality_flags_json" not in rapid_columns:
            connection.execute(text("ALTER TABLE rapid_evaluations ADD COLUMN input_quality_flags_json TEXT DEFAULT '[]'"))
        if "is_low_quality" not in rapid_columns:
            connection.execute(text("ALTER TABLE rapid_evaluations ADD COLUMN is_low_quality BOOLEAN DEFAULT 0"))

        answer_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(answers)"))}
        if "kind" not in answer_columns:
            connection.execute(text("ALTER TABLE answers ADD COLUMN kind TEXT"))
        if "category" not in answer_columns:
            connection.execute(text("ALTER TABLE answers ADD COLUMN category TEXT"))
        if "input_quality_score" not in answer_columns:
            connection.execute(text("ALTER TABLE answers ADD COLUMN input_quality_score INTEGER"))
        if "input_quality_flags_json" not in answer_columns:
            connection.execute(text("ALTER TABLE answers ADD COLUMN input_quality_flags_json TEXT DEFAULT '[]'"))
        if "is_low_quality" not in answer_columns:
            connection.execute(text("ALTER TABLE answers ADD COLUMN is_low_quality BOOLEAN DEFAULT 0"))

        micro_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(micro_answers)"))}
        if "kind" not in micro_columns:
            connection.execute(text("ALTER TABLE micro_answers ADD COLUMN kind TEXT"))
        if "category" not in micro_columns:
            connection.execute(text("ALTER TABLE micro_answers ADD COLUMN category TEXT"))
        if "input_quality_score" not in micro_columns:
            connection.execute(text("ALTER TABLE micro_answers ADD COLUMN input_quality_score INTEGER"))
        if "input_quality_flags_json" not in micro_columns:
            connection.execute(text("ALTER TABLE micro_answers ADD COLUMN input_quality_flags_json TEXT DEFAULT '[]'"))
        if "is_low_quality" not in micro_columns:
            connection.execute(text("ALTER TABLE micro_answers ADD COLUMN is_low_quality BOOLEAN DEFAULT 0"))
        connection.commit()


def ensure_micro_schema() -> None:
    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(micro_answers)"))}
        if not columns:
            return
        if "answered_at" in columns:
            return
        connection.execute(text("""
            CREATE TABLE micro_answers_new (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                entry_date DATE NOT NULL,
                kind TEXT,
                category TEXT,
                value_json TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                answered_at DATETIME NOT NULL,
                input_quality_score INTEGER,
                input_quality_flags_json TEXT DEFAULT '[]',
                is_low_quality BOOLEAN DEFAULT 0,
                CONSTRAINT uq_micro_user_question_time UNIQUE (user_id, question_id, answered_at)
            )
        """))
        connection.execute(text("""
            INSERT INTO micro_answers_new (
                id, user_id, question_id, entry_date, kind, category, value_json, created_at, answered_at,
                input_quality_score, input_quality_flags_json, is_low_quality
            )
            SELECT id, user_id, question_id, entry_date, 'micro', NULL, value_json, created_at, created_at,
                   NULL, '[]', 0
            FROM micro_answers
        """))
        connection.execute(text("DROP TABLE micro_answers"))
        connection.execute(text("ALTER TABLE micro_answers_new RENAME TO micro_answers"))
        connection.commit()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise credentials_exception
    return user


@app.get("/health")
def health() -> dict:
    db_status = "ok"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"
    return {
        "status": "ok",
        "version": APP_VERSION,
        "db": db_status,
        "dev_mode": is_dev_mode(),
    }


@app.get("/meta")
def meta() -> dict:
    return {"version": APP_VERSION, "dev_mode": is_dev_mode(), "db_path": DB_PATH}


@app.get("/safety/resources")
def safety_resources() -> dict:
    return {
        "us": [
            {"label": "988 Lifeline", "note": "Call or text 988 in the U.S. for immediate support."},
            {"label": "Emergency", "note": "If you are in immediate danger, call 911 or local emergency services."},
        ],
        "international": [
            "If you are outside the U.S., contact local emergency services or a local crisis line.",
            "If you are in immediate danger, seek urgent help right away.",
        ],
        "safety_note": "This app is not medical advice. If you feel unsafe, seek immediate support.",
    }


@app.get("/safety/events")
def safety_events(
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[dict]:
    if not is_dev_mode():
        raise HTTPException(status_code=403, detail="Developer mode disabled")
    start_date = date.today() - timedelta(days=days - 1)
    events = (
        db.query(CrisisEvent)
        .filter(
            CrisisEvent.user_id == user.id,
            CrisisEvent.entry_date >= start_date,
        )
        .order_by(CrisisEvent.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "entry_date": event.entry_date.isoformat(),
            "source": event.source,
            "level": event.level,
            "matched_terms": json.loads(event.matched_terms_json or "[]"),
            "created_at": event.created_at.isoformat(),
        }
        for event in events
    ]


def is_dev_mode() -> bool:
    value = os.getenv("MINDTRIAGE_DEV_MODE", "").strip().lower()
    alt = os.getenv("DEV_MODE", "").strip().lower()
    return value in {"1", "true", "yes", "on"} or alt in {"1", "true", "yes", "on"}


@app.post("/auth/register", response_model=TokenResponse)
def register_user(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    password_bytes = payload.password.encode("utf-8")
    if len(password_bytes) > 72:
        raise HTTPException(
            status_code=400,
            detail="Password too long (bcrypt limit is 72 bytes). Use a shorter password.",
        )
    try:
        hashed_password = get_password_hash(payload.password)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Unable to process password at this time.",
        ) from exc
    user = User(email=payload.email, hashed_password=hashed_password)
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token({"sub": str(user.id), "email": user.email})
    return TokenResponse(access_token=token, token_type="bearer")


@app.post("/auth/login", response_model=TokenResponse)
def login_user(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
) -> TokenResponse:
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Invalid email or password")
    token = create_access_token({"sub": str(user.id), "email": user.email})
    return TokenResponse(access_token=token, token_type="bearer")


@app.get("/questions", response_model=List[QuestionResponse])
def get_questions(
    kind: str = Query("onboarding", pattern="^(onboarding|daily)$"),
    db: Session = Depends(get_db)
) -> List[QuestionResponse]:
    questions = db.query(Question).filter(Question.kind == kind).order_by(Question.id).all()
    return [QuestionResponse(id=q.id, kind=q.kind, slug=q.slug, text=q.text) for q in questions]


@app.get("/questions/next", response_model=NextQuestionsResponse)
def get_next_questions(
    kind: str = Query(..., pattern="^(micro|daily)$"),
    date_override: Optional[date] = Query(None, alias="date"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NextQuestionsResponse:
    target_date = local_today()
    if date_override is not None:
        if not is_dev_mode():
            raise HTTPException(status_code=403, detail="Developer mode disabled")
        target_date = date_override
    questions = select_next_questions(user.id, kind, target_date, db)
    return NextQuestionsResponse(
        kind=kind,
        date=target_date.isoformat(),
        questions=[NextQuestion(**item) for item in questions],
    )


@app.get("/onboarding/status")
def onboarding_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> dict:
    onboarding_ids = [q.id for q in db.query(Question).filter(Question.kind == "onboarding").all()]
    answered_ids = {
        a.question_id
        for a in db.query(Answer)
        .filter(Answer.user_id == user.id, Answer.question_id.in_(onboarding_ids))
        .all()
    }
    missing_ids = [qid for qid in onboarding_ids if qid not in answered_ids]

    profile_questions = db.query(OnboardingQuestion).filter(OnboardingQuestion.is_active.is_(True)).all()
    profile_total = len(profile_questions)
    if profile_questions:
        profile_answered = (
            db.query(OnboardingAnswer)
            .filter(
                OnboardingAnswer.user_id == user.id,
                OnboardingAnswer.question_id.in_([q.id for q in profile_questions]),
            )
            .count()
        )
    else:
        profile_answered = 0
    last_answered = (
        db.query(func.max(OnboardingAnswer.created_at))
        .filter(OnboardingAnswer.user_id == user.id)
        .scalar()
    )
    completed_percent = round((profile_answered / profile_total) * 100, 1) if profile_total else 0.0

    return {
        "complete": len(missing_ids) == 0,
        "missing_question_ids": missing_ids,
        "profile": {
            "total_questions": profile_total,
            "answered": profile_answered,
            "completed_percent": completed_percent,
            "last_answered_at": last_answered.isoformat() if last_answered else None,
        },
    }


@app.get("/daily/pick", response_model=List[QuestionResponse])
def daily_pick(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> List[QuestionResponse]:
    today = local_today()
    questions = select_next_questions(user.id, "daily", today, db)
    if not questions:
        return []
    return [
        QuestionResponse(
            id=item["id"],
            kind="daily",
            slug=item.get("slug") or "",
            text=item["text"],
        )
        for item in questions
    ]


@app.get("/onboarding/questions", response_model=List[OnboardingQuestionResponse])
def onboarding_questions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[OnboardingQuestionResponse]:
    questions = (
        db.query(OnboardingQuestion)
        .filter(OnboardingQuestion.is_active.is_(True))
        .order_by(OnboardingQuestion.id)
        .all()
    )
    answered_ids = {
        item.question_id
        for item in db.query(OnboardingAnswer)
        .filter(OnboardingAnswer.user_id == user.id)
        .all()
    }
    remaining = [q for q in questions if q.id not in answered_ids]
    selected = remaining[:4]
    return [
        OnboardingQuestionResponse(
            id=q.id,
            question=q.question,
            options=json.loads(q.options_json),
            category=q.category,
            weight=q.weight,
        )
        for q in selected
    ]


@app.post("/onboarding/answer")
def onboarding_answer(
    payload: OnboardingAnswerBatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not payload.answers:
        raise HTTPException(status_code=400, detail="No answers provided")

    question_ids = [item.question_id for item in payload.answers]
    questions = (
        db.query(OnboardingQuestion)
        .filter(OnboardingQuestion.id.in_(question_ids), OnboardingQuestion.is_active.is_(True))
        .all()
    )
    question_map = {q.id: q for q in questions}
    missing = [qid for qid in question_ids if qid not in question_map]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown question IDs: {missing}")

    saved = 0
    for item in payload.answers:
        question = question_map[item.question_id]
        selected = (item.selected_option or "skipped").strip()
        options = json.loads(question.options_json)
        if selected != "skipped" and selected not in options:
            raise HTTPException(status_code=400, detail=f"Invalid option for question {question.id}")

        existing = (
            db.query(OnboardingAnswer)
            .filter(
                OnboardingAnswer.user_id == user.id,
                OnboardingAnswer.question_id == question.id,
            )
            .first()
        )
        if existing:
            existing.selected_option = selected
            existing.created_at = datetime.utcnow()
        else:
            db.add(OnboardingAnswer(
                user_id=user.id,
                question_id=question.id,
                selected_option=selected,
            ))
        saved += 1

    db.commit()
    return {"saved": saved}


@app.get("/micro/today")
def micro_today(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    today = local_today()
    questions = select_next_questions(user.id, "micro", today, db)
    answered = (
        db.query(MicroAnswer)
        .filter(
            MicroAnswer.user_id == user.id,
            MicroAnswer.entry_date == today,
        )
        .count()
    )
    return {
        "questions": questions,
        "answered": answered > 0,
    }


@app.post("/micro/answers")
def micro_answer(
    payload: MicroAnswerCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    today = local_today()
    if payload.override_entry_date and not is_dev_mode():
        raise HTTPException(status_code=403, detail="Developer mode disabled")
    entry_date = payload.entry_date or today
    if not is_dev_mode():
        if payload.entry_date and payload.entry_date != today:
            raise HTTPException(
                status_code=400,
                detail="entry_date must be today unless dev mode is enabled.",
            )
        entry_date = today
    if payload.override_entry_date:
        entry_date = payload.override_entry_date

    question = (
        db.query(MicroQuestion)
        .filter(MicroQuestion.id == payload.question_id, MicroQuestion.is_active.is_(True))
        .first()
    )
    if not question:
        raise HTTPException(status_code=400, detail="Unknown micro question.")

    existing = (
        db.query(MicroAnswer)
        .filter(
            MicroAnswer.user_id == user.id,
            MicroAnswer.entry_date == entry_date,
            MicroAnswer.question_id == payload.question_id,
        )
        .first()
    )
    value = payload.value.strip()
    if question.question_type == "scale":
        if value not in json.loads(question.options_json):
            raise HTTPException(status_code=400, detail="Invalid scale value.")
    elif question.question_type == "choice":
        if value not in json.loads(question.options_json):
            raise HTTPException(status_code=400, detail="Invalid choice value.")
    else:
        raise HTTPException(status_code=400, detail="Unknown micro question type.")

    now = datetime.utcnow()
    recent_values = [
        json.loads(item.value_json).get("value", "")
        for item in db.query(MicroAnswer)
        .filter(MicroAnswer.user_id == user.id)
        .order_by(MicroAnswer.answered_at.desc())
        .limit(10)
        .all()
    ]
    short_window_count = (
        db.query(MicroAnswer)
        .filter(
            MicroAnswer.user_id == user.id,
            MicroAnswer.answered_at >= now - timedelta(minutes=10),
        )
        .count()
    )
    quality = assess_structured_quality([value], recent_values, short_window_count)
    if existing:
        existing.question_id = question.id
        existing.value_json = json.dumps({"value": value})
        existing.created_at = now
        existing.answered_at = now
        existing.kind = "micro"
        existing.category = question.category
        existing.input_quality_score = quality["quality_score"]
        existing.input_quality_flags_json = json.dumps(quality["flags"])
        existing.is_low_quality = quality["is_low_quality"]
        saved = existing
    else:
        saved = MicroAnswer(
            user_id=user.id,
            question_id=question.id,
            entry_date=entry_date,
            kind="micro",
            category=question.category,
            value_json=json.dumps({"value": value}),
            created_at=now,
            answered_at=now,
            input_quality_score=quality["quality_score"],
            input_quality_flags_json=json.dumps(quality["flags"]),
            is_low_quality=quality["is_low_quality"],
        )
        db.add(saved)
    db.commit()
    update_user_baseline(user.id, db)
    return {
        "saved": True,
        "entry_date": saved.entry_date.isoformat(),
        "created_at": saved.created_at.isoformat(),
        "answered_at": saved.answered_at.isoformat(),
        "question_id": saved.question_id,
        "value_json": saved.value_json,
        "input_quality_score": saved.input_quality_score,
        "input_quality_flags": quality["flags"],
        "is_low_quality": saved.is_low_quality,
        "reason_summary": quality["reason_summary"],
    }


@app.post("/micro/answer", include_in_schema=False)
def micro_answer_legacy(
    payload: MicroAnswerCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return micro_answer(payload, user, db)


@app.get("/micro/history")
def micro_history(
    days: int = Query(30, ge=1, le=365),
    include_low_quality: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[dict]:
    start_date = date.today() - timedelta(days=days - 1)
    if include_low_quality and not is_dev_mode():
        include_low_quality = False
    rows = (
        db.query(MicroAnswer, MicroQuestion)
        .join(MicroQuestion, MicroAnswer.question_id == MicroQuestion.id)
        .filter(
            MicroAnswer.user_id == user.id,
            func.date(MicroAnswer.entry_date) >= start_date.isoformat(),
            MicroAnswer.is_low_quality.is_(False) if not include_low_quality else True,
        )
        .order_by(MicroAnswer.entry_date.desc(), MicroAnswer.answered_at.desc())
        .all()
    )
    history = []
    for answer, question in rows:
        value = json.loads(answer.value_json).get("value")
        history.append({
            "entry_date": answer.entry_date.isoformat(),
            "question": question.prompt,
            "category": question.category,
            "value": value,
            "created_at": answer.answered_at.isoformat(),
            "input_quality_score": answer.input_quality_score,
            "input_quality_flags": json.loads(answer.input_quality_flags_json or "[]"),
            "is_low_quality": answer.is_low_quality,
        })
    return history


@app.get("/micro/questions")
def micro_questions(
    entry_date: date = Query(...),
    k: int = Query(2, ge=1, le=3),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    questions = select_next_questions(user.id, "micro", entry_date, db)
    questions = questions[:k]
    return {"entry_date": entry_date.isoformat(), "questions": questions}


@app.get("/micro/status")
def micro_status(
    entry_date: date = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    rows = (
        db.query(MicroAnswer)
        .filter(
            MicroAnswer.user_id == user.id,
            MicroAnswer.entry_date == entry_date,
        )
        .order_by(MicroAnswer.answered_at.desc())
        .all()
    )
    done = len({row.question_id for row in rows}) >= 2
    last_created = rows[0].answered_at.isoformat() if rows else None
    return {
        "entry_date": entry_date.isoformat(),
        "done": done,
        "count": len(rows),
        "last_created_at": last_created,
    }


@app.get("/dev/debug/micro")
def debug_micro(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not is_dev_mode():
        raise HTTPException(status_code=404, detail="Not found")
    total = (
        db.query(MicroAnswer)
        .filter(MicroAnswer.user_id == user.id)
        .count()
    )
    last_rows = (
        db.query(MicroAnswer)
        .filter(MicroAnswer.user_id == user.id)
        .order_by(MicroAnswer.answered_at.desc())
        .limit(5)
        .all()
    )
    last_items = [
        {
            "entry_date": row.entry_date.isoformat() if row.entry_date else None,
            "created_at": row.answered_at.isoformat(),
            "answered_at": row.answered_at.isoformat(),
            "question_id": row.question_id,
            "value_json": row.value_json,
        }
        for row in last_rows
    ]
    return {
        "count_micro_answers_total": total,
        "last_5_micro_answers": last_items,
        "server_today_date": date.today().isoformat(),
        "timezone": str(datetime.now().astimezone().tzinfo),
    }


@app.get("/micro/streak")
def micro_streak(
    include_low_quality: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if include_low_quality and not is_dev_mode():
        include_low_quality = False
    dates = fetch_micro_dates(user.id, db, include_low_quality=include_low_quality)
    last_entry_date = dates[-1] if dates else None
    streak = compute_streak_from_latest(dates)
    entry_dates_last_30 = [d.isoformat() for d in dates[-30:]]
    return {
        "streak_days": streak,
        "last_entry_date": last_entry_date.isoformat() if last_entry_date else None,
        "entry_dates_last_30": entry_dates_last_30,
    }


@app.post("/plan/generate", response_model=ActionPlanOutput)
def plan_generate(payload: ActionPlanRequest) -> ActionPlanOutput:
    plan = build_action_plan(
        risk_level=payload.risk_level,
        confidence=payload.confidence,
        baseline_deviation_z=payload.baseline_deviation_z,
        micro_streak_days=payload.micro_streak_days,
        answered_last_7_days=payload.answered_last_7_days,
        self_harm_flag=payload.self_harm_flag,
    )
    return ActionPlanOutput(**plan)


@app.post("/evaluate")
def evaluate_endpoint(
    payload: EvaluationRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    result = run_evaluation(
        journal_text=payload.journal_text,
        daily_answers=payload.daily_answers,
        rapid_answers=payload.rapid_answers,
        duration_seconds=payload.duration_seconds,
    )
    hopeless_value = None
    if payload.daily_answers:
        hopeless_value = payload.daily_answers.get("daily_hopeless")
    crisis_payload = detect_crisis(
        texts=[
            payload.journal_text or "",
            json.dumps(payload.daily_answers or {}),
            json.dumps(payload.rapid_answers or {}),
        ],
        structured={
            "risk_score": result.risk_score,
            "hopelessness_score": 10 if hopeless_value and indicates_hopeless(str(hopeless_value)) else None,
        },
    )
    session_id = uuid.uuid4().hex
    session = EvaluationSession(
        id=session_id,
        user_id=user.id,
        inputs_json=json.dumps({
            "journal_text": payload.journal_text,
            "daily_answers": payload.daily_answers,
            "rapid_answers": payload.rapid_answers,
            "duration_seconds": payload.duration_seconds,
        }),
        result_json=json.dumps({
            "risk_score": result.risk_score,
            "risk_level": result.risk_level,
            "signals": result.signals,
            "confidence": result.confidence,
            "quality": {
                "quality_score": result.quality.score,
                "flags": result.quality.flags,
                "is_suspected_fake": result.quality.is_suspected_fake,
                "reason_summary": result.quality.reason_summary,
            },
        }),
        followups_json=json.dumps(result.recommended_followups),
    )
    db.add(session)
    db.commit()
    return {
        "session_id": session_id,
        "risk_score": result.risk_score,
        "risk_level": result.risk_level,
        "signals": result.signals,
        "confidence": result.confidence,
        "quality": {
            "quality_score": result.quality.score,
            "flags": result.quality.flags,
            "is_suspected_fake": result.quality.is_suspected_fake,
            "reason_summary": result.quality.reason_summary,
        },
        "crisis": crisis_payload if crisis_payload.get("is_crisis") else None,
        "recommended_followups": result.recommended_followups,
    }


@app.post("/evaluate/followup")
def evaluate_followup_endpoint(
    payload: EvaluationFollowupRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    session = (
        db.query(EvaluationSession)
        .filter(EvaluationSession.id == payload.session_id, EvaluationSession.user_id == user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=400, detail="Unknown evaluation session.")

    followups = json.loads(session.followups_json or "[]")
    for key, answer in payload.answers.items():
        question = next((q for q in followups if q["key"] == key), None)
        if question:
            db.add(EvaluationFollowup(
                session_id=session.id,
                user_id=user.id,
                question_key=key,
                question_prompt=question["prompt"],
                answer_text=str(answer),
            ))
    db.commit()

    inputs = json.loads(session.inputs_json)
    result = run_evaluation(
        journal_text=inputs.get("journal_text"),
        daily_answers=inputs.get("daily_answers"),
        rapid_answers=inputs.get("rapid_answers"),
        duration_seconds=inputs.get("duration_seconds"),
        followup_answers=payload.answers,
    )
    crisis_payload = detect_crisis(
        texts=[
            inputs.get("journal_text") or "",
            json.dumps(inputs.get("daily_answers") or {}),
            json.dumps(inputs.get("rapid_answers") or {}),
            json.dumps(payload.answers or {}),
        ],
        structured={"risk_score": result.risk_score},
    )
    session.result_json = json.dumps({
        "risk_score": result.risk_score,
        "risk_level": result.risk_level,
        "signals": result.signals,
        "confidence": result.confidence,
        "quality": {
            "quality_score": result.quality.score,
            "flags": result.quality.flags,
            "is_suspected_fake": result.quality.is_suspected_fake,
            "reason_summary": result.quality.reason_summary,
        },
    })
    session.followups_json = json.dumps([])
    db.commit()
    return {
        "risk_score": result.risk_score,
        "risk_level": result.risk_level,
        "signals": result.signals,
        "confidence": result.confidence,
        "quality": {
            "quality_score": result.quality.score,
            "flags": result.quality.flags,
            "is_suspected_fake": result.quality.is_suspected_fake,
            "reason_summary": result.quality.reason_summary,
        },
        "crisis": crisis_payload if crisis_payload.get("is_crisis") else None,
        "recommended_followups": [],
    }


def is_recent_mood_or_anxiety_low(user_id: int, db: Session) -> bool:
    recent_answers = (
        db.query(Answer, Question)
        .join(Question, Answer.question_id == Question.id)
        .filter(
            Answer.user_id == user_id,
            Question.slug.in_(["daily_mood", "daily_anxiety"]),
        )
        .order_by(Answer.created_at.desc())
        .limit(6)
        .all()
    )
    for answer, question in recent_answers:
        value = parse_numeric(answer.answer_text)
        if question.slug == "daily_mood" and value is not None and value <= 3:
            return True
        if question.slug == "daily_anxiety" and value is not None and value >= 8:
            return True
        if question.slug == "daily_anxiety" and contains_high_intensity(answer.answer_text):
            return True
    return False


def parse_numeric(text: str) -> Optional[int]:
    cleaned = "".join(ch for ch in text if ch.isdigit())
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def contains_high_intensity(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in ["high", "severe", "panic", "overwhelmed", "extreme"])


@app.post("/answers")
def submit_answers(
    payload: AnswerBatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> dict:
    if payload.override_datetime and not is_dev_mode():
        raise HTTPException(status_code=403, detail="Developer mode disabled")
    if not payload.answers:
        raise HTTPException(status_code=400, detail="No answers provided")

    question_ids = [item.question_id for item in payload.answers]
    questions = db.query(Question).filter(Question.id.in_(question_ids)).all()
    question_map = {q.id: q for q in questions}
    existing_questions = {q.id for q in questions}
    is_daily = any(q.kind == "daily" for q in questions)
    missing = [qid for qid in question_ids if qid not in existing_questions]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown question IDs: {missing}")

    today = local_today()
    override_dt = payload.override_datetime if is_dev_mode() else None
    if override_dt:
        today = override_dt.date()
    if not is_dev_mode():
        for item in payload.answers:
            if item.entry_date and item.entry_date != today:
                raise HTTPException(
                    status_code=400,
                    detail="entry_date must be today unless dev mode is enabled.",
                )

    quality = None
    if is_daily:
        recent_answers = (
            db.query(Answer, Question)
            .join(Question, Answer.question_id == Question.id)
            .filter(
                Answer.user_id == user.id,
                Question.kind == "daily",
            )
            .order_by(Answer.entry_date.desc(), Answer.created_at.desc())
            .limit(30)
            .all()
        )
        recent_by_date: dict[date, List[str]] = {}
        for answer, _question in recent_answers:
            if answer.entry_date:
                recent_by_date.setdefault(answer.entry_date, []).append(answer.answer_text)
        recent_texts = [
            " | ".join(items)
            for _day, items in sorted(recent_by_date.items(), reverse=True)
        ][:10]
        short_window_count = (
            db.query(Answer)
            .join(Question, Answer.question_id == Question.id)
            .filter(
                Answer.user_id == user.id,
                Question.kind == "daily",
                Answer.created_at >= datetime.utcnow() - timedelta(minutes=10),
            )
            .count()
        )
        current_texts = [item.answer_text.strip() for item in payload.answers]
        quality = assess_structured_quality(current_texts, recent_texts, short_window_count)

    daily_category_map = build_daily_category_map(db) if is_daily else {}
    answer_slug_map: dict[str, str] = {}
    created = []
    for item in payload.answers:
        entry_date = item.entry_date or today
        if not is_dev_mode():
            entry_date = today
        created_at = override_dt if override_dt else datetime.utcnow()
        question = question_map.get(item.question_id)
        category = None
        kind = None
        if question:
            kind = question.kind
            if question.kind == "daily":
                category = daily_category_map.get(question.id)
            else:
                category = question.kind
            answer_slug_map[question.slug] = item.answer_text.strip()

        existing = (
            db.query(Answer)
            .filter(
                Answer.user_id == user.id,
                Answer.question_id == item.question_id,
                Answer.entry_date == entry_date,
            )
            .first()
        )
        if existing:
            existing.answer_text = item.answer_text.strip()
            existing.entry_date = entry_date
            existing.created_at = created_at
            existing.kind = kind
            existing.category = category
            if quality:
                existing.input_quality_score = quality["quality_score"]
                existing.input_quality_flags_json = json.dumps(quality["flags"])
                existing.is_low_quality = quality["is_low_quality"]
            created.append(existing)
        else:
            created.append(Answer(
                user_id=user.id,
                question_id=item.question_id,
                answer_text=item.answer_text.strip(),
                entry_date=entry_date,
                created_at=created_at,
                kind=kind,
                category=category,
                input_quality_score=quality["quality_score"] if quality else None,
                input_quality_flags_json=json.dumps(quality["flags"]) if quality else "[]",
                is_low_quality=quality["is_low_quality"] if quality else False,
            ))
    db.add_all([item for item in created if item.id is None])
    db.commit()
    update_user_baseline(user.id, db)
    if is_daily and quality and not quality["is_low_quality"]:
        store_baseline_snapshot(user.id, db)
    crisis_payload = None
    if is_daily:
        answer_texts = [item.answer_text for item in payload.answers]
        mood_value = answer_slug_map.get("daily_mood")
        anxiety_value = answer_slug_map.get("daily_anxiety")
        hopeless_value = answer_slug_map.get("daily_hopeless")
        structured = {
            "risk_score": None,
            "mood_score": parse_numeric(mood_value or "") if mood_value else None,
            "anxiety_score": parse_numeric(anxiety_value or "") if anxiety_value else None,
            "hopelessness_score": 10 if hopeless_value and indicates_hopeless(hopeless_value) else None,
        }
        crisis_payload = detect_crisis(texts=answer_texts, structured=structured)
        if crisis_payload.get("is_crisis"):
            snippet = " | ".join(answer_texts)[:200]
            record_crisis_event(
                user_id=user.id,
                entry_date=today,
                source="daily",
                level=crisis_payload["level"],
                matched_terms=crisis_payload.get("matched_terms", []),
                snippet=snippet,
                risk_score=None,
                db=db,
            )
            db.commit()
    response = {
        "saved": len(created),
        "micro_signal": build_micro_signal(user.id, db),
        "crisis": crisis_payload if crisis_payload and crisis_payload.get("is_crisis") else None,
    }
    if is_daily:
        response.update({
            "input_quality_score": quality["quality_score"] if quality else None,
            "input_quality_flags": quality["flags"] if quality else [],
            "is_low_quality": quality["is_low_quality"] if quality else False,
            "reason_summary": quality["reason_summary"] if quality else "Looks good.",
        })
    return response


@app.post("/journal", response_model=JournalResponse)
def create_journal_entry(
    payload: JournalCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> JournalResponse:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Journal content cannot be empty")
    if payload.override_datetime and not is_dev_mode():
        raise HTTPException(status_code=403, detail="Developer mode disabled")
    now = datetime.utcnow()
    override_dt = payload.override_datetime if is_dev_mode() else None
    if override_dt:
        now = override_dt
    cutoff = now - timedelta(hours=1)
    recent_count = (
        db.query(JournalEntry)
        .filter(JournalEntry.user_id == user.id, JournalEntry.created_at >= cutoff)
        .count()
    )
    if recent_count >= 10:
        oldest = (
            db.query(JournalEntry)
            .filter(JournalEntry.user_id == user.id, JournalEntry.created_at >= cutoff)
            .order_by(JournalEntry.created_at.asc())
            .first()
        )
        retry_after = calculate_retry_after(oldest.created_at if oldest else None, now)
        raise HTTPException(
            status_code=429,
            detail="Journal rate limit reached (10 per hour). Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    today = date.today()
    entry_date = payload.entry_date or today
    if not is_dev_mode():
        if payload.entry_date and payload.entry_date != today:
            raise HTTPException(
                status_code=400,
                detail="entry_date must be today unless dev mode is enabled.",
            )
        entry_date = today
    if override_dt:
        entry_date = override_dt.date()
    recent_texts = [
        item.content
        for item in db.query(JournalEntry)
        .filter(JournalEntry.user_id == user.id)
        .order_by(JournalEntry.created_at.desc())
        .limit(3)
        .all()
    ]
    short_window_count = (
        db.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user.id,
            JournalEntry.created_at >= datetime.utcnow() - timedelta(minutes=10),
        )
        .count()
    )
    quality = assess_input_quality(content, recent_texts, short_window_count)
    entry = JournalEntry(
        user_id=user.id,
        content=content,
        entry_date=entry_date,
        created_at=now,
        input_quality_score=quality["quality_score"],
        input_quality_flags_json=json.dumps(quality["flags"]),
        is_low_quality=quality["is_low_quality"],
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    update_user_baseline(user.id, db)
    crisis_payload = detect_crisis(texts=[entry.content], structured={})
    if crisis_payload.get("is_crisis"):
        record_crisis_event(
            user_id=user.id,
            entry_date=entry.entry_date or date.today(),
            source="journal",
            level=crisis_payload["level"],
            matched_terms=crisis_payload.get("matched_terms", []),
            snippet=entry.content[:200],
            risk_score=None,
            db=db,
        )
        db.commit()
    return JournalResponse(
        id=entry.id,
        content=entry.content,
        created_at=entry.created_at,
        input_quality_score=entry.input_quality_score,
        input_quality_flags=quality["flags"],
        is_low_quality=entry.is_low_quality,
        reason_summary=quality["reason_summary"],
        crisis=crisis_payload if crisis_payload.get("is_crisis") else None,
    )


@app.get("/journal", response_model=List[JournalResponse])
def list_journal_entries(
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> List[JournalResponse]:
    start_date = date.today() - timedelta(days=days - 1)
    entries = (
        db.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user.id,
            JournalEntry.entry_date.isnot(None),
            JournalEntry.entry_date >= start_date,
        )
        .order_by(JournalEntry.created_at.desc())
        .limit(200)
        .all()
    )
    return [
        JournalResponse(
            id=e.id,
            content=e.content,
            created_at=e.created_at,
            input_quality_score=e.input_quality_score,
            input_quality_flags=json.loads(e.input_quality_flags_json or "[]"),
            is_low_quality=e.is_low_quality,
            reason_summary=summarize_quality_flags(json.loads(e.input_quality_flags_json or "[]")),
        )
        for e in entries
    ]


@app.get("/risk/latest", response_model=RiskResponse)
def risk_latest(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> RiskResponse:
    answers = (
        db.query(Answer, Question)
        .join(Question, Answer.question_id == Question.id)
        .filter(
            Answer.user_id == user.id,
            Question.kind == "daily",
            Answer.is_low_quality.is_(False),
        )
        .order_by(Answer.created_at.desc())
        .limit(10)
        .all()
    )

    last_journal = (
        db.query(JournalEntry)
        .filter(JournalEntry.user_id == user.id)
        .filter(JournalEntry.is_low_quality.is_(False))
        .order_by(JournalEntry.created_at.desc())
        .first()
    )

    risk_level, score, reasons, excerpt = compute_risk_details(answers, last_journal)
    return RiskResponse(
        risk_level=risk_level,
        score=score,
        reasons=reasons,
        last_journal_excerpt=excerpt,
    )


@app.get("/risk/history", response_model=List[RiskHistoryEntry])
def risk_history(
    days: int = Query(30, ge=1, le=365),
    include_low_quality: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[RiskHistoryEntry]:
    start_date = date.today() - timedelta(days=days - 1)
    if include_low_quality and not is_dev_mode():
        include_low_quality = False

    answers = (
        db.query(Answer, Question)
        .join(Question, Answer.question_id == Question.id)
        .filter(
            Answer.user_id == user.id,
            Question.kind == "daily",
            Answer.entry_date.isnot(None),
            Answer.entry_date >= start_date,
            Answer.is_low_quality.is_(False) if not include_low_quality else True,
        )
        .order_by(Answer.entry_date.asc(), Answer.created_at.desc())
        .all()
    )
    journals = (
        db.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user.id,
            JournalEntry.entry_date.isnot(None),
            JournalEntry.entry_date >= start_date,
            JournalEntry.is_low_quality.is_(False) if not include_low_quality else True,
        )
        .order_by(JournalEntry.entry_date.asc(), JournalEntry.created_at.desc())
        .all()
    )

    answers_by_date: dict[date, List[tuple[Answer, Question]]] = {}
    for answer, question in answers:
        day = answer.entry_date
        answers_by_date.setdefault(day, []).append((answer, question))

    journals_by_date: dict[date, JournalEntry] = {}
    for entry in journals:
        day = entry.entry_date
        if day not in journals_by_date:
            journals_by_date[day] = entry

    all_days = sorted(set(answers_by_date.keys()) | set(journals_by_date.keys()))
    history: List[RiskHistoryEntry] = []
    for day in all_days:
        day_answers = answers_by_date.get(day, [])
        day_journal = journals_by_date.get(day)
        risk_level, score, _, _ = compute_risk_details(day_answers, day_journal)
        history.append(RiskHistoryEntry(date=day.isoformat(), score=score, level=risk_level))
    return history


def compute_risk_details(
    answers: List[tuple[Answer, Question]],
    last_journal: Optional[JournalEntry],
) -> tuple[str, int, List[str], Optional[str]]:
    score = 0
    reasons: List[str] = []
    for answer, question in answers:
        value = parse_numeric(answer.answer_text)
        if question.slug == "daily_hopeless" and indicates_hopeless(answer.answer_text):
            score += 2
            reasons.append("Reported hopelessness")
        if question.slug == "daily_isolation" and indicates_isolation(answer.answer_text):
            score += 1
            reasons.append("Reported isolation")
        if question.slug == "daily_mood" and value is not None and value <= 3:
            score += 1
            reasons.append("Low mood rating")
        if question.slug == "daily_anxiety" and value is not None and value >= 8:
            score += 1
            reasons.append("High anxiety rating")

    journal_flag = False
    excerpt = None
    if last_journal:
        excerpt = (last_journal.content[:140] + "...") if len(last_journal.content) > 140 else last_journal.content
        if contains_risk_keywords(last_journal.content):
            journal_flag = True
            score += 3
            reasons.append("Risk keywords in recent journal")

    if journal_flag or score >= 4:
        risk_level = "high"
    elif score >= 2:
        risk_level = "medium"
    else:
        risk_level = "low"

    return risk_level, score, list(dict.fromkeys(reasons)), excerpt


def indicates_hopeless(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in ["yes", "often", "always", "very", "high", "severe"])


def indicates_isolation(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in ["yes", "often", "mostly", "all day", "alone"])


def contains_risk_keywords(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in [
        "suicide",
        "kill myself",
        "end it",
        "end my life",
        "self-harm",
        "self harm",
        "can't go on",
    ])


@app.get("/rapid/questions", response_model=List[RapidQuestion])
def rapid_questions() -> List[RapidQuestion]:
    return [RapidQuestion(**question) for question in RAPID_QUESTIONS]


@app.post("/rapid/start", response_model=RapidStartResponse)
def rapid_start(
    payload: RapidStartRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RapidStartResponse:
    today = date.today()
    entry_date = payload.entry_date or today
    if not is_dev_mode():
        if payload.entry_date and payload.entry_date != today:
            raise HTTPException(
                status_code=400,
                detail="entry_date must be today unless dev mode is enabled.",
            )
        entry_date = today
    now = datetime.utcnow()

    evaluation = RapidEvaluation(
        user_id=user.id,
        started_at=now,
        entry_date=entry_date,
        answers_json="{}",
        score=0,
        level="PENDING",
        signals_json="[]",
        is_valid=True,
        quality_flags_json="[]",
    )
    db.add(evaluation)
    db.commit()
    db.refresh(evaluation)

    return RapidStartResponse(
        session_id=evaluation.id,
        started_at=now.isoformat(),
        entry_date=entry_date.isoformat(),
    )


@app.post("/rapid/submit", response_model=RapidSubmitResponse)
def rapid_submit(
    payload: RapidSubmitRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RapidSubmitResponse:
    if payload.override_datetime and not is_dev_mode():
        raise HTTPException(status_code=403, detail="Developer mode disabled")
    now = datetime.utcnow()
    override_dt = payload.override_datetime if is_dev_mode() else None
    if override_dt:
        now = override_dt
    if is_dev_mode():
        cooldown_seconds = 5
        daily_limit = 50
    else:
        cooldown_seconds = 5 * 60
        daily_limit = 3

    last_eval = (
        db.query(RapidEvaluation)
        .filter(RapidEvaluation.user_id == user.id, RapidEvaluation.submitted_at.isnot(None))
        .order_by(RapidEvaluation.submitted_at.desc())
        .first()
    )
    if last_eval:
        if last_eval.submitted_at and (now - last_eval.submitted_at) < timedelta(seconds=cooldown_seconds):
            wait_seconds = int(cooldown_seconds - (now - last_eval.submitted_at).total_seconds())
            raise HTTPException(
                status_code=429,
                detail="Please wait before starting another rapid evaluation.",
                headers={"Retry-After": str(max(wait_seconds, 1))},
            )

    cutoff = now - timedelta(hours=24)
    recent_count = (
        db.query(RapidEvaluation)
        .filter(
            RapidEvaluation.user_id == user.id,
            RapidEvaluation.submitted_at.isnot(None),
            RapidEvaluation.submitted_at >= cutoff,
        )
        .count()
    )
    if recent_count >= daily_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Daily limit reached ({daily_limit} rapid evaluations in 24 hours). Please try again later.",
        )

    if not payload.answers:
        raise HTTPException(status_code=400, detail="No answers provided")

    active_session = None
    if payload.session_id is not None:
        active_session = (
            db.query(RapidEvaluation)
            .filter(
                RapidEvaluation.id == payload.session_id,
                RapidEvaluation.user_id == user.id,
                RapidEvaluation.submitted_at.is_(None),
            )
            .first()
        )
        if not active_session:
            raise HTTPException(status_code=400, detail="Invalid or expired rapid session.")

    question_lookup = {q["id"]: q for q in RAPID_QUESTIONS}
    answers_by_slug: dict[str, str] = {}
    for answer in payload.answers:
        question = question_lookup.get(answer.question_id)
        if not question:
            raise HTTPException(status_code=400, detail=f"Unknown question ID: {answer.question_id}")
        answers_by_slug[question["slug"]] = answer.answer_text.strip()

    level, score, signals, explanations, actions, crisis = compute_rapid_risk(answers_by_slug)
    crisis_payload = detect_crisis(
        texts=list(answers_by_slug.values()),
        structured={
            "risk_score": score,
            "self_harm_thoughts": is_yes(answers_by_slug.get("rapid_self_harm_thoughts", "")) is True,
            "self_harm_plan": is_yes(answers_by_slug.get("rapid_self_harm_plan", "")) is True,
            "hopelessness_score": 10 if is_yes(answers_by_slug.get("rapid_hopeless", "")) else None,
        },
    )
    today = date.today()
    entry_date = payload.entry_date or (active_session.entry_date if active_session else today)
    if not is_dev_mode():
        if payload.entry_date and payload.entry_date != today:
            raise HTTPException(
                status_code=400,
                detail="entry_date must be today unless dev mode is enabled.",
            )
        entry_date = today
    if override_dt:
        entry_date = override_dt.date()
    started_at = active_session.started_at if active_session else (payload.started_at or now)
    submitted_at = now
    time_taken_seconds = (submitted_at - started_at).total_seconds() if started_at else 0.0

    invalid_flags: List[str] = []
    if started_at and time_taken_seconds < 25:
        invalid_flags.append("too_fast")
    attention = answers_by_slug.get("rapid_attention_check", "")
    if attention.strip().lower() != "sometimes":
        invalid_flags.append("failed_attention_check")

    answers_payload = json.dumps(answers_by_slug, sort_keys=True)
    last_valid = (
        db.query(RapidEvaluation)
        .filter(
            RapidEvaluation.user_id == user.id,
            RapidEvaluation.is_valid.is_(True),
            RapidEvaluation.submitted_at.isnot(None),
        )
        .order_by(func.coalesce(RapidEvaluation.submitted_at, RapidEvaluation.created_at).desc())
        .first()
    )
    if last_valid and last_valid.answers_json == answers_payload:
        invalid_flags.append("duplicate_answers")

    recent_inputs = [
        item.answers_json
        for item in db.query(RapidEvaluation)
        .filter(RapidEvaluation.user_id == user.id, RapidEvaluation.submitted_at.isnot(None))
        .order_by(RapidEvaluation.submitted_at.desc())
        .limit(3)
        .all()
    ]
    short_window_count = (
        db.query(RapidEvaluation)
        .filter(
            RapidEvaluation.user_id == user.id,
            RapidEvaluation.submitted_at.isnot(None),
            RapidEvaluation.submitted_at >= datetime.utcnow() - timedelta(minutes=10),
        )
        .count()
    )
    quality = assess_input_quality(" ".join(answers_by_slug.values()), recent_inputs, short_window_count)

    soft_flags: List[str] = []
    if detect_patterned_answers(answers_by_slug):
        soft_flags.append("patterned_answers")
    if detect_extreme_only_answers(answers_by_slug):
        soft_flags.append("extreme_only_answers")

    quality_flags = list(dict.fromkeys(invalid_flags + soft_flags))
    is_valid = len(invalid_flags) == 0
    confidence_score = compute_rapid_confidence_score(time_taken_seconds, quality_flags)
    micro_signal = build_micro_signal(user.id, db)
    confidence_score = apply_micro_confidence_bonus(confidence_score, micro_signal)

    explanations_sorted = sorted(explanations, key=lambda item: item.weight, reverse=True)
    top_explanations = explanations_sorted[:3]
    signals = [item.reason for item in top_explanations]

    if active_session:
        active_session.entry_date = entry_date
        active_session.started_at = started_at
        active_session.submitted_at = submitted_at
        active_session.answers_json = answers_payload
        active_session.score = score
        active_session.level = level
        active_session.signals_json = json.dumps(signals)
        active_session.confidence_score = confidence_score
        active_session.input_quality_score = quality["quality_score"]
        active_session.input_quality_flags_json = json.dumps(quality["flags"])
        active_session.is_low_quality = quality["is_low_quality"]
        active_session.explainability_json = json.dumps([item.model_dump() for item in top_explanations])
        active_session.time_taken_seconds = time_taken_seconds
        active_session.is_valid = is_valid
        active_session.quality_flags_json = json.dumps(quality_flags)
        if override_dt:
            active_session.created_at = override_dt
    else:
        evaluation = RapidEvaluation(
            user_id=user.id,
            entry_date=entry_date,
            started_at=started_at,
            submitted_at=submitted_at,
            created_at=now,
            answers_json=answers_payload,
            score=score,
            level=level,
            signals_json=json.dumps(signals),
            confidence_score=confidence_score,
            input_quality_score=quality["quality_score"],
            input_quality_flags_json=json.dumps(quality["flags"]),
            is_low_quality=quality["is_low_quality"],
            explainability_json=json.dumps([item.model_dump() for item in top_explanations]),
            time_taken_seconds=time_taken_seconds,
            is_valid=is_valid,
            quality_flags_json=json.dumps(quality_flags),
        )
        db.add(evaluation)
    db.commit()
    update_user_baseline(user.id, db)
    if not quality["is_low_quality"]:
        store_baseline_snapshot(user.id, db)
    if crisis_payload.get("is_crisis"):
        snippet = " | ".join(answers_by_slug.values())[:200]
        record_crisis_event(
            user_id=user.id,
            entry_date=entry_date,
            source="rapid",
            level=crisis_payload["level"],
            matched_terms=crisis_payload.get("matched_terms", []),
            snippet=snippet,
            risk_score=score,
            db=db,
        )
        db.commit()

    return RapidSubmitResponse(
        level=level,
        score=score,
        signals=signals,
        recommended_actions=actions,
        crisis_guidance=crisis,
        crisis=crisis_payload if crisis_payload.get("is_crisis") else None,
        confidence_score=confidence_score,
        explanations=top_explanations,
        is_valid=is_valid,
        quality_flags=quality_flags,
        time_taken_seconds=time_taken_seconds,
        micro_signal=micro_signal,
        input_quality_score=quality["quality_score"],
        input_quality_flags=quality["flags"],
        is_low_quality=quality["is_low_quality"],
        reason_summary=quality["reason_summary"],
        entry_date=entry_date.isoformat(),
    )


@app.get("/rapid/history", response_model=List[RiskHistoryEntry])
def rapid_history(
    days: int = Query(30, ge=1, le=365),
    include_invalid: bool = Query(False),
    include_low_quality: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[RiskHistoryEntry]:
    start_date = date.today() - timedelta(days=days - 1)
    if include_low_quality and not is_dev_mode():
        include_low_quality = False
    query = (
        db.query(RapidEvaluation)
        .filter(
            RapidEvaluation.user_id == user.id,
            RapidEvaluation.entry_date.isnot(None),
            RapidEvaluation.entry_date >= start_date,
            RapidEvaluation.submitted_at.isnot(None),
            RapidEvaluation.is_low_quality.is_(False) if not include_low_quality else True,
        )
    )
    if not include_invalid:
        query = query.filter(or_(RapidEvaluation.is_valid.is_(True), RapidEvaluation.is_valid.is_(None)))
    evaluations = query.order_by(
        RapidEvaluation.entry_date.asc(),
        RapidEvaluation.created_at.desc(),
    ).all()

    by_date: dict[date, RapidEvaluation] = {}
    for evaluation in evaluations:
        day = evaluation.entry_date
        existing = by_date.get(day)
        if not existing or evaluation.score > existing.score:
            by_date[day] = evaluation

    history = [
        RiskHistoryEntry(
            date=day.isoformat(),
            score=entry.score,
            level=entry.level,
        )
        for day, entry in sorted(by_date.items())
    ]
    return history


def clear_demo_rows(user_id: int, db: Session) -> dict:
    answers_deleted = (
        db.query(Answer)
        .filter(Answer.user_id == user_id, Answer.is_demo.is_(True))
        .delete(synchronize_session=False)
    )
    journals_deleted = (
        db.query(JournalEntry)
        .filter(JournalEntry.user_id == user_id, JournalEntry.is_demo.is_(True))
        .delete(synchronize_session=False)
    )
    rapid_deleted = (
        db.query(RapidEvaluation)
        .filter(RapidEvaluation.user_id == user_id, RapidEvaluation.is_demo.is_(True))
        .delete(synchronize_session=False)
    )
    return {
        "answers": answers_deleted,
        "journals": journals_deleted,
        "rapid_evaluations": rapid_deleted,
    }


@app.post("/dev/seed_demo")
def seed_demo_data(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not is_dev_mode():
        raise HTTPException(status_code=404, detail="Not found")

    deleted = clear_demo_rows(user.id, db)
    created_answers = 0
    created_journals = 0
    created_rapid = 0

    daily_questions = (
        db.query(Question)
        .filter(Question.kind == "daily")
        .all()
    )
    questions_by_slug = {q.slug: q for q in daily_questions}
    target_slugs = ["daily_mood", "daily_anxiety", "daily_hopeless", "daily_isolation"]
    required = [questions_by_slug.get(slug) for slug in target_slugs]
    if any(item is None for item in required):
        raise HTTPException(status_code=500, detail="Daily questions missing for demo data.")

    today = date.today()
    answer_rows: List[Answer] = []
    for i in range(14):
        day = today - timedelta(days=i)
        created_at = datetime.combine(day, datetime.min.time()) + timedelta(hours=9)
        mood_value = 7 if i % 3 else 3
        anxiety_value = 4 if i % 4 else 8
        hopeless_value = "Yes" if i % 5 == 0 else "No"
        isolation_value = "Yes" if i % 4 == 0 else "No"
        demo_answers = {
            "daily_mood": str(mood_value),
            "daily_anxiety": str(anxiety_value),
            "daily_hopeless": hopeless_value,
            "daily_isolation": isolation_value,
        }
        for slug, value in demo_answers.items():
            question = questions_by_slug[slug]
            answer_rows.append(Answer(
                user_id=user.id,
                question_id=question.id,
                answer_text=value,
                created_at=created_at,
                entry_date=day,
                is_demo=True,
            ))
        created_answers += len(demo_answers)

    db.add_all(answer_rows)

    journal_days = [0, 3, 6, 9, 12]
    journal_texts = [
        "Felt steady today and took a short walk.",
        "A bit drained, but I reached out to a friend.",
        "Feeling isolated and low energy.",
        "Hard day. Thoughts of self-harm came up, but I stayed safe.",
        "Sleep was better and I felt calmer.",
    ]
    for offset, text in zip(journal_days, journal_texts):
        day = today - timedelta(days=offset)
        created_at = datetime.combine(day, datetime.min.time()) + timedelta(hours=20)
        db.add(JournalEntry(
            user_id=user.id,
            content=text,
            created_at=created_at,
            entry_date=day,
            is_demo=True,
        ))
        created_journals += 1

    rapid_dates = [1, 4, 8, 12]
    for idx, offset in enumerate(rapid_dates):
        day = today - timedelta(days=offset)
        started_at = datetime.combine(day, datetime.min.time()) + timedelta(hours=10)
        submitted_at = started_at + timedelta(seconds=70 if idx % 2 == 0 else 15)
        answers_by_slug = {
            "rapid_mood": "3" if idx % 2 == 0 else "7",
            "rapid_anxiety": "8" if idx % 3 == 0 else "4",
            "rapid_hopeless": "Yes" if idx == 1 else "No",
            "rapid_isolation": "Yes" if idx % 2 == 0 else "No",
            "rapid_sleep": "Poor" if idx % 2 == 0 else "Okay",
            "rapid_appetite": "Okay",
            "rapid_support": "No" if idx == 2 else "Yes",
            "rapid_self_harm_thoughts": "No",
            "rapid_self_harm_plan": "No",
            "rapid_substance": "No",
            "rapid_attention_check": "Sometimes" if idx != 3 else "Never",
        }
        level, score, _, explanations, _, _ = compute_rapid_risk(answers_by_slug)
        time_taken_seconds = (submitted_at - started_at).total_seconds()
        quality_flags: List[str] = []
        invalid_flags: List[str] = []
        if time_taken_seconds < 25:
            invalid_flags.append("too_fast")
        if answers_by_slug["rapid_attention_check"].lower() != "sometimes":
            invalid_flags.append("failed_attention_check")
        if idx == 2:
            quality_flags.append("patterned_answers")
        quality_flags = list(dict.fromkeys(invalid_flags + quality_flags))
        is_valid = len(invalid_flags) == 0
        confidence_score = compute_rapid_confidence_score(time_taken_seconds, quality_flags)
        top_explanations = sorted(explanations, key=lambda item: item.weight, reverse=True)[:3]
        signals = [item.reason for item in top_explanations]

        db.add(RapidEvaluation(
            user_id=user.id,
            created_at=submitted_at,
            entry_date=day,
            started_at=started_at,
            submitted_at=submitted_at,
            answers_json=json.dumps(answers_by_slug, sort_keys=True),
            score=score,
            level=level,
            signals_json=json.dumps(signals),
            confidence_score=confidence_score,
            explainability_json=json.dumps([item.model_dump() for item in top_explanations]),
            time_taken_seconds=time_taken_seconds,
            is_valid=is_valid,
            quality_flags_json=json.dumps(quality_flags),
            is_demo=True,
        ))
        created_rapid += 1

    db.commit()
    return {
        "created": {
            "answers": created_answers,
            "journals": created_journals,
            "rapid_evaluations": created_rapid,
        },
        "deleted": deleted,
    }


@app.post("/dev/clear_demo")
def clear_demo_data(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not is_dev_mode():
        raise HTTPException(status_code=404, detail="Not found")
    deleted = clear_demo_rows(user.id, db)
    db.commit()
    return {"deleted": deleted}


@app.get("/export/anonymized")
def export_anonymized(
    days: int = Query(30, ge=1, le=365),
    format: str = Query("zip", pattern="^(zip|json)$"),
    include_journal_text: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    if format == "json":
        export_payload = build_export_json(user, db, days, include_journal_text)
        return Response(
            content=json.dumps(export_payload, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=mindtriage_export.json"},
        )

    export_bytes = build_export_zip(user, db, days, include_journal_text)
    filename = f"mindtriage_export_{date.today().isoformat()}.zip"
    return Response(
        content=export_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.post("/import/anonymized")
async def import_anonymized(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    content = await file.read()
    try:
        payload = json.loads(content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON file.") from exc

    regular_rows = payload.get("regular_checkins", [])
    rapid_rows = payload.get("rapid_evaluations", [])
    journal_rows = payload.get("journals", [])

    question_map = {
        q.slug: q.id
        for q in db.query(Question).all()
    }
    daily_category_map = build_daily_category_map(db)
    existing_answer_keys = {
        (entry.entry_date.isoformat(), entry.question_id)
        for entry in db.query(Answer)
        .filter(Answer.user_id == user.id, Answer.entry_date.isnot(None))
        .all()
    }
    existing_journal_dates = {
        entry.entry_date.isoformat()
        for entry in db.query(JournalEntry)
        .filter(JournalEntry.user_id == user.id, JournalEntry.entry_date.isnot(None))
        .all()
    }
    existing_rapid_dates = {
        entry.entry_date.isoformat()
        for entry in db.query(RapidEvaluation)
        .filter(RapidEvaluation.user_id == user.id, RapidEvaluation.entry_date.isnot(None))
        .all()
    }

    created = {"answers": 0, "journals": 0, "rapid_evaluations": 0}

    for row in regular_rows:
        entry_date = row.get("entry_date")
        question_slug = row.get("question_slug")
        if not entry_date or not question_slug:
            continue
        question_id = question_map.get(question_slug)
        if not question_id:
            continue
        key = (entry_date, question_id)
        if key in existing_answer_keys:
            continue
        try:
            parsed_date = date.fromisoformat(entry_date)
        except ValueError:
            continue
        created_at = parse_datetime_safe(row.get("created_at")) or datetime.utcnow()
        answer = Answer(
            user_id=user.id,
            question_id=question_id,
            answer_text=str(row.get("answer_text", "")).strip(),
            entry_date=parsed_date,
            created_at=created_at,
            kind=row.get("kind") or "daily",
            category=row.get("category") or daily_category_map.get(question_id),
        )
        db.add(answer)
        existing_answer_keys.add(key)
        created["answers"] += 1

    for row in journal_rows:
        entry_date = row.get("entry_date")
        text_value = row.get("text")
        if not entry_date or not text_value:
            continue
        if entry_date in existing_journal_dates:
            continue
        try:
            parsed_date = date.fromisoformat(entry_date)
        except ValueError:
            continue
        created_at = parse_datetime_safe(row.get("created_at")) or datetime.utcnow()
        entry = JournalEntry(
            user_id=user.id,
            content=str(text_value).strip(),
            entry_date=parsed_date,
            created_at=created_at,
        )
        db.add(entry)
        existing_journal_dates.add(entry_date)
        created["journals"] += 1

    for row in rapid_rows:
        entry_date = row.get("entry_date")
        if not entry_date or entry_date in existing_rapid_dates:
            continue
        try:
            parsed_date = date.fromisoformat(entry_date)
        except ValueError:
            continue
        created_at = parse_datetime_safe(row.get("created_at")) or datetime.utcnow()
        evaluation = RapidEvaluation(
            user_id=user.id,
            created_at=created_at,
            entry_date=parsed_date,
            started_at=None,
            submitted_at=created_at,
            answers_json="{}",
            score=int(row.get("score", 0) or 0),
            level=str(row.get("level", "GREEN")),
            signals_json=str(row.get("signals", "[]")),
            confidence_score=row.get("confidence_score"),
            explainability_json=str(row.get("explanations", "[]")),
            time_taken_seconds=row.get("time_taken_seconds"),
            is_valid=bool(row.get("is_valid", True)),
            quality_flags_json=str(row.get("quality_flags", "[]")),
            is_low_quality=False,
        )
        db.add(evaluation)
        existing_rapid_dates.add(entry_date)
        created["rapid_evaluations"] += 1

    db.commit()
    update_user_baseline(user.id, db)
    return {"created": created}


@app.get("/export/anonymized/self_check")
def export_anonymized_self_check(
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    export_bytes = build_export_zip(user, db, days, include_journal_text=False)
    email = user.email.lower()
    pii_detected = email in export_bytes.decode(errors="ignore").lower()
    return {"pii_detected": pii_detected, "bytes": len(export_bytes)}


@app.get("/metrics/summary")
def metrics_summary(
    days: int = Query(30, ge=1, le=365),
    include_low_quality: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    start_date = date.today() - timedelta(days=days - 1)
    if include_low_quality and not is_dev_mode():
        include_low_quality = False
    regular_summary = build_regular_metrics(user.id, db, start_date, days, include_low_quality)
    rapid_summary = build_rapid_metrics(user.id, db, start_date, include_low_quality)
    safety_summary = build_safety_metrics(user.id, db, start_date, include_low_quality)
    return {
        "regular": regular_summary,
        "rapid": rapid_summary,
        "safety": safety_summary,
    }


@app.get("/baseline/summary")
def baseline_summary(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    baseline = update_user_baseline(user.id, db)
    if not baseline:
        return {
            "baseline_ready": False,
            "sample_count": 0,
            "mean": None,
            "std": None,
            "response_time_mean": None,
            "response_time_std": None,
            "confidence_mean": None,
            "confidence_std": None,
            "recommended_personal_thresholds": None,
        }
    ready = baseline.sample_count >= 5
    thresholds = None
    if ready and baseline.baseline_score_mean is not None and baseline.baseline_score_std is not None:
        thresholds = {
            "low": round(max(baseline.baseline_score_mean - baseline.baseline_score_std, 0.0), 2),
            "high": round(baseline.baseline_score_mean + baseline.baseline_score_std, 2),
        }
    return {
        "baseline_ready": ready,
        "sample_count": baseline.sample_count,
        "mean": baseline.baseline_score_mean,
        "std": baseline.baseline_score_std,
        "response_time_mean": baseline.baseline_response_time_mean,
        "response_time_std": baseline.baseline_response_time_std,
        "confidence_mean": baseline.baseline_confidence_mean,
        "confidence_std": baseline.baseline_confidence_std,
        "recommended_personal_thresholds": thresholds,
    }


@app.get("/insights/today")
def insights_today(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    baseline = update_user_baseline(user.id, db)
    if not baseline or baseline.sample_count < 5 or baseline.baseline_score_mean is None:
        return {
            "baseline_ready": False,
            "message": "Baseline building. Complete at least 5 check-ins.",
        }

    today = date.today()
    answers = (
        db.query(Answer, Question)
        .join(Question, Answer.question_id == Question.id)
        .filter(
            Answer.user_id == user.id,
            Question.kind == "daily",
            Answer.entry_date == today,
            Answer.is_low_quality.is_(False),
        )
        .all()
    )
    journal = (
        db.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user.id,
            JournalEntry.entry_date == today,
            JournalEntry.is_low_quality.is_(False),
        )
        .order_by(JournalEntry.created_at.desc())
        .first()
    )
    if not answers and not journal:
        return {
            "baseline_ready": True,
            "message": "No check-in data for today.",
        }

    _, score, _, _ = compute_risk_details(answers, journal)
    std = baseline.baseline_score_std or 0.0
    if std > 0:
        z_score = (score - baseline.baseline_score_mean) / std
    else:
        z_score = 0.0
    if z_score >= 1:
        interpretation = "higher than your usual"
    elif z_score <= -1:
        interpretation = "lower than your usual"
    else:
        interpretation = "within your normal range"

    return {
        "baseline_ready": True,
        "today_score": score,
        "z_score": round(z_score, 2),
        "interpretation": interpretation,
    }


def store_baseline_snapshot(
    user_id: int,
    db: Session,
    window_days: int = 14,
    include_low_quality: bool = False,
    end_date: Optional[date] = None,
) -> dict:
    target_end = end_date or (local_today() - timedelta(days=1))
    if window_days < 1:
        window_days = 1
    payload = compute_baseline_snapshot(
        user_id=user_id,
        window_days=window_days,
        include_low_quality=include_low_quality,
        end_date=target_end,
        db=db,
    )
    snapshot = BaselineSnapshot(
        user_id=user_id,
        computed_at=datetime.utcnow(),
        window_days=window_days,
        json_payload=json.dumps(payload),
    )
    db.add(snapshot)
    db.commit()
    return payload


@app.get("/insights/drift")
def insights_drift(
    window_days: int = Query(14, ge=7, le=60),
    date_override: Optional[date] = Query(None, alias="date"),
    include_low_quality: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if include_low_quality and not is_dev_mode():
        include_low_quality = False
    target_date = local_today()
    if date_override is not None:
        if not is_dev_mode():
            raise HTTPException(status_code=403, detail="Developer mode disabled")
        target_date = date_override

    baseline_end = target_date - timedelta(days=1)
    baseline_payload = store_baseline_snapshot(
        user_id=user.id,
        db=db,
        window_days=window_days,
        include_low_quality=include_low_quality,
        end_date=baseline_end,
    )

    signals_window = collect_signals_for_window(
        user_id=user.id,
        start_date=target_date,
        end_date=target_date,
        include_low_quality=include_low_quality,
        db=db,
    )
    signals_today = signals_window.get(target_date, {})
    baseline_signals = baseline_payload.get("signals", {})
    drift, top_changes, confidence, recommendations = compute_drift(signals_today, baseline_signals)

    return {
        "date": target_date.isoformat(),
        "baseline_window_days": window_days,
        "signals_today": signals_today,
        "baseline": baseline_payload,
        "drift": drift,
        "top_changes": top_changes,
        "confidence": confidence,
        "recommendations": recommendations,
    }


def compute_rapid_risk(
    answers_by_slug: dict[str, str]
) -> tuple[str, int, List[str], List[RapidExplainabilityItem], List[str], Optional[List[str]]]:
    score = 0
    signals: List[str] = []
    explanations: List[RapidExplainabilityItem] = []

    def add_signal(signal: str, weight: float, reason: str) -> None:
        explanations.append(RapidExplainabilityItem(signal=signal, weight=weight, reason=reason))
        signals.append(reason)

    mood_value = parse_numeric(answers_by_slug.get("rapid_mood", ""))
    if mood_value is not None and mood_value <= 3:
        score += 3
        add_signal("low_mood", 3, "Low mood rating")

    anxiety_value = parse_numeric(answers_by_slug.get("rapid_anxiety", ""))
    if anxiety_value is not None and anxiety_value >= 8:
        score += 3
        add_signal("high_anxiety", 3, "High anxiety rating")

    if is_yes(answers_by_slug.get("rapid_hopeless", "")):
        score += 4
        add_signal("hopelessness", 4, "Reported hopelessness")

    if is_yes(answers_by_slug.get("rapid_isolation", "")):
        score += 2
        add_signal("isolation", 2, "Reported isolation")

    if is_choice(answers_by_slug.get("rapid_sleep", ""), "Poor"):
        score += 1
        add_signal("poor_sleep", 1, "Poor sleep")

    if is_choice(answers_by_slug.get("rapid_appetite", ""), "Poor"):
        score += 1
        add_signal("low_appetite", 1, "Low appetite")

    if is_yes(answers_by_slug.get("rapid_support", "")) is False:
        score += 1
        add_signal("limited_support", 1, "Limited support right now")

    if is_yes(answers_by_slug.get("rapid_substance", "")):
        score += 1
        add_signal("substance_use", 1, "Substance use today")

    self_harm_thoughts = is_yes(answers_by_slug.get("rapid_self_harm_thoughts", ""))
    self_harm_plan = is_yes(answers_by_slug.get("rapid_self_harm_plan", ""))
    if self_harm_thoughts:
        score += 6
        add_signal("self_harm_thoughts", 6, "Self-harm thoughts")

    crisis_guidance = None
    if self_harm_plan:
        before = score
        level = "RED"
        score = max(score, 18)
        add_signal(
            "self_harm_plan",
            max(0, score - before),
            "Self-harm plan or intent",
        )
        crisis_guidance = crisis_resources()
    elif score >= 12:
        level = "RED"
        crisis_guidance = crisis_resources()
    elif score >= 6:
        level = "YELLOW"
    else:
        level = "GREEN"

    actions = recommended_actions(level)
    return level, score, list(dict.fromkeys(signals)), explanations, actions, crisis_guidance


def is_yes(value: str) -> Optional[bool]:
    if not value:
        return None
    lowered = value.strip().lower()
    if lowered in {"yes", "y", "true", "1"}:
        return True
    if lowered in {"no", "n", "false", "0"}:
        return False
    return None


def is_choice(value: str, target: str) -> bool:
    return value.strip().lower() == target.strip().lower()


def recommended_actions(level: str) -> List[str]:
    if level == "RED":
        return [
            "Pause and focus on slow breathing for 2 minutes.",
            "Move to a safer, quieter space if possible.",
            "Reach out to someone you trust and let them know you need support.",
        ]
    if level == "YELLOW":
        return [
            "Do a 2-minute grounding exercise (name 5 things you can see).",
            "Drink water and take a short break from screens.",
            "Write down one small next step you can do today.",
        ]
    return [
        "Take a slow breath and notice how your body feels.",
        "Pick one small, kind action for yourself in the next hour.",
        "Stay connected to a supportive person if you can.",
    ]


def crisis_resources() -> List[str]:
    return [
        "If you feel unsafe, contact local emergency services.",
        "Reach out to a trusted person or local crisis line.",
        "If you are in the U.S., you can call or text 988 for immediate support.",
    ]


def record_crisis_event(
    user_id: int,
    entry_date: date,
    source: str,
    level: str,
    matched_terms: List[str],
    snippet: Optional[str],
    risk_score: Optional[int],
    db: Session,
) -> None:
    existing = (
        db.query(CrisisEvent)
        .filter(
            CrisisEvent.user_id == user_id,
            CrisisEvent.entry_date == entry_date,
            CrisisEvent.source == source,
            CrisisEvent.level == level,
        )
        .order_by(CrisisEvent.created_at.desc())
        .first()
    )
    if existing and snippet and existing.snippet == snippet:
        return
    db.add(CrisisEvent(
        user_id=user_id,
        entry_date=entry_date,
        created_at=datetime.utcnow(),
        source=source,
        level=level,
        matched_terms_json=json.dumps(matched_terms),
        snippet=snippet,
        risk_score_at_time=risk_score,
    ))


def compute_rapid_confidence_score(time_taken_seconds: float, quality_flags: List[str]) -> float:
    confidence = 0.6
    if time_taken_seconds >= 60:
        confidence += 0.15
    elif 35 <= time_taken_seconds <= 59:
        confidence += 0.10

    if "too_fast" in quality_flags:
        confidence -= 0.20
    if "failed_attention_check" in quality_flags:
        confidence -= 0.25
    if "duplicate_answers" in quality_flags:
        confidence -= 0.10
    if "patterned_answers" in quality_flags:
        confidence -= 0.10
    if "extreme_only_answers" in quality_flags:
        confidence -= 0.10

    return max(0.05, min(0.95, confidence))


def detect_patterned_answers(answers_by_slug: dict[str, str]) -> bool:
    values = [
        value.strip().lower()
        for slug, value in answers_by_slug.items()
        if slug != "rapid_attention_check" and value.strip()
    ]
    if len(values) < 5:
        return False
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    most_common = max(counts.values()) if counts else 0
    if most_common == len(values):
        return True
    return (most_common / len(values)) >= 0.8


def detect_extreme_only_answers(answers_by_slug: dict[str, str]) -> bool:
    numeric_values: List[int] = []
    for slug in ["rapid_mood", "rapid_anxiety"]:
        numeric = parse_numeric(answers_by_slug.get(slug, ""))
        if numeric is not None:
            numeric_values.append(numeric)
    if len(numeric_values) < 2:
        return False
    return all(value <= 2 or value >= 9 for value in numeric_values)


def pseudonymize_user(user_id: int) -> str:
    return sha256(f"{user_id}:{EXPORT_SALT}".encode("utf-8")).hexdigest()[:16]


def build_export_zip(
    user: User,
    db: Session,
    days: int,
    include_journal_text: bool,
) -> bytes:
    start_date = date.today() - timedelta(days=days - 1)
    pseudonym = pseudonymize_user(user.id)

    regular_rows = build_regular_checkins_rows(user.id, db, start_date, pseudonym)
    rapid_rows = build_rapid_rows(user.id, db, start_date, pseudonym)
    risk_rows = build_risk_history_rows(user.id, db, start_date, pseudonym)
    journal_rows = build_journal_rows(user.id, db, start_date, pseudonym, include_journal_text)

    schema = {
        "regular_checkins.csv": list(regular_rows[0].keys()) if regular_rows else [],
        "rapid_evaluations.csv": list(rapid_rows[0].keys()) if rapid_rows else [],
        "risk_history.csv": list(risk_rows[0].keys()) if risk_rows else [],
        "journals.csv": list(journal_rows[0].keys()) if journal_rows else [],
    }

    readme_text = (
        "MindTriage anonymized export.\n"
        "- PII removed (email/username/user_id replaced by pseudonym).\n"
        "- Includes only the current user's data within the requested date range.\n"
        "- Journal text included only if include_journal_text=true.\n"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("regular_checkins.csv", rows_to_csv(regular_rows))
        archive.writestr("rapid_evaluations.csv", rows_to_csv(rapid_rows))
        archive.writestr("risk_history.csv", rows_to_csv(risk_rows))
        archive.writestr("journals.csv", rows_to_csv(journal_rows))
        archive.writestr("schema.json", json.dumps(schema, indent=2))
        archive.writestr("README_EXPORT.txt", readme_text)

    return buffer.getvalue()


def build_export_json(
    user: User,
    db: Session,
    days: int,
    include_journal_text: bool,
) -> dict:
    start_date = date.today() - timedelta(days=days - 1)
    pseudonym = pseudonymize_user(user.id)

    regular_rows = build_regular_checkins_rows(user.id, db, start_date, pseudonym)
    rapid_rows = build_rapid_rows(user.id, db, start_date, pseudonym)
    risk_rows = build_risk_history_rows(user.id, db, start_date, pseudonym)
    journal_rows = build_journal_rows(user.id, db, start_date, pseudonym, include_journal_text)

    schema = {
        "regular_checkins": list(regular_rows[0].keys()) if regular_rows else [],
        "rapid_evaluations": list(rapid_rows[0].keys()) if rapid_rows else [],
        "risk_history": list(risk_rows[0].keys()) if risk_rows else [],
        "journals": list(journal_rows[0].keys()) if journal_rows else [],
    }

    return {
        "meta": {
            "generated_at": datetime.utcnow().isoformat(),
            "days": days,
            "subject_id": pseudonym,
        },
        "regular_checkins": regular_rows,
        "rapid_evaluations": rapid_rows,
        "risk_history": risk_rows,
        "journals": journal_rows,
        "schema": schema,
    }


def rows_to_csv(rows: List[dict]) -> str:
    if not rows:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def build_regular_checkins_rows(
    user_id: int,
    db: Session,
    start_date: date,
    pseudonym: str,
) -> List[dict]:
    category_map = build_daily_category_map(db)
    answers = (
        db.query(Answer, Question)
        .join(Question, Answer.question_id == Question.id)
        .filter(
            Answer.user_id == user_id,
            Answer.entry_date.isnot(None),
            Answer.entry_date >= start_date,
        )
        .order_by(Answer.entry_date.asc(), Answer.created_at.asc())
        .all()
    )
    rows = []
    for answer, question in answers:
        rows.append({
            "subject_id": pseudonym,
            "entry_date": answer.entry_date.isoformat(),
            "question_slug": question.slug,
            "kind": answer.kind or question.kind,
            "category": answer.category or category_map.get(answer.question_id),
            "answer_text": answer.answer_text,
            "created_at": answer.created_at.isoformat(),
            "is_demo": answer.is_demo,
        })
    return rows


def build_rapid_rows(
    user_id: int,
    db: Session,
    start_date: date,
    pseudonym: str,
) -> List[dict]:
    evaluations = (
        db.query(RapidEvaluation)
        .filter(
            RapidEvaluation.user_id == user_id,
            RapidEvaluation.entry_date.isnot(None),
            RapidEvaluation.entry_date >= start_date,
            RapidEvaluation.submitted_at.isnot(None),
        )
        .order_by(RapidEvaluation.entry_date.asc(), RapidEvaluation.submitted_at.asc())
        .all()
    )
    rows = []
    for evaluation in evaluations:
        rows.append({
            "subject_id": pseudonym,
            "entry_date": evaluation.entry_date.isoformat(),
            "score": evaluation.score,
            "level": evaluation.level,
            "confidence_score": evaluation.confidence_score,
            "time_taken_seconds": evaluation.time_taken_seconds,
            "is_valid": evaluation.is_valid,
            "quality_flags": evaluation.quality_flags_json,
            "signals": evaluation.signals_json,
            "explanations": evaluation.explainability_json,
            "created_at": evaluation.created_at.isoformat(),
            "is_demo": evaluation.is_demo,
        })
    return rows


def build_risk_history_rows(
    user_id: int,
    db: Session,
    start_date: date,
    pseudonym: str,
) -> List[dict]:
    answers = (
        db.query(Answer, Question)
        .join(Question, Answer.question_id == Question.id)
        .filter(
            Answer.user_id == user_id,
            Question.kind == "daily",
            Answer.entry_date.isnot(None),
            Answer.entry_date >= start_date,
            Answer.is_low_quality.is_(False),
        )
        .order_by(Answer.entry_date.asc(), Answer.created_at.desc())
        .all()
    )
    journals = (
        db.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.entry_date.isnot(None),
            JournalEntry.entry_date >= start_date,
            JournalEntry.is_low_quality.is_(False),
        )
        .order_by(JournalEntry.entry_date.asc(), JournalEntry.created_at.desc())
        .all()
    )

    answers_by_date: dict[date, List[tuple[Answer, Question]]] = {}
    for answer, question in answers:
        day = answer.entry_date
        answers_by_date.setdefault(day, []).append((answer, question))

    journals_by_date: dict[date, JournalEntry] = {}
    for entry in journals:
        day = entry.entry_date
        if day not in journals_by_date:
            journals_by_date[day] = entry

    all_days = sorted(set(answers_by_date.keys()) | set(journals_by_date.keys()))
    rows = []
    for day in all_days:
        day_answers = answers_by_date.get(day, [])
        day_journal = journals_by_date.get(day)
        risk_level, score, _, _ = compute_risk_details(day_answers, day_journal)
        rows.append({
            "subject_id": pseudonym,
            "entry_date": day.isoformat(),
            "score": score,
            "level": risk_level,
        })
    return rows


def build_journal_rows(
    user_id: int,
    db: Session,
    start_date: date,
    pseudonym: str,
    include_text: bool,
) -> List[dict]:
    journals = (
        db.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.entry_date.isnot(None),
            JournalEntry.entry_date >= start_date,
        )
        .order_by(JournalEntry.entry_date.asc(), JournalEntry.created_at.asc())
        .all()
    )
    rows = []
    for entry in journals:
        row = {
            "subject_id": pseudonym,
            "entry_date": entry.entry_date.isoformat(),
            "created_at": entry.created_at.isoformat(),
            "length": len(entry.content),
            "sentiment_score": "",
            "is_demo": entry.is_demo,
        }
        if include_text:
            row["text"] = entry.content
        rows.append(row)
    return rows


def build_regular_metrics(user_id: int, db: Session, start_date: date, days: int, include_low_quality: bool) -> dict:
    daily_scores = []
    scores_by_day: dict[date, int] = {}

    answers = (
        db.query(Answer, Question)
        .join(Question, Answer.question_id == Question.id)
        .filter(
            Answer.user_id == user_id,
            Question.kind == "daily",
            Answer.entry_date.isnot(None),
            Answer.entry_date >= start_date,
            Answer.is_low_quality.is_(False) if not include_low_quality else True,
        )
        .order_by(Answer.entry_date.asc(), Answer.created_at.desc())
        .all()
    )
    journals = (
        db.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.entry_date.isnot(None),
            JournalEntry.entry_date >= start_date,
            JournalEntry.is_low_quality.is_(False) if not include_low_quality else True,
        )
        .order_by(JournalEntry.entry_date.asc(), JournalEntry.created_at.desc())
        .all()
    )

    answers_by_date: dict[date, List[tuple[Answer, Question]]] = {}
    for answer, question in answers:
        answers_by_date.setdefault(answer.entry_date, []).append((answer, question))

    journals_by_date: dict[date, JournalEntry] = {}
    for entry in journals:
        if entry.entry_date not in journals_by_date:
            journals_by_date[entry.entry_date] = entry

    all_days = sorted(set(answers_by_date.keys()) | set(journals_by_date.keys()))
    for day in all_days:
        _, score, _, _ = compute_risk_details(
            answers_by_date.get(day, []),
            journals_by_date.get(day),
        )
        scores_by_day[day] = score
        daily_scores.append(score)

    count_checkins = len(all_days)
    missing_days = max(0, days - count_checkins)
    mean_score = statistics.mean(daily_scores) if daily_scores else 0.0
    median_score = statistics.median(daily_scores) if daily_scores else 0.0
    std_score = statistics.pstdev(daily_scores) if len(daily_scores) >= 2 else 0.0

    trend_slope_14d = compute_trend_slope(scores_by_day, lookback_days=14)

    return {
        "count_checkins": count_checkins,
        "missing_days": missing_days,
        "mean_score": round(mean_score, 2),
        "median_score": round(median_score, 2),
        "std_score": round(std_score, 2),
        "trend_slope_14d": round(trend_slope_14d, 4),
    }


def build_rapid_metrics(user_id: int, db: Session, start_date: date, include_low_quality: bool) -> dict:
    evaluations = (
        db.query(RapidEvaluation)
        .filter(
            RapidEvaluation.user_id == user_id,
            RapidEvaluation.entry_date.isnot(None),
            RapidEvaluation.entry_date >= start_date,
            RapidEvaluation.submitted_at.isnot(None),
            RapidEvaluation.is_low_quality.is_(False) if not include_low_quality else True,
        )
        .order_by(RapidEvaluation.entry_date.asc(), RapidEvaluation.submitted_at.desc())
        .all()
    )
    count_total = len(evaluations)
    count_valid = sum(1 for item in evaluations if item.is_valid)
    count_invalid = count_total - count_valid

    invalid_reason_counts: dict[str, int] = {}
    for item in evaluations:
        if not item.is_valid:
            flags = json.loads(item.quality_flags_json or "[]")
            for flag in flags:
                invalid_reason_counts[flag] = invalid_reason_counts.get(flag, 0) + 1

    valid_times = [
        item.time_taken_seconds
        for item in evaluations
        if item.is_valid and item.time_taken_seconds is not None
    ]
    mean_time_seconds_valid = statistics.mean(valid_times) if valid_times else 0.0

    confidence_counts = {"low": 0, "medium": 0, "high": 0}
    level_counts = {"green": 0, "yellow": 0, "orange": 0, "red": 0}
    for item in evaluations:
        if item.is_valid and item.confidence_score is not None:
            if item.confidence_score >= 0.8:
                confidence_counts["high"] += 1
            elif item.confidence_score >= 0.55:
                confidence_counts["medium"] += 1
            else:
                confidence_counts["low"] += 1

        if item.is_valid:
            level = (item.level or "").lower()
            if level in level_counts:
                level_counts[level] += 1

    return {
        "count_total": count_total,
        "count_valid": count_valid,
        "count_invalid": count_invalid,
        "invalid_reason_counts": invalid_reason_counts,
        "mean_time_seconds_valid": round(mean_time_seconds_valid, 2),
        "confidence_counts": confidence_counts,
        "level_counts": level_counts,
    }


def build_safety_metrics(user_id: int, db: Session, start_date: date, include_low_quality: bool) -> dict:
    evaluations = (
        db.query(RapidEvaluation)
        .filter(
            RapidEvaluation.user_id == user_id,
            RapidEvaluation.entry_date.isnot(None),
            RapidEvaluation.entry_date >= start_date,
            RapidEvaluation.submitted_at.isnot(None),
            RapidEvaluation.is_low_quality.is_(False) if not include_low_quality else True,
        )
        .order_by(RapidEvaluation.entry_date.asc())
        .all()
    )
    red_trigger_count = sum(1 for item in evaluations if (item.level or "").upper() == "RED")
    red_low_confidence_count = sum(
        1
        for item in evaluations
        if (item.level or "").upper() == "RED"
        and item.confidence_score is not None
        and item.confidence_score < 0.55
    )
    escalation_shown_count = red_trigger_count

    return {
        "red_trigger_count": red_trigger_count,
        "red_low_confidence_count": red_low_confidence_count,
        "escalation_shown_count": escalation_shown_count,
    }


def compute_trend_slope(scores_by_day: dict[date, int], lookback_days: int) -> float:
    if not scores_by_day:
        return 0.0
    days_sorted = sorted(scores_by_day.keys())[-lookback_days:]
    if len(days_sorted) < 2:
        return 0.0
    y_values = [scores_by_day[day] for day in days_sorted]
    x_values = list(range(len(y_values)))
    x_mean = statistics.mean(x_values)
    y_mean = statistics.mean(y_values)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    if denominator == 0:
        return 0.0
    return numerator / denominator


def local_today() -> date:
    return datetime.now().date()


def build_rotation_seed(user_id: int, target_date: date, kind: str) -> int:
    seed_material = f"{user_id}:{target_date.isoformat()}:{kind}:{ROTATION_SALT}"
    digest = sha256(seed_material.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def select_questions_with_seed(
    questions: List[dict],
    missing_categories: set[str],
    recent_question_ids: set[int],
    exclude_ids: set[int],
    count: int,
    seed: int,
) -> List[dict]:
    if count <= 0:
        return []
    candidates = [q for q in questions if q["id"] not in exclude_ids]
    if not candidates:
        return []
    fresh = [q for q in candidates if q["id"] not in recent_question_ids]
    ordered_candidates = fresh if len(fresh) >= count else fresh + [q for q in candidates if q["id"] in recent_question_ids]
    missing = [q for q in ordered_candidates if q.get("category") in missing_categories]
    others = [q for q in ordered_candidates if q.get("category") not in missing_categories]
    rng = random.Random(seed)
    rng.shuffle(missing)
    rng.shuffle(others)
    selected = missing[:count]
    if len(selected) < count:
        selected.extend(others[: count - len(selected)])
    return selected


def build_daily_question_sets(db: Session) -> tuple[List[dict], List[dict]]:
    daily_questions = (
        db.query(Question)
        .filter(Question.kind == "daily")
        .all()
    )
    by_slug = {q.slug: q for q in daily_questions}
    core = []
    for item in DAILY_CORE:
        question = by_slug.get(item["slug"])
        if question:
            core.append({
                "id": question.id,
                "slug": question.slug,
                "text": question.text,
                "category": item["category"],
                "kind": "daily",
            })
    rotating = []
    for item in DAILY_ROTATING_POOL:
        question = by_slug.get(item["slug"])
        if question:
            rotating.append({
                "id": question.id,
                "slug": question.slug,
                "text": question.text,
                "category": item["category"],
                "kind": "daily",
            })
    return core, rotating


def build_daily_category_map(db: Session) -> dict[int, str]:
    core, rotating = build_daily_question_sets(db)
    return {item["id"]: item["category"] for item in core + rotating}


def build_micro_question_set(db: Session) -> List[dict]:
    pool_by_prompt = {item["prompt"]: item for item in MICRO_POOL}
    questions = (
        db.query(MicroQuestion)
        .filter(MicroQuestion.is_active.is_(True))
        .all()
    )
    selected = []
    for question in questions:
        meta = pool_by_prompt.get(question.prompt)
        if not meta:
            continue
        options = json.loads(question.options_json)
        selected.append({
            "id": question.id,
            "text": question.prompt,
            "category": meta["category"],
            "kind": "micro",
            "question_type": question.question_type,
            "options": options,
        })
    return selected


def collect_recent_question_ids(
    user_id: int,
    kind: str,
    start_date: date,
    db: Session,
) -> set[int]:
    if kind == "micro":
        rows = (
            db.query(MicroAnswer.question_id)
            .filter(
                MicroAnswer.user_id == user_id,
                MicroAnswer.entry_date >= start_date,
                MicroAnswer.is_low_quality.is_(False),
            )
            .distinct()
            .all()
        )
        return {row[0] for row in rows}
    rows = (
        db.query(Answer.question_id)
        .join(Question, Answer.question_id == Question.id)
        .filter(
            Answer.user_id == user_id,
            Answer.entry_date >= start_date,
            Answer.is_low_quality.is_(False),
            Question.kind == "daily",
        )
        .distinct()
        .all()
    )
    return {row[0] for row in rows}


def collect_recent_categories(
    user_id: int,
    kind: str,
    start_date: date,
    question_category_map: dict[int, str],
    db: Session,
) -> set[str]:
    categories: set[str] = set()
    if kind == "micro":
        answers = (
            db.query(MicroAnswer)
            .filter(
                MicroAnswer.user_id == user_id,
                MicroAnswer.entry_date >= start_date,
                MicroAnswer.is_low_quality.is_(False),
            )
            .all()
        )
        for answer in answers:
            category = answer.category or question_category_map.get(answer.question_id)
            if category:
                categories.add(category)
        return categories

    answers = (
        db.query(Answer)
        .join(Question, Answer.question_id == Question.id)
        .filter(
            Answer.user_id == user_id,
            Answer.entry_date >= start_date,
            Answer.is_low_quality.is_(False),
            Question.kind == "daily",
        )
        .all()
    )
    for answer in answers:
        category = answer.category or question_category_map.get(answer.question_id)
        if category:
            categories.add(category)
    return categories


def select_next_questions(
    user_id: int,
    kind: str,
    target_date: date,
    db: Session,
) -> List[dict]:
    start_date = target_date - timedelta(days=6)
    seed = build_rotation_seed(user_id, target_date, kind)

    if kind == "micro":
        pool = build_micro_question_set(db)
        category_map = {item["id"]: item["category"] for item in pool}
        answered_today = {
            row[0]
            for row in db.query(MicroAnswer.question_id)
            .filter(
                MicroAnswer.user_id == user_id,
                MicroAnswer.entry_date == target_date,
            )
            .distinct()
            .all()
        }
        recent_question_ids = collect_recent_question_ids(user_id, kind, start_date, db)
        recent_categories = collect_recent_categories(user_id, kind, start_date, category_map, db)
        missing_categories = {item["category"] for item in pool} - recent_categories
        selected = select_questions_with_seed(
            pool,
            missing_categories,
            recent_question_ids,
            answered_today,
            count=2,
            seed=seed,
        )
        return selected

    core, rotating = build_daily_question_sets(db)
    category_map = {item["id"]: item["category"] for item in core + rotating}
    answered_today = {
        row[0]
        for row in db.query(Answer.question_id)
        .join(Question, Answer.question_id == Question.id)
        .filter(
            Answer.user_id == user_id,
            Answer.entry_date == target_date,
            Question.kind == "daily",
        )
        .distinct()
        .all()
    }
    recent_question_ids = collect_recent_question_ids(user_id, kind, start_date, db)
    recent_categories = collect_recent_categories(user_id, kind, start_date, category_map, db)
    missing_categories = {item["category"] for item in rotating} - recent_categories
    core_remaining = [item for item in core if item["id"] not in answered_today]
    rotating_selected = select_questions_with_seed(
        rotating,
        missing_categories,
        recent_question_ids,
        answered_today | {item["id"] for item in core},
        count=2,
        seed=seed,
    )
    return core_remaining + rotating_selected


def fetch_micro_dates(user_id: int, db: Session, include_low_quality: bool = False) -> List[date]:
    query = db.query(MicroAnswer.entry_date).filter(MicroAnswer.user_id == user_id)
    if not include_low_quality:
        query = query.filter(MicroAnswer.is_low_quality.is_(False))
    rows = query.distinct().all()
    return sorted({row[0] for row in rows if row[0]})


def compute_current_streak(dates: List[date], today: date) -> int:
    if not dates:
        return 0
    date_set = set(dates)
    streak = 0
    day = today
    while day in date_set:
        streak += 1
        day = day - timedelta(days=1)
    return streak


def compute_best_streak(dates: List[date]) -> int:
    if not dates:
        return 0
    best = 1
    current = 1
    for prev, curr in zip(dates, dates[1:]):
        if curr == prev + timedelta(days=1):
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best


def compute_streak_from_latest(dates: List[date]) -> int:
    if not dates:
        return 0
    date_set = set(dates)
    expected = max(date_set)
    streak = 0
    while expected in date_set:
        streak += 1
        expected = expected - timedelta(days=1)
    return streak


def build_micro_signal(user_id: int, db: Session, include_low_quality: bool = False) -> dict:
    today = date.today()
    start_date = today - timedelta(days=6)
    query = (
        db.query(MicroAnswer)
        .filter(
            MicroAnswer.user_id == user_id,
            MicroAnswer.entry_date >= start_date,
        )
    )
    if not include_low_quality:
        query = query.filter(MicroAnswer.is_low_quality.is_(False))
    count_last_7 = query.count()
    dates = fetch_micro_dates(user_id, db, include_low_quality=include_low_quality)
    streak_days = compute_current_streak(dates, today)
    confidence_bonus = 0.0
    if count_last_7 >= 5:
        confidence_bonus += 0.03
    if streak_days >= 3:
        confidence_bonus += 0.02
    confidence_bonus = min(0.05, confidence_bonus)
    return {
        "answered_last_7_days": count_last_7,
        "streak_days": streak_days,
        "confidence_bonus": round(confidence_bonus, 3),
    }


def apply_micro_confidence_bonus(confidence_score: float, micro_signal: dict) -> float:
    bonus = micro_signal.get("confidence_bonus", 0.0)
    if bonus <= 0:
        return confidence_score
    return min(0.95, confidence_score + bonus)


def build_action_plan(
    risk_level: str,
    confidence: str,
    baseline_deviation_z: Optional[float],
    micro_streak_days: int,
    answered_last_7_days: int,
    self_harm_flag: bool,
) -> dict:
    normalized = risk_level.strip().lower()
    if normalized in {"red", "high"}:
        tier = "red"
    elif normalized in {"orange"}:
        tier = "orange"
    elif normalized in {"yellow", "medium"}:
        tier = "yellow"
    else:
        tier = "green"

    next_15 = []
    next_24 = []
    resources = []
    safety_note = "Not a diagnosis. Use what fits, skip what doesn't."

    if confidence.lower() == "low":
        safety_note = "Not a diagnosis. This is only an estimate."

    if tier == "red":
        next_15.extend([
            {"title": "Pause and breathe slowly", "why": "Short pauses can lower immediate intensity.", "duration_min": 5},
            {"title": "Move to a safer space", "why": "Distance from triggers can reduce urges.", "duration_min": 5},
            {"title": "Contact someone you trust", "why": "Support helps you stay grounded.", "duration_min": 10},
        ])
        resources.extend([
            {"label": "Call or text 988 (US)", "type": "crisis", "note": "Immediate support if you feel unsafe."},
            {"label": "Local emergency services", "type": "crisis", "note": "Use local emergency services if in danger."},
        ])
    elif tier in {"orange", "yellow"}:
        next_15.extend([
            {"title": "2-minute grounding", "why": "Name 5 things you can see, 4 you can feel.", "duration_min": 5},
            {"title": "Short walk or stretch", "why": "Movement can reset stress response.", "duration_min": 10},
        ])
        next_24.extend([
            {"title": "Plan a small supportive task", "why": "A single doable step reduces overwhelm.", "timeframe": "today"},
            {"title": "Connect with a friend", "why": "Light connection can lower isolation.", "timeframe": "tonight"},
        ])
        resources.append({"label": "Self-care basics", "type": "selfcare", "note": "Hydrate, eat, and rest if possible."})
    else:
        next_15.extend([
            {"title": "Check in with your body", "why": "Notice tension and soften your shoulders.", "duration_min": 5},
            {"title": "Small positive action", "why": "Pick one kind thing for yourself.", "duration_min": 10},
        ])
        next_24.extend([
            {"title": "Protect sleep window", "why": "Consistent sleep supports mood.", "timeframe": "tonight"},
            {"title": "Keep one routine", "why": "Stability helps maintain momentum.", "timeframe": "tomorrow"},
        ])
        resources.append({"label": "Mood skills", "type": "education", "note": "Brief journaling or reflection can help."})

    if baseline_deviation_z is not None:
        if baseline_deviation_z >= 1:
            next_24.append({
                "title": "Reduce load slightly",
                "why": "You're above your usual range today.",
                "timeframe": "today",
            })
        elif baseline_deviation_z <= -1:
            next_24.append({
                "title": "Reinforce what's working",
                "why": "You're below your usual range; keep supports in place.",
                "timeframe": "today",
            })

    if answered_last_7_days >= 5:
        next_24.append({
            "title": "Keep your micro streak",
            "why": "Small daily check-ins build stability.",
            "timeframe": "tomorrow",
        })
    elif micro_streak_days == 0:
        next_24.append({
            "title": "Try a 10-second check-in",
            "why": "Short reflection helps spot patterns early.",
            "timeframe": "today",
        })

    if self_harm_flag and not any(item["type"] == "crisis" for item in resources):
        resources.extend([
            {"label": "Call or text 988 (US)", "type": "crisis", "note": "Immediate support if you feel unsafe."},
            {"label": "Local emergency services", "type": "crisis", "note": "Use local emergency services if in danger."},
        ])

    return {
        "next_15_min": next_15[:3],
        "next_24_hours": next_24[:3],
        "resources": resources[:3],
        "safety_note": safety_note,
    }


def assess_input_quality(text: str, recent_texts: List[str], short_window_count: int) -> dict:
    flags: List[str] = []
    cleaned = text.strip()
    lowered = cleaned.lower()
    tokens = re.findall(r"\b\w+\b", lowered)
    word_count = len(tokens)

    if len(cleaned) < 30:
        flags.append("too_short")
    if word_count < 5:
        flags.append("low_word_count")
    if re.search(r"(.)\1{4,}", lowered):
        flags.append("repeated_characters")
    if re.search(r"[bcdfghjklmnpqrstvwxyz]{5,}", lowered):
        flags.append("keyboard_smash")
    if tokens:
        unique_ratio = len(set(tokens)) / len(tokens)
        if unique_ratio < 0.5:
            flags.append("repeated_tokens")
    profanity = {"fuck", "shit", "bitch", "asshole", "damn", "cunt"}
    if tokens and all(token in profanity for token in tokens):
        flags.append("profanity_only")
    normalized_recent = [item.strip().lower() for item in recent_texts]
    if normalized_recent and lowered in normalized_recent:
        flags.append("duplicate_recent")
    if short_window_count >= 4:
        flags.append("rapid_submissions")

    score = 100
    deductions = {
        "too_short": 15,
        "low_word_count": 15,
        "repeated_characters": 10,
        "repeated_tokens": 10,
        "keyboard_smash": 10,
        "profanity_only": 20,
        "duplicate_recent": 25,
        "rapid_submissions": 10,
    }
    for flag in flags:
        score -= deductions.get(flag, 0)
    score = max(0, min(100, score))
    is_low_quality = score < 60
    reason_summary = summarize_quality_flags(flags)
    return {
        "quality_score": score,
        "flags": flags,
        "is_low_quality": is_low_quality,
        "reason_summary": reason_summary,
    }


def assess_structured_quality(answers: List[str], recent_texts: List[str], short_window_count: int) -> dict:
    flags: List[str] = []
    cleaned_answers = [answer.strip() for answer in answers if answer is not None]
    combined = " | ".join(cleaned_answers).strip()
    lowered = combined.lower()
    tokens = re.findall(r"\b\w+\b", lowered)

    if not combined:
        flags.append("too_short")
    else:
        is_numeric_only = all(item.isdigit() for item in cleaned_answers if item)
        if len(combined) < 4 and not is_numeric_only:
            flags.append("too_short")
        if len(tokens) < 2 and not is_numeric_only:
            flags.append("low_word_count")

    if len(cleaned_answers) >= 2:
        unique_answers = {item.lower() for item in cleaned_answers if item}
        if len(unique_answers) == 1 and len(cleaned_answers[0]) >= 4:
            flags.append("repeated_across_fields")

    if re.search(r"(.)\1{4,}", lowered):
        flags.append("repeated_characters")
    if re.search(r"[bcdfghjklmnpqrstvwxyz]{5,}", lowered):
        flags.append("keyboard_smash")
    if tokens:
        unique_ratio = len(set(tokens)) / len(tokens)
        if unique_ratio < 0.4:
            flags.append("repeated_tokens")

    profanity = {"fuck", "shit", "bitch", "asshole", "damn", "cunt"}
    if tokens and all(token in profanity for token in tokens):
        flags.append("profanity_only")

    normalized_recent = [item.strip().lower() for item in recent_texts]
    if normalized_recent and lowered in normalized_recent:
        flags.append("duplicate_recent")
    if short_window_count >= 4:
        flags.append("rapid_submissions")

    score = 100
    deductions = {
        "too_short": 15,
        "low_word_count": 15,
        "repeated_characters": 10,
        "repeated_tokens": 10,
        "keyboard_smash": 10,
        "profanity_only": 20,
        "duplicate_recent": 25,
        "rapid_submissions": 10,
        "repeated_across_fields": 10,
    }
    for flag in flags:
        score -= deductions.get(flag, 0)
    score = max(0, min(100, score))
    is_low_quality = score < 60
    reason_summary = summarize_quality_flags(flags)
    return {
        "quality_score": score,
        "flags": flags,
        "is_low_quality": is_low_quality,
        "reason_summary": reason_summary,
    }


def summarize_quality_flags(flags: List[str]) -> str:
    mapping = {
        "too_short": "Too short",
        "low_word_count": "Not enough words",
        "repeated_characters": "Repeated characters",
        "repeated_tokens": "Repetitive wording",
        "keyboard_smash": "Looks like keyboard mash",
        "profanity_only": "Profanity only",
        "duplicate_recent": "Same as a recent entry",
        "rapid_submissions": "Many submissions in a short time",
        "repeated_across_fields": "Same answer across fields",
    }
    if not flags:
        return "Looks good."
    summary = [mapping.get(flag, flag) for flag in flags[:3]]
    return "; ".join(summary)


def calculate_retry_after(oldest_created_at: Optional[datetime], now: Optional[datetime] = None) -> int:
    if not oldest_created_at:
        return 3600
    now = now or datetime.utcnow()
    remaining = 3600 - (now - oldest_created_at).total_seconds()
    return max(60, int(remaining))


def parse_datetime_safe(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        parsed = parsed.astimezone(tz=None).replace(tzinfo=None)
    return parsed


def update_user_baseline(user_id: int, db: Session, lookback_days: int = 30) -> Optional[UserBaseline]:
    start_date = date.today() - timedelta(days=lookback_days - 1)

    answers = (
        db.query(Answer, Question)
        .join(Question, Answer.question_id == Question.id)
        .filter(
            Answer.user_id == user_id,
            Question.kind == "daily",
            Answer.entry_date.isnot(None),
            Answer.entry_date >= start_date,
        )
        .order_by(Answer.entry_date.asc(), Answer.created_at.desc())
        .all()
    )
    journals = (
        db.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.entry_date.isnot(None),
            JournalEntry.entry_date >= start_date,
        )
        .order_by(JournalEntry.entry_date.asc(), JournalEntry.created_at.desc())
        .all()
    )

    answers_by_date: dict[date, List[tuple[Answer, Question]]] = {}
    for answer, question in answers:
        answers_by_date.setdefault(answer.entry_date, []).append((answer, question))

    journals_by_date: dict[date, JournalEntry] = {}
    for entry in journals:
        if entry.entry_date not in journals_by_date:
            journals_by_date[entry.entry_date] = entry

    daily_scores = []
    for day in sorted(set(answers_by_date.keys()) | set(journals_by_date.keys())):
        _, score, _, _ = compute_risk_details(
            answers_by_date.get(day, []),
            journals_by_date.get(day),
        )
        daily_scores.append(score)

    rapid_scores = [
        item.score
        for item in db.query(RapidEvaluation)
        .filter(
            RapidEvaluation.user_id == user_id,
            RapidEvaluation.entry_date.isnot(None),
            RapidEvaluation.entry_date >= start_date,
            RapidEvaluation.submitted_at.isnot(None),
            RapidEvaluation.is_valid.is_(True),
            RapidEvaluation.is_low_quality.is_(False),
        )
        .all()
    ]

    score_samples = daily_scores + rapid_scores
    sample_count = len(score_samples)

    baseline = db.query(UserBaseline).filter(UserBaseline.user_id == user_id).first()
    if not baseline:
        baseline = UserBaseline(user_id=user_id, sample_count=0)
        db.add(baseline)

    if sample_count == 0:
        baseline.sample_count = 0
        baseline.last_updated_at = datetime.utcnow()
        db.commit()
        return baseline

    baseline.baseline_score_mean = round(statistics.mean(score_samples), 4)
    baseline.baseline_score_std = round(statistics.pstdev(score_samples), 4) if sample_count >= 2 else 0.0
    baseline.sample_count = sample_count

    rapid_valid = (
        db.query(RapidEvaluation)
        .filter(
            RapidEvaluation.user_id == user_id,
            RapidEvaluation.entry_date.isnot(None),
            RapidEvaluation.entry_date >= start_date,
            RapidEvaluation.submitted_at.isnot(None),
            RapidEvaluation.is_valid.is_(True),
            RapidEvaluation.is_low_quality.is_(False),
        )
        .all()
    )
    response_times = [
        item.time_taken_seconds
        for item in rapid_valid
        if item.time_taken_seconds is not None
    ]
    confidences = [
        item.confidence_score
        for item in rapid_valid
        if item.confidence_score is not None
    ]

    if response_times:
        baseline.baseline_response_time_mean = round(statistics.mean(response_times), 2)
        baseline.baseline_response_time_std = round(
            statistics.pstdev(response_times), 2
        ) if len(response_times) >= 2 else 0.0
    else:
        baseline.baseline_response_time_mean = None
        baseline.baseline_response_time_std = None

    if confidences:
        baseline.baseline_confidence_mean = round(statistics.mean(confidences), 4)
        baseline.baseline_confidence_std = round(
            statistics.pstdev(confidences), 4
        ) if len(confidences) >= 2 else 0.0
    else:
        baseline.baseline_confidence_mean = None
        baseline.baseline_confidence_std = None

    baseline.last_updated_at = datetime.utcnow()
    db.commit()
    return baseline
