import os
import sys
from collections import defaultdict, deque

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")
import json
from datetime import datetime

# Ensure src/ is importable for DB helpers
ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# Load data from Postgres (contract schema)
from ufc_ingest.db import get_engine
from ufc_ingest.migrate import ensure_schemas_and_tables
from sqlalchemy import text

engine = get_engine()
with engine.connect() as conn:
    fighters = pd.read_sql_query("SELECT * FROM contract.fighters_csv", conn)
    fights = pd.read_sql_query("SELECT * FROM contract.fights_csv", conn)
    events = pd.read_sql_query("SELECT * FROM contract.events_csv", conn)


# Create target and Winner
# Make binary label from Result_1 ('W' -> Fighter_1 wins)
fights['Fighter_1_Win'] = fights['Result_1'].apply(lambda x: 1 if str(x).strip().upper() == 'W' else 0)

# Create readable Winner column (useful for recent-form and debugging)
fights['Winner'] = fights.apply(
    lambda r: r['Fighter_1'] if str(r['Result_1']).strip().upper() == 'W'
              else (r['Fighter_2'] if str(r['Result_2']).strip().upper() == 'W' else 'Draw'),
    axis=1
)

print(fights[['Fighter_1','Fighter_2','Result_1','Result_2','Fighter_1_Win','Winner']].head())


# Merge event date
events['Date'] = pd.to_datetime(events['Date'], errors='coerce')
fights = fights.merge(events[['Event_Id','Date']], on='Event_Id', how='left')
fights['Date'] = pd.to_datetime(fights['Date'], errors='coerce')
fights['Date'] = fights['Date'].fillna(pd.Timestamp("1900-01-01"))
print("Fights with dates:", fights['Date'].notna().sum(), "of", len(fights))

# Build pre-fight features using only past fights
fights_sorted = fights.sort_values('Date').reset_index(drop=True)
cutoff_env = os.getenv("UFC_CUTOFF_DATE")
cutoff_date = pd.to_datetime(cutoff_env) if cutoff_env else None
if cutoff_date is not None:
    print(f"Applying cutoff date: {cutoff_date.date()}")

# Physicals lookup
phys_cols = ['Full Name', 'Ht.', 'Wt.', 'Reach', 'Stance', 'Belt']
fighters_phys = fighters[phys_cols].drop_duplicates(subset='Full Name')
phys_map = fighters_phys.set_index('Full Name').to_dict(orient='index')

# History trackers
N_recent = 3
TREND_N = 5
HALF_LIFE_DAYS = 730.0
DECAY_BASE = 0.5
K_ELO = 32.0

stats_sum = defaultdict(lambda: {'STR': 0.0, 'KD': 0.0, 'TD': 0.0, 'SUB': 0.0, 'SIG': 0.0})
stats_cnt = defaultdict(int)
wins = defaultdict(int)
losses = defaultdict(int)
draws = defaultdict(int)
recent = defaultdict(lambda: deque(maxlen=N_recent))
trend_hist = defaultdict(lambda: deque(maxlen=TREND_N))
last_fight_date = {}
elo = defaultdict(lambda: 1500.0)
history = defaultdict(list)  # list of dicts: date, stats, win_score
opp_hist = defaultdict(list)  # opponent-adjusted stat history


def _safe_num(x):
    return 0.0 if pd.isna(x) else float(x)


def _weighted_avg(vals, weights):
    if not vals:
        return 0.0
    w = weights[:len(vals)]
    return float(np.average(vals, weights=w))


def _decayed_avg(hist_list, key, current_date):
    if not hist_list:
        return 0.0
    sum_w = 0.0
    sum_wx = 0.0
    for h in hist_list:
        dt_days = (current_date - h['date']).days
        if dt_days < 0:
            dt_days = 0
        w = DECAY_BASE ** (dt_days / HALF_LIFE_DAYS)
        sum_w += w
        sum_wx += w * h[key]
    return (sum_wx / sum_w) if sum_w > 0 else 0.0


def _trend_slope(vals):
    if len(vals) < 2:
        return 0.0
    x = np.arange(len(vals))
    y = np.array(vals, dtype=float)
    try:
        slope = np.polyfit(x, y, 1)[0]
    except Exception:
        slope = 0.0
    return float(slope)


def _elo_expected(r_a, r_b):
    return 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400.0))


def _result_score(r):
    r = str(r).strip().upper()
    if r == 'W':
        return 1.0
    if r == 'L':
        return 0.0
    if r in {'D', 'NC'}:
        return 0.5
    return 0.5


def _max_rounds(time_format):
    tf = str(time_format or "")
    # Prefer explicit numbers in the time format
    for n in ['5', '3']:
        if n in tf:
            return int(n)
    # Default to 3 rounds if unknown
    return 3


def _ratio(numer, denom):
    try:
        if denom and denom > 0:
            return float(numer) / float(denom)
    except Exception:
        pass
    return 0.0


def _apply_updates(row):
    f1 = row['Fighter_1']
    f2 = row['Fighter_2']
    if pd.isna(f1) or pd.isna(f2):
        return
    fight_date = row['Date']

    # update histories AFTER building features (no leakage)
    r1 = str(row.get('Result_1')).strip().upper()
    r2 = str(row.get('Result_2')).strip().upper()
    if r1 == 'W':
        wins[f1] += 1
    elif r1 == 'L':
        losses[f1] += 1
    elif r1 == 'D':
        draws[f1] += 1

    if r2 == 'W':
        wins[f2] += 1
    elif r2 == 'L':
        losses[f2] += 1
    elif r2 == 'D':
        draws[f2] += 1

    # pre-fight Elo snapshot for opponent-adjusted stats
    old_f1 = elo[f1]
    old_f2 = elo[f2]

    # stats update from this fight
    for name, prefix in [(f1, '_1'), (f2, '_2')]:
        str_val = _safe_num(row.get(f'STR{prefix}'))
        kd_val = _safe_num(row.get(f'KD{prefix}'))
        td_val = _safe_num(row.get(f'TD{prefix}'))
        sub_val = _safe_num(row.get(f'SUB{prefix}'))
        sig_val = _safe_num(row.get(f'Sig. Str. %{prefix}'))

        stats_sum[name]['STR'] += str_val
        stats_sum[name]['KD'] += kd_val
        stats_sum[name]['TD'] += td_val
        stats_sum[name]['SUB'] += sub_val
        stats_sum[name]['SIG'] += sig_val
        stats_cnt[name] += 1
        win_score = _result_score(r1 if prefix == '_1' else r2)
        history[name].append({
            'date': fight_date,
            'STR': str_val,
            'KD': kd_val,
            'TD': td_val,
            'SUB': sub_val,
            'SIG': sig_val,
            'win_score': win_score,
        })
        opp_elo = old_f2 if prefix == '_1' else old_f1
        opp_factor = opp_elo / 1500.0 if opp_elo else 1.0
        opp_hist[name].append({
            'date': fight_date,
            'STR_adj': str_val * opp_factor,
            'KD_adj': kd_val * opp_factor,
            'TD_adj': td_val * opp_factor,
            'SUB_adj': sub_val * opp_factor,
            'SIG_adj': sig_val * opp_factor,
            'opp_elo': opp_elo,
        })
        recent[name].append({
            'STR': str_val,
            'KD': kd_val,
            'TD': td_val,
            'SUB': sub_val,
            'win': (r1 == 'W') if prefix == '_1' else (r2 == 'W')
        })
        trend_hist[name].append({
            'STR': str_val,
            'TD': td_val,
        })
        last_fight_date[name] = fight_date

    # update Elo ratings (margin/round-weighted)
    exp1 = _elo_expected(old_f1, old_f2)
    exp2 = _elo_expected(old_f2, old_f1)
    s1 = _result_score(r1)
    s2 = _result_score(r2)
    method = str(row.get('Method') or '').upper()
    is_decision = 'DEC' in method
    max_r = _max_rounds(row.get('Time Format'))
    try:
        rnd = int(row.get('Round')) if row.get('Round') is not None else 1
    except Exception:
        rnd = 1
    if rnd < 1:
        rnd = 1
    finish_mult = 1.0
    if not is_decision:
        # earlier finishes get a slightly larger update
        finish_mult = 1.1 + (max_r - rnd) / max_r * 0.4
    k = K_ELO * finish_mult
    elo[f1] = old_f1 + k * (s1 - exp1)
    elo[f2] = old_f2 + k * (s2 - exp2)


