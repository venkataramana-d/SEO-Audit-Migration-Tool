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
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; SEO-Audit-Tool/1.0; pre-migration audit)"
HEADERS = {"User-Agent": USER_AGENT}
TIMEOUT = 20


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


def _text_len(soup):
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return len(re.findall(r"\b\w+\b", text))


def extract_page(url, resp, soup):
    """Pull every SEO-relevant element from a fetched page into a dict."""
    def attr(sel, name, **kw):
        el = soup.find(sel, **kw)
        return el.get(name, "").strip() if el else ""

    title_el = soup.find("title")
    title = title_el.get_text(strip=True) if title_el else ""

    meta_desc = ""
    md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    if md:
        meta_desc = (md.get("content") or "").strip()

    robots = ""
    mr = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    if mr:
        robots = (mr.get("content") or "").strip().lower()

    canonical = ""
    cl = soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
    if cl:
        canonical = (cl.get("href") or "").strip()

    viewport = bool(soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}))
    lang = (soup.find("html").get("lang", "").strip() if soup.find("html") else "")

    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2s = [h.get_text(strip=True) for h in soup.find_all("h2")]

    imgs = soup.find_all("img")
    imgs_missing_alt = [
        (img.get("src") or "")
        for img in imgs
        if not (img.get("alt") or "").strip()
    ]

    # structured data
    ld_types = []
    for s in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        txt = s.string or ""
        ld_types += re.findall(r'"@type"\s*:\s*"([^"]+)"', txt)

    hreflangs = [
        (l.get("hreflang", ""), l.get("href", ""))
        for l in soup.find_all("link", attrs={"rel": re.compile("alternate", re.I)})
        if l.get("hreflang")
    ]

    og = {
        m.get("property", ""): (m.get("content") or "")
        for m in soup.find_all("meta", attrs={"property": re.compile("^og:", re.I)})
    }
    twitter_card = bool(soup.find("meta", attrs={"name": re.compile("^twitter:card$", re.I)}))
    meta_keywords = bool(soup.find("meta", attrs={"name": re.compile("^keywords$", re.I)}))

    # duplicate-tag detection (multiple titles / canonicals / descriptions)
    title_count = len(soup.find_all("title"))
    canonical_count = len(soup.find_all("link", attrs={"rel": re.compile("canonical", re.I)}))
    desc_count = len(soup.find_all("meta", attrs={"name": re.compile("^description$", re.I)}))

    # heading order (list of levels in document order, e.g. [1,2,2,3])
    heading_seq = [int(h.name[1]) for h in soup.find_all(re.compile("^h[1-6]$"))]

    # HTTPS + mixed content
    is_https = url.lower().startswith("https")
    mixed_content = 0
    if is_https:
        for tag, attr in (("img", "src"), ("script", "src"), ("link", "href")):
            for el in soup.find_all(tag):
                if (el.get(attr) or "").strip().lower().startswith("http://"):
                    mixed_content += 1

    # links — capture rel/target for full link analysis
    links = []
    internal, external = [], []
    base = url
    self_host = urlparse(url).netloc.lower().replace("www.", "")
    for a in soup.find_all("a", href=True):
        href = normalize(urljoin(base, a["href"]))
        if not href.startswith("http"):
            continue
        rel_str = " ".join(a.get("rel") or []).lower()
        target = (a.get("target") or "").lower()
        host = urlparse(href).netloc.lower().replace("www.", "")
        is_internal = host == self_host
        anchor = a.get_text(strip=True)[:80]
        links.append({
            "href": href,
            "anchor": anchor,
            "internal": is_internal,
            "nofollow": "nofollow" in rel_str,
            "sponsored": "sponsored" in rel_str,
            "ugc": "ugc" in rel_str,
            "target_blank": target == "_blank",
            "noopener": ("noopener" in rel_str or "noreferrer" in rel_str),
        })
        (internal if is_internal else external).append(href)

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
        "word_count": _text_len(BeautifulSoup(str(soup), "lxml")),
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


