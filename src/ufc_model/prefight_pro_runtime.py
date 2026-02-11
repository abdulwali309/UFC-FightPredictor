from __future__ import annotations

import os
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import joblib
import numpy as np
import pandas as pd

from ufc_ingest.db import get_engine


N_RECENT = 3
TREND_N = 5
HALF_LIFE_DAYS = 730.0
DECAY_BASE = 0.5
K_ELO = 32.0


def _safe_num(x) -> float:
    return 0.0 if pd.isna(x) else float(x)


def _weighted_avg(vals, weights) -> float:
    if not vals:
        return 0.0
    w = weights[: len(vals)]
    return float(np.average(vals, weights=w))


def _decayed_avg(hist_list, key: str, current_date: pd.Timestamp) -> float:
    if not hist_list:
        return 0.0
    sum_w = 0.0
    sum_wx = 0.0
    for h in hist_list:
        dt_days = (current_date - h["date"]).days
        if dt_days < 0:
            dt_days = 0
        w = DECAY_BASE ** (dt_days / HALF_LIFE_DAYS)
        sum_w += w
        sum_wx += w * h[key]
    return (sum_wx / sum_w) if sum_w > 0 else 0.0


def _trend_slope(vals) -> float:
    if len(vals) < 2:
        return 0.0
    x = np.arange(len(vals))
    y = np.array(vals, dtype=float)
    try:
        slope = np.polyfit(x, y, 1)[0]
    except Exception:
        slope = 0.0
    return float(slope)


def _elo_expected(r_a: float, r_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400.0))


def _result_score(r) -> float:
    r = str(r).strip().upper()
    if r == "W":
        return 1.0
    if r == "L":
        return 0.0
    if r in {"D", "NC"}:
        return 0.5
    return 0.5


def _max_rounds(time_format) -> int:
    tf = str(time_format or "")
    for n in ["5", "3"]:
        if n in tf:
            return int(n)
    return 3


def _ratio(numer: float, denom: float) -> float:
    try:
        if denom and denom > 0:
            return float(numer) / float(denom)
    except Exception:
        pass
    return 0.0


def _norm_name(s: Optional[str]) -> str:
    return " ".join((s or "").strip().split())


@dataclass
class PrefightProRuntime:
    fights_df: pd.DataFrame
    fighters_df: pd.DataFrame
    events_df: pd.DataFrame
    phys_df: pd.DataFrame

    stats_sum: Dict[str, Dict[str, float]]
    stats_cnt: Dict[str, int]
    wins: Dict[str, int]
    losses: Dict[str, int]
    draws: Dict[str, int]
    recent: Dict[str, Any]
    trend_hist: Dict[str, Any]
    last_fight_date: Dict[str, pd.Timestamp]
    elo: Dict[str, float]
    history: Dict[str, Any]
    opp_hist: Dict[str, Any]

    global_phys_means: Dict[str, float]
    wc_phys_means: Dict[str, Dict[str, float]]


def _load_contract_tables() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    engine = get_engine()
    with engine.connect() as conn:
        fighters = pd.read_sql_query('SELECT * FROM contract.fighters_csv', conn)
        fights = pd.read_sql_query('SELECT * FROM contract.fights_csv', conn)
        events = pd.read_sql_query('SELECT * FROM contract.events_csv', conn)
    return fighters, fights, events


