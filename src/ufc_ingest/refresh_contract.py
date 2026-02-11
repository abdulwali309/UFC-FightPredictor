"""Refresh contract.* CSV tables from canonical tables in schema ufc."""
from sqlalchemy import text
from .db import get_engine, get_session
from .models import Event, Fighter, Fight, FightTotal
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def refresh_events_csv():
    engine = get_engine()
    with engine.begin() as conn:
        # Supabase can enforce aggressive statement timeouts; contract refresh is batchy.
        conn.execute(text("SET LOCAL statement_timeout = 0"))
        rows = conn.execute(text("SELECT event_id, name, date, location FROM ufc.events"))
        for ev in rows:
            conn.execute(text(
                "INSERT INTO contract.events_csv (\"Event_Id\", \"Name\", \"Date\", \"Location\") VALUES (:id, :name, :date, :loc)"
                " ON CONFLICT (\"Event_Id\") DO UPDATE SET \"Name\" = EXCLUDED.\"Name\", \"Date\" = EXCLUDED.\"Date\", \"Location\" = EXCLUDED.\"Location\""
            ), {'id': ev.event_id, 'name': ev.name, 'date': ev.date, 'loc': ev.location})
    logger.info('Refreshed contract.events_csv')


def refresh_fighters_csv():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout = 0"))
        rows = conn.execute(text("SELECT fighter_id, full_name, nickname, ht_inches, wt_lbs, reach_inches, stance, w, l, d, belt FROM ufc.fighters"))
        for f in rows:
            conn.execute(text(
                "INSERT INTO contract.fighters_csv (\"Full Name\", \"Nickname\", \"Ht.\", \"Wt.\", \"Reach\", \"Stance\", \"W\", \"L\", \"D\", \"Belt\") "
                "VALUES (:name, :nick, :ht, :wt, :reach, :stance, :w, :l, :d, :belt) "
                "ON CONFLICT (\"Full Name\") DO UPDATE SET \"Nickname\" = EXCLUDED.\"Nickname\", \"Ht.\" = EXCLUDED.\"Ht.\", \"Wt.\" = EXCLUDED.\"Wt.\", \"Reach\" = EXCLUDED.\"Reach\", \"Stance\" = EXCLUDED.\"Stance\", \"W\" = EXCLUDED.\"W\", \"L\" = EXCLUDED.\"L\", \"D\" = EXCLUDED.\"D\", \"Belt\" = EXCLUDED.\"Belt\""
            ), {'name': f.full_name, 'nick': f.nickname, 'ht': float(f.ht_inches) if f.ht_inches is not None else None,
               'wt': float(f.wt_lbs) if f.wt_lbs is not None else None, 'reach': float(f.reach_inches) if f.reach_inches is not None else None,
               'stance': f.stance, 'w': f.w, 'l': f.l, 'd': f.d, 'belt': bool(f.belt) if f.belt is not None else False})
    logger.info('Refreshed contract.fighters_csv')