class Crawler:
    def __init__(self, start_url, max_pages=1000, workers=8, delay=0.15,
                 progress_cb=None, stop_flag=None):
        self.start_url = normalize(start_url)
        p = urlparse(self.start_url)
        self.domain = p.netloc
        self.prefix = p.path if p.path else "/"
        self.max_pages = max_pages
        self.workers = workers
        self.delay = delay
        self.progress_cb = progress_cb or (lambda **k: None)
        self.stop_flag = stop_flag or threading.Event()

        self.session = requests.Session()
        self.session.headers.update(HEADERS)
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
        try:
            resp = self.session.get(url, timeout=TIMEOUT, allow_redirects=True)
            ctype = resp.headers.get("Content-Type", "")
            if "html" not in ctype.lower():
                return {"url": url, "final_url": str(resp.url), "status": resp.status_code,
                        "content_type": ctype, "non_html": True}
            soup = BeautifulSoup(resp.text, "lxml")
            return extract_page(url, resp, soup)
        except requests.RequestException as e:
            return {"url": url, "status": 0, "error": str(e)[:200]}

    def _sitemap_locs(self, sm_url, depth=0):
        """Return all page URLs from a sitemap, recursing into sitemap indexes."""
        out = []
        if depth > 3:
            return out
        try:
            r = self.session.get(sm_url, timeout=TIMEOUT)
            if r.status_code != 200 or not r.text:
                return out
            locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text, re.I)
            is_index = "<sitemapindex" in r.text.lower()
            for loc in locs:
                loc = loc.strip()
                if is_index or loc.lower().endswith((".xml", ".xml.gz")):
                    out += self._sitemap_locs(loc, depth + 1)
                else:
                    out.append(normalize(loc))
        except Exception:
            pass
        return out

    def _seed_from_sitemap(self):
        """Discover sitemap URLs (from robots.txt + common paths) and return
        the in-scope ones, while recording total counts."""
        base = f"{urlparse(self.start_url).scheme}://{self.domain}"
        candidates = []
        # sitemaps declared in robots.txt (most reliable)
        try:
            r = self.session.get(f"{base}/robots.txt", timeout=TIMEOUT)
            candidates += re.findall(r"(?i)sitemap:\s*(\S+)", r.text)
        except Exception:
            pass
        # common fallback locations
        candidates += [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml",
                       f"{base}/global-sitemap.xml"]

        all_urls = set()
        for sm in dict.fromkeys(candidates):        # dedup, keep order
            for u in self._sitemap_locs(sm.strip()):
                all_urls.add(u)

        scoped = [u for u in all_urls if same_scope(u, self.domain, self.prefix)]
        self.sitemap_total = len(all_urls)
        self.sitemap_scope = len(scoped)
        return scoped

    def run(self):
        queue = deque()
        queue.append(self.start_url)
        self.seen.add(self.start_url)

        for u in self._seed_from_sitemap():
            if u not in self.seen:
                self.seen.add(u)
                queue.append(u)

        self.progress_cb(phase="crawling", crawled=0, found=len(self.seen))

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = {}
            while (queue or futures) and len(self.results) < self.max_pages:
                if self.stop_flag.is_set():
                    break
                # top up the pool
                while queue and len(futures) < self.workers and \
                        len(self.results) + len(futures) < self.max_pages:
                    url = queue.popleft()
                    if not self._allowed(url):
                        continue
                    futures[ex.submit(self._fetch, url)] = url
                    time.sleep(self.delay)

                if not futures:
                    break

                done_future = next(as_completed(list(futures)))
                url = futures.pop(done_future)
                data = done_future.result()

                with self.lock:
                    self.results.append(data)

                # enqueue new internal links within scope
                for link in data.get("internal_links", []):
                    if link not in self.seen and same_scope(link, self.domain, self.prefix):
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
