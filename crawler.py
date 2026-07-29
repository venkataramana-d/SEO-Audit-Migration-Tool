"""
Crawler — discovers URLs under a target path and extracts on-page SEO data.

Politely crawls same-domain pages within a path prefix (e.g. /in/), respects
robots.txt, and returns a structured record per page for the auditor.
"""

import re
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser
from urllib.request import urlopen

import requests
from requests.adapters import HTTPAdapter
import lxml.html

USER_AGENT = "Mozilla/5.0 (compatible; SEO-Audit-Tool/1.0; pre-migration audit)"
HEADERS = {"User-Agent": USER_AGENT}
TIMEOUT = 12          # fail slow pages faster instead of blocking a worker for 20s


def normalize(url):
    """Drop fragments and trailing whitespace; keep query strings."""
    url, _ = urldefrag(url.strip())
    return url


def same_scope(url, domain, prefix):
    """True if url is on the same domain and inside the path prefix."""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False
    if p.netloc.lower().replace("www.", "") != domain.lower().replace("www.", ""):
        return False
    return p.path.startswith(prefix)


def extract_page(url, resp):
    """Pull every SEO-relevant element from a fetched page into a dict.
    Uses raw lxml (much faster than BeautifulSoup for the parse + traversal)."""
    try:
        doc = lxml.html.fromstring(resp.text)
    except Exception:
        return {"url": url, "final_url": str(resp.url), "status": resp.status_code,
                "content_type": resp.headers.get("Content-Type", ""),
                "error": "HTML parse failed"}

    self_host = urlparse(url).netloc.lower().replace("www.", "")

    # title (+ duplicate count)
    titles = doc.xpath("//title")
    title = titles[0].text_content().strip() if titles else ""
    title_count = len(titles)

    # meta tags — single pass
    meta_desc = robots = ""
    desc_count = 0
    viewport = twitter_card = meta_keywords = False
    og = {}
    for m in doc.xpath("//meta"):
        name = (m.get("name") or "").strip().lower()
        content = (m.get("content") or "").strip()
        if name == "description":
            if not meta_desc:
                meta_desc = content
            desc_count += 1
        elif name == "robots":
            robots = content.lower()
        elif name == "viewport":
            viewport = True
        elif name == "twitter:card":
            twitter_card = True
        elif name == "keywords":
            meta_keywords = True
        prop = (m.get("property") or "").strip().lower()
        if prop.startswith("og:"):
            og[prop] = content

    # link tags — canonical (+ count) and hreflang
    canonical = ""
    canonical_count = 0
    hreflangs = []
    for l in doc.xpath("//link"):
        rel = (l.get("rel") or "").lower()
        if "canonical" in rel:
            if not canonical:
                canonical = (l.get("href") or "").strip()
            canonical_count += 1
        if "alternate" in rel and l.get("hreflang"):
            hreflangs.append((l.get("hreflang"), l.get("href") or ""))

    htmls = doc.xpath("//html")
    lang = (htmls[0].get("lang") or "").strip() if htmls else ""

    # headings in document order
    h1s, h2s, heading_seq = [], [], []
    for h in doc.xpath("//h1|//h2|//h3|//h4|//h5|//h6"):
        lvl = int(h.tag[1])
        heading_seq.append(lvl)
        if lvl == 1:
            h1s.append(h.text_content().strip())
        elif lvl == 2:
            h2s.append(h.text_content().strip())

    imgs = doc.xpath("//img")
    imgs_missing_alt = [i for i in imgs if not (i.get("alt") or "").strip()]

    # structured data (JSON-LD types) — before we strip scripts for word count
    ld_types = []
    for s in doc.xpath("//script"):
        if "ld+json" in (s.get("type") or "").lower():
            ld_types += re.findall(r'"@type"\s*:\s*"([^"]+)"', s.text_content() or "")

    # HTTPS + mixed content
    is_https = url.lower().startswith("https")
    mixed_content = 0
    if is_https:
        for el in doc.xpath("//img[@src]|//script[@src]|//link[@href]"):
            v = (el.get("src") or el.get("href") or "").strip().lower()
            if v.startswith("http://"):
                mixed_content += 1

    # links — capture rel/target for full link analysis
    links = []
    internal, external = [], []
    for a in doc.xpath("//a[@href]"):
        href = normalize(urljoin(url, a.get("href")))
        if not href.startswith("http"):
            continue
        rel_str = (a.get("rel") or "").lower()
        target = (a.get("target") or "").lower()
        host = urlparse(href).netloc.lower().replace("www.", "")
        is_internal = host == self_host
        links.append({
            "href": href,
            "anchor": a.text_content().strip()[:80],
            "internal": is_internal,
            "nofollow": "nofollow" in rel_str,
            "sponsored": "sponsored" in rel_str,
            "ugc": "ugc" in rel_str,
            "target_blank": target == "_blank",
            "noopener": ("noopener" in rel_str or "noreferrer" in rel_str),
        })
        (internal if is_internal else external).append(href)

    # word count — drop non-content nodes then count words in visible text
    for bad in doc.xpath("//script|//style|//noscript|//template"):
        bad.drop_tree()
    word_count = len(re.findall(r"\w+", doc.text_content() or ""))

    link_summary = {
        "total": len(links),
        "internal": len(set(internal)),
        "external": len(set(external)),
        "internal_nofollow": sum(1 for l in links if l["internal"] and l["nofollow"]),
        "external_dofollow": sum(1 for l in links if not l["internal"] and not l["nofollow"]),
        "blank_unsafe": sum(1 for l in links if l["target_blank"] and not l["noopener"]),
    }

    return {
        "url": url,
        "final_url": str(resp.url),
        "status": resp.status_code,
        "redirected": normalize(str(resp.url)) != normalize(url),
        "content_type": resp.headers.get("Content-Type", ""),
        "size_kb": round(len(resp.content) / 1024, 1),
        "response_ms": int(resp.elapsed.total_seconds() * 1000),
        "title": title,
        "title_len": len(title),
        "meta_desc": meta_desc,
        "meta_desc_len": len(meta_desc),
        "meta_robots": robots,
        "canonical": canonical,
        "has_viewport": viewport,
        "html_lang": lang,
        "h1": h1s,
        "h1_count": len(h1s),
        "h2_count": len(h2s),
        "word_count": word_count,
        "img_count": len(imgs),
        "img_missing_alt": len(imgs_missing_alt),
        "ld_types": sorted(set(ld_types)),
        "has_schema": bool(ld_types),
        "hreflang_count": len(hreflangs),
        "og_present": bool(og.get("og:title")),
        "og_image": bool(og.get("og:image")),
        "twitter_card": twitter_card,
        "meta_keywords": meta_keywords,
        "title_count": title_count,
        "canonical_count": canonical_count,
        "desc_count": desc_count,
        "heading_seq": heading_seq,
        "is_https": is_https,
        "mixed_content": mixed_content,
        "internal_links": sorted(set(internal)),
        "external_links": sorted(set(external)),
        "links": links,
        "link_summary": link_summary,
    }


