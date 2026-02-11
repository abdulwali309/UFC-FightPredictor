FROM python:3.12-slim

# System deps:
# - libgomp1 is required by xgboost wheels (OpenMP)
RUN apt-get update \
  && apt-get install -y --no-install-recommends libgomp1 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Keep imports working without packaging.
ENV PYTHONPATH=/app/src
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

# Copy source last for better layer caching.
COPY src /app/src
COPY UFC_prefight_pro.py /app/UFC_prefight_pro.py

# Include a default model bundle so /predict works even if DB artifact_uri points elsewhere.
COPY ufc_model_bundle_prefight_pro.joblib /app/ufc_model_bundle_prefight_pro.joblib

# Expose is informational; platform runtime controls published port.
EXPOSE 8000

# Cloud platforms commonly inject $PORT; default to 8000.
CMD ["sh", "-c", "python -m uvicorn ufc_api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