rows = []
pending = []
current_date = None
for _, row in fights_sorted.iterrows():
    f1 = row['Fighter_1']
    f2 = row['Fighter_2']
    if pd.isna(f1) or pd.isna(f2):
        continue
    fight_date = row['Date']
    if cutoff_date is not None and fight_date > cutoff_date:
        for prow in pending:
            _apply_updates(prow)
        pending = []
        break
    if current_date is None:
        current_date = fight_date
    if fight_date != current_date:
        for prow in pending:
            _apply_updates(prow)
        pending = []
        current_date = fight_date
    weight_class = row.get('Weight_Class')

    # pre-fight averages (simple and decay)
    def _avg_stat(name, key):
        cnt = stats_cnt[name]
        return (stats_sum[name][key] / cnt) if cnt > 0 else 0.0

    f1_avg = {k: _avg_stat(f1, k) for k in ['STR','KD','TD','SUB','SIG']}
    f2_avg = {k: _avg_stat(f2, k) for k in ['STR','KD','TD','SUB','SIG']}

    f1_decay = {k: _decayed_avg(history[f1], k, fight_date) for k in ['STR','KD','TD','SUB','SIG']}
    f2_decay = {k: _decayed_avg(history[f2], k, fight_date) for k in ['STR','KD','TD','SUB','SIG']}

    # opponent-adjusted decayed stats (weighted by opponent Elo at the time)
    f1_adj = {k: _decayed_avg(opp_hist[f1], k, fight_date) for k in ['STR_adj','KD_adj','TD_adj','SUB_adj','SIG_adj']}
    f2_adj = {k: _decayed_avg(opp_hist[f2], k, fight_date) for k in ['STR_adj','KD_adj','TD_adj','SUB_adj','SIG_adj']}
    f1_opp_elo_avg = _decayed_avg(opp_hist[f1], 'opp_elo', fight_date)
    f2_opp_elo_avg = _decayed_avg(opp_hist[f2], 'opp_elo', fight_date)

    # KO power (KD per STR)
    f1_ko_rate = _ratio(f1_decay['KD'], f1_decay['STR'])
    f2_ko_rate = _ratio(f2_decay['KD'], f2_decay['STR'])
    f1_ko_rate_adj = _ratio(f1_adj['KD_adj'], f1_adj['STR_adj'])
    f2_ko_rate_adj = _ratio(f2_adj['KD_adj'], f2_adj['STR_adj'])

    # pre-fight win rates
    def _winrate(name):
        w = wins[name]; l = losses[name]; d = draws[name]
        denom = w + l + d
        return (w / denom) if denom > 0 else 0.0

    f1_wr = _winrate(f1)
    f2_wr = _winrate(f2)

    f1_decay_wr = _decayed_avg(history[f1], 'win_score', fight_date)
    f2_decay_wr = _decayed_avg(history[f2], 'win_score', fight_date)

    # recent-form (last N fights)
    def _recent_metrics(name):
        hist = list(recent[name])
        if not hist:
            return {'winrate': 0.0, 'STR': 0.0, 'KD': 0.0, 'TD': 0.0, 'SUB': 0.0}
        wins_n = sum(1 for h in hist if h['win'])
        winrate = wins_n / len(hist)
        return {
            'winrate': winrate,
            'STR': _weighted_avg([h['STR'] for h in hist], [0.6, 0.3, 0.1]),
            'KD': _weighted_avg([h['KD'] for h in hist], [0.6, 0.3, 0.1]),
            'TD': _weighted_avg([h['TD'] for h in hist], [0.6, 0.3, 0.1]),
            'SUB': _weighted_avg([h['SUB'] for h in hist], [0.6, 0.3, 0.1]),
        }

    f1_recent = _recent_metrics(f1)
    f2_recent = _recent_metrics(f2)

    # physicals
    p1 = phys_map.get(f1, {})
    p2 = phys_map.get(f2, {})
    f1_belt = 1 if p1.get('Belt') else 0
    f2_belt = 1 if p2.get('Belt') else 0

    # style heuristic based on decayed averages
    def _style(avg):
        if avg['TD'] >= 1.4 or avg['SUB'] >= 0.9:
            return "Grappler"
        if avg['STR'] >= 20.0 and avg['TD'] < 0.5 and avg['SUB'] < 0.3:
            return "Striker"
        return "Hybrid"

    f1_style = _style(f1_decay)
    f2_style = _style(f2_decay)

    # stance/style matchup labels
    f1_stance = p1.get('Stance') if p1.get('Stance') else 'Unknown'
    f2_stance = p2.get('Stance') if p2.get('Stance') else 'Unknown'
    stance_matchup = f"{f1_stance}_vs_{f2_stance}"
    style_matchup = f"{f1_style}_vs_{f2_style}"

    # layoff and activity
    f1_last = last_fight_date.get(f1)
    f2_last = last_fight_date.get(f2)
    f1_days_since = (fight_date - f1_last).days if f1_last is not None else np.nan
    f2_days_since = (fight_date - f2_last).days if f2_last is not None else np.nan

    def _fights_last_365(name):
        if not history[name]:
            return 0
        return sum(1 for h in history[name] if (fight_date - h['date']).days <= 365)

    f1_last_365 = _fights_last_365(f1)
    f2_last_365 = _fights_last_365(f2)

    # experience
    f1_prior = stats_cnt[f1]
    f2_prior = stats_cnt[f2]
    f1_w = wins[f1]; f1_l = losses[f1]; f1_d = draws[f1]
    f2_w = wins[f2]; f2_l = losses[f2]; f2_d = draws[f2]

    # momentum trends
    def _trend_metrics(name):
        hist = list(trend_hist[name])
        if not hist:
            return {'STR': 0.0, 'TD': 0.0}
        return {
            'STR': _trend_slope([h['STR'] for h in hist]),
            'TD': _trend_slope([h['TD'] for h in hist])
        }

    f1_trend = _trend_metrics(f1)
    f2_trend = _trend_metrics(f2)

    # elo
    f1_elo = elo[f1]
    f2_elo = elo[f2]

    rows.append({
        'Fighter_1': f1,
        'Fighter_2': f2,
        'Result_1': row.get('Result_1'),
        'Result_2': row.get('Result_2'),
        'Fighter_1_Win': 1 if str(row.get('Result_1')).strip().upper() == 'W' else 0,
        'Winner': f1 if str(row.get('Result_1')).strip().upper() == 'W' else (f2 if str(row.get('Result_2')).strip().upper() == 'W' else 'Draw'),
        'Date': fight_date,
        'Weight_Class': weight_class,
        'F1_Stance': p1.get('Stance'),
        'F2_Stance': p2.get('Stance'),
        'F1_Fighting Style': f1_style,
        'F2_Fighting Style': f2_style,
        'Stance_Matchup': stance_matchup,
        'Style_Matchup': style_matchup,
        'F1_Ht.': p1.get('Ht.'),
        'F2_Ht.': p2.get('Ht.'),
        'F1_Wt.': p1.get('Wt.'),
        'F2_Wt.': p2.get('Wt.'),
        'F1_Reach': p1.get('Reach'),
        'F2_Reach': p2.get('Reach'),
        'F1_Belt': f1_belt,
        'F2_Belt': f2_belt,
        'F1_WinRate': f1_wr,
        'F2_WinRate': f2_wr,
        'F1_DecayWinRate': f1_decay_wr,
        'F2_DecayWinRate': f2_decay_wr,
        'F1_STR_avg': f1_avg['STR'],
        'F2_STR_avg': f2_avg['STR'],
        'F1_TD_avg': f1_avg['TD'],
        'F2_TD_avg': f2_avg['TD'],
        'F1_KD_avg': f1_avg['KD'],
        'F2_KD_avg': f2_avg['KD'],
        'F1_SUB_avg': f1_avg['SUB'],
        'F2_SUB_avg': f2_avg['SUB'],
        'F1_SIG_avg': f1_avg['SIG'],
        'F2_SIG_avg': f2_avg['SIG'],
        'F1_STR_decay': f1_decay['STR'],
        'F2_STR_decay': f2_decay['STR'],
        'F1_TD_decay': f1_decay['TD'],
        'F2_TD_decay': f2_decay['TD'],
        'F1_KD_decay': f1_decay['KD'],
        'F2_KD_decay': f2_decay['KD'],
        'F1_SUB_decay': f1_decay['SUB'],
        'F2_SUB_decay': f2_decay['SUB'],
        'F1_SIG_decay': f1_decay['SIG'],
        'F2_SIG_decay': f2_decay['SIG'],
        'F1_STR_adj': f1_adj['STR_adj'],
        'F2_STR_adj': f2_adj['STR_adj'],
        'F1_TD_adj': f1_adj['TD_adj'],
        'F2_TD_adj': f2_adj['TD_adj'],
        'F1_KD_adj': f1_adj['KD_adj'],
        'F2_KD_adj': f2_adj['KD_adj'],
        'F1_SUB_adj': f1_adj['SUB_adj'],
        'F2_SUB_adj': f2_adj['SUB_adj'],
        'F1_SIG_adj': f1_adj['SIG_adj'],
        'F2_SIG_adj': f2_adj['SIG_adj'],
        'F1_opp_elo': f1_opp_elo_avg,
        'F2_opp_elo': f2_opp_elo_avg,
        'F1_KO_rate': f1_ko_rate,
        'F2_KO_rate': f2_ko_rate,
        'F1_KO_rate_adj': f1_ko_rate_adj,
        'F2_KO_rate_adj': f2_ko_rate_adj,
        'F1_recent_winrate': f1_recent['winrate'],
        'F2_recent_winrate': f2_recent['winrate'],
        'F1_recent_STR': f1_recent['STR'],
        'F2_recent_STR': f2_recent['STR'],
        'F1_recent_KD': f1_recent['KD'],
        'F2_recent_KD': f2_recent['KD'],
        'F1_recent_TD': f1_recent['TD'],
        'F2_recent_TD': f2_recent['TD'],
        'F1_recent_SUB': f1_recent['SUB'],
        'F2_recent_SUB': f2_recent['SUB'],
        'F1_days_since': f1_days_since,
        'F2_days_since': f2_days_since,
        'F1_fights_365': f1_last_365,
        'F2_fights_365': f2_last_365,
        'F1_PriorFights': f1_prior,
        'F2_PriorFights': f2_prior,
        'F1_Wins': f1_w,
        'F2_Wins': f2_w,
        'F1_Losses': f1_l,
        'F2_Losses': f2_l,
        'F1_Draws': f1_d,
        'F2_Draws': f2_d,
        'F1_STR_trend': f1_trend['STR'],
        'F2_STR_trend': f2_trend['STR'],
        'F1_TD_trend': f1_trend['TD'],
        'F2_TD_trend': f2_trend['TD'],
        'F1_elo': f1_elo,
        'F2_elo': f2_elo
    })

    pending.append(row)