def collect_sitemap_urls(sitemap_url, max_urls=15000):
    """Fetch a sitemap (or sitemap index) and return all page URLs it declares.
    Recurses into child sitemaps and fetches each level in parallel."""
    session = requests.Session()
    session.headers.update(HEADERS)
    adapter = HTTPAdapter(pool_connections=48, pool_maxsize=48)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    def fetch(u):
        try:
            r = session.get(u.strip(), timeout=TIMEOUT)
            if r.status_code != 200 or not r.text:
                return [], []
            locs = [l.strip() for l in re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text, re.I)]
            if "<sitemapindex" in r.text.lower():
                return locs, []
            return [], [normalize(l) for l in locs]
        except Exception:
            return [], []

    to_visit = [sitemap_url.strip()]
    seen_sm = set(to_visit)
    urls = set()
    depth = 0
    while to_visit and depth < 6 and len(urls) < max_urls:
        with ThreadPoolExecutor(max_workers=min(32, len(to_visit))) as ex:
            results = list(ex.map(fetch, to_visit))
        nxt = []
        for children, pages in results:
            urls.update(pages)
            for c in children:
                if c not in seen_sm:
                    seen_sm.add(c)
                    nxt.append(c)
        to_visit = nxt
        depth += 1
    return sorted(urls)[:max_urls]


