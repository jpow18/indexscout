"""Deterministic analysis of Search Console rows. No I/O, no randomness, no model calls.

Conventions:
- Aggregate CTR = total clicks / total impressions.
- Aggregate position = impression-weighted mean of row positions (1 = top). It is an
  average, not a fixed rank.
- A key absent from returned rows is "absent", never zero: Google omits anonymized and
  low-volume rows, and row limits truncate long tails.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

Record = dict[str, Any]

# --- basic metrics --------------------------------------------------------------------------


def to_records(rows: Iterable[dict[str, Any]], dimensions: Sequence[str]) -> list[Record]:
    """Turn API rows into flat records that keep every dimension of the row together."""
    out: list[Record] = []
    for row in rows:
        rec: Record = dict(zip(dimensions, row.get("keys", []), strict=False))
        rec["clicks"] = float(row.get("clicks", 0))
        rec["impressions"] = float(row.get("impressions", 0))
        rec["ctr"] = float(row.get("ctr", 0))
        rec["position"] = float(row.get("position", 0))
        out.append(rec)
    return out


def aggregate(records: Iterable[Record]) -> Record:
    clicks = impressions = weighted_pos = 0.0
    for r in records:
        clicks += r["clicks"]
        impressions += r["impressions"]
        weighted_pos += r["position"] * r["impressions"]
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": clicks / impressions if impressions else 0.0,
        "position": weighted_pos / impressions if impressions else None,
    }


def pct_change(current: float, baseline: float) -> float | None:
    return None if not baseline else round((current - baseline) / baseline * 100, 1)


def compare(current: Record, baseline: Record | None) -> Record:
    """Absolute and relative changes. Position delta < 0 means the average moved up (better)."""
    if baseline is None:
        return {"baseline_present": False}
    out: Record = {"baseline_present": True}
    for m in ("clicks", "impressions"):
        out[f"{m}_change"] = round(current[m] - baseline[m], 2)
        out[f"{m}_change_pct"] = pct_change(current[m], baseline[m])
    out["ctr_change_pp"] = round((current["ctr"] - baseline["ctr"]) * 100, 2)
    out["ctr_change_pct"] = pct_change(current["ctr"], baseline["ctr"])
    if current.get("position") is not None and baseline.get("position") is not None:
        out["position_change"] = round(current["position"] - baseline["position"], 2)
    return out


def rounded(m: Record) -> Record:
    out = dict(m)
    for k in ("clicks", "impressions"):
        if k in out:
            out[k] = round(out[k])
    if "ctr" in out:
        out["ctr"] = round(out["ctr"], 4)
    if out.get("position") is not None:
        out["position"] = round(out["position"], 1)
    return out


def fmt_ctr(ctr: float) -> str:
    return f"{ctr * 100:.1f}%"


def fmt_metrics(m: Record | None) -> str:
    """Readable metrics for evidence text; None means the key was absent from returned rows."""
    if m is None:
        return "absent from returned rows"
    pos = f", average position {m['position']:.1f}" if m.get("position") is not None else ""
    return f"{int(m['clicks']):,} clicks, {int(m['impressions']):,} impressions, {fmt_ctr(m['ctr'])} CTR{pos}"


def q(value: object) -> str:
    """Quote an untrusted string for use inside generated prose, so it reads as data."""
    return json.dumps(str(value), ensure_ascii=False)


# --- decomposition --------------------------------------------------------------------------


def group(records: Iterable[Record], keys: Sequence[str]) -> dict[tuple[Any, ...], Record]:
    buckets: dict[tuple[Any, ...], list[Record]] = defaultdict(list)
    for r in records:
        buckets[tuple(r.get(k) for k in keys)].append(r)
    return {k: aggregate(v) for k, v in buckets.items()}


def decompose(
    current: Sequence[Record], baseline: Sequence[Record], keys: Sequence[str], metric: str
) -> Record:
    """Split the change in `metric` across `keys`.

    Returns positive and negative contributors sorted by size, plus keys that are new in or
    absent from returned rows. `explained_change` sums only returned rows.
    """
    cur, base = group(current, keys), group(baseline, keys)
    items = []
    for k in set(cur) | set(base):
        c, b = cur.get(k), base.get(k)
        change = (c[metric] if c else 0.0) - (b[metric] if b else 0.0)
        status = (
            "in_both_periods" if c and b else ("new_in_returned_rows" if c else "absent_from_returned_rows")
        )
        items.append(
            {
                **dict(zip(keys, k, strict=True)),
                "change": round(change, 2),
                "status": status,
                "current": rounded(c) if c else None,
                "baseline": rounded(b) if b else None,
            }
        )
    items.sort(key=lambda i: (i["change"], str(i)))
    return {
        "metric": metric,
        "dimensions": list(keys),
        "explained_change": round(sum(i["change"] for i in items), 2),
        "negative": [i for i in items if i["change"] < 0],
        "positive": [i for i in reversed(items) if i["change"] > 0],
        "new": [i for i in items if i["status"] == "new_in_returned_rows"],
        "absent": [i for i in items if i["status"] == "absent_from_returned_rows"],
    }


def classify_trend(current: float, baseline: float | None) -> str:
    """Documented thresholds: ±20% impressions change separates growing/declining from stable."""
    if not baseline:
        return "new" if current else "no_data"
    if not current:
        return "absent_in_current_period"
    change = (current - baseline) / baseline
    if change >= 0.2:
        return "growing"
    if change <= -0.2:
        return "declining"
    return "stable"


# --- opportunities --------------------------------------------------------------------------

BUCKETS = ("1", "2", "3", "4-5", "6-10", "11-20", "21+")
_BUCKET_UPPER = (1.5, 2.5, 3.5, 5.5, 10.5, 20.5, float("inf"))
# Conservative fallback CTRs, used only when the property has < 1,000 impressions in a
# bucket. They are rough assumptions, not Google data; responses label which basis applied.
DEFAULT_BUCKET_CTR = {"1": 0.25, "2": 0.13, "3": 0.09, "4-5": 0.06, "6-10": 0.03, "11-20": 0.01, "21+": 0.005}
MIN_BUCKET_IMPRESSIONS = 1000
LOW_CTR_RATIO = 0.5
TREND_THRESHOLD = 0.2
TREND_ADJUSTMENT = 0.2

SCORING_FORMULA = (
    "score = potential_clicks x (1 + trend_adjustment). "
    "potential_clicks = impressions x max(0, target_ctr - current_ctr). "
    "target_ctr = the property's own impression-weighted CTR for the target position bucket "
    f"(buckets {', '.join(BUCKETS)}); if that bucket has < {MIN_BUCKET_IMPRESSIONS} impressions, a "
    f"conservative default curve {DEFAULT_BUCKET_CTR} is used and labeled. Striking-distance pairs "
    "(average position 3.5-20.5) target the next better bucket; low-CTR pairs target their own bucket. "
    f"trend_adjustment = +{TREND_ADJUSTMENT} if impressions grew >= {TREND_THRESHOLD:.0%} vs the "
    f"baseline, -{TREND_ADJUSTMENT} if they fell >= {TREND_THRESHOLD:.0%}, else 0. "
    "potential_clicks is a ranking aid, not a traffic forecast."
)


def bucket(position: float) -> str:
    for name, upper in zip(BUCKETS, _BUCKET_UPPER, strict=True):
        if position < upper:
            return name
    return BUCKETS[-1]


def bucket_ctrs(records: Iterable[Record]) -> dict[str, tuple[float, str]]:
    """Target CTR per bucket and its basis ("property" or "default_curve")."""
    grouped: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        grouped[bucket(r["position"])].append(r)
    out = {}
    for b in BUCKETS:
        agg = aggregate(grouped.get(b, []))
        if agg["impressions"] >= MIN_BUCKET_IMPRESSIONS:
            out[b] = (agg["ctr"], "property")
        else:
            out[b] = (DEFAULT_BUCKET_CTR[b], "default_curve")
    return out


def _better(b: str) -> str:
    i = BUCKETS.index(b)
    return BUCKETS[max(0, i - 1)]


def _trend_adjustment(cur_impr: float, base: Record | None) -> float:
    if not base or not base["impressions"]:
        return 0.0
    change = (cur_impr - base["impressions"]) / base["impressions"]
    if change >= TREND_THRESHOLD:
        return TREND_ADJUSTMENT
    if change <= -TREND_THRESHOLD:
        return -TREND_ADJUSTMENT
    return 0.0


_PRIMARY_ORDER = (
    "low_ctr",
    "near_page_one",
    "near_top_three",
    "striking_distance",
    "impressions_rising_without_clicks",
)

_ACTIONS = {
    "low_ctr": (
        "Review how {page} appears for {query}: compare its title and snippet with the intent shown on the "
        "live results page. Do not rewrite only because CTR is low; SERP features, ads, or brand results can "
        "lower CTR. Check the SERP and the page first."
    ),
    "near_page_one": (
        "Review whether {page} fully answers {query} and receives internal links from related pages. "
        "Improve the existing page rather than creating a new one."
    ),
    "near_top_three": (
        "Compare {page} with the pages above it for {query} and close specific content gaps. "
        "Improve the existing page rather than creating a new one."
    ),
    "striking_distance": (
        "Review whether {page} matches the intent of {query} and whether internal links support it. "
        "Improve the existing page rather than creating a new one."
    ),
    "impressions_rising_without_clicks": (
        "Find which queries drive the new impressions to {page} before changing it; the new queries may "
        "have different intent."
    ),
}

_WHY = {
    "low_ctr": "The result is shown often at this average position but earns fewer clicks than the property "
    "typically earns there.",
    "near_page_one": "The average position is just below the first ten results, where small gains can change "
    "visibility a lot.",
    "near_top_three": "The average position is just below the top three, where CTR usually rises sharply.",
    "striking_distance": "The page already ranks on average between positions 4 and 20 for this query, so an "
    "existing page may improve with targeted work.",
    "impressions_rising_without_clicks": "Google shows the page more often, but clicks have not followed.",
}

OPPORTUNITY_LIMITATIONS = [
    "Average position is impression-weighted across all searches and may hide large day-to-day or "
    "per-device variation.",
    "potential_clicks assumes the target CTR; real CTR depends on SERP features, brand, and intent.",
    "Anonymized queries are not returned, so some opportunities are invisible.",
]


def find_opportunities(
    current: Sequence[Record], baseline: Sequence[Record], min_impressions: int
) -> list[Record]:
    """Rank query/page pairs and pages by transparent potential-click scoring."""
    targets = bucket_ctrs(current)
    base_pairs = {(r["query"], r["page"]): r for r in baseline}
    out: list[Record] = []

    for r in current:
        if r["impressions"] < min_impressions:
            continue
        pos, ctr, b = r["position"], r["ctr"], bucket(r["position"])
        cats: dict[str, str] = {}  # category -> target bucket
        if b in ("4-5", "6-10", "11-20"):
            cats["striking_distance"] = _better(b)
            if pos >= 10.5 and pos < 13.5:
                cats["near_page_one"] = _better(b)
            if pos < 5.5:
                cats["near_top_three"] = _better(b)
        if pos < 10.5 and ctr < LOW_CTR_RATIO * targets[b][0]:
            cats["low_ctr"] = b
        if not cats:
            continue
        base = base_pairs.get((r["query"], r["page"]))
        out.append(_opportunity(r, base, cats, targets, query=r["query"]))

    cur_pages, base_pages = group(current, ["page"]), group(baseline, ["page"])
    for (page,), agg in cur_pages.items():
        base = base_pages.get((page,))
        if not base or agg["impressions"] < min_impressions or agg["position"] is None:
            continue
        grew = agg["impressions"] - base["impressions"]
        if grew >= max(min_impressions / 2, 0.5 * base["impressions"]) and agg["clicks"] <= base["clicks"]:
            rec = {"page": page, **agg}
            out.append(
                _opportunity(
                    rec, base, {"impressions_rising_without_clicks": bucket(agg["position"])}, targets
                )
            )

    out.sort(key=lambda o: (-o["score"], -o["impressions"], str(o.get("query")), o["page"]))
    return out


def _opportunity(
    r: Record,
    base: Record | None,
    cats: dict[str, str],
    targets: dict[str, tuple[float, str]],
    query: Any = None,
) -> Record:
    primary = next(c for c in _PRIMARY_ORDER if c in cats)
    target_bucket = cats[primary]
    target_ctr, basis = targets[target_bucket]
    potential = r["impressions"] * max(0.0, target_ctr - r["ctr"])
    adj = _trend_adjustment(r["impressions"], base)
    change = compare(r, base)
    evidence = (
        f"{int(r['impressions']):,} impressions, {int(r['clicks']):,} clicks, {fmt_ctr(r['ctr'])} CTR, "
        f"average position {r['position']:.1f}"
    )
    if change.get("baseline_present"):
        if change.get("impressions_change_pct") is not None:
            evidence += f"; impressions {change['impressions_change_pct']:+.1f}% vs baseline"
        if change.get("ctr_change_pct") is not None:
            evidence += f"; CTR {change['ctr_change_pct']:+.1f}% vs baseline"
    else:
        evidence += "; no matching row in the baseline period (absent, not necessarily zero)"
    target_label = f"target CTR {fmt_ctr(target_ctr)} (bucket {target_bucket}, basis {basis})"
    fields = {"page": q(r["page"]), "query": q(query) if query is not None else "its queries"}
    validation = (
        f"Run gsc_page_analysis for this page and check the live results page for {fields['query']} "
        "before editing."
    )
    return {
        "query": query,
        "page": r["page"],
        **rounded({k: r[k] for k in ("clicks", "impressions", "ctr", "position")}),
        "period_change": change,
        "category": primary,
        "categories": sorted(cats, key=_PRIMARY_ORDER.index),
        "score": round(potential * (1 + adj), 1),
        "score_components": {
            "impressions": int(r["impressions"]),
            "current_ctr": round(r["ctr"], 4),
            "target_bucket": target_bucket,
            "target_ctr": round(target_ctr, 4),
            "target_basis": basis,
            "ctr_gap": round(max(0.0, target_ctr - r["ctr"]), 4),
            "potential_clicks": round(potential, 1),
            "trend_adjustment": adj,
        },
        "evidence": f"{evidence}; {target_label}.",
        "why_it_may_matter": _WHY[primary],
        "suggested_action": _ACTIONS[primary].format(**fields),
        "limitations": "Estimate based on returned rows only; see response limitations.",
        "validation_step": validation,
    }


# --- cannibalization ------------------------------------------------------------------------

CANNIBALIZATION_RULES = (
    "Only queries with >= 2 pages that each have >= min_page_impressions are considered. "
    "review: one page has >= 80% of impressions, or no stronger rule applies. "
    "possible_competition: >= 2 pages each have >= 20% of impressions AND either impression share "
    "moved >= 20 points between periods, or the top two pages average within 3 positions of each "
    "other and both rank below position 3.5. "
    "likely_intent_split: >= 2 pages each have >= 20% of impressions and shares moved < 10 points, "
    "which suggests Google shows different pages for different contexts. "
    "Multi-page queries are normal; a classification is a prompt to review, not proof of harm."
)


def find_cannibalization(
    current: Sequence[Record],
    baseline: Sequence[Record],
    min_page_impressions: int,
    min_query_impressions: int,
) -> list[Record]:
    cur_by_q: dict[str, list[Record]] = defaultdict(list)
    base_by_q: dict[str, list[Record]] = defaultdict(list)
    for r in current:
        cur_by_q[r["query"]].append(r)
    for r in baseline:
        base_by_q[r["query"]].append(r)
    out: list[Record] = []
    for query, rows in cur_by_q.items():
        pages = [r for r in rows if r["impressions"] >= min_page_impressions]
        total = sum(r["impressions"] for r in rows)
        if len(pages) < 2 or total < min_query_impressions:
            continue
        base_rows = base_by_q.get(query, [])
        base_total = sum(r["impressions"] for r in base_rows)
        base_share = {r["page"]: r["impressions"] / base_total for r in base_rows} if base_total else {}
        per_page = []
        for r in sorted(pages, key=lambda x: -x["impressions"]):
            share = r["impressions"] / total
            prev = base_share.get(r["page"]) if base_total else None
            per_page.append(
                {
                    "page": r["page"],
                    **rounded({k: r[k] for k in ("clicks", "impressions", "ctr", "position")}),
                    "impression_share": round(share, 3),
                    "baseline_impression_share": round(prev, 3) if prev is not None else None,
                }
            )
        shares = [p["impression_share"] for p in per_page]
        shift = (
            max(abs(p["impression_share"] - (p["baseline_impression_share"] or 0.0)) for p in per_page)
            if base_total
            else None
        )
        top, second = per_page[0], per_page[1]
        material = shares[1] >= 0.2
        close = (
            abs(top["position"] - second["position"]) <= 3 and min(top["position"], second["position"]) > 3.5
        )
        if shares[0] >= 0.8:
            cls, why = (
                "review",
                f"{q(top['page'])} holds {shares[0]:.0%} of impressions; other pages are minor.",
            )
        elif material and shift is not None and shift >= 0.2:
            cls, why = (
                "possible_competition",
                f"Impression share moved {shift * 100:.0f} points between pages.",
            )
        elif material and close:
            cls, why = (
                "possible_competition",
                f"Top two pages average positions {top['position']} and {second['position']}, "
                "close together and outside the top 3.",
            )
        elif material and shift is not None and shift < 0.1:
            cls, why = (
                "likely_intent_split",
                f"Pages keep stable shares (max shift {shift * 100:.0f} points), which suggests different "
                "contexts or intents.",
            )
        else:
            cls, why = "review", "Several pages appear, but no rule indicates competition."
        out.append(
            {
                "query": query,
                "classification": cls,
                "total_impressions": int(total),
                "dominant_page": top["page"] if shares[0] >= 0.8 else None,
                "traffic_shifted": bool(shift is not None and shift >= 0.2),
                "max_share_shift_points": round(shift * 100, 1) if shift is not None else None,
                "baseline_present": bool(base_total),
                "pages": per_page,
                "evidence": why,
            }
        )
    order = {"possible_competition": 0, "likely_intent_split": 1, "review": 2}
    out.sort(key=lambda o: (order[o["classification"]], -o["total_impressions"], o["query"]))
    return out


# --- URL inspection -------------------------------------------------------------------------

INSPECTION_NOTICE = (
    "This is Google's indexed version of the URL from its last crawl, not a live test. "
    "The live page may differ."
)
_OK_FETCH = (None, "SUCCESSFUL", "PAGE_FETCH_STATE_UNSPECIFIED")


def summarize_inspection(result: dict[str, Any]) -> Record:
    s = result.get("indexStatusResult") or {}
    return {
        "verdict": s.get("verdict"),
        "coverage_state": s.get("coverageState"),
        "last_crawl_time": s.get("lastCrawlTime"),
        "crawled_as": s.get("crawledAs"),
        "google_canonical": s.get("googleCanonical"),
        "user_canonical": s.get("userCanonical"),
        "robots_txt_state": s.get("robotsTxtState"),
        "indexing_state": s.get("indexingState"),
        "page_fetch_state": s.get("pageFetchState"),
        "referring_urls": list(s.get("referringUrls", []))[:20],
        "sitemaps": list(s.get("sitemap", []))[:20],
        "inspection_result_link": result.get("inspectionResultLink"),
    }


GROUP_ORDER = (
    "not_indexed",
    "blocked",
    "fetch_problem",
    "canonical_mismatch",
    "inspection_unavailable",
    "errors",
    "stale_crawl",
    "recently_crawled",
    "indexed_normally",
)
RECENT_CRAWL_DAYS = 14
STALE_CRAWL_DAYS = 90


def inspection_groups(summary: Record | None, error: str | None, today: date) -> list[str]:
    """Groups a URL belongs to. A URL can be in several, e.g. not_indexed and blocked."""
    if error:
        return ["errors"]
    if not summary or summary.get("verdict") in (None, "VERDICT_UNSPECIFIED"):
        return ["inspection_unavailable"]
    groups = []
    blocked = (
        summary.get("robots_txt_state") == "DISALLOWED"
        or str(summary.get("indexing_state", "")).startswith("BLOCKED")
        or summary.get("page_fetch_state") == "BLOCKED_ROBOTS_TXT"
    )
    gc, uc = summary.get("google_canonical"), summary.get("user_canonical")
    mismatch = bool(gc and uc and gc != uc)
    if summary["verdict"] != "PASS":
        groups.append("not_indexed")
    if blocked:
        groups.append("blocked")
    if summary.get("page_fetch_state") not in (*_OK_FETCH, "BLOCKED_ROBOTS_TXT"):
        groups.append("fetch_problem")
    if mismatch:
        groups.append("canonical_mismatch")
    crawl = summary.get("last_crawl_time")
    age = None
    if crawl:
        try:
            age = (today - datetime.fromisoformat(crawl.replace("Z", "+00:00")).date()).days
        except ValueError:
            age = None
    if age is None or age > STALE_CRAWL_DAYS:
        groups.append("stale_crawl")
    elif age <= RECENT_CRAWL_DAYS:
        groups.append("recently_crawled")
    if summary["verdict"] == "PASS" and not blocked and not mismatch:
        groups.append("indexed_normally")
    return groups