for prow in pending:
    _apply_updates(prow)


fights_merged = pd.DataFrame(rows)
print("After prefight PRO feature build, shape:", fights_merged.shape)

# Impute missing physicals (weight-class aware if possible)
if 'Weight_Class' in fights_merged.columns:
    wcol1 = wcol2 = 'Weight_Class'
else:
    wcol1 = wcol2 = None

# Function to impute column by grouping on weight class when available
def impute_by_weight_or_global(df, col_f1, col_f2, weight_col):
    # global means
    global_mean_f1 = df[col_f1].mean()
    global_mean_f2 = df[col_f2].mean()
    if weight_col:
        # fill with group means then global mean
        df[col_f1] = df.groupby(weight_col)[col_f1].transform(lambda g: g.fillna(g.mean()))
        df[col_f2] = df.groupby(weight_col)[col_f2].transform(lambda g: g.fillna(g.mean()))
    # fallback to global mean
    df[col_f1] = df[col_f1].fillna(global_mean_f1)
    df[col_f2] = df[col_f2].fillna(global_mean_f2)
    return df

fights_merged = impute_by_weight_or_global(fights_merged, 'F1_Reach', 'F2_Reach', wcol1)
fights_merged = impute_by_weight_or_global(fights_merged, 'F1_Ht.', 'F2_Ht.', wcol1)
fights_merged = impute_by_weight_or_global(fights_merged, 'F1_Wt.', 'F2_Wt.', wcol1)


# Core features based on pre-fight averages (decayed)
for stat in ['STR','TD','KD','SUB']:
    fights_merged[f'{stat}_diff'] = fights_merged[f'F1_{stat}_decay'] - fights_merged[f'F2_{stat}_decay']
    fights_merged[f'{stat}_1'] = fights_merged[f'F1_{stat}_decay']
    fights_merged[f'{stat}_2'] = fights_merged[f'F2_{stat}_decay']

fights_merged['SigStr_diff'] = fights_merged['F1_SIG_decay'] - fights_merged['F2_SIG_decay']

# opponent-adjusted diffs
fights_merged['STR_adj_diff'] = fights_merged['F1_STR_adj'] - fights_merged['F2_STR_adj']
fights_merged['TD_adj_diff'] = fights_merged['F1_TD_adj'] - fights_merged['F2_TD_adj']
fights_merged['KD_adj_diff'] = fights_merged['F1_KD_adj'] - fights_merged['F2_KD_adj']
fights_merged['SUB_adj_diff'] = fights_merged['F1_SUB_adj'] - fights_merged['F2_SUB_adj']
fights_merged['SIG_adj_diff'] = fights_merged['F1_SIG_adj'] - fights_merged['F2_SIG_adj']
fights_merged['OppElo_diff'] = fights_merged['F1_opp_elo'] - fights_merged['F2_opp_elo']

# KO power diffs
fights_merged['KO_rate_diff'] = fights_merged['F1_KO_rate'] - fights_merged['F2_KO_rate']
fights_merged['KO_rate_adj_diff'] = fights_merged['F1_KO_rate_adj'] - fights_merged['F2_KO_rate_adj']

