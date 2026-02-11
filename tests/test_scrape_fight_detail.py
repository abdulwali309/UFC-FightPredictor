from ufc_ingest.scrape_fight_detail import scrape_fight


class FakeResp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200
        self.url = "http://ufcstats.com/fight-details/fake"
        self.content = text.encode("utf-8")
    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, text):
        self._text = text
    def get(self, url, timeout=20, **kwargs):
        return FakeResp(self._text)


def _load_fixture(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_scrape_fight_detail_fixtures():
    fixtures = [
        {
            "path": "tests/data/sample_fight_1_b06b43670238c2d8.html",
            "red_id": "e1248941344b3288",
            "blue_id": "f166e93d04a8c274",
            "method": "Decision - Unanimous",
            "round": 5,
            "fight_time": "5:00",
            "red_str": 98,
            "blue_str": 70,
            "red_td": 2,
            "blue_td": 2,
            "red_ctrl": 169,
            "blue_ctrl": 110,
            "red_sig_pct": 0.61,
            "blue_sig_pct": 0.44,
            "red_sig_ratio": 0.61,
            "blue_sig_ratio": 0.44,
            "red_total_ratio": 0.63,
            "blue_total_ratio": 0.46,
            "red_breakdown": {
                "head": 0.61, "body": 0.5, "leg": 0.64, "distance": 0.59, "clinch": 0.75, "ground": 1.0
            },
            "blue_breakdown": {
                "head": 0.34, "body": 0.52, "leg": 0.74, "distance": 0.43, "clinch": 0.75, "ground": 0.5
            },
            "method_details_contains": "Ben Cartlidge",
        },
        {
            "path": "tests/data/sample_fight_2_a7ae8f6eb5fc3a79.html",
            "red_id": "193b9d1858bc4df3",
            "blue_id": "c2299ec916bc7c56",
            "method": "KO/TKO",
            "round": 2,
            "fight_time": "4:45",
            "red_str": 30,
            "blue_str": 97,
            "red_td": 0,
            "blue_td": 2,
            "red_ctrl": 28,
            "blue_ctrl": 374,
            "red_sig_pct": 0.63,
            "blue_sig_pct": 0.76,
            "red_sig_ratio": 0.64,
            "blue_sig_ratio": 0.76,
            "red_total_ratio": 0.75,
            "blue_total_ratio": 0.83,
            "red_breakdown": {
                "head": 0.59, "body": 0.67, "leg": 1.0, "distance": 0.64, "clinch": 0.67, "ground": 0.0
            },
            "blue_breakdown": {
                "head": 0.73, "body": 1.0, "leg": 0.0, "distance": 0.62, "clinch": 0.91, "ground": 0.8
            },
            "method_details_contains": "Punches to Head From Mount",
        },
    ]

    for fx in fixtures:
        html = _load_fixture(fx["path"])
        parsed = scrape_fight("http://ufcstats.com/fight-details/fake", FakeSession(html))
        assert parsed["red"]["fighter_id"] == fx["red_id"]
        assert parsed["blue"]["fighter_id"] == fx["blue_id"]
        assert parsed["fight_meta"]["method"] == fx["method"]
        assert parsed["fight_meta"]["round"] == fx["round"]
        assert parsed["fight_meta"]["fight_time"] == fx["fight_time"]
        assert parsed["red"]["totals"]["str"] == fx["red_str"]
        assert parsed["blue"]["totals"]["str"] == fx["blue_str"]
        assert parsed["red"]["totals"]["td"] == fx["red_td"]
        assert parsed["blue"]["totals"]["td"] == fx["blue_td"]
        assert parsed["red"]["totals"]["ctrl_seconds"] == fx["red_ctrl"]
        assert parsed["blue"]["totals"]["ctrl_seconds"] == fx["blue_ctrl"]
        assert parsed["red"]["totals"]["sig_str_pct"] == fx["red_sig_pct"]
        assert parsed["blue"]["totals"]["sig_str_pct"] == fx["blue_sig_pct"]
        assert parsed["red"]["totals"]["sig_str_dist_pct"] == fx["red_sig_ratio"]
        assert parsed["blue"]["totals"]["sig_str_dist_pct"] == fx["blue_sig_ratio"]
        assert parsed["red"]["totals"]["total_str_pct"] == fx["red_total_ratio"]
        assert parsed["blue"]["totals"]["total_str_pct"] == fx["blue_total_ratio"]
        assert parsed["red"]["totals"]["head_pct"] == fx["red_breakdown"]["head"]
        assert parsed["red"]["totals"]["body_pct"] == fx["red_breakdown"]["body"]
        assert parsed["red"]["totals"]["leg_pct"] == fx["red_breakdown"]["leg"]
        assert parsed["red"]["totals"]["distance_pct"] == fx["red_breakdown"]["distance"]
        assert parsed["red"]["totals"]["clinch_pct"] == fx["red_breakdown"]["clinch"]
        assert parsed["red"]["totals"]["ground_pct"] == fx["red_breakdown"]["ground"]
        assert parsed["blue"]["totals"]["head_pct"] == fx["blue_breakdown"]["head"]
        assert parsed["blue"]["totals"]["body_pct"] == fx["blue_breakdown"]["body"]
        assert parsed["blue"]["totals"]["leg_pct"] == fx["blue_breakdown"]["leg"]
        assert parsed["blue"]["totals"]["distance_pct"] == fx["blue_breakdown"]["distance"]
        assert parsed["blue"]["totals"]["clinch_pct"] == fx["blue_breakdown"]["clinch"]
        assert parsed["blue"]["totals"]["ground_pct"] == fx["blue_breakdown"]["ground"]
        assert fx["method_details_contains"] in (parsed["fight_meta"]["method_details"] or "")


def test_scrape_fight_detail_surrogate_id():
    html = """
    <div class="b-fight-details">
      <div class="b-fight-details__persons clearfix">
        <div class="b-fight-details__person">
          <div class="b-fight-details__person-text">
            <h3 class="b-fight-details__person-name">Test Fighter One</h3>
            <p class="b-fight-details__person-title">"Nick"</p>
          </div>
        </div>
        <div class="b-fight-details__person">
          <div class="b-fight-details__person-text">
            <h3 class="b-fight-details__person-name">Test Fighter Two</h3>
          </div>
        </div>
      </div>
      <table>
        <thead><tr><th>Fighter</th><th>KD</th><th>Sig. Str.</th><th>Sig. Str. %</th><th>Total Str.</th><th>Td</th><th>Td %</th><th>Sub. att</th><th>Rev.</th><th>Ctrl</th></tr></thead>
        <tbody><tr><td>Fighters</td><td><p>0</p><p>0</p></td><td><p>1 of 2</p><p>0 of 1</p></td><td><p>50%</p><p>0%</p></td><td><p>1 of 3</p><p>0 of 2</p></td><td><p>0 of 0</p><p>0 of 0</p></td><td><p>0%</p><p>0%</p></td><td><p>0</p><p>0</p></td><td><p>0</p><p>0</p></td><td><p>0:00</p><p>0:00</p></td></tr></tbody>
      </table>
    </div>
    """
    parsed = scrape_fight("http://ufcstats.com/fight-details/fake", FakeSession(html))
    assert parsed["red"]["fighter_id"].startswith("sur_")
    assert parsed["blue"]["fighter_id"].startswith("sur_")


def test_scrape_fight_detail_submission_sets_sub_flag():
    html = _load_fixture("tests/data/sample_fight_submission_4b9ae533ccb3fcdf.html")
    parsed = scrape_fight("http://ufcstats.com/fight-details/4b9ae533ccb3fcdf", FakeSession(html))

    assert parsed["fight_meta"]["method"] == "Submission"
    wc = parsed["fight_meta"]["winner_corner"]
    assert wc in {"red", "blue"}

    red_sub = parsed["red"]["totals"]["sub"]
    blue_sub = parsed["blue"]["totals"]["sub"]

    if wc == "red":
        assert red_sub == 1
        assert blue_sub == 0
    else:
        assert red_sub == 0
        assert blue_sub == 1
