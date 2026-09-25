from datetime import timedelta

import pytest

from indexscout import validate as v


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sc-domain:Example.COM", "sc-domain:example.com"),
        ("https://www.example.com/", "https://www.example.com/"),
        ("https://WWW.Example.com:443/blog/", "https://www.example.com/blog/"),
        ("http://example.com:8080/", "http://example.com:8080/"),
    ],
)
def test_normalize_property(raw, expected):
    assert v.normalize_property(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "example.com",
        "https://www.example.com",
        "ftp://example.com/",
        "sc-domain:",
        "sc-domain:exa mple.com",
        "https://user:pw@example.com/",
        "https://example.com/?a=1",
    ],
)
def test_normalize_property_rejects(raw):
    with pytest.raises(v.ValidationError):
        v.normalize_property(raw)


@pytest.mark.parametrize(
    ("url", "prop", "ok"),
    [
        ("https://example.com/a", "sc-domain:example.com", True),
        ("http://blog.example.com/a", "sc-domain:example.com", True),
        ("https://example.com:8443/a", "sc-domain:example.com", True),
        ("https://notexample.com/a", "sc-domain:example.com", False),
        ("https://example.com.evil.net/a", "sc-domain:example.com", False),
        ("https://user@example.com/a", "sc-domain:example.com", False),
        ("javascript:alert(1)", "sc-domain:example.com", False),
        ("https://www.example.com/blog/post", "https://www.example.com/blog/", True),
        ("https://www.example.com/blog", "https://www.example.com/blog/", False),
        ("https://www.example.com/other", "https://www.example.com/blog/", False),
        ("http://www.example.com/blog/x", "https://www.example.com/blog/", False),
        ("https://example.com/blog/x", "https://www.example.com/blog/", False),
        ("https://www.example.com:443/blog/x", "https://www.example.com/blog/", True),
        ("https://www.example.com:8443/blog/x", "https://www.example.com/blog/", False),
        ("https://www.example.com:8443/x", "https://www.example.com:8443/", True),
    ],
)
def test_url_in_property(url, prop, ok):
    assert v.url_in_property(url, prop) is ok


def test_allowlist(monkeypatch):
    assert v.require_property("sc-domain:a.com") == "sc-domain:a.com"
    monkeypatch.setenv("INDEXSCOUT_ALLOWED_PROPERTIES", "sc-domain:a.com, https://b.com/")
    assert v.require_property("sc-domain:A.com") == "sc-domain:a.com"
    assert v.require_property("https://b.com/") == "https://b.com/"
    with pytest.raises(v.ValidationError, match="not in INDEXSCOUT_ALLOWED_PROPERTIES"):
        v.require_property("sc-domain:c.com")
    with pytest.raises(v.ValidationError):
        v.require_property("https://b.com/sub/")


def test_dates():
    today = v.today_pt()
    with pytest.raises(v.ValidationError):
        v.parse_date("2026/01/01")
    with pytest.raises(v.ValidationError, match="after"):
        v.check_range(today, today - timedelta(days=1))
    with pytest.raises(v.ValidationError, match="future"):
        v.check_range(today, today + timedelta(days=1))
    with pytest.raises(v.ValidationError, match="retention"):
        v.check_range(today - timedelta(days=600), today)
    s, e = today - timedelta(days=6), today
    ps, pe = v.previous_period(s, e)
    assert v.days_in(ps, pe) == v.days_in(s, e) == 7 and pe == s - timedelta(days=1)


def test_dimensions_and_filters():
    assert v.check_dimensions(["query", "page"]) == ["query", "page"]
    for bad in (["hour"], ["query", "query"]):
        with pytest.raises(v.ValidationError):
            v.check_dimensions(bad)
    with pytest.raises(v.ValidationError, match="query dimension"):
        v.check_dimensions(["query"], "discover")
    assert v.check_filter({"dimension": "device", "expression": "mobile"})["expression"] == "MOBILE"
    assert v.check_filter({"dimension": "country", "expression": "USA"})["expression"] == "usa"
    for bad in (
        {"dimension": "date", "expression": "x"},
        {"dimension": "query", "operator": "like", "expression": "x"},
        {"dimension": "query", "expression": ""},
        {"dimension": "country", "expression": "us"},
    ):
        with pytest.raises(v.ValidationError):
            v.check_filter(bad)
    with pytest.raises(v.ValidationError):
        v.check_filters([{"dimension": "query", "expression": "x"}] * 11)


def test_clean_text_strips_control_and_bidi():
    assert v.clean_text("a\x00b‮c​d") == "abcd"
    assert len(v.clean_text("x" * 5000, 100)) == 100
