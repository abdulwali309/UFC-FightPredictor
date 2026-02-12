"""Simple CLI for ingest, refresh-contract, export-csv and run-all."""
import argparse
import logging
from .migrate import ensure_schemas_and_tables
from .scrape_events import scrape_events_index
from .scrape_event_detail import scrape_event_fights
from .scrape_fight_detail import scrape_fight, FightDetailParseError
from .enrich_fighters import enrich_fighters
from .http import RateLimitedSession
from .db import get_engine, get_session
from .models import Event, Fighter, Fight, FightTotal, PipelineState
from .refresh_contract import refresh_all
from .export_csv import export_all
from .backfill_fight_order import backfill_fight_order
from datetime import datetime
from typing import Optional
from sqlalchemy import text

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _load_existing_event_state(engine):
    """Return existing event IDs and events that need a refresh (currently 0 fights)."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT e.event_id, COUNT(f.fight_id) AS fight_count
            FROM ufc.events e
            LEFT JOIN ufc.fights f ON f.event_id = e.event_id
            GROUP BY e.event_id
            """
        )).fetchall()

    existing_event_ids = {r[0] for r in rows}
    refresh_event_ids = {r[0] for r in rows if int(r[1] or 0) == 0}
    return existing_event_ids, refresh_event_ids


def ingest(mode: str = 'incremental', limit: Optional[int] = None):
    session_http = RateLimitedSession()
    engine = get_engine()
    db = get_session()

    ensure_schemas_and_tables()

    existing_event_ids, refresh_event_ids = _load_existing_event_state(engine)
    events = list(scrape_events_index(session_http))

    if mode == 'incremental':
        selected = []
        new_count = 0
        refresh_count = 0
        seen = set()
        for ev in events:
            event_id = ev.get('event_id')
            if not event_id or event_id in seen:
                continue
            if event_id not in existing_event_ids:
                selected.append(ev)
                seen.add(event_id)
                new_count += 1
            elif event_id in refresh_event_ids:
                # Recover from earlier partial ingests where event row existed but fights did not.
                selected.append(ev)
                seen.add(event_id)
                refresh_count += 1
        new_events = selected
    else:
        new_events = events
        new_count = len(new_events)
        refresh_count = 0

    if limit:
        new_events = new_events[:limit]

    logger.info(
        'Found %d events total, %d to ingest (%d brand new, %d refresh-needed)',
        len(events), len(new_events), new_count, refresh_count
    )

    for ev in new_events:
        # transaction per event
        with engine.begin() as conn:
            logger.info('Ingesting event %s - %s', ev['event_id'], ev['name'])
            conn.execute(text("INSERT INTO ufc.events (event_id, name, date, location, url, scraped_at) VALUES (:event_id, :name, :date, :location, :url, now()) ON CONFLICT (event_id) DO UPDATE SET name = EXCLUDED.name, date = EXCLUDED.date, location = EXCLUDED.location, url = EXCLUDED.url"), ev)

            fights = scrape_event_fights(ev['url'], session_http)
            logger.info('Found %d fights for event %s', len(fights), ev['event_id'])
            fighter_ids = set()
            for fmeta in fights:
                fight_url = fmeta['url']
                fight_id = fmeta['fight_id']
                try:
                    parsed = scrape_fight(fight_url, session_http)
                    # upsert fighters
                    for corner in ['red', 'blue']:
                        finfo = parsed[corner]
                        if finfo.get('fighter_id'):
                            fighter_ids.add(finfo.get('fighter_id'))
                            conn.execute(
                                text(
                                    "INSERT INTO ufc.fighters (fighter_id, full_name, url, scraped_at) "
                                    "VALUES (:fid, :fn, :url, now()) "
                                    "ON CONFLICT (fighter_id) DO UPDATE SET full_name=EXCLUDED.full_name, url=COALESCE(ufc.fighters.url, EXCLUDED.url)"
                                ),
                                {'fid': finfo.get('fighter_id'), 'fn': finfo.get('name'), 'url': finfo.get('url')}
                            )
                    # upsert fight
                    conn.execute(text("INSERT INTO ufc.fights (fight_id, event_id, red_fighter_id, blue_fighter_id, weight_class, method, round, fight_time, time_format, referee, method_details, winner_corner, fight_order, url, scraped_at) VALUES (:fid, :eid, :rid, :bid, :wc, :method, :round, :ftime, :tformat, :ref, :md, :winner, :forder, :url, now()) ON CONFLICT (fight_id) DO UPDATE SET weight_class=EXCLUDED.weight_class, method=EXCLUDED.method, round=EXCLUDED.round, fight_time=EXCLUDED.fight_time, time_format=EXCLUDED.time_format, referee=EXCLUDED.referee, method_details=EXCLUDED.method_details, winner_corner=EXCLUDED.winner_corner, fight_order=COALESCE(EXCLUDED.fight_order, ufc.fights.fight_order)"), {
                        'fid': fight_id, 'eid': ev['event_id'], 'rid': parsed['red'].get('fighter_id'), 'bid': parsed['blue'].get('fighter_id'), 'wc': parsed['fight_meta'].get('weight_class'), 'method': parsed['fight_meta'].get('method'), 'round': parsed['fight_meta'].get('round'), 'ftime': parsed['fight_meta'].get('fight_time'), 'tformat': parsed['fight_meta'].get('time_format'), 'ref': parsed['fight_meta'].get('referee'), 'md': parsed['fight_meta'].get('method_details'), 'winner': parsed['fight_meta'].get('winner_corner'), 'forder': fmeta.get('fight_order'), 'url': fight_url
                    })
                    # upsert fight_totals for each corner
                    for corner in ['red', 'blue']:
                        corn = parsed[corner]['totals']
                        # integer counts default to 0 when missing; percents and ctrl_seconds remain NULL when missing
                        def int_or_zero(x):
                            return int(x) if x is not None else 0

                        corn_params = {
                            'fight_id': fight_id,
                            'corner': corner,
                            'kd': int_or_zero(corn.get('kd')),
                            'str': int_or_zero(corn.get('str')),
                            'td': int_or_zero(corn.get('td')),
                            'sub': int_or_zero(corn.get('sub')),
                            'sig_str_pct': corn.get('sig_str_pct'),
                            'sub_att': int_or_zero(corn.get('sub_att')),
                            'rev': int_or_zero(corn.get('rev')),
                            'ctrl_seconds': corn.get('ctrl_seconds'),
                            'head_pct': corn.get('head_pct'),
                            'body_pct': corn.get('body_pct'),
                            'leg_pct': corn.get('leg_pct'),
                            'distance_pct': corn.get('distance_pct'),
                            'clinch_pct': corn.get('clinch_pct'),
                            'ground_pct': corn.get('ground_pct'),
                            'total_str_pct': corn.get('total_str_pct'),
                            'sig_str_dist_pct': corn.get('sig_str_dist_pct')
                        }
                        conn.execute(text("INSERT INTO ufc.fight_totals (fight_id, corner, kd, \"str\", td, sub, sig_str_pct, sub_att, rev, ctrl_seconds, head_pct, body_pct, leg_pct, distance_pct, clinch_pct, ground_pct, total_str_pct, sig_str_dist_pct) VALUES (:fight_id, :corner, :kd, :str, :td, :sub, :sig_str_pct, :sub_att, :rev, :ctrl_seconds, :head_pct, :body_pct, :leg_pct, :distance_pct, :clinch_pct, :ground_pct, :total_str_pct, :sig_str_dist_pct) ON CONFLICT (fight_id, corner) DO UPDATE SET kd=EXCLUDED.kd, \"str\"=EXCLUDED.\"str\", td=EXCLUDED.td, sub=EXCLUDED.sub, sig_str_pct=EXCLUDED.sig_str_pct, sub_att=EXCLUDED.sub_att, rev=EXCLUDED.rev, ctrl_seconds=EXCLUDED.ctrl_seconds, head_pct=EXCLUDED.head_pct, body_pct=EXCLUDED.body_pct, leg_pct=EXCLUDED.leg_pct, distance_pct=EXCLUDED.distance_pct, clinch_pct=EXCLUDED.clinch_pct, ground_pct=EXCLUDED.ground_pct, total_str_pct=EXCLUDED.total_str_pct, sig_str_dist_pct=EXCLUDED.sig_str_dist_pct"), corn_params)
                except FightDetailParseError as e:
                    logger.warning('Skipping fight %s due to parse error: %s', fight_id, e)
                    continue
                except Exception as e:
                    logger.exception('Failed to ingest fight %s: %s', fight_id, e)
                    raise
            # enrich fighter bios for fighters encountered in this event
            if fighter_ids:
                enriched = enrich_fighters(conn, session_http, fighter_ids)
                logger.info("Enriched %d fighters for event %s", enriched, ev['event_id'])
        # update pipeline state
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO ufc.pipeline_state (id, last_run_at, last_success_event_id, status) VALUES (1, now(), :eid, 'success') ON CONFLICT (id) DO UPDATE SET last_run_at = EXCLUDED.last_run_at, last_success_event_id = EXCLUDED.last_success_event_id, status = EXCLUDED.status"), {'eid': ev['event_id']})

    logger.info('Ingest complete')