# physical diffs (already imputed)
fights_merged['Reach_diff'] = fights_merged['F1_Reach'] - fights_merged['F2_Reach']
fights_merged['Ht._diff'] = fights_merged['F1_Ht.'] - fights_merged['F2_Ht.']
fights_merged['Wt._diff'] = fights_merged['F1_Wt.'] - fights_merged['F2_Wt.']
fights_merged['Belt_diff'] = fights_merged['F1_Belt'] - fights_merged['F2_Belt']

# WinRate diff
fights_merged['WinRate_diff'] = fights_merged['F1_WinRate'] - fights_merged['F2_WinRate']

# Decayed winrate diff
fights_merged['DecayWinRate_diff'] = fights_merged['F1_DecayWinRate'] - fights_merged['F2_DecayWinRate']

# Elo diff
fights_merged['Elo_diff'] = fights_merged['F1_elo'] - fights_merged['F2_elo']

# Layoff diff
fights_merged['Layoff_diff'] = fights_merged['F1_days_since'] - fights_merged['F2_days_since']

# Experience diffs
fights_merged['Exp_diff'] = fights_merged['F1_PriorFights'] - fights_merged['F2_PriorFights']
fights_merged['Wins_diff'] = fights_merged['F1_Wins'] - fights_merged['F2_Wins']
fights_merged['Losses_diff'] = fights_merged['F1_Losses'] - fights_merged['F2_Losses']
fights_merged['Draws_diff'] = fights_merged['F1_Draws'] - fights_merged['F2_Draws']

# Momentum diffs
fights_merged['STR_trend_diff'] = fights_merged['F1_STR_trend'] - fights_merged['F2_STR_trend']
fights_merged['TD_trend_diff'] = fights_merged['F1_TD_trend'] - fights_merged['F2_TD_trend']

print("Core features created. Sample:")
print(fights_merged[['F1_WinRate','F2_WinRate','WinRate_diff','STR_diff','TD_diff','KD_diff','SUB_diff','Reach_diff']].head())


# Recent-form diffs already computed in prefight features
for m in ['STR','KD','TD','SUB']:
    fights_merged[f'{m}_recent_diff'] = fights_merged[f'F1_recent_{m}'] - fights_merged[f'F2_recent_{m}']
fights_merged['recent_winrate_diff'] = fights_merged['F1_recent_winrate'] - fights_merged['F2_recent_winrate']

print("Recent-form features done. Sample:")
print(fights_merged[['F1_recent_winrate','F2_recent_winrate','recent_winrate_diff']].head())


# Symmetrize to reduce red/blue corner bias
def _symmetrize(df):
    swap = df.copy()
    cols = df.columns

    # swap F1_/F2_ prefixed columns
    f1_cols = [c for c in cols if c.startswith('F1_')]
    for c in f1_cols:
        other = 'F2_' + c[3:]
        if other in cols:
            swap[c] = df[other]
            swap[other] = df[c]

    # swap fighter names and results
    if 'Fighter_1' in cols and 'Fighter_2' in cols:
        swap['Fighter_1'] = df['Fighter_2']
        swap['Fighter_2'] = df['Fighter_1']
    if 'Result_1' in cols and 'Result_2' in cols:
        swap['Result_1'] = df['Result_2']
        swap['Result_2'] = df['Result_1']
        swap['Fighter_1_Win'] = df['Result_2'].apply(lambda x: 1 if str(x).strip().upper() == 'W' else 0)
    elif 'Fighter_1_Win' in cols:
        swap['Fighter_1_Win'] = df['Fighter_1_Win'].apply(lambda v: 0 if v == 1 else 1)

    # invert diff columns
    for c in cols:
        if c.endswith('_diff'):
            swap[c] = -df[c]

    # recompute matchup strings
    if 'F1_Stance' in cols and 'F2_Stance' in cols:
        swap['Stance_Matchup'] = swap['F1_Stance'].fillna('Unknown') + "_vs_" + swap['F2_Stance'].fillna('Unknown')
    if 'F1_Fighting Style' in cols and 'F2_Fighting Style' in cols:
        swap['Style_Matchup'] = swap['F1_Fighting Style'].fillna('Unknown') + "_vs_" + swap['F2_Fighting Style'].fillna('Unknown')

    return pd.concat([df, swap], ignore_index=True)


fights_merged = _symmetrize(fights_merged)


# Final features & missing handling
feature_cols = []

# diffs
for c in ['STR_diff','TD_diff','KD_diff','SUB_diff','SigStr_diff',
          'STR_adj_diff','TD_adj_diff','KD_adj_diff','SUB_adj_diff','SIG_adj_diff','OppElo_diff',
          'KO_rate_diff','KO_rate_adj_diff','Belt_diff',
          'WinRate_diff','DecayWinRate_diff','Elo_diff','Layoff_diff','Exp_diff','Wins_diff','Losses_diff','Draws_diff','STR_trend_diff','TD_trend_diff']:
    if c in fights_merged.columns:
        feature_cols.append(c)

# physical diffs
for c in ['Ht._diff','Wt._diff','Reach_diff']:
    if c in fights_merged.columns:
        feature_cols.append(c)

# recent diffs
for m in ['STR','KD','TD','SUB']:
    col = f'{m}_recent_diff'
    if col in fights_merged.columns:
        feature_cols.append(col)
if 'recent_winrate_diff' in fights_merged.columns:
    feature_cols.append('recent_winrate_diff')

# add raw corner winrates
for c in ['F1_WinRate','F2_WinRate','F1_DecayWinRate','F2_DecayWinRate']:
    if c in fights_merged.columns:
        feature_cols.append(c)

# add belt and KO rates
for c in ['F1_Belt','F2_Belt','F1_KO_rate','F2_KO_rate','F1_KO_rate_adj','F2_KO_rate_adj']:
    if c in fights_merged.columns:
        feature_cols.append(c)

# add opponent-adjusted raw stats and opponent Elo
for c in ['F1_STR_adj','F2_STR_adj','F1_TD_adj','F2_TD_adj','F1_KD_adj','F2_KD_adj','F1_SUB_adj','F2_SUB_adj','F1_SIG_adj','F2_SIG_adj','F1_opp_elo','F2_opp_elo']:
    if c in fights_merged.columns:
        feature_cols.append(c)

# add Elo
for c in ['F1_elo','F2_elo']:
    if c in fights_merged.columns:
        feature_cols.append(c)

# include avg side stats (decayed)
for base in ['STR_1','STR_2','TD_1','TD_2','KD_1','KD_2','SUB_1','SUB_2']:
    if base in fights_merged.columns:
        feature_cols.append(base)

# include sig strike % (decayed)
if 'F1_SIG_decay' in fights_merged.columns and 'F2_SIG_decay' in fights_merged.columns:
    feature_cols.extend(['F1_SIG_decay', 'F2_SIG_decay'])

# include experience and layoff raw features
for c in ['F1_PriorFights','F2_PriorFights','F1_Wins','F2_Wins','F1_Losses','F2_Losses','F1_Draws','F2_Draws','F1_days_since','F2_days_since','F1_fights_365','F2_fights_365','F1_STR_trend','F2_STR_trend','F1_TD_trend','F2_TD_trend']:
    if c in fights_merged.columns:
        feature_cols.append(c)

# dedupe and ensure exists
feature_cols = [c for i,c in enumerate(dict.fromkeys(feature_cols)) if c in fights_merged.columns]
print("Using features (count):", len(feature_cols))

