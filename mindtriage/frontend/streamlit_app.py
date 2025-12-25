from datetime import date, datetime
import os
import time

import altair as alt
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="MindTriage", page_icon="??", layout="centered")

API_BASE = st.text_input("API base URL", value="http://127.0.0.1:8000")

if "token" not in st.session_state:
    st.session_state.token = None
if "dev_mode" not in st.session_state:
    st.session_state.dev_mode = False
if "backend_dev_mode" not in st.session_state:
    st.session_state.backend_dev_mode = False
if "daily_questions" not in st.session_state:
    st.session_state.daily_questions = None
if "rapid_session_id" not in st.session_state:
    st.session_state.rapid_session_id = None
if "rapid_session_date" not in st.session_state:
    st.session_state.rapid_session_date = None
if "export_bytes" not in st.session_state:
    st.session_state.export_bytes = None
if "action_plan_rapid" not in st.session_state:
    st.session_state.action_plan_rapid = None
if "action_plan_regular" not in st.session_state:
    st.session_state.action_plan_regular = None
if "micro_signal" not in st.session_state:
    st.session_state.micro_signal = None
if "micro_streak_days" not in st.session_state:
    st.session_state.micro_streak_days = 0
if "micro_answered_last_7" not in st.session_state:
    st.session_state.micro_answered_last_7 = 0
if "baseline_insights" not in st.session_state:
    st.session_state.baseline_insights = None
if "eval_daily" not in st.session_state:
    st.session_state.eval_daily = None
if "eval_daily_followups" not in st.session_state:
    st.session_state.eval_daily_followups = []
if "eval_daily_session_id" not in st.session_state:
    st.session_state.eval_daily_session_id = None
if "eval_rapid" not in st.session_state:
    st.session_state.eval_rapid = None
if "eval_rapid_followups" not in st.session_state:
    st.session_state.eval_rapid_followups = []
if "eval_rapid_session_id" not in st.session_state:
    st.session_state.eval_rapid_session_id = None
if "rapid_started_ts" not in st.session_state:
    st.session_state.rapid_started_ts = None
if "include_low_quality" not in st.session_state:
    st.session_state.include_low_quality = False


def api_headers() -> dict:
    if st.session_state.token:
        return {"Authorization": f"Bearer {st.session_state.token}"}
    return {}


def api_url(path: str) -> str:
    return f"{API_BASE}{path}"


def safe_json(resp: requests.Response):
    content_type = resp.headers.get("content-type", "")
    if "application/json" not in content_type:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def show_response_error(resp: requests.Response, path: str, fallback_message: str) -> None:
    url = api_url(path)
    payload = safe_json(resp)
    if payload and isinstance(payload, dict):
        detail = payload.get("detail", fallback_message)
        st.error(f"{fallback_message} ({resp.status_code}) | {url} | {detail}")
        return
    text = (resp.text or "").strip()
    snippet = text[:500] if text else "No response body."
    st.error(f"{fallback_message} ({resp.status_code}) | {url} | {snippet}")


def api_get(path: str):
    try:
        return requests.get(api_url(path), headers=api_headers(), timeout=10)
    except requests.RequestException as exc:
        st.error(f"Request failed: {exc}")
        return None


def api_post(path: str, json=None, data=None):
    try:
        return requests.post(
            api_url(path),
            headers=api_headers(),
            json=json,
            data=data,
            timeout=10,
        )
    except requests.RequestException as exc:
        st.error(f"Request failed: {exc}")
        return None


st.title("MindTriage")
st.caption("Not a diagnosis. If you feel unsafe contact local emergency services.")

home_tab_label = "Home"
micro_tab_label = "Quick Check-in"
daily_tab_label = "Daily Check-in"
journal_tab_label = "Journal"
insights_tab_label = "Insights"
export_tab_label = "Export"
home_tab, micro_tab, daily_tab, journal_tab, insights_tab, export_tab = st.tabs(
    [
        home_tab_label,
        micro_tab_label,
        daily_tab_label,
        journal_tab_label,
        insights_tab_label,
        export_tab_label,
    ]
)

st.subheader("Backend connection check")
health_resp = api_get("/health")
if health_resp is None:
    st.error("Backend check failed. Start backend with: uvicorn app.main:app --reload --port 8000")
elif health_resp.ok:
    message = f"Backend healthy ({health_resp.status_code}) | {api_url('/health')}"
    st.success(message)
else:
    snippet = (health_resp.text or "").strip()
    st.error(
        f"Backend unhealthy ({health_resp.status_code}) | {api_url('/health')} | "
        f"{snippet[:500] if snippet else 'No response body.'}"
    )
    st.error("Start backend with: uvicorn app.main:app --reload --port 8000")

meta_resp = api_get("/meta")
if meta_resp is not None and meta_resp.ok:
    meta_payload = safe_json(meta_resp) or {}
    st.session_state.backend_dev_mode = bool(meta_payload.get("dev_mode"))
else:
    st.session_state.backend_dev_mode = False

def get_query_param(key: str) -> str:
    try:
        params = st.query_params
    except Exception:
        params = st.experimental_get_query_params()
    value = params.get(key)
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""

