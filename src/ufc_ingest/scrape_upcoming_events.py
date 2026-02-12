"""Scrape upcoming events and fight cards from UFCStats."""
import re
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from .http import RateLimitedSession, resilient_get
from .normalize import normalize_ufcstats_url, extract_ufcstats_id

UPCOMING_EVENTS_URL = "http://ufcstats.com/statistics/events/upcoming"

_date_re = re.compile(r"(.+?)\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})$")

# Known weight classes (text as appears on UFCStats)
_WEIGHT_CLASSES = {
    "Flyweight",
    "Bantamweight",
    "Featherweight",
    "Lightweight",
    "Welterweight",
    "Middleweight",
    "Light Heavyweight",
    "Heavyweight",
    "Open Weight",
    "Catch Weight",
    "Women's Strawweight",
    "Women's Flyweight",
    "Women's Bantamweight",
    "Women's Featherweight",
}


def _parse_name_date(text: str):
    text = (text or "").strip()
    if not text:
        return None, None
    m = _date_re.match(text)
    if not m:
        return text, None
    name = m.group(1).strip()
    date = m.group(2).strip()
    return name, date


def scrape_upcoming_events_index(session: RateLimitedSession = None):
    session = session or RateLimitedSession()
    resp = resilient_get(session, UPCOMING_EVENTS_URL)
    soup = BeautifulSoup(resp.text, "lxml")

    rows = soup.select("table.b-statistics__table-events tbody tr")
    for r in rows:
        a = r.select_one("a[href*='event-details']")
        if not a:
            continue
        href = a.get("href")
        url = normalize_ufcstats_url(href)
        event_id = extract_ufcstats_id(url, "event-details")
        tds = r.find_all("td")
        name_date = tds[0].get_text(" ", strip=True) if len(tds) > 0 else ""
        location = tds[1].get_text(" ", strip=True) if len(tds) > 1 else None
        name, date = _parse_name_date(name_date)
        yield {
            "event_id": event_id,
            "name": name,
            "date": date,
            "location": location,
            "url": url,
        }


def _extract_weight_class(tr) -> Optional[str]:
    tds = tr.find_all("td")
    for td in tds:
        text = td.get_text(" ", strip=True)
        if not text:
            continue
        if text in _WEIGHT_CLASSES:
            return text
        if "weight" in text.lower():
            return text
        if text.lower() == "catch weight":
            return "Catch Weight"
    return None


def scrape_upcoming_event_fights(event_url: str, session: RateLimitedSession = None) -> List[Dict]:
    session = session or RateLimitedSession()
    event_url = normalize_ufcstats_url(event_url)
    r = resilient_get(session, event_url)
    soup = BeautifulSoup(r.text, "lxml")
    rows = soup.select(".b-fight-details__table tbody tr")
    fights: List[Dict] = []
    for idx, tr in enumerate(rows, start=1):
        fighter_links = tr.select('a[href*="/fighter-details/"]')
        if len(fighter_links) < 2:
            continue
        f1 = fighter_links[0]
        f2 = fighter_links[1]
        fights.append({
            "fighter_1": f1.get_text(" ", strip=True),
            "fighter_2": f2.get_text(" ", strip=True),
            "fighter_1_id": extract_ufcstats_id(normalize_ufcstats_url(f1.get("href")), "fighter-details"),
            "fighter_2_id": extract_ufcstats_id(normalize_ufcstats_url(f2.get("href")), "fighter-details"),
            "weight_class": _extract_weight_class(tr),
            # UFCStats upcoming event tables are ordered top->bottom by card order.
            "card_order": idx,
        })

    # de-duplicate
    seen = set()
    deduped = []
    for f in fights:
        key = (f["fighter_1"], f["fighter_2"], f.get("weight_class"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    return deduped
