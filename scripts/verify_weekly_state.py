import os
import sys
from sqlalchemy import text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ufc_ingest.db import get_engine


def main() -> int:
    engine = get_engine()
    with engine.connect() as conn:
        latest_model = conn.execute(
            text(
                """
                SELECT model_version
                FROM app.model_artifacts
                ORDER BY trained_at DESC NULLS LAST, created_at DESC
                LIMIT 1
                """
            )
        ).scalar()

        if not latest_model:
            print("FAIL: no rows in app.model_artifacts")
            return 1

        totals = conn.execute(
            text(
                """
                SELECT COUNT(*) AS upcoming_active
                FROM app.upcoming_fights
                WHERE is_active = true
                """
            )
        ).scalar() or 0

        predicted = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM app.upcoming_fights u
                JOIN app.predictions p
                  ON p.fight_key = u.fight_key
                 AND p.model_version = :model_version
                WHERE u.is_active = true
                """
            ),
            {"model_version": latest_model},
        ).scalar() or 0

        missing = int(totals) - int(predicted)
        print(
            f"latest_model={latest_model} "
            f"upcoming_active={int(totals)} "
            f"predicted_for_latest={int(predicted)} "
            f"missing={missing}"
        )

        if totals > 0 and missing > 0:
            print("FAIL: active upcoming fights missing predictions for latest model")
            return 1

    print("PASS: weekly state checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
