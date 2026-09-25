from datetime import date

import pytest

from indexscout import analysis as a


def rec(**kw):
    base = {"clicks": 0.0, "impressions": 0.0, "ctr": 0.0, "position": 0.0}
    base.update(kw)
    if base["impressions"]:
        base["ctr"] = base["clicks"] / base["impressions"]
    return base


def test_weighted_ctr_and_position():
    rows = [rec(clicks=10, impressions=100, position=1.0), rec(clicks=0, impressions=900, position=11.0)]
    agg = a.aggregate(rows)
    assert agg["ctr"] == pytest.approx(0.01)  # 10 / 1000, not mean(0.1, 0) = 0.05
    assert agg["position"] == pytest.approx(10.0)  # (1*100 + 11*900) / 1000, not mean = 6
    assert a.aggregate([])["position"] is None


def test_compare_and_pct():
    c = a.compare(rec(clicks=50, impressions=1000, position=5), rec(clicks=100, impressions=1000, position=4))
    assert c["clicks_change"] == -50 and c["clicks_change_pct"] == -50.0
    assert c["ctr_change_pp"] == -5.0 and c["position_change"] == 1.0
    assert a.pct_change(5, 0) is None
    assert a.compare(rec(), None) == {"baseline_present": False}


def test_decompose_gains_losses_new_absent():
    cur = [rec(page="/a", clicks=10, impressions=100), rec(page="/new", clicks=5, impressions=10)]
    base = [rec(page="/a", clicks=30, impressions=100), rec(page="/gone", clicks=2, impressions=10)]
    d = a.decompose(cur, base, ["page"], "clicks")
    assert [i["page"] for i in d["negative"]] == ["/a", "/gone"]
    assert [i["page"] for i in d["positive"]] == ["/new"]
    assert d["new"][0]["page"] == "/new" and d["new"][0]["baseline"] is None  # missing, not zero
    assert d["absent"][0]["page"] == "/gone" and d["absent"][0]["current"] is None
    assert d["explained_change"] == -17


def test_decompose_preserves_query_page_pairs():
    cur = [
        rec(query="q", page="/a", clicks=1, impressions=5),
        rec(query="q", page="/b", clicks=9, impressions=5),
    ]
    d = a.decompose(cur, [], ["query", "page"], "clicks")
    assert {(i["query"], i["page"]) for i in d["positive"]} == {("q", "/a"), ("q", "/b")}


def test_trend_labels():
    assert a.classify_trend(10, None) == "new"
    assert a.classify_trend(0, 10) == "absent_in_current_period"
    assert a.classify_trend(130, 100) == "growing"
    assert a.classify_trend(70, 100) == "declining"
    assert a.classify_trend(110, 100) == "stable"


def test_buckets():
    assert [a.bucket(p) for p in (1.0, 1.6, 3.4, 4.0, 10.4, 10.6, 20.4, 25)] == [
        "1",
        "2",
        "3",
        "4-5",
        "6-10",
        "11-20",
        "11-20",
        "21+",
    ]


def test_opportunity_scoring_is_transparent():
    # Property benchmark: 2,000 impressions in bucket 6-10 at 5% CTR.
    anchor = [rec(query=f"a{i}", page="/x", clicks=50, impressions=1000, position=7) for i in range(2)]
    low = rec(query="low", page="/p", clicks=2, impressions=1000, position=7)
    cur = [*anchor, low]
    base = [rec(query="low", page="/p", clicks=5, impressions=700, position=7)]
    opps = a.find_opportunities(cur, base, min_impressions=100)
    o = next(o for o in opps if o["query"] == "low")
    sc = o["score_components"]
    assert o["category"] == "low_ctr" and "striking_distance" in o["categories"]
    target = (50 + 50 + 2) / 3000  # property's own bucket CTR, weighted
    assert sc["target_basis"] == "property" and sc["target_ctr"] == pytest.approx(round(target, 4))
    assert sc["potential_clicks"] == pytest.approx(round(1000 * (target - 0.002), 1))
    assert sc["trend_adjustment"] == 0.2  # impressions up 43% >= 20%
    assert o["score"] == pytest.approx(round(1000 * (target - 0.002) * 1.2, 1), abs=0.11)
    assert (
        "1,000 impressions" in o["evidence"]
        and "0.2% CTR" in o["evidence"]
        and "position 7.0" in o["evidence"]
    )
    assert "Do not rewrite only because CTR is low" in o["suggested_action"]
    assert o["validation_step"]


