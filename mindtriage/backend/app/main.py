from __future__ import annotations

import random
from datetime import date, datetime, timedelta
import json
import os
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, create_engine, func, text, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship, sessionmaker

DATABASE_URL = "sqlite:///./mindtriage.db"
SECRET_KEY = "CHANGE_ME"
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


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    entry_date = Column(Date, default=date.today, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)

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
    answer_text = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    entry_date = Column(Date, default=date.today, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)

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


class AnswerCreate(BaseModel):
    question_id: int
    answer_text: str
    entry_date: Optional[date] = None


class AnswerBatch(BaseModel):
    answers: List[AnswerCreate]


class JournalCreate(BaseModel):
    content: str
    entry_date: Optional[date] = None


class JournalResponse(BaseModel):
    id: int
    content: str
    created_at: datetime


class RiskResponse(BaseModel):
    risk_level: str
    score: int
    reasons: List[str]
    last_journal_excerpt: Optional[str]


class RiskHistoryEntry(BaseModel):
    date: str
    score: int
    level: str


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
    confidence_score: float
    explanations: List[RapidExplainabilityItem]
    is_valid: bool
    quality_flags: List[str]
    time_taken_seconds: float
    entry_date: str


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

DAILY_QUESTIONS = [
    {"kind": "daily", "slug": "daily_mood", "text": "Rate your mood today (1-10)."},
    {"kind": "daily", "slug": "daily_anxiety", "text": "Rate your anxiety today (1-10)."},
    {"kind": "daily", "slug": "daily_sleep", "text": "How was your sleep last night?"},
    {"kind": "daily", "slug": "daily_energy", "text": "How is your energy level today?"},
    {"kind": "daily", "slug": "daily_stress", "text": "How stressed do you feel today?"},
    {"kind": "daily", "slug": "daily_focus", "text": "How is your focus today?"},
    {"kind": "daily", "slug": "daily_isolation", "text": "Do you feel isolated today?"},
    {"kind": "daily", "slug": "daily_hopeless", "text": "Have you felt hopeless today?"},
]

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
    seed_questions()


def seed_questions() -> None:
    session = SessionLocal()
    try:
        existing = {q.slug for q in session.query(Question).all()}
        to_add = []
        for item in ONBOARDING_QUESTIONS + DAILY_QUESTIONS:
            if item["slug"] not in existing:
                to_add.append(Question(**item))
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
    return {"status": "ok", "dev_mode": is_dev_mode()}


