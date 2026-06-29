# Prospect Platform UI (React + FastAPI)

This folder hosts a separate UI project integrated with the existing pipeline/database in the repository root.

## Architecture

- `app-ui/backend`: FastAPI API layer (auth, monitoring, controls)
- `app-ui/frontend`: React + Vite dashboard UI
- Uses existing root DB tables (`naukri_jobs`, `company_research`, `prospects`, `search_keywords`, `linkedin_credentials`)
- Adds UI-support tables (auto-created):
  - `ui_users`
  - `pipeline_runs`
  - `app_settings`

## Backend Setup

1. Ensure root `.env` is configured (`DB_URL`, browser + LLM config).
2. Add UI env values in root `.env`:
   - `APP_UI_ADMIN_USER=admin`
   - `APP_UI_ADMIN_PASS=admin123`
   - `APP_UI_JWT_SECRET=change-me`
   - `APP_UI_JWT_EXPIRE_HOURS=12`
   - `APP_UI_ALLOWED_ORIGINS=http://127.0.0.1:5174`
   - optional: `APP_UI_PYTHON=C:\\path\\to\\python.exe`

3. Install dependencies:

```powershell
pip install -r requirements.txt
pip install -r app-ui/backend/requirements.txt
```

4. Run backend API:

```powershell
python -m uvicorn app.main:app --app-dir app-ui/backend --host 0.0.0.0 --port 8001 --reload
```

## Frontend Setup

1. Copy frontend env:

```powershell
copy app-ui\\frontend\\.env.example app-ui\\frontend\\.env
```

2. Install and run:

```powershell
cd app-ui\\frontend
npm install
npm run dev
```

3. Open: `http://127.0.0.1:5174`

## What UI Covers

- Dashboard metrics and recent runs
- Pipeline trigger controls (`ingest`, `research`, `intelligence`)
- Job posts monitoring
- Company research monitoring
- Prospect deep-view (summary, experiences, posts, LLM output, dossier/outreach payload)
- Keyword CRUD for ingest controls
- LinkedIn credential management for auth fallback
- Runtime app settings (`app_settings` table)
- Run log inspection from `pipeline_runs.log_text`

## Scheduler Integration

Your Windows Task Scheduler jobs stay unchanged. The UI can trigger the same scripts manually:

- `scheduler/run_ingest.py`
- `scheduler/run_research.py`
- `scheduler/run_candidate_hunt.py`
- `scheduler/run_intelligence.py`

This gives both scheduled background execution and manual control from UI.