class Crawler:
    def __init__(self, start_url, max_pages=1000, workers=16, delay=0,
                 progress_cb=None, stop_flag=None, url_list=None, follow_links=True):
        self.start_url = normalize(start_url)
        p = urlparse(self.start_url)
        self.domain = p.netloc
        self.prefix = p.path if p.path else "/"
        # url_list mode: audit exactly these URLs (from a sitemap / CSV / paste),
        # no link-following, no sitemap discovery
        self.url_list = [normalize(u) for u in url_list] if url_list else None
        self.follow_links = follow_links and not self.url_list
        self.max_pages = max_pages
        self.workers = workers
        self.delay = delay
        self.progress_cb = progress_cb or (lambda **k: None)
        self.stop_flag = stop_flag or threading.Event()

        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        # raise the per-host connection pool above the default 10 so concurrent
        # workers aren't throttled to 10 simultaneous requests
        _adapter = HTTPAdapter(pool_connections=64, pool_maxsize=64)
        self.session.mount("https://", _adapter)
        self.session.mount("http://", _adapter)
        self.robots = self._load_robots()

        self.seen = set()
        self.results = []
        self.lock = threading.Lock()
        self.sitemap_total = 0        # all URLs found across sitemaps
        self.sitemap_scope = 0        # sitemap URLs within the crawl scope

    def _load_robots(self):
        # Fetch robots.txt with our real headers (many CDNs 403 the default
        # urllib UA, which would make RobotFileParser disallow everything),
        # then hand the parsed lines to RobotFileParser.
        rp = RobotFileParser()
        robots_url = f"{urlparse(self.start_url).scheme}://{self.domain}/robots.txt"
        try:
            r = self.session.get(robots_url, timeout=TIMEOUT)
            if r.status_code == 200 and r.text.strip():
                rp.parse(r.text.splitlines())
            else:
                rp.allow_all = True   # no usable robots.txt -> allow
        except Exception:
            rp.allow_all = True
        return rp

    def _allowed(self, url):
        try:
            return self.robots.can_fetch(USER_AGENT, url)
        except Exception:
            return True

    def _fetch(self, url):
        # Retry on transient connection errors/timeouts. Under high concurrency a
        # Cloudflare/WAF-protected host drops a fraction of connections; a gentle
        # backoff recovers them instead of falsely reporting the live page as
        # unreachable (status 0). Matches the retry policy in redirects.py.
        last_err = None
        for attempt in range(3):
            try:
                resp = self.session.get(url, timeout=TIMEOUT, allow_redirects=True)
                ctype = resp.headers.get("Content-Type", "")
                if "html" not in ctype.lower():
                    return {"url": url, "final_url": str(resp.url), "status": resp.status_code,
                            "content_type": ctype, "non_html": True}
                return extract_page(url, resp)
            except requests.RequestException as e:
                last_err = e
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        return {"url": url, "status": 0, "error": str(last_err)[:200]}

    def _fetch_sitemap(self, sm_url):
        """Fetch one sitemap; return (child_sitemap_urls, page_urls)."""
        try:
            r = self.session.get(sm_url.strip(), timeout=TIMEOUT)
            if r.status_code != 200 or not r.text:
                return [], []
            locs = [l.strip() for l in re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text, re.I)]
            if "<sitemapindex" in r.text.lower():
                return locs, []
            return [], [normalize(l) for l in locs]
        except Exception:
            return [], []

    def _seed_from_sitemap(self):
        """Discover sitemap URLs (from robots.txt + common paths) and return the
        in-scope ones. Fetches each level of the sitemap tree in parallel for speed."""
        base = f"{urlparse(self.start_url).scheme}://{self.domain}"
        candidates = []
        try:
            r = self.session.get(f"{base}/robots.txt", timeout=TIMEOUT)
            candidates += re.findall(r"(?i)sitemap:\s*(\S+)", r.text)
        except Exception:
            pass
        candidates += [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml",
                       f"{base}/global-sitemap.xml"]

        to_visit = list(dict.fromkeys(c.strip() for c in candidates))
        seen_sm = set(to_visit)
        all_urls = set()
        depth = 0
        deadline = time.time() + 20        # don't let sitemap discovery dominate
        while to_visit and depth < 5 and time.time() < deadline:
            # fetch a whole level of sitemaps at once (indexes can have 100+ children)
            with ThreadPoolExecutor(max_workers=min(32, len(to_visit))) as ex:
                results = list(ex.map(self._fetch_sitemap, to_visit))
            next_visit = []
            for children, urls in results:
                all_urls.update(urls)
                for c in children:
                    if c not in seen_sm:
                        seen_sm.add(c)
                        next_visit.append(c)
            to_visit = next_visit
            depth += 1
            # update counts incrementally so a partial result is still usable
            self.sitemap_total = len(all_urls)
            self.sitemap_scope = sum(
                1 for u in all_urls if same_scope(u, self.domain, self.prefix))

        scoped = [u for u in all_urls if same_scope(u, self.domain, self.prefix)]
        self.sitemap_total = len(all_urls)
        self.sitemap_scope = len(scoped)
        return scoped

    def run(self):
        qlock = threading.Lock()
        if self.url_list:
            # audit exactly the provided URLs (sitemap / CSV / paste) — no crawling
            queue = deque(self.url_list)
            self.seen.update(self.url_list)
        else:
            # crawl mode: start from the URL and follow in-scope links (no sitemap
            # fetch — that kept the crawl waiting; sitemap is now a separate input mode)
            queue = deque([self.start_url])
            self.seen.add(self.start_url)

        self.progress_cb(phase="crawling", crawled=0, found=len(self.seen))

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = {}
            while len(self.results) < self.max_pages:
                if self.stop_flag.is_set():
                    break
                with qlock:
                    while queue and len(futures) < self.workers and \
                            len(self.results) + len(futures) < self.max_pages:
                        url = queue.popleft()
                        if not self._allowed(url):
                            continue
                        futures[ex.submit(self._fetch, url)] = url
                if self.delay:
                    time.sleep(self.delay)

                if not futures:
                    break

                done_future = next(as_completed(list(futures)))
                url = futures.pop(done_future)
                data = done_future.result()

                with self.lock:
                    self.results.append(data)

                if self.follow_links:
                    for link in data.get("internal_links", []):
                        if same_scope(link, self.domain, self.prefix):
                            with qlock:
                                if link not in self.seen:
                                    self.seen.add(link)
                                    queue.append(link)

                self.progress_cb(
                    phase="crawling",
                    crawled=len(self.results),
                    found=len(self.seen),
                    current=url,
                )

        # build a status map for broken-link detection
        status_map = {normalize(r["url"]): r.get("status", 0) for r in self.results}
        status_map.update({normalize(r.get("final_url", "")): r.get("status", 0)
                           for r in self.results if r.get("final_url")})
        return self.results, status_map
