from datetime import date

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
if "daily_questions" not in st.session_state:
    st.session_state.daily_questions = None
if "rapid_session_id" not in st.session_state:
    st.session_state.rapid_session_id = None
if "rapid_session_date" not in st.session_state:
    st.session_state.rapid_session_date = None
if "export_bytes" not in st.session_state:
    st.session_state.export_bytes = None


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

rapid_tab_label = "Rapid Evaluation"
export_tab_label = "Export"
methods_tab_label = "Methods + Metrics"
login_tab, care_tab, rapid_tab, export_tab, methods_tab = st.tabs(
    ["Account", "Check-in & Journal", rapid_tab_label, export_tab_label, methods_tab_label]
)

st.subheader("Backend connection check")
health_resp = api_get("/health")
if health_resp is None:
    st.error("Backend check failed. Start backend with: uvicorn app.main:app --reload --port 8000")
elif health_resp.ok:
    payload = safe_json(health_resp) or {}
    st.session_state.dev_mode = bool(payload.get("dev_mode"))
    message = f"Backend healthy ({health_resp.status_code}) | {api_url('/health')}"
    if st.session_state.dev_mode:
        message += " | Dev mode enabled"
    st.success(message)
else:
    snippet = (health_resp.text or "").strip()
    st.error(
        f"Backend unhealthy ({health_resp.status_code}) | {api_url('/health')} | "
        f"{snippet[:500] if snippet else 'No response body.'}"
    )
    st.error("Start backend with: uvicorn app.main:app --reload --port 8000")

with login_tab:
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

    if st.session_state.token:
        st.info("Authenticated.")

with care_tab:
    if not st.session_state.token:
        st.warning("Sign in on the Account tab to continue.")
    else:
        status_resp = api_get("/onboarding/status")
        if status_resp is None:
            st.stop()
        if not status_resp.ok:
            st.error(status_resp.json().get("detail", "Unable to load onboarding status."))
            st.stop()

        status = status_resp.json()
        missing_ids = status.get("missing_question_ids", [])

        if not status.get("complete"):
            st.subheader("Onboarding")
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
            st.stop()

        st.subheader("Daily check-in")
        selected_checkin_date = date.today()
        if st.session_state.dev_mode:
            st.caption("Dev mode: date controls enabled.")
            selected_checkin_date = st.date_input("Check-in date", value=date.today())
        if st.button("Load daily questions") or st.session_state.daily_questions is None:
            pick_resp = api_get("/daily/pick")
            if pick_resp is not None and pick_resp.ok:
                st.session_state.daily_questions = pick_resp.json()
            elif pick_resp is not None:
                st.error(pick_resp.json().get("detail", "Unable to load daily questions."))

        daily_questions = st.session_state.daily_questions or []
        if daily_questions:
            with st.form("daily_form"):
                daily_answers = []
                for question in daily_questions:
                    slug = question["slug"]
                    if slug in {"daily_mood", "daily_anxiety"}:
                        value = st.slider(question["text"], 1, 10, 5, key=f"daily_{question['id']}")
                        answer_text = str(value)
                    elif slug in {"daily_hopeless", "daily_isolation"}:
                        choice = st.selectbox(question["text"], ["No", "Sometimes", "Yes"], key=f"daily_{question['id']}")
                        answer_text = choice
                    else:
                        answer_text = st.text_input(question["text"], key=f"daily_{question['id']}")
                    daily_answers.append({
                        "question_id": question["id"],
                        "answer_text": answer_text,
                        "entry_date": selected_checkin_date.isoformat(),
                    })
                if st.form_submit_button("Save daily answers"):
                    payload = {"answers": daily_answers}
                    resp = api_post("/answers", json=payload)
                    if resp is not None and resp.ok:
                        st.success("Daily check-in saved.")
                        st.session_state.daily_questions = None
                    elif resp is not None:
                        st.error(resp.json().get("detail", "Unable to save daily answers."))

        st.subheader("Quick check-in (10 seconds)")
        micro_resp = api_get("/micro/today")
        if micro_resp is not None and micro_resp.ok:
            micro = safe_json(micro_resp) or {}
            question = micro.get("question")
            answered = micro.get("answered")
            if not question:
                st.info("No micro check-in available.")
            elif answered:
                st.success("Done for today ✅")
            else:
                with st.form("micro_form"):
                    prompt = question.get("prompt", "Quick check-in")
                    qtype = question.get("question_type")
                    options = question.get("options", [])
                    if qtype == "scale":
                        value = st.slider(prompt, 1, 5, 3)
                        answer_value = str(value)
                    else:
                        answer_value = st.selectbox(prompt, options)
                    if st.form_submit_button("Save quick check-in"):
                        payload = {
                            "question_id": question.get("id"),
                            "value": answer_value,
                        }
                        resp = api_post("/micro/answer", json=payload)
                        if resp is not None and resp.ok:
                            st.success("Quick check-in saved.")
                        elif resp is not None:
                            show_response_error(resp, "/micro/answer", "Unable to save quick check-in.")
        elif micro_resp is not None:
            show_response_error(micro_resp, "/micro/today", "Unable to load quick check-in.")

        history_resp = api_get("/micro/history?days=7")
        if history_resp is not None and history_resp.ok:
            history = safe_json(history_resp) or []
            if history:
                st.caption("Last 7 days")
                st.write(", ".join(f"{item['entry_date']}: {item['value']}" for item in history))
        elif history_resp is not None:
            show_response_error(history_resp, "/micro/history", "Unable to load quick check-in history.")

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

        st.subheader("Journal")
        selected_journal_date = date.today()
        if st.session_state.dev_mode:
            st.caption("Dev mode: date controls enabled.")
            selected_journal_date = st.date_input("Journal date", value=date.today(), key="journal_date")
        journal_text = st.text_area("Write a short entry", height=140)
        if st.button("Save journal entry"):
            if not journal_text.strip():
                st.warning("Write something before saving.")
            else:
                resp = api_post(
                    "/journal",
                    json={"content": journal_text, "entry_date": selected_journal_date.isoformat()},
                )
                if resp is not None and resp.ok:
                    st.success("Journal entry saved.")
                elif resp is not None:
                    st.error(resp.json().get("detail", "Unable to save journal."))

        journal_resp = api_get("/journal")
        if journal_resp is not None and journal_resp.ok:
            entries = journal_resp.json()
            if entries:
                for entry in entries:
                    st.markdown(f"**{entry['created_at']}**")
                    st.write(entry["content"])
                    st.divider()
            else:
                st.info("No journal entries yet.")

        if st.session_state.dev_mode:
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
                    st.markdown("[Go to Risk Trend](#risk-trend)")
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

        st.subheader("Triage")
        risk_resp = api_get("/risk/latest")
        if risk_resp is not None and risk_resp.ok:
            risk = risk_resp.json()
            st.metric("Risk level", risk.get("risk_level", "unknown"))
            st.write("Score:", risk.get("score", 0))
            reasons = risk.get("reasons", [])
            if reasons:
                st.write("Signals:", ", ".join(reasons))
            excerpt = risk.get("last_journal_excerpt")
            if excerpt:
                st.write("Recent journal excerpt:")
                st.write(excerpt)
        elif risk_resp is not None:
            st.error(risk_resp.json().get("detail", "Unable to load risk status."))

        st.markdown("<a name='risk-trend'></a>", unsafe_allow_html=True)
        st.subheader("Risk Trend")
        history_resp = api_get("/risk/history")
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

        st.subheader("Your Baseline")
        baseline_resp = api_get("/baseline/summary")
        insights_resp = api_get("/insights/today")
        if baseline_resp is not None and baseline_resp.ok:
            baseline = safe_json(baseline_resp) or {}
            if baseline.get("baseline_ready"):
                st.success("Baseline ready.")
                st.write(
                    f"Mean score: {baseline.get('mean', 0)} | "
                    f"Std: {baseline.get('std', 0)} | "
                    f"Samples: {baseline.get('sample_count', 0)}"
                )
            else:
                st.info("Baseline building. Complete at least 5 check-ins.")
        elif baseline_resp is not None:
            show_response_error(baseline_resp, "/baseline/summary", "Unable to load baseline summary.")

        if insights_resp is not None and insights_resp.ok:
            insights = safe_json(insights_resp) or {}
            if insights.get("baseline_ready"):
                if "today_score" in insights:
                    st.write(
                        f"Today's deviation: z={insights.get('z_score', 0)} "
                        f"({insights.get('interpretation', '')})."
                    )
                else:
                    st.write(insights.get("message", "No insight for today."))
            else:
                st.write(insights.get("message", "Baseline not ready."))
        elif insights_resp is not None:
            show_response_error(insights_resp, "/insights/today", "Unable to load insights.")

