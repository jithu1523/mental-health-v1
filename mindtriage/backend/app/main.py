from __future__ import annotations

import random
from datetime import date, datetime, timedelta
import json
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, create_engine, func, text
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

    user = relationship("User", back_populates="journal_entries")


class RapidEvaluation(Base):
    __tablename__ = "rapid_evaluations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    entry_date = Column(Date, default=date.today, nullable=True)
    answers_json = Column(String, nullable=False)
    score = Column(Integer, nullable=False)
    level = Column(String, nullable=False)
    signals_json = Column(String, nullable=False)


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
    answers: List[RapidAnswer]


class RapidSubmitResponse(BaseModel):
    level: str
    score: int
    signals: List[str]
    recommended_actions: List[str]
    crisis_guidance: Optional[List[str]] = None
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
]


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_entry_date_columns()
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
        journal_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(journal_entries)"))}
        if "entry_date" not in journal_columns:
            connection.execute(text("ALTER TABLE journal_entries ADD COLUMN entry_date DATE"))
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
    return {"status": "ok"}


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


@app.post("/rapid/submit", response_model=RapidSubmitResponse)
def rapid_submit(
    payload: RapidSubmitRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RapidSubmitResponse:
    if not payload.answers:
        raise HTTPException(status_code=400, detail="No answers provided")

    question_lookup = {q["id"]: q for q in RAPID_QUESTIONS}
    answers_by_slug: dict[str, str] = {}
    for answer in payload.answers:
        question = question_lookup.get(answer.question_id)
        if not question:
            raise HTTPException(status_code=400, detail=f"Unknown question ID: {answer.question_id}")
        answers_by_slug[question["slug"]] = answer.answer_text.strip()

    level, score, signals, actions, crisis = compute_rapid_risk(answers_by_slug)
    entry_date = payload.entry_date or date.today()

    evaluation = RapidEvaluation(
        user_id=user.id,
        entry_date=entry_date,
        answers_json=json.dumps(answers_by_slug),
        score=score,
        level=level,
        signals_json=json.dumps(signals),
    )
    db.add(evaluation)
    db.commit()

    return RapidSubmitResponse(
        level=level,
        score=score,
        signals=signals,
        recommended_actions=actions,
        crisis_guidance=crisis,
        entry_date=entry_date.isoformat(),
    )


@app.get("/rapid/history", response_model=List[RiskHistoryEntry])
def rapid_history(
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[RiskHistoryEntry]:
    start_date = date.today() - timedelta(days=days - 1)
    evaluations = (
        db.query(RapidEvaluation)
        .filter(
            RapidEvaluation.user_id == user.id,
            RapidEvaluation.entry_date.isnot(None),
            RapidEvaluation.entry_date >= start_date,
        )
        .order_by(RapidEvaluation.entry_date.asc(), RapidEvaluation.created_at.desc())
        .all()
    )

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


def compute_rapid_risk(
    answers_by_slug: dict[str, str]
) -> tuple[str, int, List[str], List[str], Optional[List[str]]]:
    score = 0
    signals: List[str] = []

    mood_value = parse_numeric(answers_by_slug.get("rapid_mood", ""))
    if mood_value is not None and mood_value <= 3:
        score += 3
        signals.append("Low mood rating")

    anxiety_value = parse_numeric(answers_by_slug.get("rapid_anxiety", ""))
    if anxiety_value is not None and anxiety_value >= 8:
        score += 3
        signals.append("High anxiety rating")

    if is_yes(answers_by_slug.get("rapid_hopeless", "")):
        score += 4
        signals.append("Reported hopelessness")

    if is_yes(answers_by_slug.get("rapid_isolation", "")):
        score += 2
        signals.append("Reported isolation")

    if is_choice(answers_by_slug.get("rapid_sleep", ""), "Poor"):
        score += 1
        signals.append("Poor sleep")

    if is_choice(answers_by_slug.get("rapid_appetite", ""), "Poor"):
        score += 1
        signals.append("Low appetite")

    if is_yes(answers_by_slug.get("rapid_support", "")) is False:
        score += 1
        signals.append("Limited support right now")

    if is_yes(answers_by_slug.get("rapid_substance", "")):
        score += 1
        signals.append("Substance use today")

    self_harm_thoughts = is_yes(answers_by_slug.get("rapid_self_harm_thoughts", ""))
    self_harm_plan = is_yes(answers_by_slug.get("rapid_self_harm_plan", ""))
    if self_harm_thoughts:
        score += 6
        signals.append("Self-harm thoughts")

    crisis_guidance = None
    if self_harm_plan:
        level = "RED"
        score = max(score, 18)
        signals.append("Self-harm plan or intent")
        crisis_guidance = crisis_resources()
    elif score >= 12:
        level = "RED"
        crisis_guidance = crisis_resources()
    elif score >= 6:
        level = "YELLOW"
    else:
        level = "GREEN"

    actions = recommended_actions(level)
    return level, score, list(dict.fromkeys(signals)), actions, crisis_guidance


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
