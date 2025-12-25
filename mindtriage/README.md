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
pip uninstall -y bcrypt
pip install bcrypt==4.0.1
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

### Dev mode (rapid evaluation limits)

Enable lower rapid-evaluation cooldowns/limits for local testing:

Windows PowerShell:

```powershell
$env:MINDTRIAGE_DEV_MODE="1"
```

Windows CMD:

```cmd
set MINDTRIAGE_DEV_MODE=1
```

macOS / Linux:

```bash
export MINDTRIAGE_DEV_MODE=1
```

You can also use `DEV_MODE=1` as an alias.

Frontend developer controls (date/time overrides, quality details) appear only when dev mode is enabled in the backend.

### Developer Mode (UI overrides)

Backend and frontend dev tools are disabled by default. To enable:

Windows PowerShell:

```powershell
$env:DEV_MODE="1"
```

This unlocks UI overrides and allows override fields on the backend.

## Run the frontend

In a second terminal (with the venv active):

```bash
streamlit run mindtriage/frontend/streamlit_app.py
```

The Streamlit app uses `http://127.0.0.1:8000` by default.

## Notes

- `SECRET_KEY` in `mindtriage/backend/app/main.py` is set to `CHANGE_ME`. Update before real use.
- The SQLite database file `mindtriage.db` is created locally on first run.

## Drift tracking

- Daily check-ins and journals feed a simple risk score.
- The app plots recent scores in a trend chart using `/risk/history`.
- For demos, you can backdate check-ins and journal entries with `entry_date` (YYYY-MM-DD) to generate a trend quickly (dev mode only).

## Export (anonymized)

- Download anonymized data from `/export/anonymized` (zip with CSVs and schema).
- Use `include_journal_text=true` only if you intend to share journal text safely.