# missing treatment: numeric medians
for c in feature_cols:
    if fights_merged[c].dtype.kind in 'biufc':
        med = fights_merged[c].median()
        fights_merged[c].fillna(med, inplace=True)
    else:
        fights_merged[c].fillna(0, inplace=True)

# categorical encoding
cat_cols = []
for c in ['F1_Stance','F2_Stance','F1_Fighting Style','F2_Fighting Style','Weight_Class','Stance_Matchup','Style_Matchup']:
    if c in fights_merged.columns:
        cat_cols.append(c)

print("Categorical columns:", cat_cols)
if len(cat_cols)>0:
    fights_merged = pd.get_dummies(fights_merged, columns=cat_cols, dummy_na=False, drop_first=True)
    # add dummies to feature list
    dummies = [c for c in fights_merged.columns if any(orig in c for orig in cat_cols)]
    feature_cols += dummies

# final feature cols dedupe
feature_cols = [c for i,c in enumerate(dict.fromkeys(feature_cols)) if c in fights_merged.columns]
print("Final feature count:", len(feature_cols))

# Leakage guard: prevent accidental inclusion of post-fight stats
# columns that should never appear in prefight features
forbidden = {
    'Sig. Str. %_1', 'Sig. Str. %_2',
    'Ctrl_1', 'Ctrl_2',
    'Head_%_1', 'Head_%_2', 'Body_%_1', 'Body_%_2',
    'Leg_%_1', 'Leg_%_2', 'Distance_%_1', 'Distance_%_2',
    'Clinch_%_1', 'Clinch_%_2', 'Ground_%_1', 'Ground_%_2',
    'Total Str._%_1', 'Total Str._%_2', 'Sig. Str._%_1', 'Sig. Str._%_2',
    'Sub. Att_1', 'Sub. Att_2', 'Rev._1', 'Rev._2'
}
leaked = [c for c in feature_cols if c in forbidden]
leaked_df = [c for c in fights_merged.columns if c in forbidden]
if leaked or leaked_df:
    raise ValueError(f"Leakage guard: forbidden post-fight columns detected: {sorted(set(leaked + leaked_df))}")


# Train/test split
from sklearn.model_selection import train_test_split

n_rows = len(fights_merged)
train_df = fights_merged.copy()
test_df = fights_merged.iloc[0:0].copy()

train_all = os.getenv("UFC_TRAIN_ALL", "0") == "1"
if train_all:
    print("UFC_TRAIN_ALL=1; training on all data, no holdout evaluation.")
else:
    if 'Date' in fights_merged.columns and fights_merged['Date'].notna().sum() > 0:
        fights_sorted = fights_merged.sort_values('Date').reset_index(drop=True)
        cutoff_date = fights_sorted['Date'].quantile(0.9)
        train_df = fights_sorted[fights_sorted['Date'] <= cutoff_date].copy()
        test_df  = fights_sorted[fights_sorted['Date'] > cutoff_date].copy()
        print("Time split:", cutoff_date, "train/test sizes:", train_df.shape[0], test_df.shape[0])

# Fallback to random split if time split yields empty train/test (only if not training on all data)
if (not train_all) and (len(train_df) == 0 or len(test_df) == 0):
    if n_rows >= 2:
        test_size = max(1, int(round(n_rows * 0.2)))
        test_size = min(test_size, n_rows - 1)
        train_df, test_df = train_test_split(fights_merged, test_size=test_size, random_state=42)
        print("Random split used. train/test sizes:", train_df.shape[0], test_df.shape[0])
    else:
        print("Not enough rows to create a test split. Proceeding without evaluation.")

X_train = train_df[feature_cols]
y_train = train_df['Fighter_1_Win']
X_test = test_df[feature_cols]
y_test = test_df['Fighter_1_Win']


# Preprocessing categorical columns for XGBoost

# Find categorical columns (dtype = object)
cat_cols = X_train.select_dtypes(include=['object']).columns

# Apply label encoding for each categorical column
from sklearn.preprocessing import LabelEncoder

for col in cat_cols:
    le = LabelEncoder()
    # Fit on training data, transform both train and test
    X_train[col] = le.fit_transform(X_train[col].astype(str))
    X_test[col] = le.transform(X_test[col].astype(str))
print(X_train.dtypes)


# Train + calibrate + evaluate
from xgboost import XGBClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import accuracy_score, roc_auc_score, log_loss, classification_report, confusion_matrix

if y_train.nunique() < 2:
    raise ValueError("Training data has only one class; need both win/loss outcomes to train.")

model = XGBClassifier(n_estimators=300, learning_rate=0.05, max_depth=5,
                      use_label_encoder=False, eval_metric='logloss',
                      random_state=42, n_jobs=-1)
model.fit(X_train, y_train)

# Calibrate probabilities (CV) when possible
min_class = y_train.value_counts().min()
cv_folds = min(3, int(min_class))
calibrated = False
if cv_folds >= 2:
    calibrator = CalibratedClassifierCV(model, method='isotonic', cv=cv_folds)
    calibrator.fit(X_train, y_train)
    calibrated = True
else:
    calibrator = model
    print("Skipping calibration (insufficient class counts). Using raw model probabilities.")

# Predict & metrics (skip if no test set)
metrics = {}
if len(X_test) > 0:
    y_pred = calibrator.predict(X_test)
    y_prob = calibrator.predict_proba(X_test)[:,1]

    print("\n=== Evaluation ===")
    acc = round(accuracy_score(y_test, y_pred),4)
    print("Accuracy:", acc)
    try:
        auc = round(roc_auc_score(y_test, y_prob),4)
        print("ROC AUC:", auc)
    except:
        auc = None
        print("ROC AUC: could not compute")
    ll = round(log_loss(y_test, y_prob),4)
    print("Log Loss:", ll)

    # Brier score
    try:
        brier = round(float(np.mean((y_prob - y_test.values) ** 2)),4)
        print("Brier Score:", brier)
    except Exception:
        brier = None

    metrics = {
        "accuracy": acc,
        "roc_auc": auc,
        "log_loss": ll,
        "brier": brier,
        "test_rows": int(len(y_test)),
    }

    print("\nClassification report:\n", classification_report(y_test, y_pred))
    print("Confusion matrix:\n", confusion_matrix(y_test, y_pred))
else:
    print("No test set available; skipping evaluation.")

# top features via XGBoost importance
try:
    importances = pd.Series(model.feature_importances_, index=X_train.columns).sort_values(ascending=False)
    print("\nTop features:\n", importances.head(20))
except Exception as e:
    print("Feature importance error:", e)


# Save model and helpers
import joblib
model_path = os.getenv("UFC_MODEL_PATH", "ufc_model_bundle_prefight_pro.joblib")
joblib.dump({'model': model, 'calibrator': calibrator, 'features': feature_cols}, model_path)
model_version = os.getenv("UFC_MODEL_VERSION") or f"prefight_pro_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

# Persist model metadata (optional)
if os.getenv("UFC_SAVE_MODEL_META", "0") == "1":
    ensure_schemas_and_tables()
    artifact_uri = os.getenv("UFC_MODEL_URI") or model_path
    with engine.begin() as conn:
        conn.execute(text(
            """
            INSERT INTO app.model_artifacts
                (model_name, model_version, trained_at, dataset_rows, feature_count, metrics_json, artifact_uri, created_at)
            VALUES
                (:model_name, :model_version, now(), :dataset_rows, :feature_count, :metrics_json, :artifact_uri, now())
            ON CONFLICT (model_version)
            DO UPDATE SET
                model_name = EXCLUDED.model_name,
                trained_at = EXCLUDED.trained_at,
                dataset_rows = EXCLUDED.dataset_rows,
                feature_count = EXCLUDED.feature_count,
                metrics_json = EXCLUDED.metrics_json,
                artifact_uri = EXCLUDED.artifact_uri,
                created_at = EXCLUDED.created_at
            """
        ), {
            "model_name": "prefight_pro",
            "model_version": model_version,
            "dataset_rows": int(len(fights_merged)),
            "feature_count": int(len(feature_cols)),
            "metrics_json": json.dumps(metrics) if metrics else None,
            "artifact_uri": artifact_uri,
        })

