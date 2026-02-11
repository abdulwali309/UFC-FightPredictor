"""SQLAlchemy models for canonical ufc schema and helper for contract tables."""
from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    Text,
    Boolean,
    DateTime,
    Numeric,
    ForeignKey,
    PrimaryKeyConstraint,
    UniqueConstraint,
    CheckConstraint,
    Table,
    MetaData,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base(metadata=MetaData())


class Event(Base):
    __tablename__ = "events"
    __table_args__ = {"schema": "ufc"}

    event_id = Column(String, primary_key=True)
    name = Column(Text)
    date = Column(String)  # store as text to preserve format, can be parsed later
    location = Column(Text)
    url = Column(Text)
    scraped_at = Column(DateTime, default=datetime.utcnow)

    fights = relationship("Fight", back_populates="event")


class Fighter(Base):
    __tablename__ = "fighters"
    __table_args__ = {"schema": "ufc"}

    fighter_id = Column(String, primary_key=True)
    full_name = Column(Text)
    nickname = Column(Text, nullable=True)
    ht_inches = Column(Numeric, nullable=True)
    wt_lbs = Column(Numeric, nullable=True)
    reach_inches = Column(Numeric, nullable=True)
    stance = Column(Text, nullable=True)
    w = Column(Integer, default=0)
    l = Column(Integer, default=0)
    d = Column(Integer, default=0)
    belt = Column(Boolean, default=False)
    url = Column(Text)
    scraped_at = Column(DateTime, default=datetime.utcnow)


class Fight(Base):
    __tablename__ = "fights"
    __table_args__ = ({"schema": "ufc"},)

    fight_id = Column(String, primary_key=True)
    event_id = Column(String, ForeignKey("ufc.events.event_id"), nullable=False)
    red_fighter_id = Column(String, ForeignKey("ufc.fighters.fighter_id"))
    blue_fighter_id = Column(String, ForeignKey("ufc.fighters.fighter_id"))
    weight_class = Column(Text)
    method = Column(Text)
    round = Column(Integer)
    fight_time = Column(Text)
    time_format = Column(Text)
    referee = Column(Text)
    method_details = Column(Text)
    winner_corner = Column(String)  # 'red'/'blue'/'draw'/'nc'
    url = Column(Text)
    scraped_at = Column(DateTime, default=datetime.utcnow)

    event = relationship("Event", back_populates="fights")


class FightTotal(Base):
    __tablename__ = "fight_totals"
    __table_args__ = (
        PrimaryKeyConstraint("fight_id", "corner", name="pk_fight_totals"),
        {"schema": "ufc"},
    )

    fight_id = Column(String, ForeignKey("ufc.fights.fight_id"))
    corner = Column(String)  # 'red' or 'blue'
    kd = Column(Integer, default=0)
    str = Column(Integer, default=0)
    td = Column(Integer, default=0)
    sub = Column(Integer, default=0)
    sig_str_pct = Column(Numeric, nullable=True)
    sub_att = Column(Integer, nullable=True)
    rev = Column(Integer, nullable=True)
    ctrl_seconds = Column(Integer, nullable=True)
    head_pct = Column(Numeric, nullable=True)
    body_pct = Column(Numeric, nullable=True)
    leg_pct = Column(Numeric, nullable=True)
    distance_pct = Column(Numeric, nullable=True)
    clinch_pct = Column(Numeric, nullable=True)
    ground_pct = Column(Numeric, nullable=True)
    total_str_pct = Column(Numeric, nullable=True)
    sig_str_dist_pct = Column(Numeric, nullable=True)


class PipelineState(Base):
    __tablename__ = "pipeline_state"
    __table_args__ = {"schema": "ufc"}

    id = Column(Integer, primary_key=True)
    last_run_at = Column(DateTime, nullable=True)
    last_success_event_id = Column(String, nullable=True)
    status = Column(Text, nullable=True)


class ModelArtifact(Base):
    __tablename__ = "model_artifacts"
    __table_args__ = (UniqueConstraint("model_version", name="uq_model_artifacts_version"), {"schema": "app"})

    id = Column(Integer, primary_key=True)
    model_name = Column(Text, nullable=False, default="prefight_pro")
    model_version = Column(Text, nullable=False)
    trained_at = Column(DateTime, nullable=True)
    dataset_rows = Column(Integer, nullable=True)
    feature_count = Column(Integer, nullable=True)
    metrics_json = Column(Text, nullable=True)
    artifact_uri = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UpcomingFight(Base):
    __tablename__ = "upcoming_fights"
    __table_args__ = (
        UniqueConstraint("fight_key", name="uq_upcoming_fights_key"),
        {"schema": "app"},
    )

    id = Column(Integer, primary_key=True)
    fight_key = Column(Text, nullable=False)
    event_id = Column(Text, nullable=True)
    fighter_1_id = Column(Text, nullable=True)
    fighter_2_id = Column(Text, nullable=True)
    fighter_1 = Column(Text, nullable=False)
    fighter_2 = Column(Text, nullable=False)
    weight_class = Column(Text, nullable=True)
    scheduled_date = Column(Date, nullable=True)
    event_name = Column(Text, nullable=True)
    source = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("model_version", "fight_key", name="uq_predictions_key"),
        {"schema": "app"},
    )

    id = Column(Integer, primary_key=True)
    model_name = Column(Text, nullable=False, default="prefight_pro")
    model_version = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    upcoming_id = Column(Integer, nullable=True)
    fight_key = Column(Text, nullable=True)
    fight_id = Column(Text, nullable=True)
    event_id = Column(Text, nullable=True)
    scheduled_date = Column(Date, nullable=True)
    fighter_1 = Column(Text, nullable=False)
    fighter_2 = Column(Text, nullable=False)
    fighter_1_id = Column(Text, nullable=True)
    fighter_2_id = Column(Text, nullable=True)
    weight_class = Column(Text, nullable=True)
    prob_f1 = Column(Numeric, nullable=True)
    source = Column(Text, nullable=True)

