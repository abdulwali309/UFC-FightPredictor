"""Quick DB inspection script for debugging smoke ingest outputs."""
from ufc_ingest.db import get_engine
engine = get_engine()

def q(sql):
    try:
        return list(engine.execute(sql))
    except Exception as e:
        return str(e)

checks = [
    ("count_events","SELECT count(*) FROM ufc.events"),
    ("count_fights","SELECT count(*) FROM ufc.fights"),
    ("count_fight_totals","SELECT count(*) FROM ufc.fight_totals"),
    ("count_fighters","SELECT count(*) FROM ufc.fighters"),
    ("sample_fights","SELECT fight_id, event_id, red_fighter_id, blue_fighter_id, weight_class, method, round, fight_time FROM ufc.fights LIMIT 10"),
    ("sample_totals","SELECT fight_id, corner, kd, \"str\", td, sub, sig_str_pct, ctrl_seconds FROM ufc.fight_totals LIMIT 20"),
    ("sample_fighters","SELECT fighter_id, full_name, ht_inches, wt_lbs, reach_inches, stance FROM ufc.fighters LIMIT 10"),
    ("count_contract_fights","SELECT count(*) FROM contract.fights_csv"),
    ("sample_contract_fights","SELECT \"Fighter_1\", \"Fighter_2\", \"KD_1\", \"KD_2\", \"Ctrl_1\", \"Ctrl_2\" FROM contract.fights_csv LIMIT 10")
]

for name, sql in checks:
    print('---', name, '---')
    res = q(sql)
    for r in (res if isinstance(res, list) else [res]):
        print(r)
    print()