# Store predictions for upcoming fights (optional)


# helper: normalize and infer weight class for predictions
def _normalize_weight_class(weight_class, fights_df):
    if weight_class is None:
        return None
    wc = str(weight_class).strip()
    if not wc:
        return None
    if fights_df is None or 'Weight_Class' not in fights_df.columns:
        return wc
    classes = [str(c).strip() for c in fights_df['Weight_Class'].dropna().unique()]
    for c in classes:
        if c.lower() == wc.lower():
            return c
    return wc


def _infer_weight_class(f1_name, f2_name, fights_df):
    if fights_df is None or 'Weight_Class' not in fights_df.columns:
        return None

    def _last_weight(name):
        df = fights_df[(fights_df['Fighter_1'] == name) | (fights_df['Fighter_2'] == name)]
        if df.empty:
            return None, pd.Timestamp.min
        df = df.dropna(subset=['Weight_Class'])
        if df.empty:
            return None, pd.Timestamp.min
        if 'Date' in df.columns:
            df = df.sort_values('Date')
            row = df.iloc[-1]
            return row['Weight_Class'], row['Date']
        row = df.iloc[-1]
        return row['Weight_Class'], pd.Timestamp.min

    w1, d1 = _last_weight(f1_name)
    w2, d2 = _last_weight(f2_name)
    if w1 and w2 and str(w1).strip().lower() == str(w2).strip().lower():
        return w1
    if w1 and not w2:
        return w1
    if w2 and not w1:
        return w2
    if w1 and w2:
        return w1 if d1 >= d2 else w2
    return None


def _last_weight_class(name, fights_df):
    if fights_df is None or 'Weight_Class' not in fights_df.columns:
        return None
    df = fights_df[(fights_df['Fighter_1'] == name) | (fights_df['Fighter_2'] == name)]
    if df.empty:
        return None
    df = df.dropna(subset=['Weight_Class'])
    if df.empty:
        return None
    if 'Date' in df.columns:
        df = df.sort_values('Date')
        return df.iloc[-1]['Weight_Class']
    return df.iloc[-1]['Weight_Class']

