"""
perf — Google PageSpeed Insights integration (real performance / Core Web Vitals).

Uses a simple Google API key (from Settings) if provided; works without one at
low volume too. Called on demand from the per-URL detail page, not during the crawl.
"""

import requests

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def pagespeed(url, api_key="", strategy="mobile"):
    """Return a compact dict of performance score + Core Web Vitals, or {'error': ...}."""
    params = {"url": url, "strategy": strategy, "category": "performance"}
    if api_key:
        params["key"] = api_key
    try:
        r = requests.get(ENDPOINT, params=params, timeout=70)
        data = r.json()
    except Exception as e:
        return {"error": f"Request failed: {e}"}

    if "error" in data:
        return {"error": data["error"].get("message", "PageSpeed API error")}

    lh = data.get("lighthouseResult", {})
    audits = lh.get("audits", {})
    cats = lh.get("categories", {})

    def metric(key):
        a = audits.get(key, {})
        return {"display": a.get("displayValue", "—"),
                "score": a.get("score")}

    perf_score = cats.get("performance", {}).get("score")

    # CrUX field data (real users), if available
    field = {}
    loading = data.get("loadingExperience", {}).get("metrics", {})
    for k, label in (("LARGEST_CONTENTFUL_PAINT_MS", "LCP"),
                     ("CUMULATIVE_LAYOUT_SHIFT_SCORE", "CLS"),
                     ("INTERACTION_TO_NEXT_PAINT", "INP"),
                     ("FIRST_CONTENTFUL_PAINT_MS", "FCP")):
        if k in loading:
            field[label] = loading[k].get("category", "—")

    return {
        "strategy": strategy,
        "performance_score": round(perf_score * 100) if perf_score is not None else None,
        "lab": {
            "LCP": metric("largest-contentful-paint"),
            "FCP": metric("first-contentful-paint"),
            "CLS": metric("cumulative-layout-shift"),
            "TBT": metric("total-blocking-time"),
            "SI": metric("speed-index"),
        },
        "field": field,
    }
