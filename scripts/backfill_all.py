import os
import sys
import time
from typing import Dict

from sqlalchemy import text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ufc_ingest.cli import ingest
from ufc_ingest.db import get_engine
from ufc_ingest.migrate import ensure_schemas_and_tables, validate_contract_schema
from ufc_ingest.refresh_contract import refresh_all
from ufc_ingest.scrape_events import scrape_events_index


def _get_counts() -> Dict[str, int]:
    engine = get_engine()
    with engine.connect() as conn:
        events = conn.execute(text("SELECT COUNT(*) FROM ufc.events")).scalar() or 0
        fights = conn.execute(text("SELECT COUNT(*) FROM ufc.fights")).scalar() or 0
        totals = conn.execute(text("SELECT COUNT(*) FROM ufc.fight_totals")).scalar() or 0
        fighters = conn.execute(text("SELECT COUNT(*) FROM ufc.fighters")).scalar() or 0
        missing_fighters = conn.execute(
            text("SELECT COUNT(*) FROM ufc.fights WHERE red_fighter_id IS NULL OR blue_fighter_id IS NULL")
        ).scalar() or 0
    return {
        "events": int(events),
        "fights": int(fights),
        "fight_totals": int(totals),
        "fighters": int(fighters),
        "missing_fighters": int(missing_fighters),
    }


def main():
    # Safety: enable debug HTML saving for suspicious fight pages
    os.environ.setdefault("UFC_DEBUG_HTML", "1")

    batch_size = int(os.getenv("UFC_BACKFILL_BATCH", "25"))
    sleep_seconds = float(os.getenv("UFC_BACKFILL_SLEEP", "0.5"))
    max_batches_env = os.getenv("UFC_BACKFILL_MAX_BATCHES")
    max_events_env = os.getenv("UFC_BACKFILL_MAX_EVENTS")
    max_batches = int(max_batches_env) if max_batches_env else None
    max_events = int(max_events_env) if max_events_env else None

    ensure_schemas_and_tables()

    total_events = len(list(scrape_events_index()))
    counts = _get_counts()
    remaining = max(0, total_events - counts["events"])

    print(f"Total events on UFCStats: {total_events}")
    print(f"Already ingested events: {counts['events']}")
    print(f"Remaining events to ingest: {remaining}")
    print(f"Batch size: {batch_size}, sleep: {sleep_seconds}s")

    batch_num = 0
    stopped_early = False
    while remaining > 0:
        batch_num += 1
        if max_batches is not None and batch_num > max_batches:
            print(f"Stopping early due to UFC_BACKFILL_MAX_BATCHES={max_batches}")
            stopped_early = True
            break
        if max_events is not None and counts["events"] >= max_events:
            print(f"Stopping early due to UFC_BACKFILL_MAX_EVENTS={max_events}")
            stopped_early = True
            break
        print(f"\n=== Batch {batch_num} ===")
        ingest(mode="incremental", limit=batch_size)
        counts = _get_counts()
        remaining = max(0, total_events - counts["events"])
        coverage = 0.0
        if counts["fights"] > 0:
            coverage = counts["fight_totals"] / float(counts["fights"] * 2)
        print(
            f"Progress: events={counts['events']}/{total_events}, "
            f"fights={counts['fights']}, totals={counts['fight_totals']} "
            f"(coverage {coverage:.1%}), fighters={counts['fighters']}, "
            f"missing_fighters={counts['missing_fighters']}"
        )
        if remaining > 0:
            time.sleep(sleep_seconds)

    if remaining == 0:
        print("\nBackfill complete. Refreshing contract tables...")
    else:
        print(f"\nBackfill paused with {remaining} events remaining. Refreshing contract tables...")
    refresh_all()

    print("Validating contract schema...")
    ok = validate_contract_schema()
    print("Contract schema validation:", "PASS" if ok else "FAIL")

    counts = _get_counts()
    coverage = 0.0
    if counts["fights"] > 0:
        coverage = counts["fight_totals"] / float(counts["fights"] * 2)
    print(
        "\nFinal counts: "
        f"events={counts['events']}, fights={counts['fights']}, "
        f"totals={counts['fight_totals']} (coverage {coverage:.1%}), "
        f"fighters={counts['fighters']}, missing_fighters={counts['missing_fighters']}"
    )


if __name__ == "__main__":
    main()