# helper to build features for new match (F1 is red corner)
def build_features_for_match(f1_name, f2_name, fights_df=fights_merged, fighters_phys=fighters, feature_list=feature_cols, weight_class=None):
    # create row with zeros
    row = pd.Series(index=feature_list, dtype=float).fillna(0.0)

    # physicals lookup
    def get_phys(name):
        try:
            r = fighters_phys[fighters_phys['Full Name']==name].iloc[0]
            return {
                'Ht.': r.get('Ht.', np.nan),
                'Wt.': r.get('Wt.', np.nan),
                'Reach': r.get('Reach', np.nan),
                'Stance': r.get('Stance', None),
                'Belt': r.get('Belt', None),
            }
        except:
            return {'Ht.':np.nan,'Wt.':np.nan,'Reach':np.nan,'Stance':None,'Belt':None}

    p1 = get_phys(f1_name); p2 = get_phys(f2_name)
    f1_belt = 1 if p1.get('Belt') else 0
    f2_belt = 1 if p2.get('Belt') else 0

    # use aggregated histories from full dataset
    def avg_stat(name, key):
        cnt = stats_cnt[name]
        return (stats_sum[name][key] / cnt) if cnt > 0 else 0.0

    now = pd.Timestamp.now()

    def decayed_stat(name, key):
        return _decayed_avg(history[name], key, now)

    f1_avg = {k: avg_stat(f1_name, k) for k in ['STR','KD','TD','SUB','SIG']}
    f2_avg = {k: avg_stat(f2_name, k) for k in ['STR','KD','TD','SUB','SIG']}

    f1_decay = {k: decayed_stat(f1_name, k) for k in ['STR','KD','TD','SUB','SIG']}
    f2_decay = {k: decayed_stat(f2_name, k) for k in ['STR','KD','TD','SUB','SIG']}

    # opponent-adjusted stats and opponent Elo averages
    f1_adj = {k: _decayed_avg(opp_hist[f1_name], k, now) for k in ['STR_adj','KD_adj','TD_adj','SUB_adj','SIG_adj']}
    f2_adj = {k: _decayed_avg(opp_hist[f2_name], k, now) for k in ['STR_adj','KD_adj','TD_adj','SUB_adj','SIG_adj']}
    f1_opp_elo_avg = _decayed_avg(opp_hist[f1_name], 'opp_elo', now)
    f2_opp_elo_avg = _decayed_avg(opp_hist[f2_name], 'opp_elo', now)

    f1_ko_rate = _ratio(f1_decay['KD'], f1_decay['STR'])
    f2_ko_rate = _ratio(f2_decay['KD'], f2_decay['STR'])
    f1_ko_rate_adj = _ratio(f1_adj['KD_adj'], f1_adj['STR_adj'])
    f2_ko_rate_adj = _ratio(f2_adj['KD_adj'], f2_adj['STR_adj'])

    def winrate(name):
        w = wins[name]; l = losses[name]; d = draws[name]
        denom = w + l + d
        return (w / denom) if denom > 0 else 0.0

    f1_wr = winrate(f1_name)
    f2_wr = winrate(f2_name)

    f1_decay_wr = _decayed_avg(history[f1_name], 'win_score', now)
    f2_decay_wr = _decayed_avg(history[f2_name], 'win_score', now)

    # recent diffs based on last N fights
    def recent_metrics(name):
        hist = list(recent[name])
        if not hist:
            return {'winrate': 0.0, 'STR': 0.0, 'KD': 0.0, 'TD': 0.0, 'SUB': 0.0}
        wins_n = sum(1 for h in hist if h['win'])
        winrate = wins_n / len(hist)
        return {
            'winrate': winrate,
            'STR': _weighted_avg([h['STR'] for h in hist], [0.6, 0.3, 0.1]),
            'KD': _weighted_avg([h['KD'] for h in hist], [0.6, 0.3, 0.1]),
            'TD': _weighted_avg([h['TD'] for h in hist], [0.6, 0.3, 0.1]),
            'SUB': _weighted_avg([h['SUB'] for h in hist], [0.6, 0.3, 0.1]),
        }

    r1 = recent_metrics(f1_name)
    r2 = recent_metrics(f2_name)

    # style heuristic
    def _style(avg):
        if avg['TD'] >= 1.4 or avg['SUB'] >= 0.9:
            return "Grappler"
        if avg['STR'] >= 20.0 and avg['TD'] < 0.5 and avg['SUB'] < 0.3:
            return "Striker"
        return "Hybrid"

    f1_style = _style(f1_decay)
    f2_style = _style(f2_decay)

    f1_stance = p1.get('Stance') if p1.get('Stance') else 'Unknown'
    f2_stance = p2.get('Stance') if p2.get('Stance') else 'Unknown'
    stance_matchup = f"{f1_stance}_vs_{f2_stance}"
    style_matchup = f"{f1_style}_vs_{f2_style}"

    # weight class: use provided value or infer from recent fights
    wc = _normalize_weight_class(weight_class, fights_df)
    if wc is None:
        wc = _infer_weight_class(f1_name, f2_name, fights_df)
        wc = _normalize_weight_class(wc, fights_df)

    # layoff and activity
    f1_last = last_fight_date.get(f1_name)
    f2_last = last_fight_date.get(f2_name)
    f1_days_since = (now - f1_last).days if f1_last is not None else np.nan
    f2_days_since = (now - f2_last).days if f2_last is not None else np.nan

    def _fights_last_365(name):
        if not history[name]:
            return 0
        return sum(1 for h in history[name] if (now - h['date']).days <= 365)

    f1_last_365 = _fights_last_365(f1_name)
    f2_last_365 = _fights_last_365(f2_name)

    # experience
    f1_prior = stats_cnt[f1_name]
    f2_prior = stats_cnt[f2_name]
    f1_w = wins[f1_name]; f1_l = losses[f1_name]; f1_d = draws[f1_name]
    f2_w = wins[f2_name]; f2_l = losses[f2_name]; f2_d = draws[f2_name]

    # momentum trends
    def _trend_metrics(name):
        hist = list(trend_hist[name])
        if not hist:
            return {'STR': 0.0, 'TD': 0.0}
        return {
            'STR': _trend_slope([h['STR'] for h in hist]),
            'TD': _trend_slope([h['TD'] for h in hist])
        }

    f1_trend = _trend_metrics(f1_name)
    f2_trend = _trend_metrics(f2_name)

    # Elo
    f1_elo = elo[f1_name]
    f2_elo = elo[f2_name]

    # Fill row
    row['F1_WinRate'] = f1_wr
    row['F2_WinRate'] = f2_wr
    row['F1_DecayWinRate'] = f1_decay_wr
    row['F2_DecayWinRate'] = f2_decay_wr

    if 'F1_Belt' in feature_list:
        row['F1_Belt'] = f1_belt
        row['F2_Belt'] = f2_belt
    if 'Belt_diff' in feature_list:
        row['Belt_diff'] = f1_belt - f2_belt

    if 'F1_KO_rate' in feature_list:
        row['F1_KO_rate'] = f1_ko_rate
        row['F2_KO_rate'] = f2_ko_rate
        row['F1_KO_rate_adj'] = f1_ko_rate_adj
        row['F2_KO_rate_adj'] = f2_ko_rate_adj
    if 'KO_rate_diff' in feature_list:
        row['KO_rate_diff'] = f1_ko_rate - f2_ko_rate
        row['KO_rate_adj_diff'] = f1_ko_rate_adj - f2_ko_rate_adj

    # opponent-adjusted stats and opponent Elo
    if 'F1_STR_adj' in feature_list:
        row['F1_STR_adj'] = f1_adj['STR_adj']
        row['F2_STR_adj'] = f2_adj['STR_adj']
        row['F1_TD_adj'] = f1_adj['TD_adj']
        row['F2_TD_adj'] = f2_adj['TD_adj']
        row['F1_KD_adj'] = f1_adj['KD_adj']
        row['F2_KD_adj'] = f2_adj['KD_adj']
        row['F1_SUB_adj'] = f1_adj['SUB_adj']
        row['F2_SUB_adj'] = f2_adj['SUB_adj']
        row['F1_SIG_adj'] = f1_adj['SIG_adj']
        row['F2_SIG_adj'] = f2_adj['SIG_adj']
    if 'F1_opp_elo' in feature_list:
        row['F1_opp_elo'] = f1_opp_elo_avg
        row['F2_opp_elo'] = f2_opp_elo_avg

    for stat in ['STR','TD','KD','SUB']:
        if f'{stat}_diff' in feature_list:
            row[f'{stat}_diff'] = f1_decay[stat] - f2_decay[stat]
        if f'{stat}_1' in feature_list:
            row[f'{stat}_1'] = f1_decay[stat]
        if f'{stat}_2' in feature_list:
            row[f'{stat}_2'] = f2_decay[stat]

    if 'F1_SIG_decay' in feature_list:
        row['F1_SIG_decay'] = f1_decay['SIG']
        row['F2_SIG_decay'] = f2_decay['SIG']
    if 'SigStr_diff' in feature_list:
        row['SigStr_diff'] = f1_decay['SIG'] - f2_decay['SIG']

    if 'STR_adj_diff' in feature_list:
        row['STR_adj_diff'] = f1_adj['STR_adj'] - f2_adj['STR_adj']
        row['TD_adj_diff'] = f1_adj['TD_adj'] - f2_adj['TD_adj']
        row['KD_adj_diff'] = f1_adj['KD_adj'] - f2_adj['KD_adj']
        row['SUB_adj_diff'] = f1_adj['SUB_adj'] - f2_adj['SUB_adj']
        row['SIG_adj_diff'] = f1_adj['SIG_adj'] - f2_adj['SIG_adj']
    if 'OppElo_diff' in feature_list:
        row['OppElo_diff'] = f1_opp_elo_avg - f2_opp_elo_avg

    # physical diffs
    if 'Ht._diff' in feature_list:
        row['Ht._diff'] = (p1['Ht.'] if not pd.isna(p1['Ht.']) else 0) - (p2['Ht.'] if not pd.isna(p2['Ht.']) else 0)
    if 'Wt._diff' in feature_list:
        row['Wt._diff'] = (p1['Wt.'] if not pd.isna(p1['Wt.']) else 0) - (p2['Wt.'] if not pd.isna(p2['Wt.']) else 0)
    if 'Reach_diff' in feature_list:
        row['Reach_diff'] = (p1['Reach'] if not pd.isna(p1['Reach']) else 0) - (p2['Reach'] if not pd.isna(p2['Reach']) else 0)

    if 'WinRate_diff' in feature_list:
        row['WinRate_diff'] = f1_wr - f2_wr
    if 'DecayWinRate_diff' in feature_list:
        row['DecayWinRate_diff'] = f1_decay_wr - f2_decay_wr

    if 'Elo_diff' in feature_list:
        row['Elo_diff'] = f1_elo - f2_elo
    if 'F1_elo' in feature_list:
        row['F1_elo'] = f1_elo
        row['F2_elo'] = f2_elo

    if 'Layoff_diff' in feature_list:
        row['Layoff_diff'] = f1_days_since - f2_days_since
    if 'F1_days_since' in feature_list:
        row['F1_days_since'] = f1_days_since
        row['F2_days_since'] = f2_days_since
    if 'F1_fights_365' in feature_list:
        row['F1_fights_365'] = f1_last_365
        row['F2_fights_365'] = f2_last_365

    if 'Exp_diff' in feature_list:
        row['Exp_diff'] = f1_prior - f2_prior
    if 'F1_PriorFights' in feature_list:
        row['F1_PriorFights'] = f1_prior
        row['F2_PriorFights'] = f2_prior
    if 'F1_Wins' in feature_list:
        row['F1_Wins'] = f1_w
        row['F2_Wins'] = f2_w
        row['F1_Losses'] = f1_l
        row['F2_Losses'] = f2_l
        row['F1_Draws'] = f1_d
        row['F2_Draws'] = f2_d

    if 'STR_trend_diff' in feature_list:
        row['STR_trend_diff'] = f1_trend['STR'] - f2_trend['STR']
        row['TD_trend_diff'] = f1_trend['TD'] - f2_trend['TD']
    if 'F1_STR_trend' in feature_list:
        row['F1_STR_trend'] = f1_trend['STR']
        row['F2_STR_trend'] = f2_trend['STR']
        row['F1_TD_trend'] = f1_trend['TD']
        row['F2_TD_trend'] = f2_trend['TD']

    # categorical dummies
    def _set_dummy(prefix, value):
        if value is None:
            return
        if isinstance(value, float) and np.isnan(value):
            return
        col = f"{prefix}_{value}"
        if col in row.index:
            row[col] = 1.0

    _set_dummy('F1_Stance', f1_stance)
    _set_dummy('F2_Stance', f2_stance)
    _set_dummy('F1_Fighting Style', f1_style)
    _set_dummy('F2_Fighting Style', f2_style)
    _set_dummy('Stance_Matchup', stance_matchup)
    _set_dummy('Style_Matchup', style_matchup)
    if wc:
        _set_dummy('Weight_Class', wc)

    # ensure all feature columns exist
    for c in feature_list:
        if c not in row.index:
            row[c] = 0.0

    return pd.DataFrame([row[feature_list]])

