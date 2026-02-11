"""Enrich fighter bios for a set of fighter_ids."""
import logging
from typing import Iterable, Optional
import os
from sqlalchemy import text
from .scrape_fighter_detail import scrape_fighter
from .normalize import normalize_ufcstats_url

logger = logging.getLogger(__name__)


_BIO_FIELDS = [
    "nickname",
    "ht_inches",
    "wt_lbs",
    "reach_inches",
    "stance",
    "w",
    "l",
    "d",
    "belt",
]


def _needs_enrichment(row, force_belt: bool = False) -> bool:
    if row is None:
        return True
    if force_belt:
        return True
    missing = 0
    data = row._mapping if hasattr(row, "_mapping") else row
    for field in _BIO_FIELDS:
        if data.get(field) is None:
            missing += 1
    # enrich when mostly missing (>=6 of 9)
    return missing >= 6


def enrich_fighters(conn, session, fighter_ids: Iterable[str], force_belt: bool = False) -> int:
    """Fetch fighter-details for missing bios and upsert into ufc.fighters.

    Returns number of fighters enriched.
    """
    if not force_belt and os.getenv("UFC_ENRICH_FORCE_BELT", "").lower() in {"1", "true", "yes"}:
        force_belt = True
    enriched = 0
    for fid in sorted(set([f for f in fighter_ids if f])):
        if fid.startswith("sur_"):
            continue
        row = conn.execute(
            text(
                """
                SELECT fighter_id, full_name, nickname, ht_inches, wt_lbs, reach_inches, stance, w, l, d, belt, url
                FROM ufc.fighters WHERE fighter_id = :fid
                """
            ),
            {"fid": fid},
        ).fetchone()
        if not _needs_enrichment(row, force_belt=force_belt):
            continue

        url = None
        if row is not None:
            data = row._mapping if hasattr(row, "_mapping") else row
            url = data.get("url")
        if not url:
            url = f"http://ufcstats.com/fighter-details/{fid}"
        url = normalize_ufcstats_url(url)

        try:
            fdetail = scrape_fighter(url, session)
        except Exception as e:
            logger.warning("Failed to enrich fighter %s: %s", fid, e)
            continue

        belt = fdetail.get("belt")

        params = {
            "fid": fdetail.get("fighter_id") or fid,
            "fn": fdetail.get("full_name"),
            "nick": fdetail.get("nickname"),
            "ht": fdetail.get("ht_inches"),
            "wt": fdetail.get("wt_lbs"),
            "reach": fdetail.get("reach_inches"),
            "stance": fdetail.get("stance"),
            "w": fdetail.get("w"),
            "l": fdetail.get("l"),
            "d": fdetail.get("d"),
            "belt": belt,
            "url": fdetail.get("url") or url,
        }

        conn.execute(
            text(
                """
                INSERT INTO ufc.fighters (fighter_id, full_name, nickname, ht_inches, wt_lbs, reach_inches, stance, w, l, d, belt, url, scraped_at)
                VALUES (:fid, :fn, :nick, :ht, :wt, :reach, :stance, :w, :l, :d, :belt, :url, CURRENT_TIMESTAMP)
                ON CONFLICT (fighter_id) DO UPDATE SET
                  full_name = COALESCE(EXCLUDED.full_name, ufc.fighters.full_name),
                  nickname = COALESCE(EXCLUDED.nickname, ufc.fighters.nickname),
                  ht_inches = COALESCE(EXCLUDED.ht_inches, ufc.fighters.ht_inches),
                  wt_lbs = COALESCE(EXCLUDED.wt_lbs, ufc.fighters.wt_lbs),
                  reach_inches = COALESCE(EXCLUDED.reach_inches, ufc.fighters.reach_inches),
                  stance = COALESCE(EXCLUDED.stance, ufc.fighters.stance),
                  w = COALESCE(EXCLUDED.w, ufc.fighters.w),
                  l = COALESCE(EXCLUDED.l, ufc.fighters.l),
                  d = COALESCE(EXCLUDED.d, ufc.fighters.d),
                  belt = COALESCE(EXCLUDED.belt, ufc.fighters.belt),
                  url = COALESCE(EXCLUDED.url, ufc.fighters.url)
                """
            ),
            params,
        )
        enriched += 1
    return enriched
