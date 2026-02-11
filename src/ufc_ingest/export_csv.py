"""Export contract tables to CSV files in ./data/ for backward compatibility."""
import os
import pandas as pd
from .db import get_engine

DATA_DIR = os.path.join(os.getcwd(), 'data')
os.makedirs(DATA_DIR, exist_ok=True)


def export_table(table_name: str, filename: str):
    engine = get_engine()
    df = pd.read_sql_table(table_name, con=engine, schema='contract')
    out = os.path.join(DATA_DIR, filename)
    df.to_csv(out, index=False)
    return out


def export_all():
    paths = {}
    paths['Events.csv'] = export_table('events_csv', 'Events.csv')
    paths['Fighters.csv'] = export_table('fighters_csv', 'Fighters.csv')
    paths['Fights.csv'] = export_table('fights_csv', 'Fights.csv')
    paths['Fighters_Stats.csv'] = export_table('fighters_stats_csv', 'Fighters_Stats.csv')
    return paths