st.caption("Not a diagnosis. If you feel unsafe contact local emergency services.")

with rapid_tab:
    st.subheader("Rapid Evaluation")
    st.caption("Not a diagnosis. If you feel unsafe contact local emergency services.")
    if not st.session_state.token:
        st.warning("Sign in on the Account tab to continue.")
    else:
        rapid_date = date.today()
        if st.session_state.dev_mode:
            st.caption("Dev mode: date controls enabled.")
            rapid_date = st.date_input("Evaluation date", value=date.today(), key="rapid_date")
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

        with st.form("rapid_form"):
            rapid_answers = []
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
            if st.form_submit_button("Submit rapid evaluation"):
                payload = {
                    "entry_date": rapid_date.isoformat(),
                    "session_id": st.session_state.rapid_session_id,
                    "answers": rapid_answers,
                }
                resp = api_post("/rapid/submit", json=payload)
                if resp is not None and resp.ok:
                    st.session_state.rapid_result = safe_json(resp) or {}
                    st.session_state.rapid_session_id = None
                    st.session_state.rapid_session_date = None
                    st.success("Rapid evaluation saved.")
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
        history_resp = api_get("/rapid/history")
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

with export_tab:
    st.subheader("Export")
    st.caption("Not a diagnosis. If you feel unsafe contact local emergency services.")
    if not st.session_state.token:
        st.warning("Sign in on the Account tab to continue.")
    else:
        days = st.selectbox("Days", [7, 30, 90], index=1)
        include_text = st.checkbox("Include journal text", value=False)
        st.warning("Never share exports containing journal text publicly.")
        if st.button("Download Anonymized Export"):
            try:
                resp = requests.get(
                    api_url("/export/anonymized"),
                    headers=api_headers(),
                    params={"days": days, "format": "zip", "include_journal_text": include_text},
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
            st.download_button(
                "Download export zip",
                data=export_bytes,
                file_name="mindtriage_export.zip",
                mime="application/zip",
            )

with methods_tab:
    st.subheader("Methods + Metrics")
    st.caption("Not a diagnosis. If you feel unsafe contact local emergency services.")
    if not st.session_state.token:
        st.warning("Sign in on the Account tab to continue.")
    else:
        metrics_resp = api_get("/metrics/summary?days=30")
        if metrics_resp is None:
            st.stop()
        if not metrics_resp.ok:
            show_response_error(metrics_resp, "/metrics/summary", "Unable to load metrics.")
            st.stop()
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
