"""Scrape events index page and yield event dicts."""
from bs4 import BeautifulSoup
from .http import RateLimitedSession, resilient_get
from .normalize import normalize_ufcstats_url, extract_ufcstats_id

EVENTS_URL = "http://ufcstats.com/statistics/events/completed?page=all"


def scrape_events_index(session: RateLimitedSession = None):
    session = session or RateLimitedSession()
    resp = resilient_get(session, EVENTS_URL)
    soup = BeautifulSoup(resp.text, "lxml")

    # Table rows with events
    rows = soup.select("table tr")
    for r in rows[1:]:
        a = r.select_one("a")
        if not a:
            continue
        href = a.get('href')
        name = a.text.strip()
        # date is in a span within the first td; location is in the second td
        tds = r.find_all('td')
        date_el = r.select_one("span.b-statistics__date")
        date = date_el.text.strip() if date_el else None
        location = tds[1].text.strip() if len(tds) > 1 else None
        # parse event_id from url path
        url = normalize_ufcstats_url(href)
        event_id = extract_ufcstats_id(url, "event-details")
        yield {
            'event_id': event_id,
            'name': name,
            'date': date,
            'location': location,
            'url': url,
        }