def build_runtime() -> PrefightProRuntime:
    fighters, fights, events = _load_contract_tables()

    # Parse event dates and merge onto fights
    events["Date"] = pd.to_datetime(events["Date"], errors="coerce")
    fights = fights.merge(events[["Event_Id", "Date"]], on="Event_Id", how="left")
    fights["Date"] = pd.to_datetime(fights["Date"], errors="coerce").fillna(pd.Timestamp("1900-01-01"))

    fights_sorted = fights.sort_values("Date").reset_index(drop=True)

    # Physicals
    phys_cols = ["Full Name", "Ht.", "Wt.", "Reach", "Stance", "Belt"]
    phys_df = fighters[phys_cols].drop_duplicates(subset="Full Name")

    # Physical imputation stats (global and by last known weight class)
    global_phys_means = {
        "Ht.": float(phys_df["Ht."].mean()) if "Ht." in phys_df.columns else float("nan"),
        "Wt.": float(phys_df["Wt."].mean()) if "Wt." in phys_df.columns else float("nan"),
        "Reach": float(phys_df["Reach"].mean()) if "Reach" in phys_df.columns else float("nan"),
    }
    # derive fighter -> last known weight class from fights
    last_wc = {}
    if "Weight_Class" in fights_sorted.columns:
        for _, r in fights_sorted.dropna(subset=["Weight_Class"]).iterrows():
            wc = str(r.get("Weight_Class") or "").strip()
            if not wc:
                continue
            f1 = r.get("Fighter_1")
            f2 = r.get("Fighter_2")
            if pd.notna(f1):
                last_wc[str(f1)] = wc
            if pd.notna(f2):
                last_wc[str(f2)] = wc
    wc_phys_means: Dict[str, Dict[str, float]] = defaultdict(dict)
    # compute means by weight class using fighters with known last weight class
    tmp = phys_df.copy()
    tmp["Weight_Class"] = tmp["Full Name"].map(last_wc)
    for wc, g in tmp.dropna(subset=["Weight_Class"]).groupby("Weight_Class"):
        wc_phys_means[wc] = {
            "Ht.": float(g["Ht."].mean()) if g["Ht."].notna().any() else global_phys_means["Ht."],
            "Wt.": float(g["Wt."].mean()) if g["Wt."].notna().any() else global_phys_means["Wt."],
            "Reach": float(g["Reach"].mean()) if g["Reach"].notna().any() else global_phys_means["Reach"],
        }

    stats_sum = defaultdict(lambda: {"STR": 0.0, "KD": 0.0, "TD": 0.0, "SUB": 0.0, "SIG": 0.0})
    stats_cnt = defaultdict(int)
    wins = defaultdict(int)
    losses = defaultdict(int)
    draws = defaultdict(int)
    recent = defaultdict(lambda: deque(maxlen=N_RECENT))
    trend_hist = defaultdict(lambda: deque(maxlen=TREND_N))
    last_fight_date: Dict[str, pd.Timestamp] = {}
    elo = defaultdict(lambda: 1500.0)
    history = defaultdict(list)
    opp_hist = defaultdict(list)

    def _apply_updates(row):
        f1 = row["Fighter_1"]
        f2 = row["Fighter_2"]
        if pd.isna(f1) or pd.isna(f2):
            return
        f1 = str(f1)
        f2 = str(f2)
        fight_date = row["Date"]

        r1 = str(row.get("Result_1")).strip().upper()
        r2 = str(row.get("Result_2")).strip().upper()
        if r1 == "W":
            wins[f1] += 1
        elif r1 == "L":
            losses[f1] += 1
        elif r1 == "D":
            draws[f1] += 1

        if r2 == "W":
            wins[f2] += 1
        elif r2 == "L":
            losses[f2] += 1
        elif r2 == "D":
            draws[f2] += 1

        old_f1 = elo[f1]
        old_f2 = elo[f2]

        for name, prefix in [(f1, "_1"), (f2, "_2")]:
            str_val = _safe_num(row.get(f"STR{prefix}"))
            kd_val = _safe_num(row.get(f"KD{prefix}"))
            td_val = _safe_num(row.get(f"TD{prefix}"))
            sub_val = _safe_num(row.get(f"SUB{prefix}"))
            sig_val = _safe_num(row.get(f"Sig. Str. %{prefix}"))

            stats_sum[name]["STR"] += str_val
            stats_sum[name]["KD"] += kd_val
            stats_sum[name]["TD"] += td_val
            stats_sum[name]["SUB"] += sub_val
            stats_sum[name]["SIG"] += sig_val
            stats_cnt[name] += 1
            win_score = _result_score(r1 if prefix == "_1" else r2)
            history[name].append(
                {
                    "date": fight_date,
                    "STR": str_val,
                    "KD": kd_val,
                    "TD": td_val,
                    "SUB": sub_val,
                    "SIG": sig_val,
                    "win_score": win_score,
                }
            )

            opp_elo = old_f2 if prefix == "_1" else old_f1
            opp_factor = opp_elo / 1500.0 if opp_elo else 1.0
            opp_hist[name].append(
                {
                    "date": fight_date,
                    "STR_adj": str_val * opp_factor,
                    "KD_adj": kd_val * opp_factor,
                    "TD_adj": td_val * opp_factor,
                    "SUB_adj": sub_val * opp_factor,
                    "SIG_adj": sig_val * opp_factor,
                    "opp_elo": opp_elo,
                }
            )

            recent[name].append(
                {
                    "STR": str_val,
                    "KD": kd_val,
                    "TD": td_val,
                    "SUB": sub_val,
                    "win": (r1 == "W") if prefix == "_1" else (r2 == "W"),
                }
            )
            trend_hist[name].append({"STR": str_val, "TD": td_val})
            last_fight_date[name] = fight_date

        exp1 = _elo_expected(old_f1, old_f2)
        exp2 = _elo_expected(old_f2, old_f1)
        s1 = _result_score(r1)
        s2 = _result_score(r2)
        method = str(row.get("Method") or "").upper()
        is_decision = "DEC" in method
        max_r = _max_rounds(row.get("Time Format"))
        try:
            rnd = int(row.get("Round")) if row.get("Round") is not None else 1
        except Exception:
            rnd = 1
        if rnd < 1:
            rnd = 1
        finish_mult = 1.0
        if not is_decision:
            finish_mult = 1.1 + (max_r - rnd) / max_r * 0.4
        k = K_ELO * finish_mult
        elo[f1] = old_f1 + k * (s1 - exp1)
        elo[f2] = old_f2 + k * (s2 - exp2)

    for _, row in fights_sorted.iterrows():
        _apply_updates(row)

    return PrefightProRuntime(
        fights_df=fights,
        fighters_df=fighters,
        events_df=events,
        phys_df=phys_df,
        stats_sum=stats_sum,
        stats_cnt=stats_cnt,
        wins=wins,
        losses=losses,
        draws=draws,
        recent=recent,
        trend_hist=trend_hist,
        last_fight_date=last_fight_date,
        elo=elo,
        history=history,
        opp_hist=opp_hist,
        global_phys_means=global_phys_means,
        wc_phys_means=wc_phys_means,
    )


