"""
Auditor — professional SEO rules engine for a pre-migration audit.

Each rule inspects a crawled page record and returns zero or more issues.
Every issue carries: severity, exact location, how to fix, and why it matters.
A page with no issues from a rule is implicitly "clear".
"""

# Best-practice thresholds
TITLE_MIN, TITLE_MAX = 30, 60
DESC_MIN, DESC_MAX = 70, 160
THIN_CONTENT_WORDS = 300


def _norm_url(u):
    """Normalize a URL for comparison (ignore scheme, www, trailing slash, case)."""
    return (u or "").lower().replace("https://", "").replace("http://", "") \
        .replace("www.", "").rstrip("/")


def canonical_status(page):
    """'ok' (self-referencing), 'mismatch' (points elsewhere), or 'missing'."""
    c = page.get("canonical", "")
    if not c:
        return "missing"
    target = _norm_url(page.get("final_url") or page.get("url"))
    if _norm_url(c) in (target, _norm_url(page.get("url"))):
        return "ok"
    return "mismatch"


def _issue(url, check, severity, detail, fix, why, location=""):
    return {
        "url": url,
        "check": check,
        "severity": severity,          # Critical | High | Medium | Low
        "detail": detail,
        "location": location or url,
        "how_to_fix": fix,
        "why": why,
    }


