"""
redirects — SEO redirect validation engine.

For each source URL it follows the full redirect chain, records every hop,
inspects the final page, compares against an expected landing page, and returns
a structured verdict with issues for the report.
"""

import re
import time
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from requests.adapters import HTTPAdapter
import lxml.html

USER_AGENT = "Mozilla/5.0 (compatible; SEO-Redirect-Validator/1.0)"
HEADERS = {"User-Agent": USER_AGENT}
TIMEOUT = 15
MAX_HOPS = 15
SLOW_MS = 1000
REDIRECT_CODES = (301, 302, 303, 307, 308)


def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    a = HTTPAdapter(pool_connections=32, pool_maxsize=32)
    s.mount("https://", a)
    s.mount("http://", a)
    return s


def _norm(u):
    """Normalize a URL for equality comparison: drop scheme, www, trailing slash, fragment, lowercase host."""
    if not u:
        return ""
    u = u.split("#")[0].strip()
    p = urlparse(u if "//" in u else "http://" + u)
    host = p.netloc.lower().replace("www.", "")
    path = p.path.rstrip("/") or "/"
    q = ("?" + p.query) if p.query else ""
    return host + path + q


def same_url(a, b):
    return bool(a) and bool(b) and _norm(a) == _norm(b)


def _visit_key(u):
    """Exact identity of a URL for loop detection — keeps scheme & trailing slash
    (so http->https and /p -> /p/ are NOT treated as loops)."""
    u = (u or "").split("#")[0].strip()
    p = urlparse(u)
    return (p.scheme.lower(), p.netloc.lower(), p.path, p.query)


def _analyze_final(resp, final_url):
    """Pull canonical + indexability from the final response."""
    canonical, indexable, reason = "", True, ""
    xr = (resp.headers.get("X-Robots-Tag") or "").lower()
    if "noindex" in xr:
        indexable, reason = False, "X-Robots-Tag: noindex"
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "html" in ctype:
        try:
            doc = lxml.html.fromstring(resp.text)
            can = doc.xpath("//link[contains(translate(@rel,'CANONICAL','canonical'),'canonical')]/@href")
            if can:
                canonical = urljoin(final_url, can[0].strip())
            for m in doc.xpath("//meta"):
                if (m.get("name") or "").lower() == "robots":
                    c = (m.get("content") or "").lower()
                    if "noindex" in c:
                        indexable, reason = False, "meta robots: noindex"
        except Exception:
            pass
    return canonical, indexable, reason


