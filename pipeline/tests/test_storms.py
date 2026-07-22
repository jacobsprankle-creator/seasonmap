from pipeline.core.sources import storms

HURDAT_FIXTURE = """AL092021,                IDA,     40,
20210826, 1200,  , TD, 16.5N,  78.9W,  30, 1006,
20210829, 1655, L, HU, 29.1N,  90.2W, 130,  931,
20210830, 0600,  , TS, 32.5N,  90.9W,  35, 1000,
"""


def test_parse_hurdat2_track_and_category():
    out = storms._parse_hurdat2(HURDAT_FIXTURE, "AL")
    assert len(out) == 1
    s = out[0]
    assert s["name"] == "Ida" and s["year"] == 2021
    assert s["max_wind"] == 130 and s["max_cat"] == "4"
    assert s["track"][0] == (-78.9, 16.5, 30.0) and len(s["track"]) == 3


def test_dateline_longitudes_normalized():
    fixture = (
        "EP902015,             TEST,      3,\n"
        "20150101, 0000,  , HU, 40.0N, 179.8W, 100, -999,\n"
        "20150102, 0000,  , HU, 40.5N, 179.9E, 100, -999,\n"
        "20150103, 0000,  , HU, 41.0N, 178.0E, 100, -999,\n"
    )
    s = storms._parse_hurdat2(fixture, "EP")[0]
    lons = [p[0] for p in s["track"]]
    # eastern-hemisphere points wrap to continuous westward values (< -100)
    assert all(lon < -100 for lon in lons)
    assert max(abs(lons[i + 1] - lons[i]) for i in range(2)) < 5


def test_saffir_simpson_boundaries():
    assert storms.saffir_simpson(33) == "TD"
    assert storms.saffir_simpson(34) == "TS"
    assert storms.saffir_simpson(64) == "1"
    assert storms.saffir_simpson(96) == "3"
    assert storms.saffir_simpson(137) == "5"
