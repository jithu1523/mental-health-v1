import altair as alt
import requests
import streamlit as st

st.set_page_config(page_title="MindTriage", page_icon="??", layout="centered")

API_BASE = st.text_input("API base URL", value="http://127.0.0.1:8000")

if "token" not in st.session_state:
    st.session_state.token = None
if "daily_questions" not in st.session_state:
    st.session_state.daily_questions = None


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

login_tab, care_tab = st.tabs(["Account", "Check-in & Journal"])

st.subheader("Backend connection check")
health_resp = api_get("/health")
if health_resp is None:
    st.error("Backend check failed. Start backend with: uvicorn app.main:app --reload --port 8000")
elif health_resp.ok:
    st.success(f"Backend healthy ({health_resp.status_code}) | {api_url('/health')}")
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
                    daily_answers.append({"question_id": question["id"], "answer_text": answer_text})
                if st.form_submit_button("Save daily answers"):
                    payload = {"answers": daily_answers}
                    resp = api_post("/answers", json=payload)
                    if resp is not None and resp.ok:
                        st.success("Daily check-in saved.")
                        st.session_state.daily_questions = None
                    elif resp is not None:
                        st.error(resp.json().get("detail", "Unable to save daily answers."))

        st.subheader("Journal")
        journal_text = st.text_area("Write a short entry", height=140)
        if st.button("Save journal entry"):
            if not journal_text.strip():
                st.warning("Write something before saving.")
            else:
                resp = api_post("/journal", json={"content": journal_text})
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

        st.subheader("Risk Trend")
        history_resp = api_get("/risk/history")
        if history_resp is not None and history_resp.ok:
            history = safe_json(history_resp) or []
            if len(history) < 3:
                st.info("Add more daily check-ins to see trends.")
            else:
                max_score = max(item.get("score", 0) for item in history)
                chart_max = max(20, max_score + 2)
                band_data = [
                    {"ymin": 0, "ymax": 8, "color": "#dff3df"},
                    {"ymin": 9, "ymax": 17, "color": "#fff2cc"},
                    {"ymin": 18, "ymax": chart_max, "color": "#ffe1e1"},
                ]
                band_chart = alt.Chart(band_data).mark_rect(opacity=0.4).encode(
                    y=alt.Y("ymin:Q", title="Risk score", scale=alt.Scale(domain=[0, chart_max])),
                    y2="ymax:Q",
                    color=alt.Color("color:N", scale=None, legend=None),
                )
                line_chart = alt.Chart(history).mark_line(point=True).encode(
                    x=alt.X("date:T", title="Date"),
                    y=alt.Y("score:Q", scale=alt.Scale(domain=[0, chart_max])),
                    tooltip=["date:T", "score:Q", "level:N"],
                )
                st.altair_chart(band_chart + line_chart, use_container_width=True)
        elif history_resp is not None:
            show_response_error(history_resp, "/risk/history", "Unable to load risk history.")

st.caption("Not a diagnosis. If you feel unsafe contact local emergency services.")
