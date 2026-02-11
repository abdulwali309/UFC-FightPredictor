from sqlalchemy import create_engine, text
from ufc_ingest.enrich_fighters import enrich_fighters


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


def test_enrich_fighters_upserts_existing_row():
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as conn:
        conn.execute(text("ATTACH DATABASE ':memory:' AS ufc"))
        conn.execute(text(
            """
            CREATE TABLE ufc.fighters (
                fighter_id TEXT PRIMARY KEY,
                full_name TEXT,
                nickname TEXT,
                ht_inches REAL,
                wt_lbs REAL,
                reach_inches REAL,
                stance TEXT,
                w INTEGER,
                l INTEGER,
                d INTEGER,
                belt BOOLEAN,
                url TEXT,
                scraped_at TEXT
            )
            """
        ))
        conn.execute(
            text("INSERT INTO ufc.fighters (fighter_id, full_name) VALUES (:fid, :name)"),
            {"fid": "e1248941344b3288", "name": "Alexander Volkanovski"},
        )

        with open("tests/data/sample_fighter_e1248941344b3288.html", "r", encoding="utf-8") as f:
            html = f.read()
        fight_html = '<div class="b-fight-details__fight-title">UFC Featherweight Title Bout</div>'

        enriched = enrich_fighters(conn, FakeSession(html, fight_html), ["e1248941344b3288"])
        assert enriched == 1

        row = conn.execute(
            text(
                """
                SELECT nickname, ht_inches, wt_lbs, reach_inches, stance, w, l, d, belt
                FROM ufc.fighters WHERE fighter_id = :fid
                """
            ),
            {"fid": "e1248941344b3288"},
        ).fetchone()

        assert row.nickname == "The Great"
        assert row.ht_inches == 66.0
        assert row.wt_lbs == 145.0
        assert row.reach_inches == 71.0
        assert row.stance == "Orthodox"
        assert row.w == 28
        assert row.l == 4
        assert row.d == 0
        assert bool(row.belt) is True
