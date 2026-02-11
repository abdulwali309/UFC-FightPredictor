from ufc_ingest.scrape_fighter_detail import scrape_fighter


class FakeResp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200
        self.url = "http://ufcstats.com/fighter-details/e1248941344b3288"
        self.content = text.encode("utf-8")
    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, fighter_html, fight_html):
        self._fighter_html = fighter_html
        self._fight_html = fight_html
    def get(self, url, timeout=20, **kwargs):
        if "/fight-details/" in url:
            return FakeResp(self._fight_html)
        return FakeResp(self._fighter_html)


def test_scrape_fighter_detail_parses_bio():
    with open("tests/data/sample_fighter_e1248941344b3288.html", "r", encoding="utf-8") as f:
        html = f.read()
    fight_html = '<div class="b-fight-details__fight-title">UFC Featherweight Title Bout</div>'
    parsed = scrape_fighter(
        "http://ufcstats.com/fighter-details/e1248941344b3288",
        FakeSession(html, fight_html),
    )
    assert parsed["full_name"] == "Alexander Volkanovski"
    assert parsed["nickname"] == "The Great"
    assert parsed["ht_inches"] == 66.0
    assert parsed["wt_lbs"] == 145.0
    assert parsed["reach_inches"] == 71.0
    assert parsed["stance"] == "Orthodox"
    assert parsed["w"] == 28
    assert parsed["l"] == 4
    assert parsed["d"] == 0
    assert parsed["belt"] is True