def _normalize_weight_class(weight_class, fights_df: pd.DataFrame) -> Optional[str]:
    if weight_class is None:
        return None
    wc = str(weight_class).strip()
    if not wc:
        return None
    if fights_df is None or "Weight_Class" not in fights_df.columns:
        return wc
    classes = [str(c).strip() for c in fights_df["Weight_Class"].dropna().unique()]
    for c in classes:
        if c.lower() == wc.lower():
            return c
    return wc


def _infer_weight_class(f1_name: str, f2_name: str, fights_df: pd.DataFrame) -> Optional[str]:
    if fights_df is None or "Weight_Class" not in fights_df.columns:
        return None

    def _last_weight(name: str):
        df = fights_df[(fights_df["Fighter_1"] == name) | (fights_df["Fighter_2"] == name)]
        if df.empty:
            return None, pd.Timestamp.min
        df = df.dropna(subset=["Weight_Class"])
        if df.empty:
            return None, pd.Timestamp.min
        if "Date" in df.columns:
            df = df.sort_values("Date")
            row = df.iloc[-1]
            return row["Weight_Class"], row["Date"]
        row = df.iloc[-1]
        return row["Weight_Class"], pd.Timestamp.min

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


def build_features_for_match(
    rt: PrefightProRuntime,
    f1_name: str,
    f2_name: str,
    feature_list,
    weight_class: Optional[str] = None,
) -> pd.DataFrame:
    f1_name = _norm_name(f1_name)
    f2_name = _norm_name(f2_name)
    if not f1_name or not f2_name:
        raise ValueError("Both fighter names are required")

    row = pd.Series(index=feature_list, dtype=float).fillna(0.0)

    # physicals lookup (contract.fighters_csv)
    def get_phys(name: str):
        try:
            r = rt.phys_df[rt.phys_df["Full Name"] == name].iloc[0]
            return {
                "Ht.": r.get("Ht.", np.nan),
                "Wt.": r.get("Wt.", np.nan),
                "Reach": r.get("Reach", np.nan),
                "Stance": r.get("Stance", None),
                "Belt": r.get("Belt", None),
            }
        except Exception:
            return {"Ht.": np.nan, "Wt.": np.nan, "Reach": np.nan, "Stance": None, "Belt": None}

    p1 = get_phys(f1_name)
    p2 = get_phys(f2_name)

    # weight class: normalize provided value or infer from recent fights
    wc = _normalize_weight_class(weight_class, rt.fights_df)
    if wc is None:
        wc = _infer_weight_class(f1_name, f2_name, rt.fights_df)
        wc = _normalize_weight_class(wc, rt.fights_df)

    def _impute_phys(p: dict, wc_name: Optional[str]) -> dict:
        out = dict(p)
        means = rt.wc_phys_means.get(wc_name) if wc_name else None
        for k in ["Ht.", "Wt.", "Reach"]:
            v = out.get(k)
            if v is None or (isinstance(v, float) and np.isnan(v)):
                if means and means.get(k) is not None and not (isinstance(means.get(k), float) and np.isnan(means.get(k))):
                    out[k] = means.get(k)
                else:
                    out[k] = rt.global_phys_means.get(k, 0.0)
        return out

    p1 = _impute_phys(p1, wc)
    p2 = _impute_phys(p2, wc)

    f1_belt = 1 if p1.get("Belt") else 0
    f2_belt = 1 if p2.get("Belt") else 0

    # simple averages from full dataset
    def avg_stat(name: str, key: str) -> float:
        cnt = rt.stats_cnt[name]
        return (rt.stats_sum[name][key] / cnt) if cnt > 0 else 0.0

    now = pd.Timestamp.now()

    def decayed_stat(name: str, key: str) -> float:
        return _decayed_avg(rt.history[name], key, now)

    f1_avg = {k: avg_stat(f1_name, k) for k in ["STR", "KD", "TD", "SUB", "SIG"]}
    f2_avg = {k: avg_stat(f2_name, k) for k in ["STR", "KD", "TD", "SUB", "SIG"]}

    f1_decay = {k: decayed_stat(f1_name, k) for k in ["STR", "KD", "TD", "SUB", "SIG"]}
    f2_decay = {k: decayed_stat(f2_name, k) for k in ["STR", "KD", "TD", "SUB", "SIG"]}

    # opponent-adjusted stats and opponent Elo averages
    f1_adj = {k: _decayed_avg(rt.opp_hist[f1_name], k, now) for k in ["STR_adj", "KD_adj", "TD_adj", "SUB_adj", "SIG_adj"]}
    f2_adj = {k: _decayed_avg(rt.opp_hist[f2_name], k, now) for k in ["STR_adj", "KD_adj", "TD_adj", "SUB_adj", "SIG_adj"]}
    f1_opp_elo_avg = _decayed_avg(rt.opp_hist[f1_name], "opp_elo", now)
    f2_opp_elo_avg = _decayed_avg(rt.opp_hist[f2_name], "opp_elo", now)

    f1_ko_rate = _ratio(f1_decay["KD"], f1_decay["STR"])
    f2_ko_rate = _ratio(f2_decay["KD"], f2_decay["STR"])
    f1_ko_rate_adj = _ratio(f1_adj["KD_adj"], f1_adj["STR_adj"])
    f2_ko_rate_adj = _ratio(f2_adj["KD_adj"], f2_adj["STR_adj"])

    def winrate(name: str) -> float:
        w = rt.wins[name]
        l = rt.losses[name]
        d = rt.draws[name]
        denom = w + l + d
        return (w / denom) if denom > 0 else 0.0

    f1_wr = winrate(f1_name)
    f2_wr = winrate(f2_name)

    f1_decay_wr = _decayed_avg(rt.history[f1_name], "win_score", now)
    f2_decay_wr = _decayed_avg(rt.history[f2_name], "win_score", now)

    def recent_metrics(name: str):
        hist = list(rt.recent[name])
        if not hist:
            return {"winrate": 0.0, "STR": 0.0, "KD": 0.0, "TD": 0.0, "SUB": 0.0}
        wins_n = sum(1 for h in hist if h["win"])
        winrate_n = wins_n / len(hist)
        return {
            "winrate": winrate_n,
            "STR": _weighted_avg([h["STR"] for h in hist], [0.6, 0.3, 0.1]),
            "KD": _weighted_avg([h["KD"] for h in hist], [0.6, 0.3, 0.1]),
            "TD": _weighted_avg([h["TD"] for h in hist], [0.6, 0.3, 0.1]),
            "SUB": _weighted_avg([h["SUB"] for h in hist], [0.6, 0.3, 0.1]),
        }

    r1 = recent_metrics(f1_name)
    r2 = recent_metrics(f2_name)

    def _style(avg: dict) -> str:
        if avg["TD"] >= 1.4 or avg["SUB"] >= 0.9:
            return "Grappler"
        if avg["STR"] >= 20.0 and avg["TD"] < 0.5 and avg["SUB"] < 0.3:
            return "Striker"
        return "Hybrid"

    f1_style = _style(f1_decay)
    f2_style = _style(f2_decay)

    f1_stance = p1.get("Stance") if p1.get("Stance") else "Unknown"
    f2_stance = p2.get("Stance") if p2.get("Stance") else "Unknown"
    stance_matchup = f"{f1_stance}_vs_{f2_stance}"
    style_matchup = f"{f1_style}_vs_{f2_style}"

    # layoff and activity
    f1_last = rt.last_fight_date.get(f1_name)
    f2_last = rt.last_fight_date.get(f2_name)
    f1_days_since = (now - f1_last).days if f1_last is not None else np.nan
    f2_days_since = (now - f2_last).days if f2_last is not None else np.nan

    def _fights_last_365(name: str) -> int:
        if not rt.history[name]:
            return 0
        return sum(1 for h in rt.history[name] if (now - h["date"]).days <= 365)

    f1_last_365 = _fights_last_365(f1_name)
    f2_last_365 = _fights_last_365(f2_name)

    f1_prior = rt.stats_cnt[f1_name]
    f2_prior = rt.stats_cnt[f2_name]
    f1_w = rt.wins[f1_name]
    f1_l = rt.losses[f1_name]
    f1_d = rt.draws[f1_name]
    f2_w = rt.wins[f2_name]
    f2_l = rt.losses[f2_name]
    f2_d = rt.draws[f2_name]

    def _trend_metrics(name: str):
        hist = list(rt.trend_hist[name])
        if not hist:
            return {"STR": 0.0, "TD": 0.0}
        return {
            "STR": _trend_slope([h["STR"] for h in hist]),
            "TD": _trend_slope([h["TD"] for h in hist]),
        }

    f1_trend = _trend_metrics(f1_name)
    f2_trend = _trend_metrics(f2_name)

    f1_elo = rt.elo[f1_name]
    f2_elo = rt.elo[f2_name]

    # Fill row (match training naming)
    def _set(name: str, val: Any):
        if name in row.index:
            row[name] = float(val) if val is not None and not (isinstance(val, float) and np.isnan(val)) else 0.0

    _set("F1_WinRate", f1_wr)
    _set("F2_WinRate", f2_wr)
    _set("F1_DecayWinRate", f1_decay_wr)
    _set("F2_DecayWinRate", f2_decay_wr)
    _set("F1_KO_rate", f1_ko_rate)
    _set("F2_KO_rate", f2_ko_rate)
    _set("F1_KO_rate_adj", f1_ko_rate_adj)
    _set("F2_KO_rate_adj", f2_ko_rate_adj)
    _set("F1_Belt", f1_belt)
    _set("F2_Belt", f2_belt)

    # core decayed stats
    for stat in ["STR", "TD", "KD", "SUB"]:
        _set(f"F1_{stat}_avg", f1_avg[stat])
        _set(f"F2_{stat}_avg", f2_avg[stat])
        _set(f"F1_{stat}_decay", f1_decay[stat])
        _set(f"F2_{stat}_decay", f2_decay[stat])

    _set("F1_SIG_avg", f1_avg["SIG"])
    _set("F2_SIG_avg", f2_avg["SIG"])
    _set("F1_SIG_decay", f1_decay["SIG"])
    _set("F2_SIG_decay", f2_decay["SIG"])

    # opponent adjusted
    for k in ["STR_adj", "TD_adj", "KD_adj", "SUB_adj", "SIG_adj"]:
        _set(f"F1_{k}", f1_adj[k])
        _set(f"F2_{k}", f2_adj[k])
    _set("F1_opp_elo", f1_opp_elo_avg)
    _set("F2_opp_elo", f2_opp_elo_avg)
    _set("F1_elo", f1_elo)
    _set("F2_elo", f2_elo)

    # recent
    _set("F1_recent_winrate", r1["winrate"])
    _set("F2_recent_winrate", r2["winrate"])
    for stat in ["STR", "KD", "TD", "SUB"]:
        _set(f"F1_recent_{stat}", r1[stat])
        _set(f"F2_recent_{stat}", r2[stat])

    # activity / experience
    _set("F1_days_since", f1_days_since)
    _set("F2_days_since", f2_days_since)
    _set("F1_fights_365", f1_last_365)
    _set("F2_fights_365", f2_last_365)
    _set("F1_PriorFights", f1_prior)
    _set("F2_PriorFights", f2_prior)
    _set("F1_Wins", f1_w)
    _set("F2_Wins", f2_w)
    _set("F1_Losses", f1_l)
    _set("F2_Losses", f2_l)
    _set("F1_Draws", f1_d)
    _set("F2_Draws", f2_d)
    _set("F1_STR_trend", f1_trend["STR"])
    _set("F2_STR_trend", f2_trend["STR"])
    _set("F1_TD_trend", f1_trend["TD"])
    _set("F2_TD_trend", f2_trend["TD"])

    # derived diffs (match training)
    def _diff(name: str, a: float, b: float):
        _set(name, a - b)

    _diff("STR_diff", f1_decay["STR"], f2_decay["STR"])
    _diff("TD_diff", f1_decay["TD"], f2_decay["TD"])
    _diff("KD_diff", f1_decay["KD"], f2_decay["KD"])
    _diff("SUB_diff", f1_decay["SUB"], f2_decay["SUB"])
    _diff("SigStr_diff", f1_decay["SIG"], f2_decay["SIG"])

    _diff("STR_adj_diff", f1_adj["STR_adj"], f2_adj["STR_adj"])
    _diff("TD_adj_diff", f1_adj["TD_adj"], f2_adj["TD_adj"])
    _diff("KD_adj_diff", f1_adj["KD_adj"], f2_adj["KD_adj"])
    _diff("SUB_adj_diff", f1_adj["SUB_adj"], f2_adj["SUB_adj"])
    _diff("SIG_adj_diff", f1_adj["SIG_adj"], f2_adj["SIG_adj"])
    _diff("OppElo_diff", f1_opp_elo_avg, f2_opp_elo_avg)

    _diff("KO_rate_diff", f1_ko_rate, f2_ko_rate)
    _diff("KO_rate_adj_diff", f1_ko_rate_adj, f2_ko_rate_adj)
    _diff("Belt_diff", f1_belt, f2_belt)
    _diff("WinRate_diff", f1_wr, f2_wr)
    _diff("DecayWinRate_diff", f1_decay_wr, f2_decay_wr)
    _diff("Elo_diff", f1_elo, f2_elo)
    _diff("Layoff_diff", f1_days_since, f2_days_since)
    _diff("Exp_diff", f1_prior, f2_prior)
    _diff("Wins_diff", f1_w, f2_w)
    _diff("Losses_diff", f1_l, f2_l)
    _diff("Draws_diff", f1_d, f2_d)
    _diff("STR_trend_diff", f1_trend["STR"], f2_trend["STR"])
    _diff("TD_trend_diff", f1_trend["TD"], f2_trend["TD"])

    # physical diffs
    _diff("Reach_diff", float(p1["Reach"]), float(p2["Reach"]))
    _diff("Ht._diff", float(p1["Ht."]), float(p2["Ht."]))
    _diff("Wt._diff", float(p1["Wt."]), float(p2["Wt."]))

    # recent diffs
    for m in ["STR", "KD", "TD", "SUB"]:
        _diff(f"{m}_recent_diff", r1[m], r2[m])
    _diff("recent_winrate_diff", r1["winrate"], r2["winrate"])

    # raw side stats (decayed) as *_1/*_2 like training expects
    _set("STR_1", f1_decay["STR"])
    _set("STR_2", f2_decay["STR"])
    _set("TD_1", f1_decay["TD"])
    _set("TD_2", f2_decay["TD"])
    _set("KD_1", f1_decay["KD"])
    _set("KD_2", f2_decay["KD"])
    _set("SUB_1", f1_decay["SUB"])
    _set("SUB_2", f2_decay["SUB"])

    # categorical dummies
    def _set_dummy(prefix: str, value: Any):
        if value is None:
            return
        if isinstance(value, float) and np.isnan(value):
            return
        col = f"{prefix}_{value}"
        if col in row.index:
            row[col] = 1.0

    _set_dummy("F1_Stance", f1_stance)
    _set_dummy("F2_Stance", f2_stance)
    _set_dummy("F1_Fighting Style", f1_style)
    _set_dummy("F2_Fighting Style", f2_style)
    _set_dummy("Stance_Matchup", stance_matchup)
    _set_dummy("Style_Matchup", style_matchup)
    if wc:
        _set_dummy("Weight_Class", wc)

    # ensure all feature columns exist
    for c in feature_list:
        if c not in row.index:
            row[c] = 0.0

    return pd.DataFrame([row[feature_list]])


def predict_match(
    rt: PrefightProRuntime,
    f1_name: str,
    f2_name: str,
    model_bundle_path: str,
    weight_class: Optional[str] = None,
) -> Tuple[float, pd.DataFrame]:
    bundle = joblib.load(model_bundle_path)
    calibrator = bundle["calibrator"]
    features = bundle["features"]

    Xnew = build_features_for_match(rt, f1_name, f2_name, feature_list=features, weight_class=weight_class)
    prob1 = float(calibrator.predict_proba(Xnew)[:, 1][0])

    Xswap = build_features_for_match(rt, f2_name, f1_name, feature_list=features, weight_class=weight_class)
    prob2 = float(calibrator.predict_proba(Xswap)[:, 1][0])

    prob = (prob1 + (1.0 - prob2)) / 2.0
    return prob, Xnew


def default_model_bundle_path() -> str:
    return os.getenv("UFC_MODEL_BUNDLE_PATH") or os.getenv("UFC_MODEL_PATH") or "ufc_model_bundle_prefight_pro.joblib"