def refresh_fights_csv():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout = 0"))
        # For each fight, assemble a row
        fights = conn.execute(text("SELECT * FROM ufc.fights")).fetchall()
        for f in fights:
            # get fight_totals for both corners
            red = conn.execute(text("SELECT * FROM ufc.fight_totals WHERE fight_id = :fid AND corner='red'"), {'fid': f.fight_id}).fetchone()
            blue = conn.execute(text("SELECT * FROM ufc.fight_totals WHERE fight_id = :fid AND corner='blue'"), {'fid': f.fight_id}).fetchone()
            # get fighter names
            red_name = conn.execute(text("SELECT full_name FROM ufc.fighters WHERE fighter_id = :rid"), {'rid': f.red_fighter_id}).scalar() if f.red_fighter_id else None
            blue_name = conn.execute(text("SELECT full_name FROM ufc.fighters WHERE fighter_id = :bid"), {'bid': f.blue_fighter_id}).scalar() if f.blue_fighter_id else None
            # compute results
            if f.winner_corner == 'red':
                res1, res2 = 'W', 'L'
            elif f.winner_corner == 'blue':
                res1, res2 = 'L', 'W'
            elif f.winner_corner == 'draw':
                res1, res2 = 'D', 'D'
            elif f.winner_corner == 'nc':
                res1, res2 = 'NC', 'NC'
            else:
                res1, res2 = None, None

            # integer counts should default to 0 when missing; percents and ctrl_seconds stay NULL if missing
            def _int_or_zero(x):
                return int(x) if x is not None else 0

            params = {
                'fighter_1': red_name, 'fighter_2': blue_name,
                'kd_1': _int_or_zero(red.kd) if red is not None else 0,
                'kd_2': _int_or_zero(blue.kd) if blue is not None else 0,
                'str_1': _int_or_zero(red.str) if red is not None else 0,
                'str_2': _int_or_zero(blue.str) if blue is not None else 0,
                'td_1': _int_or_zero(red.td) if red is not None else 0,
                'td_2': _int_or_zero(blue.td) if blue is not None else 0,
                'sub_1': _int_or_zero(red.sub) if red is not None else 0,
                'sub_2': _int_or_zero(blue.sub) if blue is not None else 0,
                'weight_class': f.weight_class, 'method': f.method, 'round': f.round,
                'fight_time': f.fight_time, 'event_id': f.event_id, 'result_1': res1, 'result_2': res2,
                'time_format': f.time_format, 'referee': f.referee, 'method_details': f.method_details,
                'sig_str_pct_1': float(red.sig_str_pct) if red and red.sig_str_pct is not None else None,
                'sig_str_pct_2': float(blue.sig_str_pct) if blue and blue.sig_str_pct is not None else None,
                'sub_att_1': _int_or_zero(red.sub_att) if red is not None else 0,
                'sub_att_2': _int_or_zero(blue.sub_att) if blue is not None else 0,
                'rev_1': _int_or_zero(red.rev) if red is not None else 0,
                'rev_2': _int_or_zero(blue.rev) if blue is not None else 0,
                'ctrl_1': float(red.ctrl_seconds) if red and red.ctrl_seconds is not None else None,
                'ctrl_2': float(blue.ctrl_seconds) if blue and blue.ctrl_seconds is not None else None,
            }
            # include breakdowns
            def safe_get(obj, attr):
                return float(getattr(obj, attr)) if obj and getattr(obj, attr) is not None else None
            params.update({
                'head_1': safe_get(red, 'head_pct'), 'head_2': safe_get(blue, 'head_pct'),
                'body_1': safe_get(red, 'body_pct'), 'body_2': safe_get(blue, 'body_pct'),
                'leg_1': safe_get(red, 'leg_pct'), 'leg_2': safe_get(blue, 'leg_pct'),
                'distance_1': safe_get(red, 'distance_pct'), 'distance_2': safe_get(blue, 'distance_pct'),
                'clinch_1': safe_get(red, 'clinch_pct'), 'clinch_2': safe_get(blue, 'clinch_pct'),
                'ground_1': safe_get(red, 'ground_pct'), 'ground_2': safe_get(blue, 'ground_pct'),
                'total_str_1': safe_get(red, 'total_str_pct'), 'total_str_2': safe_get(blue, 'total_str_pct'),
                'sig_str_dist_1': safe_get(red, 'sig_str_dist_pct'), 'sig_str_dist_2': safe_get(blue, 'sig_str_dist_pct'),
            })

            # insert / upsert using safe placeholders
            sql = text(
                """
                INSERT INTO contract.fights_csv (
                  "Fighter_1", "Fighter_2", "KD_1", "KD_2", "STR_1", "STR_2", "TD_1", "TD_2", "SUB_1", "SUB_2",
                  "Weight_Class", "Method", "Round", "Fight_Time", "Event_Id", "Result_1", "Result_2", "Time Format", "Referee", "Method Details",
                  "Sig. Str. %_1", "Sig. Str. %_2", "Sub. Att_1", "Sub. Att_2", "Rev._1", "Rev._2", "Ctrl_1", "Ctrl_2",
                  "Head_%_1", "Head_%_2", "Body_%_1", "Body_%_2", "Leg_%_1", "Leg_%_2", "Distance_%_1", "Distance_%_2",
                  "Clinch_%_1", "Clinch_%_2", "Ground_%_1", "Ground_%_2", "Total Str._%_1", "Total Str._%_2", "Sig. Str._%_1", "Sig. Str._%_2"
                ) VALUES (
                  :fighter_1, :fighter_2, :kd_1, :kd_2, :str_1, :str_2, :td_1, :td_2, :sub_1, :sub_2,
                  :weight_class, :method, :round, :fight_time, :event_id, :result_1, :result_2, :time_format, :referee, :method_details,
                  :sig_str_pct_1, :sig_str_pct_2, :sub_att_1, :sub_att_2, :rev_1, :rev_2, :ctrl_1, :ctrl_2,
                  :head_1, :head_2, :body_1, :body_2, :leg_1, :leg_2, :distance_1, :distance_2,
                  :clinch_1, :clinch_2, :ground_1, :ground_2, :total_str_1, :total_str_2, :sig_str_dist_1, :sig_str_dist_2
                )
                ON CONFLICT ("Event_Id", "Fighter_1", "Fighter_2") DO UPDATE SET
                  "KD_1" = EXCLUDED."KD_1",
                  "KD_2" = EXCLUDED."KD_2",
                  "STR_1" = EXCLUDED."STR_1",
                  "STR_2" = EXCLUDED."STR_2",
                  "TD_1" = EXCLUDED."TD_1",
                  "TD_2" = EXCLUDED."TD_2",
                  "SUB_1" = EXCLUDED."SUB_1",
                  "SUB_2" = EXCLUDED."SUB_2",
                  "Method" = EXCLUDED."Method",
                  "Round" = EXCLUDED."Round",
                  "Fight_Time" = EXCLUDED."Fight_Time",
                  "Result_1" = EXCLUDED."Result_1",
                  "Result_2" = EXCLUDED."Result_2",
                  "Sig. Str. %_1" = EXCLUDED."Sig. Str. %_1",
                  "Sig. Str. %_2" = EXCLUDED."Sig. Str. %_2",
                  "Ctrl_1" = EXCLUDED."Ctrl_1",
                  "Ctrl_2" = EXCLUDED."Ctrl_2",
                  "Head_%_1" = EXCLUDED."Head_%_1",
                  "Head_%_2" = EXCLUDED."Head_%_2",
                  "Body_%_1" = EXCLUDED."Body_%_1",
                  "Body_%_2" = EXCLUDED."Body_%_2",
                  "Leg_%_1" = EXCLUDED."Leg_%_1",
                  "Leg_%_2" = EXCLUDED."Leg_%_2",
                  "Distance_%_1" = EXCLUDED."Distance_%_1",
                  "Distance_%_2" = EXCLUDED."Distance_%_2",
                  "Clinch_%_1" = EXCLUDED."Clinch_%_1",
                  "Clinch_%_2" = EXCLUDED."Clinch_%_2",
                  "Ground_%_1" = EXCLUDED."Ground_%_1",
                  "Ground_%_2" = EXCLUDED."Ground_%_2",
                  "Total Str._%_1" = EXCLUDED."Total Str._%_1",
                  "Total Str._%_2" = EXCLUDED."Total Str._%_2",
                  "Sig. Str._%_1" = EXCLUDED."Sig. Str._%_1",
                  "Sig. Str._%_2" = EXCLUDED."Sig. Str._%_2"
                """
            )
            conn.execute(sql, params)
    logger.info('Refreshed contract.fights_csv')


