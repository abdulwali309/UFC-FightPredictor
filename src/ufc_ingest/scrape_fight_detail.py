"""Scrape fight detail page and parse fight metadata and totals."""
from bs4 import BeautifulSoup
import hashlib
import logging
import os
import re
from typing import Dict, Optional
from .http import RateLimitedSession, resilient_get
from .normalize import (
    parse_x_of_y,
    parse_percent,
    parse_mmss,
    normalize_ufcstats_url,
    extract_ufcstats_id,
    normalize_stat_label,
)

logger = logging.getLogger(__name__)


class FightDetailParseError(Exception):
    pass


def _text_or_none(el):
    return el.text.strip() if el else None


def _clean_text(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return " ".join(s.split())


def _save_html(html: str, fight_id: Optional[str], reason: str) -> str:
    os.makedirs("tmp", exist_ok=True)
    fid = fight_id or "unknown"
    safe_reason = re.sub(r"[^a-z0-9_]+", "_", reason.lower()).strip("_")
    path = os.path.join("tmp", f"fight_{fid}_{safe_reason}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def _surrogate_fighter_id(name: str, nickname: Optional[str]) -> Optional[str]:
    if not name:
        return None
    base = f"{name} {nickname or ''}".strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "", base)
    if not normalized:
        return None
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"sur_{digest}"


def _parse_person(person_el) -> dict:
    if not person_el:
        return {}
    a = person_el.select_one('a[href*="/fighter-details/"]')
    name = _clean_text(a.get_text(" ", strip=True)) if a else None
    if not name:
        name_el = person_el.select_one(".b-fight-details__person-name")
        name = _clean_text(name_el.get_text(" ", strip=True)) if name_el else None
    nick_el = person_el.select_one(".b-fight-details__person-title") or person_el.select_one(
        ".b-fight-details__person-nickname"
    )
    nickname = _clean_text(nick_el.get_text(" ", strip=True)) if nick_el else None
    if nickname:
        nickname = nickname.strip('"')
    href = a.get("href") if a else None
    url = normalize_ufcstats_url(href) if href else None
    fighter_id = extract_ufcstats_id(url, "fighter-details") if url else None
    if not fighter_id and name:
        fighter_id = _surrogate_fighter_id(name, nickname)
        logger.warning("Generated surrogate fighter_id for name=%s nickname=%s", name, nickname)
    return {"name": name, "nickname": nickname, "fighter_id": fighter_id, "url": url}


def _parse_meta(soup: BeautifulSoup) -> Dict[str, str]:
    meta = {}
    for item in soup.select(".b-fight-details__text-item, .b-fight-details__text-item_first"):
        label_el = item.select_one(".b-fight-details__label")
        value_el = item.select_one(".b-fight-details__value")
        if label_el and value_el:
            key = _clean_text(label_el.get_text(" ", strip=True)).rstrip(":")
            val = _clean_text(value_el.get_text(" ", strip=True))
            if key and val:
                meta[key] = val
            continue
        text = _clean_text(item.get_text(" ", strip=True))
        if not text:
            continue
        if label_el:
            key = _clean_text(label_el.get_text(" ", strip=True)).rstrip(":")
            remainder = text.replace(label_el.get_text(" ", strip=True), "").strip(" :")
            if key and remainder:
                meta[key] = remainder
                continue
        m = re.match(r"^([^:]+):\s*(.*)$", text)
        if m:
            meta[m.group(1).strip()] = m.group(2).strip()
    for dl in soup.select("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        if len(dts) != len(dds):
            continue
        for dt, dd in zip(dts, dds):
            key = _clean_text(dt.get_text(" ", strip=True)).rstrip(":")
            val = _clean_text(dd.get_text(" ", strip=True))
            if key and val and key not in meta:
                meta[key] = val
    return meta


def _normalize_meta_key(key: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", " ", key.strip().lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    mapping = {
        "weight class": "weight_class",
        "weightclass": "weight_class",
        "method": "method",
        "round": "round",
        "time": "fight_time",
        "fight time": "fight_time",
        "time format": "time_format",
        "format": "time_format",
        "referee": "referee",
        "details": "method_details",
        "method details": "method_details",
    }
    return mapping.get(norm, norm)


def _extract_headers(table) -> list:
    header_row = table.find("tr")
    if table.find("thead"):
        header_row = table.find("thead").find("tr") or header_row
    cells = header_row.find_all(["th", "td"]) if header_row else []
    return [_clean_text(c.get_text(" ", strip=True)) for c in cells]


def _extract_cell_values(cell) -> list:
    vals = [v.get_text(" ", strip=True) for v in cell.select(".b-fight-details__table-text") if v.get_text(strip=True)]
    if vals:
        return vals
    text = _clean_text(cell.get_text(" ", strip=True))
    return [text] if text else []


def _extract_method_details(soup: BeautifulSoup) -> Optional[str]:
    for p in soup.select("p.b-fight-details__text"):
        label = p.select_one(".b-fight-details__text-item_first .b-fight-details__label")
        if not label:
            continue
        if "details" not in label.get_text(" ", strip=True).lower():
            continue
        parts = []
        for item in p.select("i.b-fight-details__text-item"):
            txt = _clean_text(item.get_text(" ", strip=True))
            if txt:
                parts.append(txt)
        if parts:
            return " ".join(parts)
        text = _clean_text(p.get_text(" ", strip=True))
        if text and text.lower().startswith("details"):
            return text.split(":", 1)[-1].strip() or None
    return None


_WEIGHT_CLASSES = [
    "Women's Strawweight",
    "Women's Flyweight",
    "Women's Bantamweight",
    "Women's Featherweight",
    "Strawweight",
    "Flyweight",
    "Bantamweight",
    "Featherweight",
    "Lightweight",
    "Welterweight",
    "Middleweight",
    "Light Heavyweight",
    "Heavyweight",
    "Catch Weight",
    "Open Weight",
    "Super Heavyweight",
]


def _extract_weight_class(title_text: Optional[str]) -> Optional[str]:
    if not title_text:
        return None
    lower = title_text.lower()
    for wc in sorted(_WEIGHT_CLASSES, key=len, reverse=True):
        if wc.lower() in lower:
            return wc
    return None


def _parse_count(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    xofy = parse_x_of_y(value)
    if xofy:
        return int(xofy[0])
    m = re.search(r"(\d+)", value)
    return int(m.group(1)) if m else None


def _ratio_from_xofy(value: Optional[str]) -> Optional[float]:
    xofy = parse_x_of_y(value or "")
    if not xofy:
        return None
    landed, attempted = xofy
    if attempted == 0:
        return 0.0 if landed == 0 else None
    return round(landed / attempted, 2)


def _fraction_from_percent(value: Optional[str]) -> Optional[float]:
    pct = parse_percent(value)
    if pct is None:
        return None
    return round(pct / 100.0, 2) if pct > 1 else round(pct, 2)


def _fraction(value: Optional[str]) -> Optional[float]:
    ratio = _ratio_from_xofy(value)
    if ratio is not None:
        return ratio
    pct = _fraction_from_percent(value)
    if pct is not None:
        return pct
    try:
        v = float(value)
        if v > 1:
            return round(v / 100.0, 2)
        return round(v, 2)
    except Exception:
        return None


def scrape_fight(url: str, session: RateLimitedSession = None) -> dict:
    session = session or RateLimitedSession()
    url = normalize_ufcstats_url(url)
    fight_id = extract_ufcstats_id(url, "fight-details")
    r = resilient_get(session, url)
    logger.info("fight-details fetch status=%s url=%s final_url=%s bytes=%s", r.status_code, url, r.url, len(r.content))
    html = r.text
    soup = BeautifulSoup(html, "lxml")

    if not soup.select_one(".b-fight-details"):
        saved = _save_html(html, fight_id, "missing_markers")
        logger.warning(
            "fight-details markers missing: fight_id=%s requested_url=%s final_url=%s status=%s saved=%s",
            fight_id,
            url,
            r.url,
            r.status_code,
            saved,
        )
        raise FightDetailParseError(f"Missing fight-details markers for {fight_id}")

    # parse fighters (prefer explicit red/blue classes, else use order)
    red_el = soup.select_one(".b-fight-details__person--red")
    blue_el = soup.select_one(".b-fight-details__person--blue")
    if not (red_el and blue_el):
        persons = soup.select(".b-fight-details__person")
        if len(persons) >= 2:
            red_el, blue_el = persons[0], persons[1]

    red_info = _parse_person(red_el)
    blue_info = _parse_person(blue_el)

    # winner corner from status (W/L/D/NC)
    def _status(person_el):
        st = person_el.select_one(".b-fight-details__person-status") if person_el else None
        text = _clean_text(st.get_text(" ", strip=True)).upper() if st else None
        return text

    red_status = _status(red_el)
    blue_status = _status(blue_el)
    winner_corner = None
    if red_status == "W":
        winner_corner = "red"
    elif blue_status == "W":
        winner_corner = "blue"
    elif red_status == "D" or blue_status == "D":
        winner_corner = "draw"
    elif red_status == "NC" or blue_status == "NC":
        winner_corner = "nc"

    if not red_info.get("fighter_id") and not red_info.get("name"):
        saved = _save_html(html, fight_id, "missing_red")
        logger.warning("Missing red fighter info: fight_id=%s url=%s saved=%s", fight_id, url, saved)
        raise FightDetailParseError(f"Missing red fighter info for {fight_id}")
    if not blue_info.get("fighter_id") and not blue_info.get("name"):
        saved = _save_html(html, fight_id, "missing_blue")
        logger.warning("Missing blue fighter info: fight_id=%s url=%s saved=%s", fight_id, url, saved)
        raise FightDetailParseError(f"Missing blue fighter info for {fight_id}")

    meta_raw = _parse_meta(soup)
    meta = {}
    for k, v in meta_raw.items():
        meta[_normalize_meta_key(k)] = v

    title_el = soup.select_one(".b-fight-details__fight-title")
    title_text = _clean_text(title_el.get_text(" ", strip=True)) if title_el else None
    weight_class = meta.get("weight_class") or _extract_weight_class(title_text)

    fight_time = meta.get("fight_time")
    time_format = meta.get("time_format")
    method_details = meta.get("method_details") or _extract_method_details(soup)

    totals_red = {
        "kd": None,
        "str": None,
        "td": None,
        "sub": None,
        "sig_str_pct": None,
        "sub_att": None,
        "rev": None,
        "ctrl_seconds": None,
        "head_pct": None,
        "body_pct": None,
        "leg_pct": None,
        "distance_pct": None,
        "clinch_pct": None,
        "ground_pct": None,
        "total_str_pct": None,
        "sig_str_dist_pct": None,
    }
    totals_blue = {k: None for k in totals_red.keys()}

    def apply_value(corner: str, key: str, value: Optional[str]):
        if key in ("sig_str", "str"):
            val = _parse_count(value)
            if corner == "red":
                totals_red["str"] = val
            else:
                totals_blue["str"] = val
            ratio = _ratio_from_xofy(value)
            if ratio is not None:
                (totals_red if corner == "red" else totals_blue)["sig_str_dist_pct"] = ratio
        elif key == "kd":
            val = _parse_count(value)
            (totals_red if corner == "red" else totals_blue)["kd"] = val
        elif key == "td":
            val = _parse_count(value)
            (totals_red if corner == "red" else totals_blue)["td"] = val
        elif key == "sub":
            val = _parse_count(value)
            (totals_red if corner == "red" else totals_blue)["sub"] = val
        elif key == "sub_att":
            val = _parse_count(value)
            (totals_red if corner == "red" else totals_blue)["sub_att"] = val
        elif key == "rev":
            val = _parse_count(value)
            (totals_red if corner == "red" else totals_blue)["rev"] = val
        elif key == "sig_str_pct":
            val = _fraction_from_percent(value)
            target = totals_red if corner == "red" else totals_blue
            target["sig_str_pct"] = val
        elif key == "total_str_pct":
            val = _fraction_from_percent(value)
            (totals_red if corner == "red" else totals_blue)["total_str_pct"] = val
        elif key == "total_str":
            ratio = _ratio_from_xofy(value)
            if ratio is not None:
                (totals_red if corner == "red" else totals_blue)["total_str_pct"] = ratio
        elif key == "ctrl":
            val = parse_mmss(value)
            (totals_red if corner == "red" else totals_blue)["ctrl_seconds"] = val
        elif key in ("head", "body", "leg", "distance", "clinch", "ground"):
            val = _fraction(value)
            key_map = {
                "head": "head_pct",
                "body": "body_pct",
                "leg": "leg_pct",
                "distance": "distance_pct",
                "clinch": "clinch_pct",
                "ground": "ground_pct",
            }
            (totals_red if corner == "red" else totals_blue)[key_map[key]] = val

    tables = soup.find_all("table")
    for table in tables:
        headers = _extract_headers(table)
        if not headers:
            continue
        header_keys = [normalize_stat_label(h) for h in headers]
        header_key_set = set([k for k in header_keys if k])
        if any(h and re.match(r"^round\\b", h.strip().lower()) for h in headers):
            continue
        if table.find("thead", class_="b-fight-details__table-head_rnd"):
            continue
        table_text = table.get_text(" ", strip=True).lower()
        if "round 1" in table_text or "round 2" in table_text:
            # per-round table
            continue
        # handle header-based tables
        if {"kd", "sig_str", "sig_str_pct", "total_str", "td", "ctrl"}.intersection(header_key_set):
            rows = [r for r in table.find_all("tr") if r.find("td")]
            if not rows:
                continue
            # combined row format with stacked values
            row = rows[0]
            cells = row.find_all("td")
            for idx, key in enumerate(header_keys):
                if idx >= len(cells):
                    continue
                if not key or key == "fighter":
                    continue
                vals = _extract_cell_values(cells[idx])
                if len(vals) >= 2:
                    apply_value("red", key, vals[0])
                    apply_value("blue", key, vals[1])
                elif len(vals) == 1:
                    # assume row order red then blue if there are two rows
                    apply_value("red", key, vals[0])
                    if len(rows) > 1:
                        vals2 = _extract_cell_values(rows[1].find_all("td")[idx])
                        if vals2:
                            apply_value("blue", key, vals2[0])
            continue
        # breakdown tables with head/body/leg/distance/clinch/ground
        if {"head", "body", "leg", "distance", "clinch", "ground"}.intersection(header_key_set):
            rows = [r for r in table.find_all("tr") if r.find("td")]
            if not rows:
                continue
            row = rows[0]
            cells = row.find_all("td")
            for idx, key in enumerate(header_keys):
                if idx >= len(cells):
                    continue
                if not key or key == "fighter":
                    continue
                vals = _extract_cell_values(cells[idx])
                if len(vals) >= 2:
                    apply_value("red", key, vals[0])
                    apply_value("blue", key, vals[1])
            continue

    # label/value fallback table (older layout)
    if totals_red["str"] is None and totals_blue["str"] is None:
        for r in soup.select(".b-fight-details__fight-data .b-fight-details__table tr"):
            tds = r.find_all("td")
            if len(tds) < 3:
                continue
            label = normalize_stat_label(_clean_text(tds[0].get_text(" ", strip=True)))
            if not label:
                continue
            left = _clean_text(tds[1].get_text(" ", strip=True))
            right = _clean_text(tds[-1].get_text(" ", strip=True))
            apply_value("red", label, left)
            apply_value("blue", label, right)

    # Legacy contract semantics: "SUB" is a *submission win flag* (not submission attempts).
    # UFCStats provides "Sub. att" in the totals table; the win method is in the fight meta.
    method_norm = str(meta.get("method") or "").strip().lower()
    is_submission = method_norm == "submission" or "submission" in method_norm or method_norm.startswith("sub")
    if is_submission and winner_corner in {"red", "blue"}:
        totals_red["sub"] = 1 if winner_corner == "red" else 0
        totals_blue["sub"] = 1 if winner_corner == "blue" else 0
    else:
        totals_red["sub"] = 0
        totals_blue["sub"] = 0

    # "sub" is derived from fight meta (submission win flag), so it should not count as evidence that
    # the totals table parsed correctly.
    suspicious = True
    for key in ["kd", "str", "td", "sub_att", "rev", "sig_str_pct", "ctrl_seconds"]:
        if totals_red.get(key) is not None or totals_blue.get(key) is not None:
            suspicious = False
            break
    if suspicious and os.getenv("UFC_DEBUG_HTML", "").lower() in {"1", "true", "yes"}:
        saved = _save_html(html, fight_id, "suspicious_totals")
        logger.warning("Suspicious empty totals: fight_id=%s url=%s saved=%s", fight_id, url, saved)

    return {
        "fight_meta": {
            "weight_class": weight_class,
            "method": meta.get("method"),
            "round": int(meta.get("round")) if meta.get("round") and str(meta.get("round")).isdigit() else None,
            "fight_time": fight_time,
            "time_format": time_format,
            "referee": meta.get("referee"),
            "method_details": method_details,
            "winner_corner": winner_corner,
        },
        "red": {
            "name": red_info.get("name"),
            "fighter_id": red_info.get("fighter_id"),
            "totals": totals_red,
        },
        "blue": {
            "name": blue_info.get("name"),
            "fighter_id": blue_info.get("fighter_id"),
            "totals": totals_blue,
        },
        "url": url,
    }
