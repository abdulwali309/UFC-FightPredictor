# Deploy FastAPI To Google Cloud Run (Artifact Registry + Cloud Run)

This is a clean, low-cost path for this project:
- Container image in Artifact Registry
- API hosted on Cloud Run (scale to zero)
- Supabase Postgres via `DATABASE_URL`

## Prereqs

- Google Cloud account + billing enabled
- `gcloud` CLI installed and authenticated (`gcloud auth login`)
- Docker installed

## 1) Set Project + Region

Pick values and export them once:

```bash
gcloud config set project <PROJECT_ID>
gcloud config set run/region us-central1
```

Enable required APIs:

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
```

## 2) Create Artifact Registry Repo

```bash
gcloud artifacts repositories create ufcml-api \
  --repository-format=docker \
  --location=us-central1 \
  --description="UFCML API images"
```

Configure Docker auth for Artifact Registry:

```bash
gcloud auth configure-docker us-central1-docker.pkg.dev
```

## 3) Build + Push Image

From repo root:

```bash
docker build -t us-central1-docker.pkg.dev/<PROJECT_ID>/ufcml-api/ufc-api:latest .
docker push us-central1-docker.pkg.dev/<PROJECT_ID>/ufcml-api/ufc-api:latest
```

You can also use Cloud Build:

```bash
gcloud builds submit --tag us-central1-docker.pkg.dev/<PROJECT_ID>/ufcml-api/ufc-api:latest .
```

## 4) Deploy Cloud Run Service

`DATABASE_URL` should be your Supabase connection string with `sslmode=require`.

```bash
gcloud run deploy ufcml-api \
  --image us-central1-docker.pkg.dev/<PROJECT_ID>/ufcml-api/ufc-api:latest \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8000 \
  --min-instances 0 \
  --max-instances 3 \
  --cpu 1 \
  --memory 1Gi \
  --set-env-vars DATABASE_URL='<DATABASE_URL>',CORS_ORIGINS='https://your-frontend.vercel.app,http://localhost:3000'
```

Verify:

- `https://<cloud-run-url>/health`
- `https://<cloud-run-url>/docs`

## 5) Redeploy On New Image

Build + push a new image tag, then:

```bash
gcloud run deploy ufcml-api \
  --image us-central1-docker.pkg.dev/<PROJECT_ID>/ufcml-api/ufc-api:latest \
  --region us-central1
```

## 6) GitHub Actions Deploy (Optional)

Workflow file: `.github/workflows/deploy_gcp_cloud_run.yml`

Set GitHub repository secrets:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `DATABASE_URL`
- `CORS_ORIGINS` (optional)

Set GitHub repository variables:

- `GCP_PROJECT_ID`
- `GCP_REGION` (example: `us-central1`)
- `ARTIFACT_REPO` (example: `ufcml-api`)
- `CLOUD_RUN_SERVICE` (example: `ufcml-api`)

Then run the workflow manually from GitHub Actions.
