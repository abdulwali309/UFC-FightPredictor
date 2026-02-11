"""Normalization helpers for heights, reach, weights, percents, times."""
import re
from typing import Optional, Tuple
from urllib.parse import urlsplit

_height_re = re.compile(r"(\d+)\s*'\s*(\d+)")
_reach_re = re.compile(r"(\d+)\s*\"")
_weight_re = re.compile(r"(\d+\.?\d*)")
_percent_re = re.compile(r"(\d+\.?\d*)\s*%")
_mmss_re = re.compile(r"(\d+):(\d{2})")
_xofy_re = re.compile(r"(\d+)\s*of\s*(\d+)", re.I)
_dash_tokens = {"--", "—", "-", "n/a", "na"}


def parse_height(s: str) -> Optional[float]:
    if not s or s.strip() == "--":
        return None
    m = _height_re.search(s)
    if not m:
        return None
    feet = int(m.group(1))
    inches = int(m.group(2))
    return float(feet * 12 + inches)


def parse_reach(s: str) -> Optional[float]:
    if not s or s.strip() == "--":
        return None
    m = _reach_re.search(s)
    if not m:
        return None
    return float(m.group(1))


def parse_weight(s: str) -> Optional[float]:
    if not s or s.strip() == "--":
        return None
    m = _weight_re.search(s)
    if not m:
        return None
    return float(m.group(1))


def parse_percent(s: str) -> Optional[float]:
    if not s:
        return None
    if s.strip().lower() in _dash_tokens:
        return None
    m = _percent_re.search(s)
    if not m:
        try:
            v = float(s)
            return v
        except Exception:
            return None
    return float(m.group(1))


def parse_mmss(s: str) -> Optional[int]:
    if not s:
        return None
    if s.strip().lower() in _dash_tokens:
        return None
    m = _mmss_re.search(s)
    if not m:
        return None
    minutes = int(m.group(1))
    seconds = int(m.group(2))
    return minutes * 60 + seconds


def parse_x_of_y(s: str) -> Optional[Tuple[int, int]]:
    if not s:
        return None
    if s.strip().lower() in _dash_tokens:
        return None
    m = _xofy_re.search(s)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def normalize_ufcstats_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if url.startswith("http://ufcstats.com"):
        return url
    parts = urlsplit(url)
    path = parts.path or ""
    if not path.startswith("/"):
        path = "/" + path
    qs = f"?{parts.query}" if parts.query else ""
    return f"http://ufcstats.com{path}{qs}"


def extract_ufcstats_id(url: Optional[str], segment: str) -> Optional[str]:
    if not url:
        return None
    parts = urlsplit(url)
    path = parts.path.strip("/")
    if not path:
        return None
    # expect .../<segment>/<id>
    try:
        idx = path.split("/").index(segment)
    except ValueError:
        # fall back to last path segment
        return path.split("/")[-1] if "/" in path else path
    pieces = path.split("/")
    if idx + 1 >= len(pieces):
        return None
    return pieces[idx + 1]


def normalize_stat_label(label: Optional[str]) -> Optional[str]:
    if not label:
        return None
    raw = label.strip().lower().replace("%", " pct ")
    text = re.sub(r"[\s\.\(\)]+", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    mapping = {
        "kd": "kd",
        "knockdowns": "kd",
        "sig str": "sig_str",
        "sig str": "sig_str",
        "significant str": "sig_str",
        "significant strikes": "sig_str",
        "sig str pct": "sig_str_pct",
        "total str": "total_str",
        "total str pct": "total_str_pct",
        "td": "td",
        "takedowns": "td",
        "td pct": "td_pct",
        "sub att": "sub_att",
        "sub attn": "sub_att",
        "sub attn.": "sub_att",
        "sub att.": "sub_att",
        "sub att": "sub_att",
        "sub. att": "sub_att",
        "reversals": "rev",
        "rev": "rev",
        "ctrl": "ctrl",
        "control": "ctrl",
        "control time": "ctrl",
        "head": "head",
        "body": "body",
        "leg": "leg",
        "distance": "distance",
        "clinch": "clinch",
        "ground": "ground",
    }
    return mapping.get(text, text)
