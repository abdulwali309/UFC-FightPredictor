import json
import logging
import os
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from itertools import combinations
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ufc_ingest.db import get_engine
from ufc_ingest.migrate import ensure_schemas_and_tables
from ufc_model.prefight_pro_runtime import build_runtime, default_model_bundle_path, predict_match


logger = logging.getLogger(__name__)


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


def _norm_name(name: str) -> str:
    return " ".join((name or "").strip().split())


def _resolve_fighter_name(conn, name: str) -> Optional[str]:
    clean = _norm_name(name)
    if not clean:
        return None
    row = conn.execute(
        text(
            """
            SELECT full_name
            FROM ufc.fighters
            WHERE lower(trim(full_name)) = lower(trim(:name))
            LIMIT 1
            """
        ),
        {"name": clean},
    ).fetchone()
    return row[0] if row else None


def _qint(v) -> int:
    """Support both FastAPI Query objects and direct function invocation."""
    return int(getattr(v, "default", v))


def _resolve_model_path(artifact: Optional[dict]) -> Optional[str]:
    model_path = (artifact or {}).get("artifact_uri")
    fallback_path = default_model_bundle_path()
    if model_path and (not model_path.startswith(("http://", "https://", "s3://"))):
        if not os.path.exists(model_path) and fallback_path and os.path.exists(fallback_path):
            model_path = fallback_path
    if not model_path:
        model_path = fallback_path
    return model_path


def _ensure_local_model_path(model_path: Optional[str]) -> str:
    if not model_path:
        raise HTTPException(status_code=500, detail="No model path configured")
    if model_path.startswith(("http://", "https://", "s3://")):
        raise HTTPException(status_code=501, detail="Remote artifact_uri not supported by this API build")
    if not os.path.exists(model_path):
        raise HTTPException(status_code=500, detail=f"Model bundle not found at {model_path!r}")
    return model_path


def _backfill_upcoming_predictions(conn, model_version: str, model_path: str) -> int:
    missing = conn.execute(
        text(
            """
            SELECT
              u.id,
              u.fight_key,
              u.event_id,
              u.scheduled_date,
              u.fighter_1,
              u.fighter_2,
              u.fighter_1_id,
              u.fighter_2_id,
              u.weight_class
            FROM app.upcoming_fights u
            LEFT JOIN app.predictions p
              ON p.fight_key = u.fight_key
             AND p.model_version = :model_version
            WHERE u.is_active = true
              AND p.id IS NULL
            ORDER BY u.scheduled_date ASC NULLS LAST, u.event_name, COALESCE(u.card_order, 9999), u.fight_key
            """
        ),
        {"model_version": model_version},
    ).mappings().fetchall()

    if not missing:
        return 0

    inserted = 0
    for row in missing:
        f1 = row.get("fighter_1")
        f2 = row.get("fighter_2")
        if not f1 or not f2:
            continue
        try:
            prob, _ = predict_match(
                _runtime_ctx(),
                f1,
                f2,
                model_bundle_path=model_path,
                weight_class=row.get("weight_class"),
            )
        except Exception as exc:
            logger.warning("upcoming prediction failed fight_key=%s error=%s", row.get("fight_key"), exc)
            continue

        conn.execute(
            text(
                """
                INSERT INTO app.predictions
                    (model_name, model_version, created_at, upcoming_id, fight_key, event_id, scheduled_date,
                     fighter_1, fighter_2, fighter_1_id, fighter_2_id, weight_class, prob_f1, source)
                VALUES
                    ('prefight_pro', :model_version, now(), :upcoming_id, :fight_key, :event_id, :scheduled_date,
                     :fighter_1, :fighter_2, :fighter_1_id, :fighter_2_id, :weight_class, :prob_f1, :source)
                ON CONFLICT (model_version, fight_key)
                DO UPDATE SET
                    prob_f1 = EXCLUDED.prob_f1,
                    created_at = EXCLUDED.created_at,
                    source = EXCLUDED.source
                """
            ),
            {
                "model_version": model_version,
                "upcoming_id": row.get("id"),
                "fight_key": row.get("fight_key"),
                "event_id": row.get("event_id"),
                "scheduled_date": row.get("scheduled_date"),
                "fighter_1": f1,
                "fighter_2": f2,
                "fighter_1_id": row.get("fighter_1_id"),
                "fighter_2_id": row.get("fighter_2_id"),
                "weight_class": row.get("weight_class"),
                "prob_f1": float(prob),
                "source": "upcoming-auto",
            },
        )
        inserted += 1

    if inserted:
        logger.info("Backfilled %d missing upcoming predictions for model_version=%s", inserted, model_version)
    return inserted


