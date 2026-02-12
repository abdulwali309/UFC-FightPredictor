"""Refresh upcoming fights from UFCStats upcoming events."""
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy import text

from .db import get_engine
from .http import RateLimitedSession
from .migrate import ensure_schemas_and_tables
from .scrape_upcoming_events import scrape_upcoming_events_index, scrape_upcoming_event_fights

logger = logging.getLogger(__name__)


def _norm_name(s: Optional[str]) -> str:
    return " ".join((s or "").strip().lower().split())


def _fight_key(f1_id: Optional[str], f2_id: Optional[str], f1: str, f2: str, scheduled_date, weight_class: Optional[str], event_id: Optional[str]) -> str:
    parts = []
    if f1_id and f2_id:
        parts.extend([f1_id, f2_id])
    else:
        parts.extend([_norm_name(f1), _norm_name(f2)])
    parts.append(event_id or "")
    parts.append(str(scheduled_date) if scheduled_date else "")
    parts.append((weight_class or "").strip())
    return "|".join(parts)


def _parse_event_date(date_str: Optional[str]):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%B %d, %Y").date()
    except Exception:
        return None


def refresh_upcoming_fights(limit_events: Optional[int] = None, session: RateLimitedSession = None) -> int:
    """Fetch upcoming events and fight cards; upsert into app.upcoming_fights."""
    ensure_schemas_and_tables()
    session = session or RateLimitedSession()
    engine = get_engine()

    events = list(scrape_upcoming_events_index(session))
    if limit_events:
        events = events[:limit_events]
    logger.info("Found %d upcoming events", len(events))

    # deactivate stale rows by date
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE app.upcoming_fights SET is_active = false "
            "WHERE scheduled_date IS NOT NULL AND scheduled_date < CURRENT_DATE"
        ))

    # deactivate rows that now exist in completed events (by event id OR event name + date match)
    with engine.begin() as conn:
        conn.execute(text(
            """
            UPDATE app.upcoming_fights u
            SET is_active = false
            FROM ufc.events e
            WHERE u.is_active = true
              AND (
                (u.event_id IS NOT NULL AND e.event_id = u.event_id)
                OR (
                  u.event_name IS NOT NULL
                  AND e.name = u.event_name
                  AND u.scheduled_date IS NOT NULL
                  AND to_date(e.date, 'FMMonth DD, YYYY') = u.scheduled_date
                )
              )
            """
        ))

    total = 0
    with engine.begin() as conn:
        for ev in events:
            fights = scrape_upcoming_event_fights(ev["url"], session)
            scheduled_date = _parse_event_date(ev.get("date"))
            for f in fights:
                if not f.get("fighter_1") or not f.get("fighter_2"):
                    continue
                fight_key = _fight_key(
                    f.get("fighter_1_id"), f.get("fighter_2_id"),
                    f.get("fighter_1"), f.get("fighter_2"),
                    scheduled_date, f.get("weight_class"),
                    ev.get("event_id")
                )
                conn.execute(text(
                    """
                    INSERT INTO app.upcoming_fights
                        (fight_key, event_id, fighter_1_id, fighter_2_id, fighter_1, fighter_2, weight_class, card_order, scheduled_date, event_name, source, is_active, created_at)
                    VALUES
                        (:fight_key, :event_id, :fighter_1_id, :fighter_2_id, :fighter_1, :fighter_2, :weight_class, :card_order, :scheduled_date, :event_name, :source, true, now())
                    ON CONFLICT (fight_key)
                    DO UPDATE SET
                        event_id = EXCLUDED.event_id,
                        fighter_1_id = EXCLUDED.fighter_1_id,
                        fighter_2_id = EXCLUDED.fighter_2_id,
                        weight_class = EXCLUDED.weight_class,
                        card_order = EXCLUDED.card_order,
                        scheduled_date = EXCLUDED.scheduled_date,
                        event_name = EXCLUDED.event_name,
                        source = EXCLUDED.source,
                        is_active = true,
                        created_at = now()
                    """
                ), {
                    "fight_key": fight_key,
                    "event_id": ev.get("event_id"),
                    "fighter_1_id": f.get("fighter_1_id"),
                    "fighter_2_id": f.get("fighter_2_id"),
                    "fighter_1": f["fighter_1"],
                    "fighter_2": f["fighter_2"],
                    "weight_class": f.get("weight_class"),
                    "card_order": f.get("card_order"),
                    "scheduled_date": scheduled_date,
                    "event_name": ev.get("name"),
                    "source": "ufcstats_upcoming",
                })
                total += 1

    logger.info("Upserted %d upcoming fights", total)
    return total