def refresh_fighters_stats_csv():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout = 0"))
        # For each fighter, aggregate fight_totals
        fighters = conn.execute(text("SELECT fighter_id, full_name, nickname, ht_inches, wt_lbs, stance, w, l, d, belt FROM ufc.fighters")).fetchall()
        for f in fighters:
            # aggregate with correct where clause and avg round
            stats = conn.execute(text(
                "SELECT AVG(ft.kd) as avg_kd, AVG(ft.str) as avg_str, AVG(ft.td) as avg_td, AVG(ft.sub) as avg_sub, AVG(ft.ctrl_seconds) as avg_ctrl, AVG(ft.sig_str_pct) as avg_sig, AVG(ft.head_pct) as avg_head, AVG(ft.body_pct) as avg_body, AVG(ft.leg_pct) as avg_leg, AVG(ft.distance_pct) as avg_distance, AVG(ft.clinch_pct) as avg_clinch, AVG(ft.ground_pct) as avg_ground, AVG(ft.sub_att) as avg_sub_att, AVG(ft.rev) as avg_rev, AVG(f.round) as avg_round, COUNT(*) as cnt "
                "FROM ufc.fight_totals ft JOIN ufc.fights f ON f.fight_id = ft.fight_id WHERE (ft.corner = 'red' AND f.red_fighter_id = :fid) OR (ft.corner = 'blue' AND f.blue_fighter_id = :fid)"
            ), {'fid': f.fighter_id}).fetchone()
            # most recent weight class
            weight_cls = conn.execute(text("SELECT weight_class FROM ufc.fights WHERE red_fighter_id = :fid OR blue_fighter_id = :fid ORDER BY scraped_at DESC LIMIT 1"), {'fid': f.fighter_id}).scalar()
            gender = None
            if weight_cls:
                gender = "Female" if str(weight_cls).lower().startswith("women") else "Male"
            # fighting style heuristic based on reference distribution
            fighting_style = None
            if stats and getattr(stats, "cnt", 0):
                td = float(stats.avg_td) if stats.avg_td is not None else 0.0
                sub = float(stats.avg_sub) if stats.avg_sub is not None else 0.0
                strk = float(stats.avg_str) if stats.avg_str is not None else 0.0
                if td >= 1.4 or sub >= 0.9:
                    fighting_style = "Grappler"
                elif strk >= 20.0 and td < 0.5 and sub < 0.3:
                    fighting_style = "Striker"
                else:
                    fighting_style = "Hybrid"
            params = {
                'fighter_id': f.fighter_id,
                'full_name': f.full_name,
                'nickname': f.nickname,
                'ht': float(f.ht_inches) if f.ht_inches is not None else None,
                'wt': float(f.wt_lbs) if f.wt_lbs is not None else None,
                'stance': f.stance,
                'w': f.w, 'l': f.l, 'd': f.d, 'belt': bool(f.belt) if f.belt is not None else False,
                'round': float(stats.avg_round) if stats and stats.avg_round is not None else None,
                'kd': float(stats.avg_kd) if stats and stats.avg_kd is not None else None,
                'str': float(stats.avg_str) if stats and stats.avg_str is not None else None,
                'td': float(stats.avg_td) if stats and stats.avg_td is not None else None,
                'sub': float(stats.avg_sub) if stats and stats.avg_sub is not None else None,
                'ctrl': float(stats.avg_ctrl) if stats and stats.avg_ctrl is not None else None,
                'sig_str_pct': float(stats.avg_sig) if stats and stats.avg_sig is not None else None,
                'head_pct': float(stats.avg_head) if stats and stats.avg_head is not None else None,
                'body_pct': float(stats.avg_body) if stats and stats.avg_body is not None else None,
                'leg_pct': float(stats.avg_leg) if stats and stats.avg_leg is not None else None,
                'distance_pct': float(stats.avg_distance) if stats and stats.avg_distance is not None else None,
                'clinch_pct': float(stats.avg_clinch) if stats and stats.avg_clinch is not None else None,
                'ground_pct': float(stats.avg_ground) if stats and stats.avg_ground is not None else None,
                'sub_att': float(stats.avg_sub_att) if stats and stats.avg_sub_att is not None else None,
                'rev': float(stats.avg_rev) if stats and stats.avg_rev is not None else None,
                'weight_class': weight_cls,
                'gender': gender,
                'fighting_style': fighting_style,
            }
            conn.execute(text(
                "INSERT INTO contract.fighters_stats_csv (\"Fighter_Id\", \"Full Name\", \"Nickname\", \"Ht.\", \"Wt.\", \"Stance\", \"W\", \"L\", \"D\", \"Belt\", \"Round\", \"KD\", \"STR\", \"TD\", \"SUB\", \"Ctrl\", \"Sig. Str. %\", \"Head_%\", \"Body_%\", \"Leg_%\", \"Distance_%\", \"Clinch_%\", \"Ground_%\", \"Sub. Att\", \"Rev.\", \"Weight_Class\", \"Gender\", \"Fighting Style\") "
                "VALUES (:fighter_id, :full_name, :nickname, :ht, :wt, :stance, :w, :l, :d, :belt, :round, :kd, :str, :td, :sub, :ctrl, :sig_str_pct, :head_pct, :body_pct, :leg_pct, :distance_pct, :clinch_pct, :ground_pct, :sub_att, :rev, :weight_class, :gender, :fighting_style) "
                "ON CONFLICT (\"Fighter_Id\") DO UPDATE SET \"Full Name\"=EXCLUDED.\"Full Name\", \"Nickname\"=EXCLUDED.\"Nickname\", \"Ht.\"=EXCLUDED.\"Ht.\", \"Wt.\"=EXCLUDED.\"Wt.\", \"Stance\"=EXCLUDED.\"Stance\", \"W\"=EXCLUDED.\"W\", \"L\"=EXCLUDED.\"L\", \"D\"=EXCLUDED.\"D\", \"Belt\"=EXCLUDED.\"Belt\", \"KD\"=EXCLUDED.\"KD\", \"STR\"=EXCLUDED.\"STR\", \"TD\"=EXCLUDED.\"TD\", \"SUB\"=EXCLUDED.\"SUB\", \"Ctrl\"=EXCLUDED.\"Ctrl\""
            ), params)
    logger.info('Refreshed contract.fighters_stats_csv')


def refresh_all():
    refresh_events_csv()
    refresh_fighters_csv()
    refresh_fights_csv()
    refresh_fighters_stats_csv()
    logger.info('Refreshed all contract tables')