@lru_cache(maxsize=1)
def _runtime_ctx():
    # Heavy but manageable; cached for process lifetime.
    return build_runtime()


app = FastAPI(title="UFCML API", version="0.1.0")


@app.on_event("startup")
def on_startup():
    # Never run DDL/migrations on API startup by default.
    # Cloud Run startup probes expect the server to bind quickly; the weekly pipeline
    # is responsible for keeping schemas/tables up to date.
    if os.getenv("UFC_API_STARTUP_MIGRATE", "0") != "1":
        logger.info("Startup schema check disabled (set UFC_API_STARTUP_MIGRATE=1 to enable).")
        return
    try:
        ensure_schemas_and_tables()
    except SQLAlchemyError as exc:
        logger.warning("Startup schema check skipped due database error: %s", exc)
    except Exception as exc:
        logger.warning("Startup schema check skipped due unexpected error: %s", exc)

# CORS: configure via CORS_ORIGINS="https://your-frontend.com,http://localhost:3000"
def _normalize_cors_origin(origin: str) -> str:
    # Browsers send Origin without a trailing slash. Normalize env values to match.
    return origin.strip().rstrip("/")


origins_env = os.getenv("CORS_ORIGINS", "")
origins = [_normalize_cors_origin(o) for o in origins_env.split(",") if o.strip()]
origin_regex = os.getenv("CORS_ORIGIN_REGEX", "").strip() or None
if origins or origin_regex:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if origins else [],
        allow_origin_regex=origin_regex,
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
              u.fight_key,
              u.event_id,
              u.event_name,
              u.scheduled_date,
              COALESCE(
                u.card_order,
                ROW_NUMBER() OVER (
                  PARTITION BY COALESCE(u.event_id, u.event_name, 'unknown')
                  ORDER BY u.created_at ASC, u.fight_key ASC
                )
              ) AS card_order,
              u.fighter_1,
              u.fighter_2,
              u.fighter_1_id,
              u.fighter_2_id,
              u.weight_class,
              u.source,
              u.is_active,
              u.created_at
            FROM app.upcoming_fights u
            WHERE u.is_active = true
            ORDER BY
              u.scheduled_date ASC NULLS LAST,
              u.event_name,
              COALESCE(
                u.card_order,
                ROW_NUMBER() OVER (
                  PARTITION BY COALESCE(u.event_id, u.event_name, 'unknown')
                  ORDER BY u.created_at ASC, u.fight_key ASC
                )
              ) ASC,
              u.fighter_1,
              u.fighter_2
            LIMIT :limit
            """
        ), {"limit": limit}).mappings().fetchall()

    return {"count": len(rows), "rows": _to_jsonable([dict(r) for r in rows])}


@app.get("/upcoming/predictions")
def upcoming_predictions(limit: int = Query(100, ge=1, le=500)):
    engine = get_engine()
    with engine.begin() as conn:
        art = _latest_model_artifact(conn)
        if not art:
            raise HTTPException(status_code=404, detail="No model artifacts found")
        model_version = art.get("model_version")
        model_path = _ensure_local_model_path(_resolve_model_path(art))
        _backfill_upcoming_predictions(conn, model_version, model_path)

        rows = conn.execute(text(
            """
            SELECT
              u.fight_key,
              u.event_id,
              u.event_name,
              u.scheduled_date,
              COALESCE(
                u.card_order,
                ROW_NUMBER() OVER (
                  PARTITION BY COALESCE(u.event_id, u.event_name, 'unknown')
                  ORDER BY u.created_at ASC, u.fight_key ASC
                )
              ) AS card_order,
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
            ORDER BY
              u.scheduled_date ASC NULLS LAST,
              u.event_name,
              COALESCE(
                u.card_order,
                ROW_NUMBER() OVER (
                  PARTITION BY COALESCE(u.event_id, u.event_name, 'unknown')
                  ORDER BY u.created_at ASC, u.fight_key ASC
                )
              ) ASC,
              u.fighter_1,
              u.fighter_2
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
        fighter_1 = _resolve_fighter_name(conn, req.fighter_1)
        fighter_2 = _resolve_fighter_name(conn, req.fighter_2)
    if not art:
        raise HTTPException(status_code=404, detail="No model artifacts found")
    if not fighter_1:
        raise HTTPException(status_code=400, detail=f"Unknown fighter: {req.fighter_1}")
    if not fighter_2:
        raise HTTPException(status_code=400, detail=f"Unknown fighter: {req.fighter_2}")
    if fighter_1 == fighter_2:
        raise HTTPException(status_code=400, detail="Fighter 1 and Fighter 2 must be different fighters")

    model_version = req.model_version or art.get("model_version")
    model_path = _ensure_local_model_path(_resolve_model_path(art))

    try:
        prob, _ = predict_match(_runtime_ctx(), fighter_1, fighter_2, model_bundle_path=model_path, weight_class=req.weight_class)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "model_version": model_version,
        "fighter_1": fighter_1,
        "fighter_2": fighter_2,
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


