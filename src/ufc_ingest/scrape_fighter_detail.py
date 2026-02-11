"""Scrape fighter detail page to fill fighter bios."""
from bs4 import BeautifulSoup
import re
from .http import RateLimitedSession, resilient_get
from .normalize import parse_height, parse_reach, parse_weight, normalize_ufcstats_url, extract_ufcstats_id

_record_re = re.compile(r"(\d+)\s*-\s*(\d+)\s*-\s*(\d+)")


def _detect_belt(soup: BeautifulSoup, session: RateLimitedSession = None):
    # Belt icons appear on title fights; consider champion if most recent fight
    # has a belt icon and result is a win. If the fight is a tournament title
    # (e.g., "Tournament Title Bout"), treat as not a UFC champion belt.
    rows = soup.select('table.b-fight-details__table tr.b-fight-details__table-row')
    for r in rows:
        if not r.select_one('a[href*="/fight-details/"]'):
            continue
        tds = r.find_all('td')
        result_txt = tds[0].get_text(" ", strip=True).lower() if tds else ""
        has_belt_icon = bool(r.select_one('img[src*="belt.png"]'))
        if not has_belt_icon:
            return False
        if result_txt.startswith("win") or result_txt.startswith("w"):
            fight_link = r.select_one('a[href*="/fight-details/"]')
            fight_url = normalize_ufcstats_url(fight_link.get("href")) if fight_link else None
            if session and fight_url:
                try:
                    fr = resilient_get(session, fight_url)
                    fsoup = BeautifulSoup(fr.text, "lxml")
                    title_el = fsoup.select_one(".b-fight-details__fight-title")
                    title_text = title_el.get_text(" ", strip=True) if title_el else ""
                    lower = title_text.lower()
                    if "title bout" in lower:
                        if "tournament" in lower:
                            return False
                        return True
                    return False
                except Exception:
                    return True
            return True
        return False
    return None


def scrape_fighter(url: str, session: RateLimitedSession = None) -> dict:
    session = session or RateLimitedSession()
    url = normalize_ufcstats_url(url)
    r = resilient_get(session, url)
    soup = BeautifulSoup(r.text, "lxml")
    name = soup.select_one('.b-content__title > span')
    full_name = name.text.strip() if name else None
    nickname_el = soup.select_one('.b-content__Nickname')
    nickname = nickname_el.text.strip().strip('"') if nickname_el else None
    record_el = soup.select_one('.b-content__title-record')
    w = l = d = None
    if record_el:
        m = _record_re.search(record_el.get_text(" ", strip=True))
        if m:
            w, l, d = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    belt = _detect_belt(soup, session=session)

    # bio items
    bio = {"Ht.": None, "Wt.": None, "Reach": None, "Stance": None}
    for li in soup.select('.b-list__box-list-item'):
        txt = li.text.strip()
        if ':' not in txt:
            continue
        key = txt.split(':', 1)[0].strip().lower()
        val = txt.split(':', 1)[1].strip()
        if key == 'height':
            bio['Ht.'] = parse_height(val)
        elif key == 'weight':
            bio['Wt.'] = parse_weight(val)
        elif key == 'reach':
            bio['Reach'] = parse_reach(val)
        elif key == 'stance':
            bio['Stance'] = val
    # fighter id from url
    fighter_id = extract_ufcstats_id(url, "fighter-details")
    return {
        'fighter_id': fighter_id,
        'full_name': full_name,
        'nickname': nickname,
        'ht_inches': bio['Ht.'],
        'wt_lbs': bio['Wt.'],
        'reach_inches': bio['Reach'],
        'stance': bio['Stance'],
        'w': w,
        'l': l,
        'd': d,
        'belt': belt,
        'url': url,
    }