def validate(source, expected="", session=None):
    """Validate one source URL. Returns a full result dict."""
    session = session or make_session()
    source = source.strip()
    chain = []                 # [{url, status}]
    visited = set()
    loop = False
    url = source
    t0 = time.time()
    final_resp = None

    for _ in range(MAX_HOPS):
        key = _visit_key(url)
        if key in visited:
            loop = True
            break
        visited.add(key)
        # retry on transient connection errors/timeouts (slow servers drop
        # connections under load) before treating the URL as unreachable
        r, last_err = None, None
        for attempt in range(3):
            try:
                r = session.get(url, allow_redirects=False, timeout=TIMEOUT)
                break
            except requests.RequestException as e:
                last_err = e
                time.sleep(0.4 * (attempt + 1))
        if r is None:
            chain.append({"url": url, "status": 0, "error": str(last_err)[:120]})
            break
        chain.append({"url": url, "status": r.status_code})
        loc = r.headers.get("Location")
        if r.status_code in REDIRECT_CODES and loc:
            url = urljoin(url, loc)
            continue
        final_resp = r
        break

    total_ms = int((time.time() - t0) * 1000)
    final_url = chain[-1]["url"] if chain else source
    final_status = chain[-1]["status"] if chain else 0
    hop_count = sum(1 for c in chain if c["status"] in REDIRECT_CODES)
    redirect_type = next((c["status"] for c in chain if c["status"] in REDIRECT_CODES), None)

    canonical, indexable, index_reason = "", True, ""
    if final_resp is not None and final_resp.status_code == 200:
        canonical, indexable, index_reason = _analyze_final(final_resp, final_url)

    # ---- comparisons & flags ----
    sp, fp = urlparse(source), urlparse(final_url)
    expected_match = same_url(final_url, expected) if expected else None
    canonical_ok = (not canonical) or same_url(canonical, final_url)
    is_https = final_url.lower().startswith("https")
    src_www = sp.netloc.lower().startswith("www.")
    fin_www = fp.netloc.lower().startswith("www.")
    src_slash = sp.path.endswith("/")
    fin_slash = fp.path.endswith("/")
    # query / utm preservation
    src_q = parse_qs(sp.query)
    fin_q = parse_qs(fp.query)
    lost_params = [k for k in src_q if k not in fin_q]
    lost_utm = [k for k in lost_params if k.startswith("utm_")]

    issues = []
    def add(sev, msg):
        issues.append({"severity": sev, "msg": msg})

    if loop:
        add("Critical", "Redirect loop detected")
    if final_status == 0:
        add("Critical", "Final URL unreachable / connection error")
    elif final_status in (404, 410):
        add("Critical", f"Final URL returns {final_status}")
    elif final_status >= 500:
        add("Critical", f"Final URL server error {final_status}")
    if expected and not expected_match:
        add("High", "Final landing page does not match expected")
    if redirect_type in (302, 307):
        add("High", f"Temporary redirect ({redirect_type}) — use 301 for migrations")
    if hop_count > 1:
        add("Medium", f"Redirect chain ({hop_count} hops) — redirect directly to the final URL")
    if not canonical_ok:
        add("Medium", "Canonical does not match the final URL")
    if final_status == 200 and not indexable:
        add("High", f"Final page is non-indexable ({index_reason})")
    if final_status == 200 and not is_https:
        add("Medium", "Final URL is not HTTPS")
    if lost_utm:
        add("Medium", "UTM parameters lost through redirect: " + ", ".join(lost_utm))
    elif lost_params:
        add("Low", "Query parameters lost: " + ", ".join(lost_params))
    slow = hop_count > 0 and total_ms > SLOW_MS   # "slow redirect" only applies when it redirects
    if slow:
        add("Low", f"Slow redirect ({total_ms} ms)")

    # PASS/FAIL — hard failures only
    fail = bool(loop) or final_status != 200 or (expected and not expected_match) \
        or (final_status == 200 and not indexable) or not canonical_ok
    result = "FAIL" if fail else "PASS"

    return {
        "source": source,
        "expected": expected,
        "final_url": final_url,
        "final_status": final_status,
        "redirect_type": redirect_type or ("—" if final_status == 200 else None),
        "hops": hop_count,
        "chain": chain,
        "is_chain": hop_count > 1,
        "is_loop": loop,
        "expected_match": expected_match,
        "canonical": canonical,
        "canonical_ok": canonical_ok,
        "indexable": indexable,
        "index_reason": index_reason,
        "is_https": is_https,
        "www": {"source": src_www, "final": fin_www, "changed": src_www != fin_www},
        "trailing_slash": {"source": src_slash, "final": fin_slash},
        "lost_params": lost_params,
        "lost_utm": lost_utm,
        "speed_ms": total_ms,
        "slow": slow,
        "issues": issues,
        "result": result,
    }


def summarize(results):
    """Dashboard metrics + SEO health score (0-100)."""
    n = len(results) or 1
    passed = sum(1 for r in results if r["result"] == "PASS")
    failed = len(results) - passed
    chains = sum(1 for r in results if r["is_chain"])
    loops = sum(1 for r in results if r["is_loop"])
    broken = sum(1 for r in results if r["final_status"] == 0 or r["final_status"] >= 400)
    wrong_landing = sum(1 for r in results if r["expected_match"] is False)
    canon_err = sum(1 for r in results if not r["canonical_ok"])
    noindex = sum(1 for r in results if r["final_status"] == 200 and not r["indexable"])
    slow = sum(1 for r in results if r["slow"])
    https_iss = sum(1 for r in results if r["final_status"] == 200 and not r["is_https"])
    temp = sum(1 for r in results if r["redirect_type"] in (302, 307))
    avg_speed = round(sum(r["speed_ms"] for r in results) / n)

    # weighted health score
    score = 100.0
    score -= (failed / n) * 35
    score -= (loops / n) * 20
    score -= (broken / n) * 20
    score -= (chains / n) * 8
    score -= (canon_err / n) * 8
    score -= (noindex / n) * 8
    score -= (temp / n) * 6
    score -= (https_iss / n) * 4
    score -= (slow / n) * 3
    score = max(0, min(100, round(score)))

    return {
        "total": len(results), "passed": passed, "failed": failed,
        "chains": chains, "loops": loops, "broken": broken,
        "wrong_landing": wrong_landing, "canonical_errors": canon_err,
        "non_indexable": noindex, "slow": slow, "https_issues": https_iss,
        "temporary": temp, "avg_speed_ms": avg_speed, "health_score": score,
    }
