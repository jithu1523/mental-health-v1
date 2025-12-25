# MindTriage (local-first mental health MVP)

Not a diagnosis. If you feel unsafe contact local emergency services.

## Quick Start (Windows)

```powershell
.\scripts\run_dev.ps1
```

Open:
- Frontend: http://localhost:8501
- Backend docs: http://127.0.0.1:8000/docs

## Quick Start (Mac/Linux)

```bash
./scripts/run_dev.sh
```

Open:
- Frontend: http://localhost:8501
- Backend docs: http://127.0.0.1:8000/docs

## Manual Run

Backend:
```bash
cd mindtriage/backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Frontend:
```bash
cd mindtriage/frontend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py --server.port 8501
```

## Configuration

Copy `.env.example` to `.env` in the repo root and edit as needed:
- `BACKEND_URL` (frontend uses this)
- `DEV_MODE` (0/1)
- `DEV_SECRET` (optional dev UI secret)
- `DB_PATH` (optional absolute or repo-relative path)

## Dev Mode

Set env vars and use the query parameter to enable dev controls:

Windows PowerShell:
```powershell
$env:DEV_MODE="1"
$env:DEV_SECRET="local_only"
```

Mac/Linux:
```bash
export DEV_MODE=1
export DEV_SECRET=local_only
```

Then open:
```
http://localhost:8501/?dev=1&dev_key=local_only
```

## Data & Privacy

- Local-first: all data stays in `mindtriage.db` at the repo root by default.
- Export/import is available from the Export tab.
- No cloud services are used.

## Paper-ready Notes

- Baseline and drift insights are deterministic, rule-based, and explainable.
- Quality gating flags low-quality entries and excludes them from trends by default.
- Crisis guardrails are conservative and always surface safety resources.

## Troubleshooting

- Backend health: http://127.0.0.1:8000/health
- If the frontend cannot connect, ensure port 8000 is free and the backend is running.
