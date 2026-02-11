import json
import os
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text

from ufc_ingest.db import get_engine
from ufc_model.prefight_pro_runtime import build_runtime, default_model_bundle_path, predict_match


def _to_jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _to_jsonable(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    return v


def _latest_model_version(conn) -> Optional[str]:
    row = conn.execute(text(
        """
        SELECT model_version
        FROM app.model_artifacts
        ORDER BY trained_at DESC NULLS LAST, created_at DESC
        LIMIT 1
        """
    )).fetchone()
    return row[0] if row else None


def _latest_model_artifact(conn) -> Optional[dict]:
    row = conn.execute(text(
        """
        SELECT model_version, artifact_uri, trained_at, created_at
        FROM app.model_artifacts
        ORDER BY trained_at DESC NULLS LAST, created_at DESC
        LIMIT 1
        """
    )).mappings().fetchone()
    return dict(row) if row else None


@lru_cache(maxsize=1)
def _runtime_ctx():
    # Heavy but manageable; cached for process lifetime.
    return build_runtime()


app = FastAPI(title="UFCML API", version="0.1.0")

# CORS: configure via CORS_ORIGINS="https://your-frontend.com,http://localhost:3000"
origins_env = os.getenv("CORS_ORIGINS", "")
origins = [o.strip() for o in origins_env.split(",") if o.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/health")
def health():
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"ok": True}


@app.get("/models/latest")
def latest_model():
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text(
            """
            SELECT
              model_name,
              model_version,
              trained_at,
              dataset_rows,
              feature_count,
              metrics_json,
              artifact_uri,
              created_at
            FROM app.model_artifacts
            ORDER BY trained_at DESC NULLS LAST, created_at DESC
            LIMIT 1
            """
        )).mappings().fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No model artifacts found")

    out = dict(row)
    metrics_raw = out.get("metrics_json")
    if metrics_raw:
        try:
            out["metrics"] = json.loads(metrics_raw)
        except Exception:
            out["metrics"] = None
    out.pop("metrics_json", None)
    return _to_jsonable(out)


@app.get("/upcoming")
def upcoming(limit: int = Query(100, ge=1, le=500)):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT
              fight_key,
              event_id,
              event_name,
              scheduled_date,
              fighter_1,
              fighter_2,
              fighter_1_id,
              fighter_2_id,
              weight_class,
              source,
              is_active,
              created_at
            FROM app.upcoming_fights
            WHERE is_active = true
            ORDER BY scheduled_date ASC NULLS LAST, event_name, fighter_1, fighter_2
            LIMIT :limit
            """
        ), {"limit": limit}).mappings().fetchall()

    return {"count": len(rows), "rows": _to_jsonable([dict(r) for r in rows])}


@app.get("/upcoming/predictions")
def upcoming_predictions(limit: int = Query(100, ge=1, le=500)):
    engine = get_engine()
    with engine.connect() as conn:
        model_version = _latest_model_version(conn)
        if not model_version:
            raise HTTPException(status_code=404, detail="No model artifacts found")

        rows = conn.execute(text(
            """
            SELECT
              u.fight_key,
              u.event_id,
              u.event_name,
              u.scheduled_date,
              u.fighter_1,
              u.fighter_2,
              u.fighter_1_id,
              u.fighter_2_id,
              u.weight_class,
              p.model_version,
              (p.prob_f1::double precision) AS prob_f1,
              CASE
                WHEN p.prob_f1 IS NULL THEN NULL
                ELSE (1.0 - p.prob_f1::double precision)
              END AS prob_f2
            FROM app.upcoming_fights u
            LEFT JOIN app.predictions p
              ON p.fight_key = u.fight_key
             AND p.model_version = :model_version
            WHERE u.is_active = true
            ORDER BY u.scheduled_date ASC NULLS LAST, u.event_name, u.fighter_1, u.fighter_2
            LIMIT :limit
            """
        ), {"model_version": model_version, "limit": limit}).mappings().fetchall()

    return {"model_version": model_version, "count": len(rows), "rows": _to_jsonable([dict(r) for r in rows])}


class PredictRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "fighter_1": "Islam Makhachev",
                    "fighter_2": "Kamaru Usman",
                    "weight_class": "Welterweight",
                    "model_version": None,
                }
            ]
        }
    }

    fighter_1: str = Field(..., description="Fighter name (must match contract/DB naming).")
    fighter_2: str = Field(..., description="Fighter name (must match contract/DB naming).")
    weight_class: Optional[str] = Field(None, description="Optional; if omitted we try to infer from fighters' recent fights.")
    model_version: Optional[str] = Field(None, description="Optional; default is latest app.model_artifacts model_version.")


@app.post("/predict")
def predict(req: PredictRequest):
    engine = get_engine()
    with engine.connect() as conn:
        art = _latest_model_artifact(conn)
    if not art:
        raise HTTPException(status_code=404, detail="No model artifacts found")

    model_version = req.model_version or art.get("model_version")
    # Prefer the latest artifact URI from DB; fall back to env/default.
    model_path = art.get("artifact_uri")
    fallback_path = default_model_bundle_path()
    if model_path and (not model_path.startswith(("http://", "https://", "s3://"))):
        # local filesystem path
        if not os.path.exists(model_path) and fallback_path and os.path.exists(fallback_path):
            model_path = fallback_path
    if not model_path:
        model_path = fallback_path
    if not model_path:
        raise HTTPException(status_code=500, detail="No model path configured")

    # Local file only for now (production will likely download from object storage).
    if (model_path.startswith("http://") or model_path.startswith("https://") or model_path.startswith("s3://")):
        raise HTTPException(status_code=501, detail="Remote artifact_uri not supported by this API build")
    if not os.path.exists(model_path):
        raise HTTPException(status_code=500, detail=f"Model bundle not found at {model_path!r}")

    try:
        prob, _ = predict_match(_runtime_ctx(), req.fighter_1, req.fighter_2, model_bundle_path=model_path, weight_class=req.weight_class)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "model_version": model_version,
        "fighter_1": req.fighter_1,
        "fighter_2": req.fighter_2,
        "weight_class": req.weight_class,
        "prob_f1": float(prob),
        "prob_f2": float(1.0 - float(prob)),
    }


@app.get("/fighters/search")
def fighters_search(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=100)):
    engine = get_engine()
    pat = f"%{q.strip()}%"
    with engine.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT fighter_id, full_name, nickname, stance, ht_inches, wt_lbs, reach_inches, belt
            FROM ufc.fighters
            WHERE full_name ILIKE :pat
            ORDER BY full_name
            LIMIT :limit
            """
        ), {"pat": pat, "limit": limit}).mappings().fetchall()
    return {"count": len(rows), "rows": _to_jsonable([dict(r) for r in rows])}