def test_opportunity_default_curve_and_thresholds():
    cur = [rec(query="near", page="/n", clicks=1, impressions=200, position=11.5)]
    o = a.find_opportunities(cur, [], 100)[0]
    assert o["category"] == "near_page_one"
    assert o["score_components"]["target_basis"] == "default_curve"
    assert "absent, not necessarily zero" in o["evidence"]
    assert (
        a.find_opportunities([rec(query="top", page="/t", clicks=50, impressions=200, position=1.2)], [], 100)
        == []
    )
    assert (
        a.find_opportunities([rec(query="few", page="/f", clicks=0, impressions=10, position=8)], [], 100)
        == []
    )


def test_rising_impressions_without_clicks():
    cur = [rec(query="q", page="/r", clicks=2, impressions=400, position=25)]
    base = [rec(query="q", page="/r", clicks=3, impressions=100, position=25)]
    opps = a.find_opportunities(cur, base, 100)
    assert [o["category"] for o in opps] == ["impressions_rising_without_clicks"]
    assert opps[0]["query"] is None


def _split(cur_shares, base_shares, positions=(2.0, 9.0)):
    cur = [
        rec(query="q", page=f"/p{i}", clicks=1, impressions=s, position=positions[i])
        for i, s in enumerate(cur_shares)
    ]
    base = [
        rec(query="q", page=f"/p{i}", clicks=1, impressions=s, position=positions[i])
        for i, s in enumerate(base_shares)
    ]
    return a.find_cannibalization(cur, base, 10, 50)


def test_cannibalization_classifications():
    assert _split([90, 10], [90, 10])[0]["classification"] == "review"
    assert _split([90, 10], [90, 10])[0]["dominant_page"] == "/p0"
    shifted = _split([30, 70], [80, 20])[0]
    assert shifted["classification"] == "possible_competition" and shifted["traffic_shifted"]
    assert _split([60, 40], [62, 38])[0]["classification"] == "likely_intent_split"
    assert _split([60, 40], [62, 38], positions=(6.0, 7.5))[0]["classification"] == "possible_competition"
    assert _split([60, 40], [])[0]["classification"] == "review"
    assert a.find_cannibalization([rec(query="q", page="/a", impressions=100, position=1)], [], 10, 50) == []


def test_inspection_groups():
    today = date(2026, 9, 20)
    ok = {
        "verdict": "PASS",
        "robots_txt_state": "ALLOWED",
        "indexing_state": "INDEXING_ALLOWED",
        "page_fetch_state": "SUCCESSFUL",
        "google_canonical": "u",
        "user_canonical": "u",
        "last_crawl_time": "2026-09-15T00:00:00Z",
    }
    assert a.inspection_groups(ok, None, today) == ["recently_crawled", "indexed_normally"]
    blocked = ok | {
        "verdict": "FAIL",
        "robots_txt_state": "DISALLOWED",
        "page_fetch_state": "BLOCKED_ROBOTS_TXT",
        "last_crawl_time": None,
    }
    assert a.inspection_groups(blocked, None, today) == ["not_indexed", "blocked", "stale_crawl"]
    canon = ok | {
        "google_canonical": "other",
        "page_fetch_state": "SOFT_404",
        "last_crawl_time": "2026-08-01T00:00:00Z",
    }
    assert a.inspection_groups(canon, None, today) == ["fetch_problem", "canonical_mismatch"]
    assert a.inspection_groups(None, "boom", today) == ["errors"]
    assert a.inspection_groups({"verdict": "VERDICT_UNSPECIFIED"}, None, today) == ["inspection_unavailable"]


def test_parse_sitemap_urlset_ignores_image_locs():
    xml = b"""<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
            xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
      <url><loc> https://example.com/a </loc><image:image><image:loc>https://example.com/a.png</image:loc>
      </image:image></url>
      <url><loc>https://example.com/b</loc></url>
    </urlset>"""
    assert a.parse_sitemap(xml) == ("urlset", ["https://example.com/a", "https://example.com/b"])


def test_parse_sitemap_index_and_rejections():
    idx = b"<sitemapindex><sitemap><loc>https://example.com/s1.xml</loc></sitemap></sitemapindex>"
    assert a.parse_sitemap(idx) == ("sitemapindex", ["https://example.com/s1.xml"])
    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><urlset><url><loc>&lol;</loc></url></urlset>'
    for bad in (bomb, b"<urlset><url>", b"<html><body/></html>"):
        with pytest.raises(ValueError):
            a.parse_sitemap(bad)
