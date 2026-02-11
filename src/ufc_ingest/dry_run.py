"""Dry-run scraper: fetch events/fights/fighters and print the DB upsert parameter dicts

Usage: python -m ufc_ingest.dry_run --limit 2
"""
import argparse
import json
import logging
from .http import RateLimitedSession
from .scrape_events import scrape_events_index
from .scrape_event_detail import scrape_event_fights
from .scrape_fight_detail import scrape_fight, FightDetailParseError
from .scrape_fighter_detail import scrape_fighter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run(limit: int = 1):
    session_http = RateLimitedSession()

    try:
        events = list(scrape_events_index(session_http))
        if limit:
            events = events[:limit]
    except Exception as e:
        logger.warning('Network fetch failed (%s). Falling back to local sample fight HTML for dry-run.', e)
        # build a synthetic event/fight based on the test fixture
        with open('tests/data/sample_fight.html', 'r', encoding='utf8') as f:
            html = f.read()
        # We will call scrape_fight using a fake session whose get() returns an object with .text
        class FakeResp:
            def __init__(self, text):
                self.text = text
            def raise_for_status(self):
                return None

        class FakeSession:
            def __init__(self, text):
                self._text = text
            def get(self, url, timeout=20, **kwargs):
                return FakeResp(self._text)

        fake_session = FakeSession(html)
        parsed = scrape_fight('http://local/sample', fake_session)
        # create synthetic event/fight meta
        events = [{'event_id': 'SAMPLE_EVT_1', 'name': 'Sample Event (dry-run)', 'date': None, 'location': None, 'url': 'http://local/sample'}]
        fights = [{'fight_id': 'SAMPLE_FIGHT_1', 'url': 'http://local/sample', 'red': parsed['red'].get('name'), 'blue': parsed['blue'].get('name')}]

        out = {'events': []}
        ev = events[0]
        ev_entry = {'event': ev, 'fights': []}
        logger.info('Dry-run: using sample fight for event %s - %s', ev['event_id'], ev['name'])

        # Use the parsed dict directly
        fight_id = fights[0]['fight_id']
        fight_url = fights[0]['url']
        fighter_upserts = []
        for corner in ['red', 'blue']:
            finfo = parsed[corner]
            if finfo.get('fighter_id'):
                # do not attempt external fighter page fetch in offline mode
                fdetail = {'fighter_id': finfo.get('fighter_id'), 'full_name': finfo.get('name')}
                fighter_upserts.append({
                    'sql': "INSERT INTO ufc.fighters (fighter_id, full_name, nickname, ht_inches, wt_lbs, reach_inches, stance, url, scraped_at) ... ON CONFLICT (fighter_id) DO UPDATE SET full_name=EXCLUDED.full_name",
                    'params': {
                        'fid': fdetail['fighter_id'], 'fn': fdetail.get('full_name'), 'nick': fdetail.get('nickname'), 'ht': fdetail.get('ht_inches'), 'wt': fdetail.get('wt_lbs'), 'reach': fdetail.get('reach_inches'), 'stance': fdetail.get('stance'), 'url': fdetail.get('url')
                    }
                })

        fight_upsert = {
            'sql': 'INSERT INTO ufc.fights (...) ON CONFLICT (fight_id) DO UPDATE ...',
            'params': {
                'fid': fight_id,
                'eid': ev['event_id'],
                'rid': parsed['red'].get('fighter_id'),
                'bid': parsed['blue'].get('fighter_id'),
                'wc': parsed['fight_meta'].get('weight_class'),
                'method': parsed['fight_meta'].get('method'),
                'round': parsed['fight_meta'].get('round'),
                'ftime': parsed['fight_meta'].get('fight_time'),
                'tformat': parsed['fight_meta'].get('time_format'),
                'ref': parsed['fight_meta'].get('referee'),
                'md': parsed['fight_meta'].get('method_details'),
                'winner': parsed['fight_meta'].get('winner_corner'),
                'url': fight_url
            }
        }

        totals_upserts = []
        def int_or_zero(x):
            return int(x) if x is not None else 0

        for corner in ['red', 'blue']:
            corn = parsed[corner]['totals']
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
            totals_upserts.append({'sql': 'INSERT INTO ufc.fight_totals (...)', 'params': corn_params})

        ev_entry['fights'].append({'fight_meta': fights[0], 'fighter_upserts': fighter_upserts, 'fight_upsert': fight_upsert, 'totals_upserts': totals_upserts})
        out['events'].append(ev_entry)
        print(json.dumps(out, indent=2, default=str))
        return

    out = {'events': []}
    for ev in events:
        ev_entry = {'event': ev, 'fights': []}
        logger.info('Dry-run: processing event %s - %s', ev['event_id'], ev['name'])
        fights = scrape_event_fights(ev['url'], session_http)
        for fmeta in fights:
            fight_id = fmeta['fight_id']
            fight_url = fmeta['url']
            try:
                parsed = scrape_fight(fight_url, session_http)
            except FightDetailParseError as e:
                logger.warning('Skipping fight %s due to parse error: %s', fight_id, e)
                continue
            except Exception as e:
                logger.exception('Failed to scrape fight %s: %s', fight_id, e)
                continue

            # build fighter upsert params
            fighter_upserts = []
            for corner in ['red', 'blue']:
                finfo = parsed[corner]
                if finfo.get('fighter_id'):
                    if finfo.get('url'):
                        try:
                            fdetail = scrape_fighter(finfo.get('url'), session_http)
                        except Exception:
                            fdetail = {'fighter_id': finfo.get('fighter_id'), 'full_name': finfo.get('name')}
                    else:
                        fdetail = {'fighter_id': finfo.get('fighter_id'), 'full_name': finfo.get('name')}
                    fighter_upserts.append({
                        'sql': "INSERT INTO ufc.fighters (fighter_id, full_name, nickname, ht_inches, wt_lbs, reach_inches, stance, url, scraped_at) ... ON CONFLICT (fighter_id) DO UPDATE SET full_name=EXCLUDED.full_name",
                        'params': {
                            'fid': fdetail['fighter_id'], 'fn': fdetail.get('full_name'), 'nick': fdetail.get('nickname'), 'ht': fdetail.get('ht_inches'), 'wt': fdetail.get('wt_lbs'), 'reach': fdetail.get('reach_inches'), 'stance': fdetail.get('stance'), 'url': fdetail.get('url')
                        }
                    })

            # build fight upsert params
            fight_upsert = {
                'sql': 'INSERT INTO ufc.fights (...) ON CONFLICT (fight_id) DO UPDATE ...',
                'params': {
                    'fid': fight_id,
                    'eid': ev['event_id'],
                    'rid': parsed['red'].get('fighter_id'),
                    'bid': parsed['blue'].get('fighter_id'),
                    'wc': parsed['fight_meta'].get('weight_class'),
                    'method': parsed['fight_meta'].get('method'),
                    'round': parsed['fight_meta'].get('round'),
                    'ftime': parsed['fight_meta'].get('fight_time'),
                    'tformat': parsed['fight_meta'].get('time_format'),
                    'ref': parsed['fight_meta'].get('referee'),
                    'md': parsed['fight_meta'].get('method_details'),
                    'winner': parsed['fight_meta'].get('winner_corner'),
                    'url': fight_url
                }
            }

            # build fight_totals upserts
            totals_upserts = []
            def int_or_zero(x):
                return int(x) if x is not None else 0

            for corner in ['red', 'blue']:
                corn = parsed[corner]['totals']
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
                totals_upserts.append({'sql': 'INSERT INTO ufc.fight_totals (...)', 'params': corn_params})

            ev_entry['fights'].append({'fight_meta': fmeta, 'fighter_upserts': fighter_upserts, 'fight_upsert': fight_upsert, 'totals_upserts': totals_upserts})

        out['events'].append(ev_entry)
    print(json.dumps(out, indent=2, default=str))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=1)
    args = parser.parse_args()
    run(limit=args.limit)
