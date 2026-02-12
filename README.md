# UFC Fight Predictor (UFCML)

End-to-end UFC prediction platform:
- Scrapes UFCStats into canonical Postgres tables (`ufc.*`).
- Materializes strict legacy-compatible contract tables for ML (`contract.*`) with exact column names/types.
- Scrapes upcoming events/cards into website-facing tables (`app.*`).
- Trains a pre-fight model from Postgres (`UFC_prefight_pro.py`) and stores model metadata + predictions in DB.
- Serves predictions via a FastAPI API (`src/ufc_api/main.py`) and a Next.js frontend (`frontend/`).

## Architecture At A Glance

Schemas:
- `ufc.*`: canonical scraped truth (events, fights, fight_totals, fighters).
- `contract.*`: stable ML interface matching the legacy CSV schema exactly.
- `app.*`: website state (upcoming fights, model artifacts, stored predictions).

Data flow (weekly):
1. Scrape completed events → ingest events/fights/totals into `ufc.*`.
2. Enrich fighter bios into `ufc.fighters` (only when missing).
3. Refresh `contract.*` from `ufc.*`.
4. Refresh `app.upcoming_fights` from UFCStats upcoming events.
5. Train + save model bundle → record metadata in `app.model_artifacts`.
6. Generate + store predictions in `app.predictions` (upcoming + completed evaluation support).

## UFCStats Fetch Rules (Important)

All scraping must use UFCStats with these exact endpoints:
- Completed events index: `http://ufcstats.com/statistics/events/completed?page=all`
- Fighters index: `http://ufcstats.com/statistics/fighters`
- Upcoming events index: `http://ufcstats.com/statistics/events/upcoming`

Follow links to:
- `http://ufcstats.com/event-details/<event_id>`
- `http://ufcstats.com/fight-details/<fight_id>`
- `http://ufcstats.com/fighter-details/<fighter_id>`

Rules:
- Always normalize discovered URLs to `http://ufcstats.com` + path.
- No `https` and no `www`.

## Local Setup

Requirements:
- Python 3.12+ (GitHub Actions uses 3.12).
- A Postgres database (Supabase recommended).
- Node 18+ (frontend).

Install Python dependencies:

```bash
pip install -r requirements.txt
```

Create `.env` with at least:
- `DATABASE_URL` (SQLAlchemy URL). Example:

```text
DATABASE_URL=postgresql+psycopg2://USER:PASSWORD@HOST:5432/postgres?sslmode=require
```

Optional runtime settings:
- `USER_AGENT=UFC-FightPredictor-Bot/1.0 (+https://github.com/<you>/<repo>)`
- `RATE_LIMIT_SECONDS=0.8`
- `UFC_DEBUG_HTML=1` (save debug HTML for suspicious fight pages)

## Database Init + Contract Validation

```powershell
$env:PYTHONPATH = "src"
python -m ufc_ingest.cli validate-contract
```

## Ingest (Completed Events)

Incremental ingest (new events + recovery refresh for partial events):

```powershell
$env:PYTHONPATH = "src"
python -m ufc_ingest.cli ingest --mode incremental --limit 1
```

Bootstrap ingest (all events):

```powershell
$env:PYTHONPATH = "src"
python -m ufc_ingest.cli ingest --mode bootstrap --limit 5
```

Backfill safely in batches (recommended for a full historical load):

```powershell
$env:PYTHONPATH = "src"
python scripts/backfill_all.py
```

## Refresh Contract Tables

```powershell
$env:PYTHONPATH = "src"
python -m ufc_ingest.cli refresh-contract
```

## Train Model (From Postgres)

By default, `UFC_prefight_pro.py` reads from `contract.*`, builds pre-fight features chronologically (no leakage), trains + calibrates, and writes a joblib model bundle.

Example:

```powershell
python UFC_prefight_pro.py
```

Useful env vars:
- `UFC_TRAIN_ALL=1` (train on all data; no holdout evaluation)
- `UFC_MODEL_PATH=models/ufc_model_bundle_prefight_pro_<version>.joblib`
- `UFC_SAVE_MODEL_META=1` (write `app.model_artifacts`)
- `UFC_PREDICT_UPCOMING=1` (store upcoming predictions into `app.predictions`)

## Weekly Pipeline (Local)

Runs: ingest → refresh contract → refresh upcoming → train → store predictions.

```powershell
python scripts/weekly_pipeline.py --mode incremental
```

## API (FastAPI)

Run locally:

```powershell
$env:PYTHONPATH = "src"
python -m uvicorn ufc_api.main:app --host 0.0.0.0 --port 8000
```

Key endpoints:
- `GET /health`
- `GET /models/latest`
- `GET /upcoming`
- `GET /upcoming/predictions`
- `GET /completed/predictions`
- `GET /matchups/quick`
- `GET /fighters/search?q=...`
- `POST /predict` (custom matchup)

## Frontend (Next.js)

Pages:
- `/` home (feature hub)
- `/predict` custom matchup + quick matchups
- `/upcoming` upcoming fight predictions
- `/results` completed fights with prediction accuracy

Run locally:

```powershell
cd frontend
copy .env.local.example .env.local
npm install
npm run dev
```

Set `NEXT_PUBLIC_API_BASE_URL` to your API (Cloud Run or local).

## Weekly Automation (GitHub Actions)

Workflow:
- `.github/workflows/weekly_pipeline.yml`

Triggers:
- scheduled weekly: Monday `09:00 UTC`
- manual runs via `workflow_dispatch` (optional `mode` and `limit`)

What it does:
- incremental ingest (including recovery refresh for partial events)
- refresh `contract.*`
- refresh `app.upcoming_fights`
- train model (`UFC_TRAIN_ALL=1`)
- store model metadata + upcoming predictions
- post-run verification (`scripts/verify_weekly_state.py`)
- uploads artifacts (logs, model bundle, debug HTML)

Required GitHub secret:
- `DATABASE_URL`

Optional: auto-deploy API after weekly training (recommended)
- Set repo variable `WEEKLY_DEPLOY_API=1`
- Configure the GCP deploy secrets/vars (see next section)

## Deploy API (Google Cloud Run)

Deployment is Docker-based using `Dockerfile` and Artifact Registry.

Docs:
- `deploy/gcp_cloud_run.md`

Manual GitHub Actions deploy workflow:
- `.github/workflows/deploy_gcp_cloud_run.yml`

Required GitHub secrets:
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `DATABASE_URL`
- `CORS_ORIGINS` (e.g. `https://<your-vercel-domain>`; no trailing slash)
- `CORS_ORIGIN_REGEX` (optional; for preview domains like `https://.*[.]vercel[.]app`)

Required GitHub variables:
- `GCP_PROJECT_ID`
- `GCP_REGION`
- `ARTIFACT_REPO`
- `CLOUD_RUN_SERVICE`

## Troubleshooting

- **Frontend shows “Failed to fetch”**:
  - Check API CORS env vars. `CORS_ORIGINS` must match the browser Origin exactly (no trailing `/`).
  - If using Vercel previews, set `CORS_ORIGIN_REGEX=https://.*[.]vercel[.]app`.

- **Latest completed event looks stale**:
  - Earlier partial ingests can leave an event in `ufc.events` with `0` fights. Incremental ingest now detects and refreshes those events automatically.

- **Debug HTML**:
  - Set `UFC_DEBUG_HTML=1` to save suspicious fight pages into `tmp/` for markup drift diagnosis.
