"""Configuration helpers for ufc_ingest."""
from dataclasses import dataclass
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    database_url: str = os.getenv("DATABASE_URL", "postgresql+psycopg2://user:pass@localhost:5432/ufc")
    user_agent: str = os.getenv("USER_AGENT", "UFC-FightPredictor-Bot/1.0 (+https://github.com/yourname)")
    rate_limit_seconds: float = float(os.getenv("RATE_LIMIT_SECONDS", "0.8"))


cfg = Config()
