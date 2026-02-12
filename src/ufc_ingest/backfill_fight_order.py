"""Backfill ufc.fights.fight_order by scraping event fight-list order."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

from .db import get_engine
from .http import RateLimitedSession
from .migrate import ensure_schemas_and_tables
from .scrape_event_detail import scrape_event_fights

logger = logging.getLogger(__name__)


def backfill_fight_order(limit_events: Optional[int] = None) -> tuple[int, int]:
    """Populate fight_order for existing fights.

    Returns:
        (events_processed, fights_updated)
    """
    ensure_schemas_and_tables()
    engine = get_engine()
    http = RateLimitedSession()

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT e.event_id, e.url
                FROM ufc.events e
                JOIN ufc.fights f ON f.event_id = e.event_id
                WHERE e.url IS NOT NULL
                GROUP BY e.event_id, e.url, e.date
                ORDER BY to_date(e.date, 'FMMonth DD, YYYY') DESC NULLS LAST, e.event_id
                """
            )
        ).fetchall()

    if limit_events is not None:
        rows = rows[:limit_events]

    events_processed = 0
    fights_updated = 0

    for event_id, event_url in rows:
        try:
            fights = scrape_event_fights(event_url, http)
            if not fights:
                logger.warning("No fights found for event %s", event_id)
                continue

            with engine.begin() as conn:
                for f in fights:
                    order = f.get("fight_order")
                    if order is None:
                        continue
                    result = conn.execute(
                        text(
                            """
                            UPDATE ufc.fights
                            SET fight_order = :fight_order
                            WHERE event_id = :event_id
                              AND fight_id = :fight_id
                              AND (fight_order IS NULL OR fight_order != :fight_order)
                            """
                        ),
                        {
                            "fight_order": int(order),
                            "event_id": event_id,
                            "fight_id": f["fight_id"],
                        },
                    )
                    fights_updated += int(result.rowcount or 0)

            events_processed += 1
            if events_processed % 25 == 0:
                logger.info("Backfill progress: events=%d fights_updated=%d", events_processed, fights_updated)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed fight-order backfill for event %s: %s", event_id, exc)

    logger.info("Backfill complete: events=%d fights_updated=%d", events_processed, fights_updated)
    return events_processed, fights_updated
