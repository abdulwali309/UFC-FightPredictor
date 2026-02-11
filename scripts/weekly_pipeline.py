import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ufc_ingest.cli import ingest
from ufc_ingest.refresh_contract import refresh_all
from ufc_ingest.migrate import ensure_schemas_and_tables
from ufc_ingest.upcoming import refresh_upcoming_fights


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Weekly UFC pipeline (ingest -> refresh -> train -> predict)")
    parser.add_argument("--mode", choices=["bootstrap", "incremental"], default="incremental")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit number of events to ingest")
    parser.add_argument("--model-dir", default=os.path.join(ROOT, "models"), help="Directory to store model artifacts")
    parser.add_argument("--model-version", default=None, help="Override model version string")
    args = parser.parse_args()

    ensure_schemas_and_tables()

    ingest(mode=args.mode, limit=args.limit)
    refresh_all()
    refresh_upcoming_fights(limit_events=None)

    os.makedirs(args.model_dir, exist_ok=True)
    model_version = args.model_version or f"prefight_pro_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    model_path = os.path.join(args.model_dir, f"ufc_model_bundle_prefight_pro_{model_version}.joblib")

    # Train + persist model + store upcoming predictions
    os.environ["UFC_INTERACTIVE"] = "0"
    os.environ["UFC_TRAIN_ALL"] = "1"
    os.environ["UFC_SAVE_MODEL_META"] = "1"
    os.environ["UFC_PREDICT_UPCOMING"] = "1"
    os.environ["UFC_MODEL_VERSION"] = model_version
    os.environ["UFC_MODEL_PATH"] = model_path
    # Optional: UFC_MODEL_URI can point to object storage path

    # Importing the module runs training with the env vars above
    import UFC_prefight_pro  # noqa: F401


if __name__ == "__main__":
    main()