def audit_page(page, status_map, all_titles, all_descs, all_canonicals):
    """Return a list of issues for a single page (empty list == fully clear)."""
    issues = []
    url = page.get("url", "")

    # --- fetch / status ---
    if page.get("error"):
        issues.append(_issue(
            url, "Fetch error", "Critical",
            f"Page could not be fetched: {page['error']}",
            "Confirm the URL is live and not blocking crawlers; check server logs.",
            "A page that can't be fetched can't be indexed or migrated."))
        return issues

    if page.get("non_html"):
        return issues  # non-HTML asset, skip on-page rules

    status = page.get("status", 0)
    if status == 0:
        issues.append(_issue(
            url, "No response", "Critical", "Server returned no response.",
            "Check hosting/DNS and that the page exists.",
            "Unreachable pages are dropped from the index."))
        return issues
    if status >= 500:
        issues.append(_issue(
            url, "Server error", "Critical", f"HTTP {status} server error.",
            "Fix the server-side error before migration.",
            "5xx errors block indexing and lose rankings."))
    elif status == 404:
        issues.append(_issue(
            url, "Broken page (404)", "Critical", "Page returns 404 Not Found.",
            "Restore the page or set a 301 redirect to the best equivalent URL.",
            "404s waste crawl budget and lose any rankings/backlinks the URL had."))
    elif 400 <= status < 500:
        issues.append(_issue(
            url, "Client error", "High", f"HTTP {status}.",
            "Investigate access rules / auth blocking the crawler.",
            "4xx pages are not indexed."))

    if page.get("redirected"):
        issues.append(_issue(
            url, "Redirect", "Low",
            f"URL redirects to {page.get('final_url')}",
            "Ensure internal links point to the final URL to avoid redirect hops.",
            "Redirect chains dilute link equity and slow crawling.",
            location=url))

    # --- indexability ---
    robots = page.get("meta_robots", "")
    if "noindex" in robots:
        issues.append(_issue(
            url, "Noindex tag", "Critical",
            "Page has a 'noindex' meta robots tag.",
            "Remove 'noindex' if this page should rank; keep it only if intentional.",
            "Noindex removes the page from Google entirely — a frequent migration mistake.",
            location="<meta name='robots'>"))

    # --- title ---
    title = page.get("title", "")
    tlen = page.get("title_len", 0)
    if not title:
        issues.append(_issue(
            url, "Missing title", "Critical", "No <title> tag / empty title.",
            "Add a unique, descriptive title with the primary keyword near the front.",
            "The title is the strongest on-page ranking signal and the main SERP headline.",
            location="<title>"))
    else:
        if tlen < TITLE_MIN:
            issues.append(_issue(
                url, "Title too short", "Medium",
                f"Title is {tlen} chars (aim {TITLE_MIN}-{TITLE_MAX}).",
                "Expand the title with descriptive, keyword-relevant wording.",
                "Short titles under-use SERP space and weaken relevance.",
                location="<title>"))
        elif tlen > TITLE_MAX:
            issues.append(_issue(
                url, "Title too long", "Low",
                f"Title is {tlen} chars (aim {TITLE_MIN}-{TITLE_MAX}).",
                "Trim to ~60 chars so it isn't truncated in results.",
                "Truncated titles reduce clarity and CTR.",
                location="<title>"))
        if all_titles.get(title, 0) > 1:
            issues.append(_issue(
                url, "Duplicate title", "High",
                f"Title is shared by {all_titles[title]} pages.",
                "Make each page's title unique.",
                "Duplicate titles cause keyword cannibalisation and confuse ranking.",
                location="<title>"))

    # --- meta description ---
    desc = page.get("meta_desc", "")
    dlen = page.get("meta_desc_len", 0)
    if not desc:
        issues.append(_issue(
            url, "Missing meta description", "Medium", "No meta description.",
            "Write a compelling 70-160 char description with a call to action.",
            "Google often uses it as the SERP snippet; a good one lifts CTR.",
            location="<meta name='description'>"))
    else:
        if dlen < DESC_MIN:
            issues.append(_issue(
                url, "Meta description too short", "Low",
                f"Description is {dlen} chars (aim {DESC_MIN}-{DESC_MAX}).",
                "Expand to better summarise the page.",
                "Very short descriptions waste snippet space.",
                location="<meta name='description'>"))
        elif dlen > DESC_MAX:
            issues.append(_issue(
                url, "Meta description too long", "Low",
                f"Description is {dlen} chars (aim {DESC_MIN}-{DESC_MAX}).",
                "Trim so it isn't truncated in the SERP.",
                "Truncated snippets look unpolished and cut the message.",
                location="<meta name='description'>"))
        if all_descs.get(desc, 0) > 1:
            issues.append(_issue(
                url, "Duplicate meta description", "Medium",
                f"Description shared by {all_descs[desc]} pages.",
                "Write a unique description per page.",
                "Duplicate descriptions signal low-value / templated content.",
                location="<meta name='description'>"))

    # --- headings ---
    h1c = page.get("h1_count", 0)
    if h1c == 0:
        issues.append(_issue(
            url, "Missing H1", "High", "No H1 heading found.",
            "Add one clear H1 describing the page's main topic.",
            "The H1 reinforces page topic for users and search engines.",
            location="<h1>"))
    elif h1c > 1:
        issues.append(_issue(
            url, "Multiple H1", "Low", f"{h1c} H1 tags found.",
            "Keep a single primary H1; demote the rest to H2/H3.",
            "Multiple H1s dilute the topical focus.",
            location="<h1>"))

    # --- canonical ---
    canonical = page.get("canonical", "")
    if not canonical:
        issues.append(_issue(
            url, "Missing canonical", "Medium", "No canonical tag.",
            "Add a self-referencing canonical tag.",
            "Canonicals prevent duplicate-content dilution across URL variants.",
            location="<link rel='canonical'>"))

    # --- content depth ---
    wc = page.get("word_count", 0)
    if wc < THIN_CONTENT_WORDS and status == 200:
        issues.append(_issue(
            url, "Thin content", "Medium",
            f"Only ~{wc} words of content.",
            "Add substantive, useful content (aim 300+ meaningful words).",
            "Thin pages struggle to rank and can trigger quality issues.",
            location="page body"))

    # --- images ---
    missing_alt = page.get("img_missing_alt", 0)
    if missing_alt > 0:
        issues.append(_issue(
            url, "Images missing ALT", "Low",
            f"{missing_alt} of {page.get('img_count', 0)} images lack ALT text.",
            "Add descriptive ALT text to every meaningful image.",
            "ALT text aids accessibility and image-search visibility.",
            location="<img> tags"))

    # --- mobile ---
    if not page.get("has_viewport"):
        issues.append(_issue(
            url, "No viewport meta", "Medium", "Missing responsive viewport meta.",
            "Add <meta name='viewport' content='width=device-width, initial-scale=1'>.",
            "Without it the page isn't mobile-friendly; Google is mobile-first.",
            location="<head>"))

    # --- structured data ---
    if not page.get("ld_types"):
        issues.append(_issue(
            url, "No structured data", "Low", "No JSON-LD schema detected.",
            "Add relevant schema (Course, Organization, BreadcrumbList, FAQ).",
            "Structured data unlocks rich results and better SERP presence.",
            location="<script type='ld+json'>"))

    # --- canonical points elsewhere ---
    if canonical:
        def _norm(u):
            return u.lower().replace("https://", "").replace("http://", "") \
                    .replace("www.", "").rstrip("/")
        if _norm(canonical) != _norm(page.get("final_url", url)) and \
                _norm(canonical) != _norm(url):
            issues.append(_issue(
                url, "Canonical points elsewhere", "Medium",
                f"Canonical points to a different URL: {canonical}",
                "Confirm this is intentional; otherwise self-reference the canonical.",
                "A canonical to another URL tells Google to index that page instead of this one.",
                location="<link rel='canonical'>"))

    # --- duplicate head tags ---
    if page.get("title_count", 1) > 1:
        issues.append(_issue(
            url, "Multiple title tags", "High",
            f"{page['title_count']} <title> tags found.",
            "Keep exactly one <title> tag.",
            "Multiple titles confuse search engines about which to use.",
            location="<head>"))
    if page.get("canonical_count", 0) > 1:
        issues.append(_issue(
            url, "Multiple canonical tags", "High",
            f"{page['canonical_count']} canonical tags found.",
            "Keep a single canonical tag.",
            "Conflicting canonicals are ignored by Google, losing duplicate protection.",
            location="<head>"))
    if page.get("desc_count", 0) > 1:
        issues.append(_issue(
            url, "Multiple meta descriptions", "Low",
            f"{page['desc_count']} meta descriptions found.",
            "Keep a single meta description.",
            "Duplicate description tags are ambiguous and look untidy.",
            location="<head>"))

    # --- HTTPS & mixed content ---
    if page.get("is_https") is False:
        issues.append(_issue(
            url, "Not served over HTTPS", "High", "Page loads over insecure HTTP.",
            "Serve the page over HTTPS and redirect HTTP to HTTPS.",
            "HTTPS is a ranking signal and browsers warn users on HTTP pages.",
            location=url))
    if page.get("mixed_content", 0) > 0:
        issues.append(_issue(
            url, "Mixed content", "High",
            f"{page['mixed_content']} resource(s) loaded over HTTP on an HTTPS page.",
            "Update those resource URLs to HTTPS.",
            "Mixed content is blocked or warned by browsers and breaks the secure padlock.",
            location="page resources"))

    # --- social tags ---
    if not page.get("og_present") or not page.get("og_image"):
        issues.append(_issue(
            url, "Incomplete Open Graph", "Low",
            "Missing og:title and/or og:image.",
            "Add og:title, og:description and og:image meta tags.",
            "Open Graph controls how the page looks when shared on social/messaging.",
            location="<head>"))
    if not page.get("twitter_card"):
        issues.append(_issue(
            url, "Missing Twitter card", "Low", "No twitter:card meta tag.",
            "Add twitter:card (summary_large_image) and related tags.",
            "Twitter/X cards improve link previews and click-through from social.",
            location="<head>"))

    # --- hreflang (important for regional /in/ pages) ---
    if page.get("hreflang_count", 0) == 0:
        issues.append(_issue(
            url, "Missing hreflang", "Medium", "No hreflang alternates declared.",
            "Add hreflang tags mapping each language/region version of this page.",
            "Without hreflang, Google may show the wrong regional URL (e.g. US instead of /in/).",
            location="<head>"))

    # --- html lang attribute ---
    if not page.get("html_lang"):
        issues.append(_issue(
            url, "Missing lang attribute", "Low", "The <html> tag has no lang attribute.",
            "Add lang, e.g. <html lang=\"en-IN\">.",
            "The lang attribute aids accessibility and correct regional targeting.",
            location="<html>"))

    # --- heading hierarchy skips ---
    seq = page.get("heading_seq", [])
    skips = []
    prev = 0
    for lvl in seq:
        if prev and lvl > prev + 1:
            skips.append(f"H{prev}->H{lvl}")
        prev = lvl
    if skips:
        issues.append(_issue(
            url, "Heading hierarchy skips", "Low",
            f"Heading levels jump: {', '.join(skips[:4])}.",
            "Don't skip levels (e.g. H2 then H4); use them in order.",
            "A logical heading order helps accessibility and content understanding.",
            location="headings"))

    # --- performance proxies ---
    rms = page.get("response_ms", 0)
    if rms > 3000:
        issues.append(_issue(
            url, "Very slow response", "High", f"Server responded in {rms} ms.",
            "Investigate server/TTFB; enable caching/CDN.",
            "Slow responses hurt Core Web Vitals and rankings, especially on mobile.",
            location="server"))
    elif rms > 1500:
        issues.append(_issue(
            url, "Slow response", "Medium", f"Server responded in {rms} ms.",
            "Aim for under ~800 ms TTFB; check hosting and caching.",
            "Slower pages convert worse and can rank lower.",
            location="server"))

    if page.get("size_kb", 0) > 3000:
        issues.append(_issue(
            url, "Large page size", "Low", f"Page HTML is {page['size_kb']} KB.",
            "Reduce inline assets, compress, and lazy-load below-the-fold content.",
            "Heavy pages load slowly on mobile and consume crawl budget.",
            location="page weight"))

    if len(page.get("internal_links", [])) > 300:
        issues.append(_issue(
            url, "Excessive internal links", "Low",
            f"{len(page['internal_links'])} internal links on the page.",
            "Trim navigation/footer link bloat to the most useful links.",
            "Too many links dilute link equity and overwhelm users.",
            location="in-page links"))

    if page.get("meta_keywords"):
        issues.append(_issue(
            url, "Legacy meta keywords", "Low", "A meta keywords tag is present.",
            "Remove it; it's unused by Google and can expose your keyword targeting.",
            "Meta keywords have been ignored by Google for years and add no value.",
            location="<head>"))

    # --- URL format ---
    from urllib.parse import urlparse as _up
    path = _up(url).path
    url_problems = []
    if any(c.isupper() for c in path):
        url_problems.append("uppercase letters")
    if "_" in path:
        url_problems.append("underscores (use hyphens)")
    if " " in url or "%20" in url:
        url_problems.append("spaces")
    if len(url) > 115:
        url_problems.append("very long")
    if url_problems:
        issues.append(_issue(
            url, "URL format", "Low",
            "URL has: " + ", ".join(url_problems) + ".",
            "Use short, lowercase, hyphen-separated URLs.",
            "Clean URLs are easier to read, share, and rank.",
            location=url))

    # --- link analysis (dofollow/nofollow, new-tab safety) ---
    ls = page.get("link_summary") or {}
    if ls.get("internal_nofollow", 0) > 0:
        issues.append(_issue(
            url, "Internal links set to nofollow", "Medium",
            f"{ls['internal_nofollow']} internal link(s) use rel=nofollow.",
            "Remove nofollow from internal links so they pass ranking value.",
            "Nofollow on your own internal links wastes link equity inside the site.",
            location="in-page links"))
    if ls.get("external_dofollow", 0) > 0:
        issues.append(_issue(
            url, "External links not nofollow", "Low",
            f"{ls['external_dofollow']} external link(s) are dofollow.",
            "Add rel='nofollow' (or sponsored/ugc) to outbound links you don't vouch for.",
            "Dofollow external links pass your ranking value out to other sites.",
            location="in-page links"))
    if ls.get("blank_unsafe", 0) > 0:
        issues.append(_issue(
            url, "Unsafe new-tab links", "Low",
            f"{ls['blank_unsafe']} link(s) open in a new tab (target=_blank) without rel=noopener.",
            "Add rel='noopener' to every target='_blank' link.",
            "Without noopener the opened page can reach your window (security & performance risk).",
            location="in-page links"))

    # --- broken internal links ---
    broken = []
    for link in page.get("internal_links", []):
        st = status_map.get(link)
        # A broken link means the target is definitively gone: 404 (Not Found) or
        # 410 (Gone). Everything else — status 0 (couldn't fetch), 429, and 5xx
        # (502/503/504 gateway/throttle) — is a transient/unverifiable condition we
        # already retry during the crawl; counting those flagged nav/footer links to
        # live-but-throttled pages as broken on ~every page (sitewide false positives).
        if st in (404, 410):
            broken.append(f"{link} ({st})")
    if broken:
        sample = "; ".join(broken[:5]) + (" ..." if len(broken) > 5 else "")
        issues.append(_issue(
            url, "Broken internal link(s)", "High",
            f"{len(broken)} internal link(s) point to error pages: {sample}",
            "Update or remove the links; fix the target pages.",
            "Broken links waste crawl budget and hurt user experience.",
            location="in-page links"))

    return issues


