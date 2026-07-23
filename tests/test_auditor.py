"""Tests for the SEO audit rules engine."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auditor  # noqa: E402
import report   # noqa: E402


def _page(**over):
    """A healthy baseline page; override fields to trigger specific rules."""
    base = {
        "url": "https://x.com/in/a/", "final_url": "https://x.com/in/a/",
        "status": 200, "redirected": False,
        "title": "A Great Course on Project Management Fundamentals",  # 30-60
        "title_len": 48,
        "meta_desc": "Learn project management with this comprehensive course covering all the essential fundamentals you need to succeed today.",
        "meta_desc_len": 120,
        "meta_robots": "", "canonical": "https://x.com/in/a/",
        "has_viewport": True, "html_lang": "en",
        "h1": ["Project Management"], "h1_count": 1, "h2_count": 4,
        "word_count": 800, "img_count": 5, "img_missing_alt": 0,
        "ld_types": ["Course"], "hreflang_count": 2, "og_present": True,
        "og_image": True, "twitter_card": True, "meta_keywords": False,
        "title_count": 1, "canonical_count": 1, "desc_count": 1,
        "heading_seq": [1, 2, 2, 3], "is_https": True, "mixed_content": 0,
        "size_kb": 120,
        "internal_links": [], "external_links": [], "response_ms": 300,
    }
    base.update(over)
    if "title" in over and "title_len" not in over:
        base["title_len"] = len(over["title"])
    if "meta_desc" in over and "meta_desc_len" not in over:
        base["meta_desc_len"] = len(over["meta_desc"])
    return base


def _audit_one(page, status_map=None):
    status_map = status_map or {page["url"]: page["status"]}
    return auditor.audit_page(page, status_map, {page.get("title"): 1},
                              {page.get("meta_desc"): 1}, {})


def _checks(issues):
    return {i["check"] for i in issues}


def test_clean_page_has_no_issues():
    assert _audit_one(_page()) == []


def test_missing_title_is_critical():
    issues = _audit_one(_page(title="", title_len=0))
    assert "Missing title" in _checks(issues)
    assert any(i["severity"] == "Critical" for i in issues if i["check"] == "Missing title")


def test_404_flagged_critical():
    issues = _audit_one(_page(status=404), {"https://x.com/in/a/": 404})
    assert "Broken page (404)" in _checks(issues)


def test_noindex_detected():
    issues = _audit_one(_page(meta_robots="noindex, follow"))
    assert "Noindex tag" in _checks(issues)


def test_missing_h1():
    issues = _audit_one(_page(h1=[], h1_count=0))
    assert "Missing H1" in _checks(issues)


def test_thin_content():
    issues = _audit_one(_page(word_count=120))
    assert "Thin content" in _checks(issues)


def test_missing_meta_description():
    issues = _audit_one(_page(meta_desc="", meta_desc_len=0))
    assert "Missing meta description" in _checks(issues)


def test_images_missing_alt():
    issues = _audit_one(_page(img_missing_alt=3))
    assert "Images missing ALT" in _checks(issues)


def test_title_too_long():
    long_title = "This is an extremely long title that clearly exceeds the recommended sixty character maximum limit"
    issues = _audit_one(_page(title=long_title))
    assert "Title too long" in _checks(issues)


def test_duplicate_title_detection():
    pages = [_page(url="https://x.com/in/a/"), _page(url="https://x.com/in/b/")]
    all_issues, page_index = auditor.audit_all(pages)
    assert any(i["check"] == "Duplicate title" for i in all_issues)


def test_canonical_mismatch():
    issues = _audit_one(_page(canonical="https://x.com/in/other/"))
    assert "Canonical points elsewhere" in _checks(issues)


def test_multiple_title_tags():
    issues = _audit_one(_page(title_count=2))
    assert "Multiple title tags" in _checks(issues)


def test_not_https():
    issues = _audit_one(_page(is_https=False))
    assert "Not served over HTTPS" in _checks(issues)


def test_mixed_content():
    issues = _audit_one(_page(mixed_content=4))
    assert "Mixed content" in _checks(issues)


def test_missing_hreflang():
    issues = _audit_one(_page(hreflang_count=0))
    assert "Missing hreflang" in _checks(issues)


def test_heading_skip():
    issues = _audit_one(_page(heading_seq=[1, 2, 4]))
    assert "Heading hierarchy skips" in _checks(issues)


def test_slow_response():
    issues = _audit_one(_page(response_ms=3500))
    assert "Very slow response" in _checks(issues)


def test_url_format_underscore():
    issues = _audit_one(_page(url="https://x.com/in/bad_page/"))
    assert "URL format" in _checks(issues)


def test_legacy_meta_keywords():
    issues = _audit_one(_page(meta_keywords=True))
    assert "Legacy meta keywords" in _checks(issues)


def test_broken_internal_link():
    page = _page(internal_links=["https://x.com/in/dead/"])
    status_map = {page["url"]: 200, "https://x.com/in/dead/": 404}
    issues = auditor.audit_page(page, status_map, {}, {}, {})
    assert "Broken internal link(s)" in _checks(issues)


def test_summarize_counts():
    pages = [_page(url="https://x.com/in/a/"),
             _page(url="https://x.com/in/b/", title="", title_len=0)]
    all_issues, page_index = auditor.audit_all(pages)
    summary = auditor.summarize(page_index, all_issues)
    assert summary["pages_crawled"] == 2
    assert summary["total_issues"] >= 1
    assert summary["by_severity"]["Critical"] >= 1


def test_report_builds_workbook():
    pages = [_page(url="https://x.com/in/a/"),
             _page(url="https://x.com/in/b/", status=404)]
    all_issues, page_index = auditor.audit_all(pages)
    summary = auditor.summarize(page_index, all_issues)
    bio = report.build_workbook(summary, page_index, all_issues,
                                {"start_url": "https://x.com/in/", "date": "2026-07-23"})
    data = bio.getvalue()
    assert data[:2] == b"PK"      # valid xlsx (zip) signature
    assert len(data) > 2000


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} tests passed")
    sys.exit(0 if passed == len(fns) else 1)
