# MindTriage MVP

Local-first mental health MVP with a FastAPI backend and Streamlit frontend.

Safety note: Not a diagnosis. If you feel unsafe contact local emergency services.

## Setup

Create and activate a virtual environment.

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install backend requirements:

```bash
pip install -r mindtriage/backend/requirements.txt
```

Install frontend requirements:

```bash
pip install -r mindtriage/frontend/requirements.txt
```

## Run the backend

```bash
uvicorn mindtriage.backend.app.main:app --reload
```

FastAPI docs: http://127.0.0.1:8000/docs

## Run the frontend

In a second terminal (with the venv active):

```bash
streamlit run mindtriage/frontend/streamlit_app.py
```

The Streamlit app uses `http://127.0.0.1:8000` by default.

## Notes

- `SECRET_KEY` in `mindtriage/backend/app/main.py` is set to `CHANGE_ME`. Update before real use.
- The SQLite database file `mindtriage.db` is created locally on first run.
