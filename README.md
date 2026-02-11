# UFC Fight Predictor

End-to-end UFC prediction system:
- Scrapes UFCStats into canonical Postgres tables (schema `ufc`)
- Materializes stable contract tables for ML (schema `contract`)
- Scrapes upcoming events/cards (schema `app`)
- Trains a pre-fight model from Postgres (`UFC_prefight_pro.py`)
- Stores predictions for upcoming fights in Postgres (`app.predictions`)

## Quickstart (Postgres)

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Set `DATABASE_URL` in `.env` (Supabase recommended).

3. Initialize schemas/tables:

```powershell
$env:PYTHONPATH = "src"
python -m ufc_ingest.cli validate-contract
```

4. Backfill (incremental in batches):

```powershell
$env:PYTHONPATH = "src"
python scripts/backfill_all.py
```

5. Run the weekly pipeline (incremental ingest + refresh contract + refresh upcoming fights + train + store predictions):

```powershell
python scripts/weekly_pipeline.py
```

## API (FastAPI)

Start the API locally:

```powershell
$env:PYTHONPATH = "src"
python -m uvicorn ufc_api.main:app --host 0.0.0.0 --port 8000
```

Endpoints:
- `GET /health`
- `GET /models/latest`
- `GET /upcoming`
- `GET /upcoming/predictions`
- `POST /predict`
- `GET /fighters/search`
- `GET /completed/predictions`

## Deploy API (Google Cloud Run)

Preferred free/low-cost production path:
- Build image from this repo (`Dockerfile`)
- Push to Artifact Registry
- Deploy to Cloud Run with `min-instances=0`

Detailed guide:
- `deploy/gcp_cloud_run.md`

GitHub Actions deploy workflow:
- `.github/workflows/deploy_gcp_cloud_run.yml`

Required GitHub secrets:
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `DATABASE_URL`
- `CORS_ORIGINS` (optional)

Required GitHub variables:
- `GCP_PROJECT_ID`
- `GCP_REGION`
- `ARTIFACT_REPO`
- `CLOUD_RUN_SERVICE`

## Notes

- Canonical tables live under `ufc.*`.
- Contract tables live under `contract.*` and match the legacy CSV schema exactly.
- Website-facing state lives under `app.*` (`upcoming_fights`, `predictions`, `model_artifacts`).
- UFCStats requests must use `http://ufcstats.com` (no https/www).