@app.get("/matchups/quick")
def quick_matchups(
    limit: int = Query(8, ge=1, le=50),
    recent_events: int = Query(4, ge=2, le=12),
    top_per_weight_class: int = Query(10, ge=4, le=20),
    min_fights: int = Query(3, ge=1, le=20),
    max_per_weight_class: int = Query(2, ge=1, le=6),
    max_elo_gap: int = Query(140, ge=40, le=350),
    rematch_cooldown_days: int = Query(730, ge=60, le=3650),
):
    """Return ELO-based, same-weight-class, diverse quick matchups from recent fighter activity.

    Rules:
    - Fighters are sourced from the last N events (recent activity pool).
    - Pairs are ranked by high average ELO and low ELO gap (closer/more realistic).
    - Rematches are allowed, but filtered out if they happened too recently.
    - Diversity is enforced across weight classes.
    """
    limit = _qint(limit)
    recent_events = _qint(recent_events)
    top_per_weight_class = _qint(top_per_weight_class)
    min_fights = _qint(min_fights)
    max_per_weight_class = _qint(max_per_weight_class)
    max_elo_gap = float(_qint(max_elo_gap))
    rematch_cooldown_days = _qint(rematch_cooldown_days)

    engine = get_engine()
    with engine.connect() as conn:
        recent_event_rows = conn.execute(
            text(
                """
                SELECT e.event_id
                FROM ufc.events e
                WHERE to_date(e.date, 'FMMonth DD, YYYY') IS NOT NULL
                ORDER BY to_date(e.date, 'FMMonth DD, YYYY') DESC
                LIMIT :recent_events
                """
            ),
            {"recent_events": recent_events},
        ).fetchall()
        recent_event_ids = {r[0] for r in recent_event_rows if r and r[0]}

        fight_rows = conn.execute(
            text(
                """
                SELECT
                  f.event_id,
                  to_date(e.date, 'FMMonth DD, YYYY') AS event_date,
                  NULLIF(trim(f.weight_class), '') AS weight_class,
                  f.red_fighter_id,
                  red.full_name AS red_name,
                  f.blue_fighter_id,
                  blue.full_name AS blue_name,
                  f.winner_corner
                FROM ufc.fights f
                JOIN ufc.events e ON e.event_id = f.event_id
                LEFT JOIN ufc.fighters red ON red.fighter_id = f.red_fighter_id
                LEFT JOIN ufc.fighters blue ON blue.fighter_id = f.blue_fighter_id
                WHERE f.red_fighter_id IS NOT NULL
                  AND f.blue_fighter_id IS NOT NULL
                  AND red.full_name IS NOT NULL
                  AND blue.full_name IS NOT NULL
                  AND to_date(e.date, 'FMMonth DD, YYYY') IS NOT NULL
                  AND NULLIF(trim(f.weight_class), '') IS NOT NULL
                ORDER BY to_date(e.date, 'FMMonth DD, YYYY') ASC, f.fight_id ASC
                """
            )
        ).mappings().fetchall()

        upcoming_rows = conn.execute(
            text(
                """
                SELECT fighter_1_id, fighter_2_id
                FROM app.upcoming_fights
                WHERE is_active = true
                  AND fighter_1_id IS NOT NULL
                  AND fighter_2_id IS NOT NULL
                """
            )
        ).mappings().fetchall()

    if not fight_rows:
        return {"count": 0, "rows": []}

    elo = defaultdict(lambda: 1500.0)
    fight_count = defaultdict(int)
    fighter_name: dict[str, str] = {}
    last_fight_date: dict[str, date] = {}
    last_meeting_date: dict[tuple[str, str], date] = {}
    recent_pool_by_wc: dict[str, set[str]] = defaultdict(set)

    def pair_key(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a < b else (b, a)

    for row in fight_rows:
        red_id = row["red_fighter_id"]
        blue_id = row["blue_fighter_id"]
        wc = (row["weight_class"] or "").strip()
        ev_date = row["event_date"]
        if not red_id or not blue_id or not wc or not ev_date:
            continue
        red_name = row["red_name"]
        blue_name = row["blue_name"]

        fighter_name[str(red_id)] = red_name
        fighter_name[str(blue_id)] = blue_name
        fight_count[str(red_id)] += 1
        fight_count[str(blue_id)] += 1
        last_fight_date[str(red_id)] = ev_date
        last_fight_date[str(blue_id)] = ev_date

        pkey = pair_key(str(red_id), str(blue_id))
        last_meeting_date[pkey] = ev_date

        if row["event_id"] in recent_event_ids:
            recent_pool_by_wc[wc].add(str(red_id))
            recent_pool_by_wc[wc].add(str(blue_id))

        red_elo = float(elo[str(red_id)])
        blue_elo = float(elo[str(blue_id)])
        expected_red = 1.0 / (1.0 + (10.0 ** ((blue_elo - red_elo) / 400.0)))
        expected_blue = 1.0 - expected_red

        winner = (row["winner_corner"] or "").strip().lower()
        if winner == "red":
            score_red, score_blue = 1.0, 0.0
        elif winner == "blue":
            score_red, score_blue = 0.0, 1.0
        else:
            score_red = score_blue = 0.5

        k = 24.0
        elo[str(red_id)] = red_elo + k * (score_red - expected_red)
        elo[str(blue_id)] = blue_elo + k * (score_blue - expected_blue)

    upcoming_pairs = {
        pair_key(str(r["fighter_1_id"]), str(r["fighter_2_id"]))
        for r in upcoming_rows
        if r["fighter_1_id"] and r["fighter_2_id"]
    }

    today = datetime.utcnow().date()
    candidate_by_wc: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for wc, fighter_ids in recent_pool_by_wc.items():
        ranked = [
            fid
            for fid in fighter_ids
            if fight_count.get(fid, 0) >= min_fights and fid in fighter_name
        ]
        ranked.sort(
            key=lambda fid: (
                float(elo.get(fid, 1500.0)),
                last_fight_date.get(fid, date.min),
            ),
            reverse=True,
        )
        ranked = ranked[:top_per_weight_class]
        if len(ranked) < 2:
            continue

        for fid_a, fid_b in combinations(ranked, 2):
            key = pair_key(fid_a, fid_b)
            if key in upcoming_pairs:
                continue

            last_meet = last_meeting_date.get(key)
            if last_meet is not None:
                days_since_meet = (today - last_meet).days
                if days_since_meet < rematch_cooldown_days:
                    continue

            elo_a = float(elo.get(fid_a, 1500.0))
            elo_b = float(elo.get(fid_b, 1500.0))
            elo_gap = abs(elo_a - elo_b)
            if elo_gap > max_elo_gap:
                continue

            # Keep fighter_1 as the higher-ELO side for display consistency.
            if elo_b > elo_a:
                fid_a, fid_b = fid_b, fid_a
                elo_a, elo_b = elo_b, elo_a

            avg_elo = (elo_a + elo_b) / 2.0
            freshness = min(
                last_fight_date.get(fid_a, date.min),
                last_fight_date.get(fid_b, date.min),
            )
            score = avg_elo - (1.45 * elo_gap)

            candidate_by_wc[wc].append(
                {
                    "weight_class": wc,
                    "fighter_1_id": fid_a,
                    "fighter_1": fighter_name.get(fid_a),
                    "fighter_2_id": fid_b,
                    "fighter_2": fighter_name.get(fid_b),
                    "fighter_1_last_fight": last_fight_date.get(fid_a),
                    "fighter_2_last_fight": last_fight_date.get(fid_b),
                    "pair_freshness_date": freshness,
                    "elo_f1": round(elo_a, 1),
                    "elo_f2": round(elo_b, 1),
                    "elo_gap": round(abs(elo_a - elo_b), 1),
                    "avg_elo": round(avg_elo, 1),
                    "score": round(score, 3),
                    "last_meeting_date": last_meet,
                }
            )

    for wc in candidate_by_wc:
        candidate_by_wc[wc].sort(
            key=lambda r: (
                r["score"],
                -r["elo_gap"],
                r["pair_freshness_date"] or date.min,
            ),
            reverse=True,
        )

    if not candidate_by_wc:
        return {"count": 0, "rows": []}

    weight_classes = sorted(
        candidate_by_wc.keys(),
        key=lambda wc: candidate_by_wc[wc][0]["score"] if candidate_by_wc[wc] else -1e9,
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    counts_by_wc = defaultdict(int)

    # Round-robin selection gives diversity first.
    while len(selected) < limit:
        progress = False
        for wc in weight_classes:
            if counts_by_wc[wc] >= max_per_weight_class:
                continue
            pool = candidate_by_wc.get(wc) or []
            if not pool:
                continue
            selected.append(pool.pop(0))
            counts_by_wc[wc] += 1
            progress = True
            if len(selected) >= limit:
                break
        if not progress:
            break

    # If limit still not met, fill from leftovers while respecting per-class cap.
    if len(selected) < limit:
        leftovers = []
        for wc, pool in candidate_by_wc.items():
            for item in pool:
                leftovers.append(item)
        leftovers.sort(key=lambda r: r["score"], reverse=True)
        for item in leftovers:
            wc = item["weight_class"]
            if counts_by_wc[wc] >= max_per_weight_class:
                continue
            selected.append(item)
            counts_by_wc[wc] += 1
            if len(selected) >= limit:
                break

    return {"count": len(selected), "rows": _to_jsonable(selected)}


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
              COALESCE(
                f.fight_order,
                ROW_NUMBER() OVER (PARTITION BY f.event_id ORDER BY f.scraped_at ASC, f.fight_id ASC)
              ) AS card_order,
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
            ORDER BY
              to_date(e.date, 'FMMonth DD, YYYY') DESC NULLS LAST,
              COALESCE(
                f.fight_order,
                ROW_NUMBER() OVER (PARTITION BY f.event_id ORDER BY f.scraped_at ASC, f.fight_id ASC)
              ) ASC,
              f.scraped_at DESC
            LIMIT :limit
            """
        ), {"model_version": mv, "limit": limit}).mappings().fetchall()

    return {"model_version": mv, "count": len(rows), "rows": _to_jsonable([dict(r) for r in rows])}