# predict helper
def predict_match(f1_name, f2_name, weight_class=None, model_bundle_path="ufc_model_bundle_prefight_pro.joblib"):
    bundle = joblib.load(model_bundle_path)
    model = bundle['model']
    calibrator = bundle['calibrator']
    features = bundle['features']
    Xnew = build_features_for_match(f1_name, f2_name, fights_df=fights_merged,
                                    fighters_phys=fighters,
                                    feature_list=features,
                                    weight_class=weight_class)
    prob1 = calibrator.predict_proba(Xnew)[:,1][0]
    # swap order to remove red/blue ordering bias
    Xswap = build_features_for_match(f2_name, f1_name, fights_df=fights_merged,
                                     fighters_phys=fighters,
                                     feature_list=features,
                                     weight_class=weight_class)
    prob2 = calibrator.predict_proba(Xswap)[:,1][0]
    prob = (prob1 + (1.0 - prob2)) / 2.0
    return prob, Xnew


def _store_upcoming_predictions(model_version, model_path):
    ensure_schemas_and_tables()
    with engine.begin() as conn:
        rows = conn.execute(text(
            """
            SELECT id, fight_key, event_id, fighter_1_id, fighter_2_id, fighter_1, fighter_2, weight_class, scheduled_date, event_name
            FROM app.upcoming_fights
            WHERE is_active = true
            """
        )).mappings().all()

    if not rows:
        print("No upcoming fights found; skipping prediction storage.")
        return 0

    inserted = 0
    with engine.begin() as conn:
        for r in rows:
            f1 = r["fighter_1"]
            f2 = r["fighter_2"]
            wc = r.get("weight_class")
            sched = r.get("scheduled_date")
            fight_key = r.get("fight_key")
            upcoming_id = r.get("id")
            event_id = r.get("event_id")
            f1_id = r.get("fighter_1_id")
            f2_id = r.get("fighter_2_id")
            if not fight_key:
                fight_key = f"{(f1 or '').strip().lower()}|{(f2 or '').strip().lower()}|{event_id or ''}|{sched or ''}|{wc or ''}"
            try:
                prob, _ = predict_match(f1, f2, weight_class=wc, model_bundle_path=model_path)
            except Exception as e:
                print(f"Prediction failed for {f1} vs {f2}: {e}")
                continue

            conn.execute(text(
                """
                INSERT INTO app.predictions
                    (model_name, model_version, created_at, upcoming_id, fight_key, event_id, scheduled_date, fighter_1, fighter_2, fighter_1_id, fighter_2_id, weight_class, prob_f1, source)
                VALUES
                    (:model_name, :model_version, now(), :upcoming_id, :fight_key, :event_id, :scheduled_date, :fighter_1, :fighter_2, :fighter_1_id, :fighter_2_id, :weight_class, :prob_f1, :source)
                ON CONFLICT (model_version, fight_key)
                DO UPDATE SET
                    prob_f1 = EXCLUDED.prob_f1,
                    created_at = EXCLUDED.created_at,
                    source = EXCLUDED.source
                """
            ), {
                "model_name": "prefight_pro",
                "model_version": model_version,
                "upcoming_id": upcoming_id,
                "fight_key": fight_key,
                "event_id": event_id,
                "scheduled_date": sched,
                "fighter_1": f1,
                "fighter_2": f2,
                "fighter_1_id": f1_id,
                "fighter_2_id": f2_id,
                "weight_class": wc,
                "prob_f1": float(prob),
                "source": "upcoming"
            })
            inserted += 1

    print(f"Stored predictions for {inserted} upcoming fights.")
    return inserted


if os.getenv("UFC_PREDICT_UPCOMING", "0") == "1":
    _store_upcoming_predictions(model_version, model_path)


# Imports
from rapidfuzz import process
from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter

# Prepare fighter list
all_fighters = sorted(set(fighters['Full Name'].dropna().unique()))

# setup autocomplete
fighter_completer = WordCompleter(all_fighters, ignore_case=True, match_middle=True)

# Fuzzy name resolver
def find_closest_fighter_name(name):
    """Finds closest match to user input using fuzzy matching."""
    match, score, _ = process.extractOne(name, all_fighters)
    if score > 70:
        return match
    else:
        return None

# Interactive loop
def interactive_predict():
    print("\n=== UFC Fight Predictor (Pre-fight PRO features) ===")
    print("Type 'quit' at any time to exit.\n")

    while True:
        # input fighter 1 (autocomplete)
        f1_input = prompt("Enter Fighter 1: ", completer=fighter_completer)
        if f1_input.lower() == "quit":
            print("Exiting predictor...")
            break

        f2_input = prompt("Enter Fighter 2: ", completer=fighter_completer)
        if f2_input.lower() == "quit":
            print("Exiting predictor...")
            break

        # fuzzy match
        f1_name = find_closest_fighter_name(f1_input)
        f2_name = find_closest_fighter_name(f2_input)

        if not f1_name or not f2_name:
            print(" One or both fighter names not found. Try again.\n")
            continue

        print(f"\nMatched fighters: {f1_name}  vs  {f2_name}")

        # Only prompt for weight class if mismatch/unknown
        w1 = _last_weight_class(f1_name, fights_merged)
        w2 = _last_weight_class(f2_name, fights_merged)
        w1n = _normalize_weight_class(w1, fights_merged)
        w2n = _normalize_weight_class(w2, fights_merged)
        wc = None
        if w1n and w2n and w1n.lower() == w2n.lower():
            wc = w1n
        else:
            default_wc = _infer_weight_class(f1_name, f2_name, fights_merged)
            default_wc = _normalize_weight_class(default_wc, fights_merged)
            prompt_msg = "Enter weight class (optional, press Enter to auto-infer"
            if default_wc:
                prompt_msg += f" = {default_wc}"
            prompt_msg += "): "
            wc_input = prompt(prompt_msg)
            wc_input = wc_input.strip() if wc_input is not None else ""
            wc = wc_input if wc_input else default_wc

        # make prediction
        try:
            prob, Xnew = predict_match(f1_name, f2_name, weight_class=wc)
            print(f"-> Probability {f1_name} wins: {prob:.2%}\n")
        except Exception as e:
            print(f" Prediction failed: {e}\n")

# Run
if __name__ == "__main__":
    if os.getenv("UFC_INTERACTIVE", "1") == "1":
        try:
            interactive_predict()
        except Exception as e:
            print(f"Interactive predictor skipped: {e}")
    else:
        print("UFC_INTERACTIVE=0; skipping interactive predictor.")
