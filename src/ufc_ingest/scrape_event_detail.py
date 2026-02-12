"""Scrape a single event page to get fight (bout) URLs and fight IDs."""
from bs4 import BeautifulSoup
from .http import RateLimitedSession, resilient_get
from .normalize import normalize_ufcstats_url, extract_ufcstats_id


def scrape_event_fights(event_url: str, session: RateLimitedSession = None):
    session = session or RateLimitedSession()
    event_url = normalize_ufcstats_url(event_url)
    r = resilient_get(session, event_url)
    soup = BeautifulSoup(r.text, "lxml")
    # fights table rows
    rows = soup.select(".b-fight-details__table tr")
    fights = []
    for tr in rows:
        a = tr.select_one('a[href*="/fight-details/"]')
        if not a:
            continue
        href = a['href']
        url = normalize_ufcstats_url(href)
        fight_id = extract_ufcstats_id(url, "fight-details")
        fights.append({'fight_id': fight_id, 'url': url})
    # if the above selector misses, sweep for links
    if not fights:
        for a in soup.select('a'):
            href = a.get('href', '')
            if '/fight-details/' in href:
                url = normalize_ufcstats_url(href)
                fights.append({'fight_id': extract_ufcstats_id(url, "fight-details"), 'url': url})
    # deduplicate
    seen = set()
    result = []
    for f in fights:
        if f['fight_id'] in seen:
            continue
        seen.add(f['fight_id'])
        result.append(f)
    # Normalize to contiguous 1..N card order for reliable display ordering.
    for idx, f in enumerate(result, start=1):
        f['fight_order'] = idx
    return result
