"""Create schemas and tables if not exists."""
from sqlalchemy import text
from .db import get_engine
from .models import Base
import logging

logger = logging.getLogger(__name__)


def ensure_schemas_and_tables():
    engine = get_engine()
    with engine.begin() as conn:
        # Create schemas
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS ufc"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS contract"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
        logger.info("Ensured schemas exist")
    # Create tables from models
    Base.metadata.create_all(bind=engine)

    # Create contract tables if not exist with exact column names (SQL to ensure quotation and types)
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS contract.events_csv (
                "Event_Id" text PRIMARY KEY,
                "Name" text,
                "Date" text,
                "Location" text
            )
            """
        ))
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS contract.fighters_csv (
                "Full Name" text PRIMARY KEY,
                "Nickname" text,
                "Ht." double precision,
                "Wt." double precision,
                "Reach" double precision,
                "Stance" text,
                "W" integer,
                "L" integer,
                "D" integer,
                "Belt" boolean
            )
            """
        ))
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS contract.fights_csv (
                "Fighter_1" text,
                "Fighter_2" text,
                "KD_1" integer,
                "KD_2" integer,
                "STR_1" integer,
                "STR_2" integer,
                "TD_1" integer,
                "TD_2" integer,
                "SUB_1" integer,
                "SUB_2" integer,
                "Weight_Class" text,
                "Method" text,
                "Round" integer,
                "Fight_Time" text,
                "Event_Id" text,
                "Result_1" text,
                "Result_2" text,
                "Time Format" text,
                "Referee" text,
                "Method Details" text,
                "Sig. Str. %_1" double precision,
                "Sig. Str. %_2" double precision,
                "Sub. Att_1" integer,
                "Sub. Att_2" integer,
                "Rev._1" integer,
                "Rev._2" integer,
                "Ctrl_1" double precision,
                "Ctrl_2" double precision,
                "Head_%_1" double precision,
                "Head_%_2" double precision,
                "Body_%_1" double precision,
                "Body_%_2" double precision,
                "Leg_%_1" double precision,
                "Leg_%_2" double precision,
                "Distance_%_1" double precision,
                "Distance_%_2" double precision,
                "Clinch_%_1" double precision,
                "Clinch_%_2" double precision,
                "Ground_%_1" double precision,
                "Ground_%_2" double precision,
                "Total Str._%_1" double precision,
                "Total Str._%_2" double precision,
                "Sig. Str._%_1" double precision,
                "Sig. Str._%_2" double precision,
                UNIQUE ("Event_Id", "Fighter_1", "Fighter_2")
            )
            """
        ))
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS contract.fighters_stats_csv (
                "Fighter_Id" text PRIMARY KEY,
                "Full Name" text,
                "Nickname" text,
                "Ht." double precision,
                "Wt." double precision,
                "Stance" text,
                "W" integer,
                "L" integer,
                "D" integer,
                "Belt" boolean,
                "Round" double precision,
                "KD" double precision,
                "STR" double precision,
                "TD" double precision,
                "SUB" double precision,
                "Ctrl" double precision,
                "Sig. Str. %" double precision,
                "Head_%" double precision,
                "Body_%" double precision,
                "Leg_%" double precision,
                "Distance_%" double precision,
                "Clinch_%" double precision,
                "Ground_%" double precision,
                "Sub. Att" double precision,
                "Rev." double precision,
                "Weight_Class" text,
                "Gender" text,
                "Fighting Style" text
            )
            """
        ))
    logger.info("Ensured contract tables exist")


def validate_contract_schema():
    """Validate that contract tables exist and match expected column names/types."""
    engine = get_engine()
    expected = {
        'events_csv': {
            'Event_Id': 'text', 'Name': 'text', 'Date': 'text', 'Location': 'text'
        },
        'fighters_csv': {
            'Full Name': 'text', 'Nickname': 'text', 'Ht.': 'double precision', 'Wt.': 'double precision', 'Reach': 'double precision', 'Stance': 'text', 'W': 'integer', 'L': 'integer', 'D': 'integer', 'Belt': 'boolean'
        },
        'fights_csv': {
            'Fighter_1': 'text','Fighter_2':'text','KD_1':'integer','KD_2':'integer','STR_1':'integer','STR_2':'integer','TD_1':'integer','TD_2':'integer','SUB_1':'integer','SUB_2':'integer','Weight_Class':'text','Method':'text','Round':'integer','Fight_Time':'text','Event_Id':'text','Result_1':'text','Result_2':'text','Time Format':'text','Referee':'text','Method Details':'text','Sig. Str. %_1':'double precision','Sig. Str. %_2':'double precision','Sub. Att_1':'integer','Sub. Att_2':'integer','Rev._1':'integer','Rev._2':'integer','Ctrl_1':'double precision','Ctrl_2':'double precision','Head_%_1':'double precision','Head_%_2':'double precision','Body_%_1':'double precision','Body_%_2':'double precision','Leg_%_1':'double precision','Leg_%_2':'double precision','Distance_%_1':'double precision','Distance_%_2':'double precision','Clinch_%_1':'double precision','Clinch_%_2':'double precision','Ground_%_1':'double precision','Ground_%_2':'double precision','Total Str._%_1':'double precision','Total Str._%_2':'double precision','Sig. Str._%_1':'double precision','Sig. Str._%_2':'double precision'
        },
        'fighters_stats_csv': {
            'Fighter_Id':'text','Full Name':'text','Nickname':'text','Ht.':'double precision','Wt.':'double precision','Stance':'text','W':'integer','L':'integer','D':'integer','Belt':'boolean','Round':'double precision','KD':'double precision','STR':'double precision','TD':'double precision','SUB':'double precision','Ctrl':'double precision','Sig. Str. %':'double precision','Head_%':'double precision','Body_%':'double precision','Leg_%':'double precision','Distance_%':'double precision','Clinch_%':'double precision','Ground_%':'double precision','Sub. Att':'double precision','Rev.':'double precision','Weight_Class':'text','Gender':'text','Fighting Style':'text'
        }
    }
    results = []
    with engine.connect() as conn:
        for tbl, cols in expected.items():
            rows = conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='contract' AND table_name = :tbl"), {'tbl': tbl}).fetchall()
            existing = {r[0]: r[1] for r in rows}
            missing = [c for c in cols.keys() if c not in existing]
            type_mismatch = {c: (cols[c], existing.get(c)) for c in cols.keys() if c in existing and cols[c] != existing.get(c)}
            extra = [c for c in existing.keys() if c not in cols.keys()]
            results.append((tbl, missing, type_mismatch, extra))
    ok = True
    for tbl, missing, type_mismatch, extra in results:
        if missing or type_mismatch or extra:
            ok = False
            logger.warning('Contract table %s issues: missing=%s, type_mismatch=%s, extra=%s', tbl, missing, type_mismatch, extra)
        else:
            logger.info('Contract table %s matches expected schema', tbl)
    return ok