def cli():
    parser = argparse.ArgumentParser(description='UFC ingest CLI')
    sub = parser.add_subparsers(dest='command')

    p_ingest = sub.add_parser('ingest')
    p_ingest.add_argument('--mode', choices=['bootstrap', 'incremental'], default='incremental')
    p_ingest.add_argument('--limit', type=int, default=None, help='Optional limit number of events to ingest (for testing)')

    p_refresh = sub.add_parser('refresh-contract')
    p_validate = sub.add_parser('validate-contract')

    p_export = sub.add_parser('export-csv')

    p_runall = sub.add_parser('run-all')
    p_runall.add_argument('--mode', choices=['bootstrap', 'incremental'], default='incremental')
    p_runall.add_argument('--limit', type=int, default=None, help='Optional limit number of events to ingest (for testing)')
    p_runall.add_argument('--train-cmd', default=None, help='Optional command to invoke training script after refresh')

    p_backfill_order = sub.add_parser('backfill-fight-order')
    p_backfill_order.add_argument('--limit-events', type=int, default=None, help='Optional limit of events to process')
    args = parser.parse_args()

    if args.command == 'ingest':
        ingest(mode=args.mode, limit=getattr(args, 'limit', None))
    elif args.command == 'refresh-contract':
        ensure_schemas_and_tables()
        refresh_all()
    elif args.command == 'validate-contract':
        ensure_schemas_and_tables()
        from .migrate import validate_contract_schema
        ok = validate_contract_schema()
        if ok:
            print('Contract tables validated successfully')
            return
        else:
            print('Contract schema validation failed - check logs')
            return
    elif args.command == 'export-csv':
        paths = export_all()
        print('Exported files:', paths)
    elif args.command == 'run-all':
        ingest(mode=args.mode, limit=getattr(args, 'limit', None))
        refresh_all()
        # Optional: invoke external training command; left as a stub that calls the provided command
        if getattr(args, 'train_cmd', None):
            import subprocess
            logger.info('Running training command: %s', args.train_cmd)
            subprocess.run(args.train_cmd, shell=True, check=False)
        print('Done run-all')
    elif args.command == 'backfill-fight-order':
        events_done, fights_done = backfill_fight_order(limit_events=getattr(args, 'limit_events', None))
        print(f'Backfill done: events={events_done} fights_updated={fights_done}')
    else:
        parser.print_help()


if __name__ == '__main__':
    cli()