def audit_all(pages):
    """Run the full audit across all crawled pages. Returns (issues, page_index)."""
    status_map = {p.get("url"): p.get("status", 0) for p in pages}
    status_map.update({p.get("final_url"): p.get("status", 0)
                       for p in pages if p.get("final_url")})

    # duplicate detection tables
    all_titles, all_descs, all_canon = {}, {}, {}
    for p in pages:
        if p.get("title"):
            all_titles[p["title"]] = all_titles.get(p["title"], 0) + 1
        if p.get("meta_desc"):
            all_descs[p["meta_desc"]] = all_descs.get(p["meta_desc"], 0) + 1
        if p.get("canonical"):
            all_canon[p["canonical"]] = all_canon.get(p["canonical"], 0) + 1

    all_issues = []
    page_index = []
    for p in pages:
        # annotate each link with its known crawl status (for broken-link display)
        broken_links = 0
        for lk in p.get("links", []):
            st = status_map.get(lk["href"])
            lk["status"] = st
            # broken = definitively gone only (404/410); see note in audit_page
            if st in (404, 410):
                broken_links += 1
        p["broken_links"] = broken_links

        pissues = audit_page(p, status_map, all_titles, all_descs, all_canon)
        all_issues.extend(pissues)
        ls = p.get("link_summary") or {}
        page_index.append({
            "url": p.get("url"),
            "status": p.get("status", 0),
            "title": p.get("title", ""),
            "title_len": p.get("title_len", 0),
            "meta_desc_len": p.get("meta_desc_len", 0),
            "h1_count": p.get("h1_count", 0),
            "word_count": p.get("word_count", 0),
            "img_missing_alt": p.get("img_missing_alt", 0),
            "canonical": p.get("canonical", ""),
            "canonical_status": canonical_status(p),
            "has_schema": p.get("has_schema", False),
            "meta_robots": p.get("meta_robots", ""),
            "internal_links": ls.get("internal", len(p.get("internal_links", []))),
            "external_links": ls.get("external", len(p.get("external_links", []))),
            "internal_nofollow": ls.get("internal_nofollow", 0),
            "external_dofollow": ls.get("external_dofollow", 0),
            "blank_unsafe": ls.get("blank_unsafe", 0),
            "broken_links": broken_links,
            "response_ms": p.get("response_ms", 0),
            "issue_count": len(pissues),
            "verdict": "CLEAR" if not pissues else "ISSUES",
        })
    return all_issues, page_index


def summarize(page_index, all_issues):
    sev_order = ["Critical", "High", "Medium", "Low"]
    by_sev = {s: 0 for s in sev_order}
    for i in all_issues:
        by_sev[i["severity"]] = by_sev.get(i["severity"], 0) + 1
    by_check = {}
    for i in all_issues:
        by_check[i["check"]] = by_check.get(i["check"], 0) + 1
    clear = sum(1 for p in page_index if p["verdict"] == "CLEAR")
    # weighted SEO health score (0-100) from severity rates per page
    n = len(page_index) or 1
    score = 100.0
    score -= min(45, by_sev["Critical"] / n * 100 * 0.60)
    score -= min(28, by_sev["High"] / n * 100 * 0.22)
    score -= min(18, by_sev["Medium"] / n * 100 * 0.10)
    score -= min(10, by_sev["Low"] / n * 100 * 0.04)
    return {
        "pages_crawled": len(page_index),
        "pages_clear": clear,
        "pages_with_issues": len(page_index) - clear,
        "total_issues": len(all_issues),
        "by_severity": by_sev,
        "by_check": dict(sorted(by_check.items(), key=lambda x: -x[1])),
        "health_score": max(0, round(score)),
    }