dev_ui_enabled = False
local_dev_env = os.getenv("DEV_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
dev_requested = get_query_param("dev") == "1"
if st.session_state.backend_dev_mode and local_dev_env and dev_requested:
    dev_ui_enabled = True

if "ui_dev_mode" not in st.session_state:
    st.session_state.ui_dev_mode = False
if "show_quality_details" not in st.session_state:
    st.session_state.show_quality_details = False
if dev_ui_enabled:
    st.sidebar.subheader("Developer Mode")
    st.session_state.ui_dev_mode = st.sidebar.checkbox(
        "Enable developer controls",
        value=st.session_state.ui_dev_mode,
    )
    st.session_state.show_quality_details = st.sidebar.checkbox(
        "Show quality details",
        value=st.session_state.show_quality_details,
    )
    st.session_state.include_low_quality = st.sidebar.checkbox(
        "Include low-quality items in insights",
        value=st.session_state.include_low_quality,
    )
else:
    st.session_state.ui_dev_mode = False
    st.session_state.show_quality_details = False
    st.session_state.include_low_quality = False

with home_tab:
    st.subheader("Today")
    if not st.session_state.token:
        st.warning("Sign in to continue.")
    else:
        risk_resp = api_get("/risk/latest")
        risk_level = "unknown"
        if risk_resp is not None and risk_resp.ok:
            risk = safe_json(risk_resp) or {}
            risk_level = risk.get("risk_level", "unknown")
            st.metric("Current risk level", risk_level)
        elif risk_resp is not None:
            show_response_error(risk_resp, "/risk/latest", "Unable to load risk status.")

        history_params = "?days=30"
        if st.session_state.ui_dev_mode and st.session_state.include_low_quality:
            history_params += "&include_low_quality=true"
        history_resp = api_get(f"/risk/history{history_params}")
        last_checkin = "No check-ins yet."
        if history_resp is not None and history_resp.ok:
            history = safe_json(history_resp) or []
            if history:
                last_checkin = history[-1].get("date", last_checkin)
        elif history_resp is not None:
            show_response_error(history_resp, "/risk/history", "Unable to load check-in history.")
        st.write(f"Last check-in: {last_checkin}")

        streak_params = "?include_low_quality=true" if st.session_state.include_low_quality else ""
        streak_resp = api_get(f"/micro/streak{streak_params}")
        if streak_resp is not None and streak_resp.ok:
            streak = safe_json(streak_resp) or {}
            st.session_state.micro_streak_days = streak.get("streak_days", 0)
            st.write(f"Micro streak: {st.session_state.micro_streak_days} days")
        elif streak_resp is not None:
            show_response_error(streak_resp, "/micro/streak", "Unable to load micro streak.")

        st.subheader("Quick note")
        quick_note = st.text_area("What's one thing you want to note today?", height=100)
        if st.button("Save note to journal"):
            if not quick_note.strip():
                st.warning("Add a short note before saving.")
            else:
                resp = api_post("/journal", json={"content": quick_note})
                if resp is not None and resp.ok:
                    st.success("Note saved.")
                elif resp is not None:
                    show_response_error(resp, "/journal", "Unable to save note.")

    st.subheader("Account")
    if not st.session_state.token:
        st.subheader("Sign up")
        with st.form("register_form"):
            reg_email = st.text_input("Email", key="reg_email")
            reg_password = st.text_input("Password", type="password", key="reg_password")
            if st.form_submit_button("Create account"):
                if not reg_email or not reg_password:
                    st.warning("Enter an email and password.")
                else:
                    resp = api_post("/auth/register", json={"email": reg_email, "password": reg_password})
                    if resp is not None and resp.ok:
                        payload = safe_json(resp) or {}
                        token = payload.get("access_token")
                        st.session_state.token = token
                        st.success("Account created. You are signed in.")
                    elif resp is not None:
                        show_response_error(resp, "/auth/register", "Registration failed.")

        st.subheader("Login")
        with st.form("login_form"):
            login_email = st.text_input("Email", key="login_email")
            login_password = st.text_input("Password", type="password", key="login_password")
            if st.form_submit_button("Sign in"):
                if not login_email or not login_password:
                    st.warning("Enter your email and password.")
                else:
                    resp = api_post(
                        "/auth/login",
                        data={"username": login_email, "password": login_password},
                    )
                    if resp is not None and resp.ok:
                        payload = safe_json(resp) or {}
                        token = payload.get("access_token")
                        st.session_state.token = token
                        st.success("Signed in.")
                    elif resp is not None:
                        show_response_error(resp, "/auth/login", "Login failed.")
    else:
        st.info("Authenticated.")
        if st.button("Logout"):
            st.session_state.token = None
            st.success("Signed out.")
            st.stop()

        st.subheader("Onboarding")
        status_resp = api_get("/onboarding/status")
        if status_resp is None:
            st.stop()
        if not status_resp.ok:
            st.error(status_resp.json().get("detail", "Unable to load onboarding status."))
            st.stop()

        status = status_resp.json()
        missing_ids = status.get("missing_question_ids", [])

        if not status.get("complete"):
            questions_resp = api_get("/questions?kind=onboarding")
            if questions_resp is None:
                st.stop()
            if not questions_resp.ok:
                st.error(questions_resp.json().get("detail", "Unable to load questions."))
                st.stop()

            all_questions = {q["id"]: q for q in questions_resp.json()}
            missing_questions = [all_questions[qid] for qid in missing_ids if qid in all_questions]

            if not missing_questions:
                st.info("No pending onboarding questions.")
            else:
                with st.form("onboarding_form"):
                    answers = []
                    for question in missing_questions:
                        answer = st.text_input(question["text"], key=f"onb_{question['id']}")
                        answers.append({"question_id": question["id"], "answer_text": answer})
                    if st.form_submit_button("Save onboarding answers"):
                        payload = {"answers": answers}
                        resp = api_post("/answers", json=payload)
                        if resp is not None and resp.ok:
                            st.success("Onboarding saved.")
                            st.session_state.daily_questions = None
                        elif resp is not None:
                            st.error(resp.json().get("detail", "Unable to save answers."))
        else:
            st.success("Onboarding complete.")

        profile = status.get("profile", {})
        total_questions = profile.get("total_questions", 0)
        answered = profile.get("answered", 0)
        if total_questions:
            st.subheader("Mini-Profile (optional)")
            st.caption("Answer a few quick questions to personalize your baseline.")
            st.write(f"Progress: {answered}/{total_questions} completed")
            if answered < total_questions:
                profile_resp = api_get("/onboarding/questions")
                if profile_resp is not None and profile_resp.ok:
                    profile_questions = safe_json(profile_resp) or []
                    if profile_questions:
                        with st.form("profile_form"):
                            profile_answers = []
                            for question in profile_questions:
                                options = ["Skip for now"] + question.get("options", [])
                                choice = st.selectbox(question["question"], options, key=f"profile_{question['id']}")
                                selected = None if choice == "Skip for now" else choice
                                profile_answers.append({
                                    "question_id": question["id"],
                                    "selected_option": selected,
                                })
                            if st.form_submit_button("Save mini-profile answers"):
                                resp = api_post("/onboarding/answer", json={"answers": profile_answers})
                                if resp is not None and resp.ok:
                                    st.success("Mini-profile saved.")
                                elif resp is not None:
                                    show_response_error(resp, "/onboarding/answer", "Unable to save mini-profile.")
                    else:
                        st.info("You're all set for now.")
                elif profile_resp is not None:
                    show_response_error(profile_resp, "/onboarding/questions", "Unable to load mini-profile.")

with micro_tab:
    if not st.session_state.token:
        st.warning("Sign in on the Home tab to continue.")
    else:
        st.subheader("Quick check-in (10 seconds)")
        override_micro_date = None
        if st.session_state.ui_dev_mode:
            st.caption("Developer Mode: date/time overrides enabled.")
            if st.checkbox("Override date (Quick check-in)", key="override_micro_dt"):
                override_micro_date = st.date_input(
                    "Micro override date",
                    value=date.today(),
                    key="micro_override_date",
                )
        micro_target_date = override_micro_date or date.today()
        questions_path = "/questions/next?kind=micro"
        if override_micro_date:
            questions_path += f"&date={micro_target_date.isoformat()}"
        questions_resp = api_get(questions_path)
        questions = []
        questions_loaded = questions_resp is not None and questions_resp.ok
        if questions_loaded:
            payload = safe_json(questions_resp) or {}
            questions = payload.get("questions", [])
        elif questions_resp is not None:
            show_response_error(questions_resp, "/questions/next", "Unable to load quick check-in.")

        if not questions_loaded:
            st.info("Quick check-in is unavailable right now.")
        elif not questions:
            label = "Done for selected date" if override_micro_date else "Done for today"
            st.success(label)
        else:
            with st.form("micro_form"):
                micro_payloads = []
                for question in questions:
                    prompt = question.get("text", "Quick check-in")
                    question_type = (question.get("question_type") or "").lower()
                    options = question.get("options") or []
                    if question_type == "scale" and options:
                        default_index = len(options) // 2
                        answer_value = st.select_slider(
                            prompt,
                            options=options,
                            value=options[default_index],
                            key=f"micro_{question['id']}",
                        )
                    elif question_type == "choice" and options:
                        answer_value = st.selectbox(
                            prompt,
                            options=options,
                            key=f"micro_{question['id']}",
                        )
                    else:
                        answer_value = st.text_input(prompt, key=f"micro_{question['id']}")
                    answer_value = str(answer_value)
                    micro_payloads.append({
                        "question_id": question.get("id"),
                        "value": answer_value,
                    })
                if st.form_submit_button("Save micro answers"):
                    saved = 0
                    reason_summaries = []
                    for payload in micro_payloads:
                        if override_micro_date:
                            payload["override_entry_date"] = override_micro_date.isoformat()
                        resp = api_post("/micro/answers", json=payload)
                        if resp is not None and resp.ok:
                            saved += 1
                            resp_payload = safe_json(resp) or {}
                            if resp_payload.get("is_low_quality"):
                                reason_summaries.append(resp_payload.get("reason_summary"))
                        elif resp is not None:
                            show_response_error(resp, "/micro/answers", "Unable to save quick check-in.")
                            break
                    if saved == len(micro_payloads):
                        if reason_summaries:
                            reason_text = "; ".join(filter(None, reason_summaries)) or "Too brief."
                            st.warning(f"Low quality: {reason_text}. Try adding 1-2 sentences.")
                        else:
                            st.success("Quick check-in saved.")
                        refresh_resp = api_get(questions_path)
                        if refresh_resp is not None and refresh_resp.ok:
                            payload = safe_json(refresh_resp) or {}
                            questions = payload.get("questions", [])
                        if not questions:
                            label = "Done for selected date" if override_micro_date else "Done for today"
                            st.success(label)
                        streak_query = "?include_low_quality=true" if st.session_state.include_low_quality else ""
                        streak_resp = api_get(f"/micro/streak{streak_query}")
                        if streak_resp is not None and streak_resp.ok:
                            streak_payload = safe_json(streak_resp) or {}
                            st.session_state.micro_streak_days = streak_payload.get("streak_days", 0)

        streak_query = "?include_low_quality=true" if st.session_state.include_low_quality else ""
        streak_resp = api_get(f"/micro/streak{streak_query}")
        if streak_resp is not None and streak_resp.ok:
            streak = safe_json(streak_resp) or {}
            st.session_state.micro_streak_days = streak.get("streak_days", 0)
            st.write(f"Micro streak: {st.session_state.micro_streak_days} days")
        elif streak_resp is not None:
            show_response_error(streak_resp, "/micro/streak", "Unable to load micro streak.")

        history_query = "days=7"
        if st.session_state.ui_dev_mode and st.session_state.include_low_quality:
            history_query += "&include_low_quality=true"
        history_resp = api_get(f"/micro/history?{history_query}")
        if history_resp is not None and history_resp.ok:
            history = safe_json(history_resp) or []
            st.session_state.micro_answered_last_7 = len(history)
            if history:
                st.caption("Last 7 days")
                st.table(history)
            else:
                st.info("No quick check-ins yet.")
        elif history_resp is not None:
            show_response_error(history_resp, "/micro/history", "Unable to load quick check-in history.")

        if st.session_state.ui_dev_mode:
            debug_resp = api_get("/dev/debug/micro")
            if debug_resp is not None and debug_resp.ok:
                debug_data = safe_json(debug_resp) or {}
                st.caption("Dev debug")
                st.write(f"Total micro answers: {debug_data.get('count_micro_answers_total', 0)}")
                last_items = debug_data.get("last_5_micro_answers", [])
                if last_items:
                    st.write(f"Last entry date: {last_items[0].get('entry_date')}")
            elif debug_resp is not None:
                show_response_error(debug_resp, "/dev/debug/micro", "Unable to load micro debug data.")

        st.caption("Not a diagnosis. If you feel unsafe contact local emergency services.")
with daily_tab:
    if not st.session_state.token:
        st.warning("Sign in on the Home tab to continue.")
    else:
        status_resp = api_get("/onboarding/status")
        if status_resp is None:
            st.stop()
        if not status_resp.ok:
            st.error(status_resp.json().get("detail", "Unable to load onboarding status."))
            st.stop()

        status = status_resp.json()
        if not status.get("complete"):
            st.info("Complete onboarding in the Home tab to unlock check-ins.")
            st.stop()

        st.subheader("Daily check-in")
        selected_checkin_date = date.today()
        override_daily_dt = None
        if st.session_state.ui_dev_mode:
            st.caption("Developer Mode: date/time overrides enabled.")
            if st.checkbox("Override date/time (Daily check-in)", key="override_daily_dt"):
                override_date = st.date_input("Daily override date", value=date.today(), key="daily_override_date")
                override_time = st.time_input("Daily override time", value=datetime.now().time(), key="daily_override_time")
                override_daily_dt = datetime.combine(override_date, override_time)
                selected_checkin_date = override_date
        questions_path = "/questions/next?kind=daily"
        if override_daily_dt:
            questions_path += f"&date={selected_checkin_date.isoformat()}"
        if st.button("Refresh daily questions") or st.session_state.daily_questions is None:
            pick_resp = api_get(questions_path)
            if pick_resp is not None and pick_resp.ok:
                payload = safe_json(pick_resp) or {}
                st.session_state.daily_questions = payload.get("questions", [])
            elif pick_resp is not None:
                show_response_error(pick_resp, "/questions/next", "Unable to load daily questions.")

        daily_questions = st.session_state.daily_questions or []
        if st.session_state.daily_questions is None:
            st.info("Load daily questions to begin.")
        elif not daily_questions:
            label = "Done for selected date" if override_daily_dt else "Done for today"
            st.success(label)
        if daily_questions:
            with st.form("daily_form"):
                daily_answers = []
                daily_answer_map = {}
                for question in daily_questions:
                    slug = question.get("slug") or ""
                    category = (question.get("category") or "").lower()
                    if category in {"mood", "anxiety"}:
                        value = st.slider(question["text"], 0, 10, 5, key=f"daily_{question['id']}")
                        answer_text = str(value)
                    elif category == "sleep":
                        value = st.slider(question["text"], 0, 12, 7, key=f"daily_{question['id']}")
                        answer_text = str(value)
                    else:
                        answer_text = st.text_input(question["text"], key=f"daily_{question['id']}")
                    daily_answers.append({
                        "question_id": question["id"],
                        "answer_text": answer_text,
                        "entry_date": selected_checkin_date.isoformat(),
                    })
                    if slug:
                        daily_answer_map[slug] = answer_text
                if st.form_submit_button("Save daily answers"):
                    payload = {"answers": daily_answers}
                    if override_daily_dt:
                        payload["override_datetime"] = override_daily_dt.isoformat()
                    resp = api_post("/answers", json=payload)
                    if resp is not None and resp.ok:
                        payload = safe_json(resp) or {}
                        if payload.get("is_low_quality"):
                            st.warning(f"Low quality: {payload.get('reason_summary', '')}. Try adding 1-2 sentences.")
                            st.info("You can edit your answers and resubmit.")
                        else:
                            st.success("Daily check-in saved.")
                            refresh_resp = api_get(questions_path)
                            if refresh_resp is not None and refresh_resp.ok:
                                refreshed = safe_json(refresh_resp) or {}
                                st.session_state.daily_questions = refreshed.get("questions", [])
                            if not st.session_state.daily_questions:
                                label = "Done for selected date" if override_daily_dt else "Done for today"
                                st.success(label)
                        if st.session_state.show_quality_details:
                            st.caption(
                                f"Quality score: {payload.get('input_quality_score')} | "
                                f"Flags: {payload.get('input_quality_flags')}"
                            )
                        st.session_state.micro_signal = payload.get("micro_signal")
                        eval_payload = {
                            "daily_answers": daily_answer_map,
                        }
                        eval_resp = api_post("/evaluate", json=eval_payload)
                        if eval_resp is not None and eval_resp.ok:
                            eval_data = safe_json(eval_resp) or {}
                            st.session_state.eval_daily = eval_data
                            st.session_state.eval_daily_followups = eval_data.get("recommended_followups", [])
                            st.session_state.eval_daily_session_id = eval_data.get("session_id")
                        elif eval_resp is not None:
                            show_response_error(eval_resp, "/evaluate", "Unable to run evaluation.")
                    elif resp is not None:
                        st.error(resp.json().get("detail", "Unable to save daily answers."))

        if st.session_state.eval_daily_followups:
            st.subheader("Quick follow-up")
            with st.form("daily_followup_form"):
                followup_answers = {}
                for item in st.session_state.eval_daily_followups:
                    answer = st.text_input(item["prompt"], key=f"daily_followup_{item['key']}")
                    followup_answers[item["key"]] = answer
                if st.form_submit_button("Submit follow-up"):
                    resp = api_post(
                        "/evaluate/followup",
                        json={
                            "session_id": st.session_state.eval_daily_session_id,
                            "answers": followup_answers,
                        },
                    )
                    if resp is not None and resp.ok:
                        updated = safe_json(resp) or {}
                        st.session_state.eval_daily = updated
                        st.session_state.eval_daily_followups = []
                        st.success("Thanks, follow-up saved.")
                    elif resp is not None:
                        show_response_error(resp, "/evaluate/followup", "Unable to submit follow-up.")

        if st.session_state.eval_daily:
            eval_result = st.session_state.eval_daily
            st.subheader("Evaluation Summary")
            st.write(f"Risk: {eval_result.get('risk_level')} | Score: {eval_result.get('risk_score')}")
            quality = eval_result.get("quality", {})
            if quality.get("is_suspected_fake"):
                st.warning(f"Low quality: {quality.get('reason_summary', '')}. Try adding 1-2 sentences.")
            elif st.session_state.show_quality_details:
                st.caption(
                    f"Quality score: {quality.get('quality_score')} | "
                    f"Flags: {quality.get('flags')}"
                )

        st.divider()
        st.subheader("Rapid Evaluation")
        st.caption("Not a diagnosis. If you feel unsafe contact local emergency services.")
        rapid_date = date.today()
        override_rapid_dt = None
        if st.session_state.ui_dev_mode:
            st.caption("Developer Mode: date/time overrides enabled.")
            if st.checkbox("Override date/time (Rapid)", key="override_rapid_dt"):
                override_date = st.date_input("Rapid date", value=date.today(), key="rapid_override_date")
                override_time = st.time_input("Rapid time", value=datetime.now().time(), key="rapid_override_time")
                override_rapid_dt = datetime.combine(override_date, override_time)
                rapid_date = override_date
        questions_resp = api_get("/rapid/questions")
        if questions_resp is None:
            st.stop()
        if not questions_resp.ok:
            show_response_error(questions_resp, "/rapid/questions", "Unable to load rapid questions.")
            st.stop()

        questions = safe_json(questions_resp) or []
        if not questions:
            st.info("No rapid questions available.")
            st.stop()

        if (
            st.session_state.rapid_session_id is None
            or st.session_state.rapid_session_date != rapid_date.isoformat()
        ):
            start_resp = api_post("/rapid/start", json={"entry_date": rapid_date.isoformat()})
            if start_resp is None:
                st.stop()
            if not start_resp.ok:
                show_response_error(start_resp, "/rapid/start", "Unable to start rapid evaluation.")
                st.stop()
            start_payload = safe_json(start_resp) or {}
            st.session_state.rapid_session_id = start_payload.get("session_id")
            st.session_state.rapid_session_date = rapid_date.isoformat()
            st.session_state.rapid_started_ts = time.time()

        with st.form("rapid_form"):
            rapid_answers = []
            rapid_answer_map = {}
            for question in questions:
                qid = question["id"]
                qslug = question["slug"]
                qtext = question["text"]
                qformat = question.get("format")
                if qformat == "scale":
                    value = st.slider(qtext, 1, 10, 5, key=f"rapid_{qid}")
                    answer_text = str(value)
                elif qformat == "choice":
                    choices = question.get("choices") or ["Good", "Okay", "Poor"]
                    answer_text = st.selectbox(qtext, choices, key=f"rapid_{qid}")
                elif qformat == "yesno":
                    answer_text = st.selectbox(qtext, ["No", "Yes"], key=f"rapid_{qid}")
                else:
                    answer_text = st.text_input(qtext, key=f"rapid_{qid}")
                rapid_answers.append({"question_id": qid, "answer_text": answer_text})
                rapid_answer_map[qslug] = answer_text
            if st.form_submit_button("Submit rapid evaluation"):
                payload = {
                    "entry_date": rapid_date.isoformat(),
                    "session_id": st.session_state.rapid_session_id,
                    "answers": rapid_answers,
                }
                if override_rapid_dt:
                    payload["override_datetime"] = override_rapid_dt.isoformat()
                resp = api_post("/rapid/submit", json=payload)
                if resp is not None and resp.ok:
                    st.session_state.rapid_result = safe_json(resp) or {}
                    st.session_state.rapid_session_id = None
                    st.session_state.rapid_session_date = None
                    st.success("Rapid evaluation saved.")
                    duration_seconds = None
                    if st.session_state.rapid_started_ts:
                        duration_seconds = time.time() - st.session_state.rapid_started_ts
                    eval_payload = {
                        "rapid_answers": rapid_answer_map,
                        "duration_seconds": duration_seconds,
                    }
                    eval_resp = api_post("/evaluate", json=eval_payload)
                    if eval_resp is not None and eval_resp.ok:
                        eval_data = safe_json(eval_resp) or {}
                        st.session_state.eval_rapid = eval_data
                        st.session_state.eval_rapid_followups = eval_data.get("recommended_followups", [])
                        st.session_state.eval_rapid_session_id = eval_data.get("session_id")
                    elif eval_resp is not None:
                        show_response_error(eval_resp, "/evaluate", "Unable to run evaluation.")
                elif resp is not None:
                    if resp.status_code == 429:
                        detail = (safe_json(resp) or {}).get("detail", resp.text)
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            st.warning(f"{detail} Retry after {retry_after} seconds.")
                        else:
                            st.warning(detail)
                    else:
                        show_response_error(resp, "/rapid/submit", "Rapid evaluation failed.")

        result = st.session_state.get("rapid_result")
        if result:
            st.subheader("Rapid results")
            st.metric("Risk level", result.get("level", "unknown"))
            st.write("Score:", result.get("score", 0))
            confidence_score = result.get("confidence_score")
            confidence_label = "Medium"
            if isinstance(confidence_score, (int, float)):
                if confidence_score >= 0.8:
                    confidence_label = "High"
                elif confidence_score >= 0.55:
                    confidence_label = "Medium"
                else:
                    confidence_label = "Low"
                st.write(f"Confidence: {confidence_label}")
                if confidence_label == "Low":
                    st.info("Confidence is low because your answers were too quick/inconsistent. You can retake slowly.")
            if result.get("is_low_quality"):
                st.warning(f"Low quality: {result.get('reason_summary', '')}. Try adding 1-2 sentences.")
            if st.session_state.show_quality_details:
                st.caption(
                    f"Quality score: {result.get('input_quality_score')} | "
                    f"Flags: {result.get('input_quality_flags')}"
                )
            if result.get("is_valid") is False:
                flags = result.get("quality_flags", [])
                flag_text = ", ".join(flags) if flags else "quality flags"
                st.info(f"This evaluation wasn't counted because: {flag_text}")
            explanations = result.get("explanations", [])
            if explanations:
                st.write("Why this result?")
                for item in explanations[:3]:
                    reason = item.get("reason", "Signal")
                    weight = item.get("weight", 0)
                    st.write(f"- {reason} (impact {weight})")

        if st.session_state.eval_rapid_followups:
            st.subheader("Quick follow-up")
            with st.form("rapid_followup_form"):
                followup_answers = {}
                for item in st.session_state.eval_rapid_followups:
                    answer = st.text_input(item["prompt"], key=f"rapid_followup_{item['key']}")
                    followup_answers[item["key"]] = answer
                if st.form_submit_button("Submit follow-up"):
                    resp = api_post(
                        "/evaluate/followup",
                        json={
                            "session_id": st.session_state.eval_rapid_session_id,
                            "answers": followup_answers,
                        },
                    )
                    if resp is not None and resp.ok:
                        updated = safe_json(resp) or {}
                        st.session_state.eval_rapid = updated
                        st.session_state.eval_rapid_followups = []
                        st.success("Thanks, follow-up saved.")
                    elif resp is not None:
                        show_response_error(resp, "/evaluate/followup", "Unable to submit follow-up.")

        if st.session_state.eval_rapid:
            eval_result = st.session_state.eval_rapid
            st.subheader("Unified Evaluation")
            st.write(f"Risk: {eval_result.get('risk_level')} | Score: {eval_result.get('risk_score')}")
            quality = eval_result.get("quality", {})
            if quality.get("is_suspected_fake"):
                st.warning(f"Low quality: {quality.get('reason_summary', '')}. Try adding 1-2 sentences.")
            elif st.session_state.show_quality_details:
                st.caption(
                    f"Quality score: {quality.get('quality_score')} | "
                    f"Flags: {quality.get('flags')}"
                )

            st.subheader("Action Plan")
            micro_signal = result.get("micro_signal", {})
            insights = st.session_state.get("baseline_insights") or {}
            baseline_z = insights.get("z_score") if insights.get("baseline_ready") else None
            plan_payload = {
                "risk_level": result.get("level", "green"),
                "confidence": confidence_label.lower(),
                "baseline_deviation_z": baseline_z,
                "micro_streak_days": micro_signal.get("streak_days", 0),
                "answered_last_7_days": micro_signal.get("answered_last_7_days", 0),
                "self_harm_flag": any(
                    "self-harm" in (item.get("reason", "").lower())
                    for item in explanations
                ),
            }
            plan_resp = api_post("/plan/generate", json=plan_payload)
            if plan_resp is not None and plan_resp.ok:
                st.session_state.action_plan_rapid = safe_json(plan_resp) or {}
            elif plan_resp is not None:
                show_response_error(plan_resp, "/plan/generate", "Unable to generate action plan.")

            plan = st.session_state.action_plan_rapid
            if plan:
                tabs = st.tabs(["Next 15 minutes", "Next 24 hours", "Resources"])
                with tabs[0]:
                    for item in plan.get("next_15_min", []):
                        st.write(f"- {item.get('title')} ({item.get('duration_min', '')} min): {item.get('why')}")
                with tabs[1]:
                    for item in plan.get("next_24_hours", []):
                        st.write(f"- {item.get('title')} ({item.get('timeframe', '')}): {item.get('why')}")
                with tabs[2]:
                    for item in plan.get("resources", []):
                        st.write(f"- {item.get('label')} ({item.get('type')}): {item.get('note')}")
                st.caption(plan.get("safety_note", ""))
            actions = result.get("recommended_actions", [])
            if actions:
                st.write("Next 15 minutes:")
                for action in actions:
                    st.write(f"- {action}")
            crisis = result.get("crisis_guidance") or []
            if crisis:
                st.error("Crisis guidance")
                for item in crisis:
                    st.write(f"- {item}")

        st.subheader("Recent rapid evaluations")
        history_params = "?days=30"
        if st.session_state.ui_dev_mode and st.session_state.include_low_quality:
            history_params += "&include_low_quality=true"
        history_resp = api_get(f"/rapid/history{history_params}")
        if history_resp is not None and history_resp.ok:
            history = safe_json(history_resp) or []
            if history:
                recent = history[-5:]
                for entry in reversed(recent):
                    st.write(f"{entry['date']} | score {entry['score']} | level {entry['level']}")
            else:
                st.info("No rapid evaluations yet.")
        elif history_resp is not None:
            show_response_error(history_resp, "/rapid/history", "Unable to load rapid history.")

        st.caption("Not a diagnosis. If you feel unsafe contact local emergency services.")
with journal_tab:
    st.subheader("Journal")
    st.caption("Not a diagnosis. If you feel unsafe contact local emergency services.")
    if not st.session_state.token:
        st.warning("Sign in on the Home tab to continue.")
    else:
        selected_journal_date = date.today()
        override_journal_dt = None
        if st.session_state.ui_dev_mode:
            st.caption("Developer Mode: date/time overrides enabled.")
            if st.checkbox("Override date/time (Journal)", key="override_journal_dt"):
                override_date = st.date_input("Journal override date", value=date.today(), key="journal_date")
                override_time = st.time_input("Journal override time", value=datetime.now().time(), key="journal_time")
                override_journal_dt = datetime.combine(override_date, override_time)
                selected_journal_date = override_date
        journal_text = st.text_area("Write a short entry", height=140)
        if st.button("Save journal entry"):
            if not journal_text.strip():
                st.warning("Write something before saving.")
            else:
                payload = {
                    "content": journal_text,
                    "entry_date": selected_journal_date.isoformat(),
                }
                if override_journal_dt:
                    payload["override_datetime"] = override_journal_dt.isoformat()
                resp = api_post("/journal", json=payload)
                if resp is not None and resp.ok:
                    payload = safe_json(resp) or {}
                    if payload.get("is_low_quality"):
                        st.warning(f"Low quality: {payload.get('reason_summary', '')}. Try adding 1-2 sentences.")
                        st.info("You can edit and resubmit if you'd like.")
                    else:
                        st.success("Journal entry saved.")
                    if st.session_state.show_quality_details:
                        st.caption(
                            f"Quality score: {payload.get('input_quality_score')} | "
                            f"Flags: {payload.get('input_quality_flags')}"
                        )
                elif resp is not None:
                    if resp.status_code == 429:
                        retry_after = resp.headers.get("Retry-After")
                        detail = (safe_json(resp) or {}).get("detail", resp.text)
                        if retry_after:
                            st.warning(f"{detail} Retry after {retry_after} seconds.")
                        else:
                            st.warning(detail)
                    else:
                        show_response_error(resp, "/journal", "Unable to save journal.")

        st.subheader("Journal History")
        days = st.selectbox("Show last", [7, 30, 90], index=1, key="journal_days")
        search = st.text_input("Search journal text", value="", key="journal_search")

        journal_resp = api_get(f"/journal?days={days}")
        if journal_resp is not None and journal_resp.ok:
            entries = safe_json(journal_resp) or []
            if search:
                entries = [
                    entry for entry in entries
                    if search.lower() in entry.get("content", "").lower()
                ]
            if entries:
                for entry in entries:
                    preview = entry.get("content", "")[:120]
                    header = f"{entry.get('created_at', '')} | {preview}"
                    with st.expander(header):
                        st.write(entry.get("content"))
                        if st.session_state.ui_dev_mode:
                            flags = entry.get("input_quality_flags") or []
                            if flags:
                                st.caption(f"Quality flags: {flags}")
                            if entry.get("is_low_quality"):
                                st.caption("Low quality entry.")
            else:
                st.info("No journal entries found.")
        elif journal_resp is not None:
            show_response_error(journal_resp, "/journal", "Unable to load journal history.")

        if st.session_state.ui_dev_mode:
            st.subheader("Developer Tools")
            if st.button("Seed demo data (14 days)"):
                resp = api_post("/dev/seed_demo")
                if resp is not None and resp.ok:
                    payload = safe_json(resp) or {}
                    created = payload.get("created", {})
                    st.success(
                        "Demo data created: "
                        f"{created.get('answers', 0)} answers, "
                        f"{created.get('journals', 0)} journals, "
                        f"{created.get('rapid_evaluations', 0)} rapid evaluations."
                    )
                elif resp is not None:
                    show_response_error(resp, "/dev/seed_demo", "Unable to seed demo data.")
            if st.button("Clear demo data"):
                resp = api_post("/dev/clear_demo")
                if resp is not None and resp.ok:
                    payload = safe_json(resp) or {}
                    deleted = payload.get("deleted", {})
                    st.success(
                        "Demo data cleared: "
                        f"{deleted.get('answers', 0)} answers, "
                        f"{deleted.get('journals', 0)} journals, "
                        f"{deleted.get('rapid_evaluations', 0)} rapid evaluations."
                    )
                elif resp is not None:
                    show_response_error(resp, "/dev/clear_demo", "Unable to clear demo data.")
with export_tab:
    st.subheader("Export")
    st.caption("Not a diagnosis. If you feel unsafe contact local emergency services.")
    if not st.session_state.token:
        st.warning("Sign in on the Home tab to continue.")
    else:
        days = st.selectbox("Export range", [7, 30, 90], index=1, key="export_days")
        include_text = st.checkbox("Include journal text", value=False)
        export_format = st.selectbox("Export format", ["zip", "json"], index=0)
        st.warning("Never share exports containing journal text publicly.")
        if st.button("Download my data"):
            try:
                resp = requests.get(
                    api_url("/export/anonymized"),
                    headers=api_headers(),
                    params={"days": days, "format": export_format, "include_journal_text": include_text},
                    timeout=30,
                )
            except requests.RequestException as exc:
                st.error(f"Request failed: {exc}")
                resp = None
            if resp is not None and resp.ok:
                st.session_state.export_bytes = resp.content
                st.success("Export ready.")
            elif resp is not None:
                show_response_error(resp, "/export/anonymized", "Export failed.")

        export_bytes = st.session_state.get("export_bytes")
        if export_bytes:
            file_name = "mindtriage_export.zip" if export_format == "zip" else "mindtriage_export.json"
            mime = "application/zip" if export_format == "zip" else "application/json"
            st.download_button(
                "Download export",
                data=export_bytes,
                file_name=file_name,
                mime=mime,
            )

        st.subheader("Import")
        st.caption("Import a JSON export into this local database.")
        upload = st.file_uploader("Import my data (JSON)", type=["json"])
        if upload is not None:
            files = {"file": (upload.name, upload.getvalue(), "application/json")}
            try:
                resp = requests.post(
                    api_url("/import/anonymized"),
                    headers=api_headers(),
                    files=files,
                    timeout=30,
                )
            except requests.RequestException as exc:
                st.error(f"Request failed: {exc}")
                resp = None
            if resp is not None and resp.ok:
                payload = safe_json(resp) or {}
                created = payload.get("created", {})
                st.success(
                    "Import complete: "
                    f"{created.get('answers', 0)} answers, "
                    f"{created.get('journals', 0)} journals, "
                    f"{created.get('rapid_evaluations', 0)} rapid evaluations."
                )
            elif resp is not None:
                show_response_error(resp, "/import/anonymized", "Import failed.")
with insights_tab:
    st.subheader("Insights")
    st.caption("Not a diagnosis. If you feel unsafe contact local emergency services.")
    if not st.session_state.token:
        st.warning("Sign in on the Home tab to continue.")
    else:
        st.subheader("Triage Summary")
        risk_level = None
        reasons = []
        risk_resp = api_get("/risk/latest")
        if risk_resp is not None and risk_resp.ok:
            risk = risk_resp.json()
            st.metric("Risk level", risk.get("risk_level", "unknown"))
            st.write("Score:", risk.get("score", 0))
            risk_level = risk.get("risk_level")
            reasons = risk.get("reasons", [])
            if reasons:
                st.write("Signals:", ", ".join(reasons))
            excerpt = risk.get("last_journal_excerpt")
            if excerpt:
                st.write("Recent journal excerpt:")
                st.write(excerpt)
        elif risk_resp is not None:
            st.error(risk_resp.json().get("detail", "Unable to load risk status."))

        st.subheader("Action Plan")
        if risk_level:
            insights = st.session_state.get("baseline_insights") or {}
            baseline_z = insights.get("z_score") if insights.get("baseline_ready") else None
            plan_payload = {
                "risk_level": risk_level,
                "confidence": "medium",
                "baseline_deviation_z": baseline_z,
                "micro_streak_days": st.session_state.micro_streak_days,
                "answered_last_7_days": st.session_state.micro_answered_last_7,
                "self_harm_flag": any(
                    "risk keywords" in reason.lower() for reason in (reasons or [])
                ),
            }
            plan_resp = api_post("/plan/generate", json=plan_payload)
            if plan_resp is not None and plan_resp.ok:
                st.session_state.action_plan_regular = safe_json(plan_resp) or {}
            elif plan_resp is not None:
                show_response_error(plan_resp, "/plan/generate", "Unable to generate action plan.")

            plan = st.session_state.action_plan_regular
            if plan:
                tabs = st.tabs(["Next 15 minutes", "Next 24 hours", "Resources"])
                with tabs[0]:
                    for item in plan.get("next_15_min", []):
                        st.write(f"- {item.get('title')} ({item.get('duration_min', '')} min): {item.get('why')}")
                with tabs[1]:
                    for item in plan.get("next_24_hours", []):
                        st.write(f"- {item.get('title')} ({item.get('timeframe', '')}): {item.get('why')}")
                with tabs[2]:
                    for item in plan.get("resources", []):
                        st.write(f"- {item.get('label')} ({item.get('type')}): {item.get('note')}")
                st.caption(plan.get("safety_note", ""))
        else:
            st.info("Complete a daily check-in to generate an action plan.")

        st.subheader("Streaks")
        streak_query = "?include_low_quality=true" if st.session_state.include_low_quality else ""
        streak_resp = api_get(f"/micro/streak{streak_query}")
        if streak_resp is not None and streak_resp.ok:
            streak = safe_json(streak_resp) or {}
            st.session_state.micro_streak_days = streak.get("streak_days", 0)
            st.write(f"Micro streak: {st.session_state.micro_streak_days} days")
        elif streak_resp is not None:
            show_response_error(streak_resp, "/micro/streak", "Unable to load micro streak.")

        st.markdown("<a name='risk-trend'></a>", unsafe_allow_html=True)
        st.subheader("Risk Trend")
        history_path = "/risk/history"
        if st.session_state.ui_dev_mode and st.session_state.include_low_quality:
            history_path = "/risk/history?include_low_quality=true"
        history_resp = api_get(history_path)
        if history_resp is not None and history_resp.ok:
            history = safe_json(history_resp) or []
            if len(history) == 0:
                st.info("Add a few daily check-ins to see trends.")
            elif len(history) == 1:
                st.info("Need at least 2 days to show a trend.")
            else:
                max_score = max(item.get("score", 0) for item in history)
                chart_max = max(20, max_score + 2)
                band_data = [
                    {"ymin": 0, "ymax": 8, "color": "#dff3df"},
                    {"ymin": 9, "ymax": 17, "color": "#fff2cc"},
                    {"ymin": 18, "ymax": chart_max, "color": "#ffe1e1"},
                ]
                bands_df = pd.DataFrame(band_data)
                trend_df = pd.DataFrame(history)
                trend_df["date"] = pd.to_datetime(trend_df["date"])

                band_chart = alt.Chart(bands_df).mark_rect(opacity=0.4).encode(
                    y=alt.Y("ymin:Q", title="Risk score", scale=alt.Scale(domain=[0, chart_max])),
                    y2="ymax:Q",
                    color=alt.Color("color:N", scale=None, legend=None),
                )
                line_chart = alt.Chart(trend_df).mark_line(point=True).encode(
                    x=alt.X("date:T", title="Date"),
                    y=alt.Y("score:Q", scale=alt.Scale(domain=[0, chart_max])),
                    tooltip=["date:T", "score:Q", "level:N"],
                )
                st.altair_chart(band_chart + line_chart, use_container_width=True)
        elif history_resp is not None:
            show_response_error(history_resp, "/risk/history", "Unable to load risk history.")

        st.subheader("Baseline (last 14 days)")
        drift_date = None
        if st.session_state.ui_dev_mode:
            drift_date = st.date_input("Drift date (dev)", value=date.today(), key="drift_date")
        drift_query = "/insights/drift"
        if drift_date:
            drift_query += f"?date={drift_date.isoformat()}"
        if st.session_state.ui_dev_mode and st.session_state.include_low_quality:
            drift_query += "&include_low_quality=true" if "?" in drift_query else "?include_low_quality=true"
        drift_resp = api_get(drift_query)
        if drift_resp is not None and drift_resp.ok:
            drift_payload = safe_json(drift_resp) or {}
            baseline_payload = drift_payload.get("baseline", {})
            signal_stats = baseline_payload.get("signals", {}) or {}
            baseline_ready = any(stat.get("mean") is not None for stat in signal_stats.values())
            if baseline_ready:
                coverages = [
                    stat.get("coverage_percent", 0)
                    for stat in signal_stats.values()
                    if stat.get("coverage_percent") is not None
                ]
                avg_coverage = round(sum(coverages) / len(coverages), 1) if coverages else 0
                st.write(f"Coverage: {avg_coverage}% of the last {baseline_payload.get('window_days', 14)} days.")
                cols = st.columns(2)
                for idx, (key, label, unit) in enumerate([
                    ("mood_score", "Mood", ""),
                    ("anxiety_score", "Anxiety", ""),
                    ("sleep_hours", "Sleep", "h"),
                    ("energy_score", "Energy", ""),
                ]):
                    mean_value = signal_stats.get(key, {}).get("mean")
                    if mean_value is not None:
                        cols[idx % 2].write(f"{label} mean: {mean_value}{unit}")
            else:
                st.info("Baseline building. Add more high-quality check-ins to improve coverage.")

            st.subheader("Today vs Baseline")
            top_changes = drift_payload.get("top_changes", [])
            if top_changes:
                for change in top_changes:
                    message = change.get("message") or f"{change.get('signal')}: {change.get('delta')}"
                    st.write(f"- {message} (Δ {change.get('delta')})")
            else:
                st.write("No meaningful changes yet.")

            st.subheader("Confidence meter")
            confidence = drift_payload.get("confidence", 0)
            st.write(f"Confidence: {int(confidence * 100)}%")
            recommendations = drift_payload.get("recommendations", [])
            if recommendations:
                st.subheader("Drift suggestions")
                for item in recommendations:
                    st.write(f"- {item}")
        elif drift_resp is not None:
            show_response_error(drift_resp, "/insights/drift", "Unable to load baseline drift.")

        insights_resp = api_get("/insights/today")
        if insights_resp is not None and insights_resp.ok:
            insights = safe_json(insights_resp) or {}
            st.session_state.baseline_insights = insights
        elif insights_resp is not None:
            show_response_error(insights_resp, "/insights/today", "Unable to load insights.")

        st.subheader("Methods + Metrics")
        metrics_query = "/metrics/summary?days=30"
        if st.session_state.ui_dev_mode and st.session_state.include_low_quality:
            metrics_query += "&include_low_quality=true"
        metrics_resp = api_get(metrics_query)
        if metrics_resp is not None and metrics_resp.ok:
            metrics = safe_json(metrics_resp) or {}
            regular = metrics.get("regular", {})
            rapid = metrics.get("rapid", {})
            safety = metrics.get("safety", {})

            st.subheader("Regular")
            col1, col2, col3 = st.columns(3)
            col1.metric("Check-ins", regular.get("count_checkins", 0))
            col2.metric("Missing days", regular.get("missing_days", 0))
            col3.metric("Mean score", regular.get("mean_score", 0))
            col4, col5, col6 = st.columns(3)
            col4.metric("Median score", regular.get("median_score", 0))
            col5.metric("Std score", regular.get("std_score", 0))
            col6.metric("Trend slope (14d)", regular.get("trend_slope_14d", 0))

            st.subheader("Rapid")
            rcol1, rcol2, rcol3 = st.columns(3)
            rcol1.metric("Total", rapid.get("count_total", 0))
            rcol2.metric("Valid", rapid.get("count_valid", 0))
            rcol3.metric("Invalid", rapid.get("count_invalid", 0))
            rcol4, rcol5, rcol6 = st.columns(3)
            rcol4.metric("Mean time (s)", rapid.get("mean_time_seconds_valid", 0))
            rcol5.metric("High confidence", rapid.get("confidence_counts", {}).get("high", 0))
            rcol6.metric("RED count", rapid.get("level_counts", {}).get("red", 0))

            level_counts = rapid.get("level_counts", {})
            if level_counts:
                level_df = pd.DataFrame(
                    [{"level": key, "count": value} for key, value in level_counts.items()]
                )
                level_chart = alt.Chart(level_df).mark_bar().encode(
                    x=alt.X("level:N", title="Level"),
                    y=alt.Y("count:Q", title="Count"),
                )
                st.altair_chart(level_chart, use_container_width=True)

            invalid_counts = rapid.get("invalid_reason_counts", {})
            if invalid_counts:
                invalid_df = pd.DataFrame(
                    [{"reason": key, "count": value} for key, value in invalid_counts.items()]
                )
                invalid_chart = alt.Chart(invalid_df).mark_bar().encode(
                    x=alt.X("reason:N", title="Invalid reason"),
                    y=alt.Y("count:Q", title="Count"),
                )
                st.altair_chart(invalid_chart, use_container_width=True)

            st.subheader("Safety")
            scol1, scol2, scol3 = st.columns(3)
            scol1.metric("RED triggers", safety.get("red_trigger_count", 0))
            scol2.metric("RED + low confidence", safety.get("red_low_confidence_count", 0))
            scol3.metric("Escalations shown", safety.get("escalation_shown_count", 0))

            st.subheader("Copy for paper")
            total = rapid.get("count_total", 0)
            invalid = rapid.get("count_invalid", 0)
            invalid_pct = (invalid / total * 100) if total else 0
            invalid_reasons = ", ".join(
                f"{key} ({value})" for key, value in (invalid_counts or {}).items()
            ) or "none"
            paper_text = (
                f"In 30 days, {total} rapid evaluations were recorded; "
                f"{invalid_pct:.1f}% were invalid due to {invalid_reasons}. "
                f"Regular check-ins: {regular.get('count_checkins', 0)} completed; "
                f"mean score {regular.get('mean_score', 0)}."
            )
            st.code(paper_text, language="text")
        elif metrics_resp is not None:
            show_response_error(metrics_resp, "/metrics/summary", "Unable to load metrics.")