def is_dev_mode() -> bool:
    value = os.getenv("MINDTRIAGE_DEV_MODE", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


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
    return {"complete": len(missing_ids) == 0, "missing_question_ids": missing_ids}


@app.get("/daily/pick", response_model=List[QuestionResponse])
def daily_pick(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> List[QuestionResponse]:
    daily_questions = db.query(Question).filter(Question.kind == "daily").all()
    questions_by_slug = {q.slug: q for q in daily_questions}
    if len(daily_questions) < 3:
        raise HTTPException(status_code=500, detail="Daily question set incomplete")

    bad_recent = is_recent_mood_or_anxiety_low(user.id, db)

    chosen = []
    if bad_recent and "daily_hopeless" in questions_by_slug:
        chosen.append(questions_by_slug["daily_hopeless"])

    remaining = [q for q in daily_questions if q not in chosen]
    chosen.extend(random.sample(remaining, k=3 - len(chosen)))

    return [QuestionResponse(id=q.id, kind=q.kind, slug=q.slug, text=q.text) for q in chosen]


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
    if not payload.answers:
        raise HTTPException(status_code=400, detail="No answers provided")

    question_ids = [item.question_id for item in payload.answers]
    existing_questions = {
        q.id for q in db.query(Question).filter(Question.id.in_(question_ids)).all()
    }
    missing = [qid for qid in question_ids if qid not in existing_questions]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown question IDs: {missing}")

    created = []
    for item in payload.answers:
        entry_date = item.entry_date or date.today()
        created.append(Answer(
            user_id=user.id,
            question_id=item.question_id,
            answer_text=item.answer_text.strip(),
            entry_date=entry_date,
        ))
    db.add_all(created)
    db.commit()
    return {"saved": len(created)}


@app.post("/journal", response_model=JournalResponse)
def create_journal_entry(
    payload: JournalCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> JournalResponse:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Journal content cannot be empty")
    entry_date = payload.entry_date or date.today()
    entry = JournalEntry(user_id=user.id, content=content, entry_date=entry_date)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return JournalResponse(id=entry.id, content=entry.content, created_at=entry.created_at)


@app.get("/journal", response_model=List[JournalResponse])
def list_journal_entries(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> List[JournalResponse]:
    entries = (
        db.query(JournalEntry)
        .filter(JournalEntry.user_id == user.id)
        .order_by(JournalEntry.created_at.desc())
        .limit(20)
        .all()
    )
    return [JournalResponse(id=e.id, content=e.content, created_at=e.created_at) for e in entries]


@app.get("/risk/latest", response_model=RiskResponse)
def risk_latest(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> RiskResponse:
    answers = (
        db.query(Answer, Question)
        .join(Question, Answer.question_id == Question.id)
        .filter(Answer.user_id == user.id, Question.kind == "daily")
        .order_by(Answer.created_at.desc())
        .limit(10)
        .all()
    )

    last_journal = (
        db.query(JournalEntry)
        .filter(JournalEntry.user_id == user.id)
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
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[RiskHistoryEntry]:
    start_date = date.today() - timedelta(days=days - 1)

    answers = (
        db.query(Answer, Question)
        .join(Question, Answer.question_id == Question.id)
        .filter(
            Answer.user_id == user.id,
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
            JournalEntry.user_id == user.id,
            JournalEntry.entry_date.isnot(None),
            JournalEntry.entry_date >= start_date,
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
    entry_date = payload.entry_date or date.today()
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
    now = datetime.utcnow()
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
    entry_date = payload.entry_date or (active_session.entry_date if active_session else date.today())
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

    soft_flags: List[str] = []
    if detect_patterned_answers(answers_by_slug):
        soft_flags.append("patterned_answers")
    if detect_extreme_only_answers(answers_by_slug):
        soft_flags.append("extreme_only_answers")

    quality_flags = list(dict.fromkeys(invalid_flags + soft_flags))
    is_valid = len(invalid_flags) == 0
    confidence_score = compute_rapid_confidence_score(time_taken_seconds, quality_flags)

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
        active_session.explainability_json = json.dumps([item.model_dump() for item in top_explanations])
        active_session.time_taken_seconds = time_taken_seconds
        active_session.is_valid = is_valid
        active_session.quality_flags_json = json.dumps(quality_flags)
    else:
        evaluation = RapidEvaluation(
            user_id=user.id,
            entry_date=entry_date,
            started_at=started_at,
            submitted_at=submitted_at,
            answers_json=answers_payload,
            score=score,
            level=level,
            signals_json=json.dumps(signals),
            confidence_score=confidence_score,
            explainability_json=json.dumps([item.model_dump() for item in top_explanations]),
            time_taken_seconds=time_taken_seconds,
            is_valid=is_valid,
            quality_flags_json=json.dumps(quality_flags),
        )
        db.add(evaluation)
    db.commit()

    return RapidSubmitResponse(
        level=level,
        score=score,
        signals=signals,
        recommended_actions=actions,
        crisis_guidance=crisis,
        confidence_score=confidence_score,
        explanations=top_explanations,
        is_valid=is_valid,
        quality_flags=quality_flags,
        time_taken_seconds=time_taken_seconds,
        entry_date=entry_date.isoformat(),
    )


@app.get("/rapid/history", response_model=List[RiskHistoryEntry])
def rapid_history(
    days: int = Query(30, ge=1, le=365),
    include_invalid: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[RiskHistoryEntry]:
    start_date = date.today() - timedelta(days=days - 1)
    query = (
        db.query(RapidEvaluation)
        .filter(
            RapidEvaluation.user_id == user.id,
            RapidEvaluation.entry_date.isnot(None),
            RapidEvaluation.entry_date >= start_date,
            RapidEvaluation.submitted_at.isnot(None),
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
