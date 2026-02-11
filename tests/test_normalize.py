from ufc_ingest.normalize import parse_height, parse_reach, parse_weight, parse_percent, parse_mmss, parse_x_of_y


def test_parse_height():
    assert parse_height("5' 11\"") == 71.0
    assert parse_height("6'0\"") == 72.0
    assert parse_height("--") is None


def test_parse_reach():
    assert parse_reach("72\"") == 72.0
    assert parse_reach("--") is None


def test_parse_weight():
    assert parse_weight("185 lbs.") == 185.0
    assert parse_weight("200") == 200.0


def test_parse_percent():
    assert parse_percent("45%") == 45.0
    assert parse_percent("45 %") == 45.0
    assert parse_percent("N/A") is None
    assert parse_percent("—") is None
    assert parse_percent("--") is None


def test_parse_mmss():
    assert parse_mmss("3:24") == 204
    assert parse_mmss("0:05") == 5
    assert parse_mmss("0:00") == 0
    assert parse_mmss("—") is None
    assert parse_mmss("--") is None


def test_parse_x_of_y():
    assert parse_x_of_y("10 of 23") == (10, 23)
    assert parse_x_of_y("10 of 23 43%") == (10, 23)
    assert parse_x_of_y("—") is None
    assert parse_x_of_y("--") is None