@app.get("/completed/predictions")
def completed_predictions(
    limit: int = Query(50, ge=1, le=500),
    model_version: Optional[str] = None,
):
    engine = get_engine()
    with engine.connect() as conn:
        mv = model_version or _latest_model_version(conn)
        if not mv:
            raise HTTPException(status_code=404, detail="No model artifacts found")

        rows = conn.execute(text(
            """
            SELECT
              f.fight_id,
              f.event_id,
              e.name AS event_name,
              e.date AS event_date_str,
              to_date(e.date, 'FMMonth DD, YYYY') AS event_date,
              red.full_name AS red_fighter,
              blue.full_name AS blue_fighter,
              f.red_fighter_id,
              f.blue_fighter_id,
              f.weight_class,
              f.method,
              f.round,
              f.fight_time,
              f.time_format,
              f.winner_corner,
              p.model_version,
              (p.prob_f1::double precision) AS prob_f1_raw,
              p.fighter_1 AS p_f1_name,
              p.fighter_2 AS p_f2_name,
              p.fighter_1_id AS p_f1_id,
              p.fighter_2_id AS p_f2_id,
              CASE
                WHEN p.id IS NULL THEN NULL
                WHEN (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL
                      AND p.fighter_1_id = f.red_fighter_id AND p.fighter_2_id = f.blue_fighter_id)
                  THEN (p.prob_f1::double precision)
                WHEN (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL
                      AND p.fighter_1_id = f.blue_fighter_id AND p.fighter_2_id = f.red_fighter_id)
                  THEN (1.0 - p.prob_f1::double precision)
                WHEN (p.fighter_1 = red.full_name AND p.fighter_2 = blue.full_name)
                  THEN (p.prob_f1::double precision)
                WHEN (p.fighter_1 = blue.full_name AND p.fighter_2 = red.full_name)
                  THEN (1.0 - p.prob_f1::double precision)
                ELSE (p.prob_f1::double precision)
              END AS prob_red,
              CASE
                WHEN p.id IS NULL THEN NULL
                ELSE (1.0 - (
                  CASE
                    WHEN (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL
                          AND p.fighter_1_id = f.red_fighter_id AND p.fighter_2_id = f.blue_fighter_id)
                      THEN (p.prob_f1::double precision)
                    WHEN (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL
                          AND p.fighter_1_id = f.blue_fighter_id AND p.fighter_2_id = f.red_fighter_id)
                      THEN (1.0 - p.prob_f1::double precision)
                    WHEN (p.fighter_1 = red.full_name AND p.fighter_2 = blue.full_name)
                      THEN (p.prob_f1::double precision)
                    WHEN (p.fighter_1 = blue.full_name AND p.fighter_2 = red.full_name)
                      THEN (1.0 - p.prob_f1::double precision)
                    ELSE (p.prob_f1::double precision)
                  END
                ))
              END AS prob_blue,
              CASE
                WHEN p.id IS NULL THEN NULL
                WHEN (
                  CASE
                    WHEN (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL
                          AND p.fighter_1_id = f.red_fighter_id AND p.fighter_2_id = f.blue_fighter_id)
                      THEN (p.prob_f1::double precision)
                    WHEN (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL
                          AND p.fighter_1_id = f.blue_fighter_id AND p.fighter_2_id = f.red_fighter_id)
                      THEN (1.0 - p.prob_f1::double precision)
                    WHEN (p.fighter_1 = red.full_name AND p.fighter_2 = blue.full_name)
                      THEN (p.prob_f1::double precision)
                    WHEN (p.fighter_1 = blue.full_name AND p.fighter_2 = red.full_name)
                      THEN (1.0 - p.prob_f1::double precision)
                    ELSE (p.prob_f1::double precision)
                  END
                ) > 0.5 THEN 'red'
                WHEN (
                  CASE
                    WHEN (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL
                          AND p.fighter_1_id = f.red_fighter_id AND p.fighter_2_id = f.blue_fighter_id)
                      THEN (p.prob_f1::double precision)
                    WHEN (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL
                          AND p.fighter_1_id = f.blue_fighter_id AND p.fighter_2_id = f.red_fighter_id)
                      THEN (1.0 - p.prob_f1::double precision)
                    WHEN (p.fighter_1 = red.full_name AND p.fighter_2 = blue.full_name)
                      THEN (p.prob_f1::double precision)
                    WHEN (p.fighter_1 = blue.full_name AND p.fighter_2 = red.full_name)
                      THEN (1.0 - p.prob_f1::double precision)
                    ELSE (p.prob_f1::double precision)
                  END
                ) < 0.5 THEN 'blue'
                ELSE NULL
              END AS predicted_corner,
              CASE
                WHEN p.id IS NULL THEN NULL
                WHEN f.winner_corner NOT IN ('red','blue') THEN NULL
                WHEN (
                  CASE
                    WHEN (
                      CASE
                        WHEN (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL
                              AND p.fighter_1_id = f.red_fighter_id AND p.fighter_2_id = f.blue_fighter_id)
                          THEN (p.prob_f1::double precision)
                        WHEN (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL
                              AND p.fighter_1_id = f.blue_fighter_id AND p.fighter_2_id = f.red_fighter_id)
                          THEN (1.0 - p.prob_f1::double precision)
                        WHEN (p.fighter_1 = red.full_name AND p.fighter_2 = blue.full_name)
                          THEN (p.prob_f1::double precision)
                        WHEN (p.fighter_1 = blue.full_name AND p.fighter_2 = red.full_name)
                          THEN (1.0 - p.prob_f1::double precision)
                        ELSE (p.prob_f1::double precision)
                      END
                    ) > 0.5 THEN 'red'
                    WHEN (
                      CASE
                        WHEN (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL
                              AND p.fighter_1_id = f.red_fighter_id AND p.fighter_2_id = f.blue_fighter_id)
                          THEN (p.prob_f1::double precision)
                        WHEN (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL
                              AND p.fighter_1_id = f.blue_fighter_id AND p.fighter_2_id = f.red_fighter_id)
                          THEN (1.0 - p.prob_f1::double precision)
                        WHEN (p.fighter_1 = red.full_name AND p.fighter_2 = blue.full_name)
                          THEN (p.prob_f1::double precision)
                        WHEN (p.fighter_1 = blue.full_name AND p.fighter_2 = red.full_name)
                          THEN (1.0 - p.prob_f1::double precision)
                        ELSE (p.prob_f1::double precision)
                      END
                    ) < 0.5 THEN 'blue'
                    ELSE NULL
                  END
                ) = f.winner_corner THEN true
                ELSE false
              END AS correct
            FROM ufc.fights f
            JOIN ufc.events e ON e.event_id = f.event_id
            LEFT JOIN ufc.fighters red ON red.fighter_id = f.red_fighter_id
            LEFT JOIN ufc.fighters blue ON blue.fighter_id = f.blue_fighter_id
            LEFT JOIN LATERAL (
              SELECT p.*
              FROM app.predictions p
              WHERE p.model_version = :model_version
                AND p.event_id = f.event_id
                AND (
                  (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL AND p.fighter_1_id = f.red_fighter_id AND p.fighter_2_id = f.blue_fighter_id)
                  OR
                  (p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL AND p.fighter_1_id = f.blue_fighter_id AND p.fighter_2_id = f.red_fighter_id)
                  OR
                  (p.fighter_1 = red.full_name AND p.fighter_2 = blue.full_name)
                  OR
                  (p.fighter_1 = blue.full_name AND p.fighter_2 = red.full_name)
                )
              ORDER BY
                CASE WHEN p.fighter_1_id IS NOT NULL AND p.fighter_2_id IS NOT NULL THEN 0 ELSE 1 END,
                p.created_at DESC
              LIMIT 1
            ) p ON true
            ORDER BY to_date(e.date, 'FMMonth DD, YYYY') DESC NULLS LAST, f.scraped_at DESC
            LIMIT :limit
            """
        ), {"model_version": mv, "limit": limit}).mappings().fetchall()

    return {"model_version": mv, "count": len(rows), "rows": _to_jsonable([dict(r) for r in rows])}
