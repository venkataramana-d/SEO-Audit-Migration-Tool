# SEO Pre-Migration Audit Tool

A local web app that crawls the **current** invensislearning.com site, audits every
page for SEO issues, and exports a color-coded Excel report — so nothing breaks when
the new design/content is migrated.

## Run it

**Easiest:** double-click **`run.bat`**, then open <http://127.0.0.1:5000> in your browser.

**Or from a terminal:**
```
cd "G:\SEO Audit & Migration\seo-audit-tool"
python app.py
```
Then open <http://127.0.0.1:5000>.

## How to use

1. **Start URL** — defaults to `https://www.invensislearning.com/in/`. Only pages under
   this path are crawled.
2. **Max pages** — start with 200 to test; raise up to 4000 for a full run.
3. **Include GSC** (optional) — tick to pull Search Console data. Matching pages get a
   gold **★ TOP** flag showing their rank and clicks, so you can see at a glance which
   URLs must keep their rankings through migration. Authorize once with `python gsc_auth.py`.
4. Click **Run Audit** and watch live progress.

## Google Search Console flagging

- Run `python gsc_auth.py` once to authorize (creates `../token.json`).
- With **Include GSC** ticked, each crawled URL that has search traffic gets a gold
  **★ TOP #rank · N clicks** badge in the list, and the sort switches to "most GSC clicks".
- Expanding a top page shows a **Priority page** box with clicks, impressions, CTR,
  average position, and rank.
- Filter **"Only top pages (GSC)"** + a severity filter = your must-fix-before-migration list.
- **Note:** after editing any `.py` file, restart the server (Python isn't auto-reloaded;
  templates are). Just re-run `run.bat`.

## Pages

Light-themed, with a top nav across four pages:

- **Site Audit** (`/`) — crawl + audit + results explorer. Each URL row is a **link to its
  own technical-audit page** (`/page?job=…&url=…`).
- **Per-URL detail** (`/page`) — full technical audit for one URL: status, all on-page SEO
  fields, every issue (severity, location, fix, why), the GSC priority box, and a live
  **PageSpeed Insights performance test** (mobile/desktop).
- **Migration Checklist** (`/checklist`) — a 7-phase, 30-item SEO migration checklist with
  Critical/High/Standard tags plus Why + How. Progress **saves automatically in the browser**.
- **Settings** (`/settings`) — GSC connection status and a **PageSpeed Insights API key**
  field (stored in `settings.json`).

## Results explorer

When the crawl finishes, every URL appears in an ordered, filterable list:

- **Sort** by most issues (default), URL A–Z, or status code.
- **Filter** by verdict (all / only issues / only clear), by severity, or free-text search.
- Each row shows the **status code**, a **CLEAR** or **N issues** verdict, and the URL.
- **Click any URL to expand** its full SEO audit: page stats (title, words, H1s, links,
  response time) plus, for each issue, the **severity, exact location, how to fix, and
  why it matters**. Clean pages show a "✓ No SEO issues found" message.

## Excel export (available in the backend)

The Excel report builder is still wired up (`/api/download/<job_id>`) and produces a
Summary, All Pages, Issues, and Redirect Map sheet. The download button is hidden in the
UI for now — say the word to re-enable it.

## Checks performed (40+ rules)

**Indexing/status:** 404/5xx errors, redirects, noindex.
**Titles/meta:** missing/duplicate/too-long/short titles, multiple title tags,
missing/duplicate meta descriptions, multiple description tags.
**Content:** missing/multiple H1, heading-hierarchy skips, thin content, images missing ALT.
**Canonical:** missing canonical, canonical points elsewhere, multiple canonical tags.
**Security:** not served over HTTPS, mixed content.
**International:** missing hreflang, missing html lang attribute.
**Social:** incomplete Open Graph, missing Twitter card.
**Performance proxies:** slow/very-slow response, oversized page, excessive internal links.
**Hygiene:** no viewport (mobile), missing structured data, legacy meta keywords,
messy URL format (uppercase/underscores/spaces/too-long), broken internal links.
**Live performance:** Core Web Vitals via PageSpeed Insights on each URL's detail page.

## Files

- `app.py` — Flask web app (UI + job management + download)
- `crawler.py` — URL discovery + on-page SEO extraction
- `auditor.py` — the SEO rules engine
- `report.py` — Excel report builder
- `gsc.py` — optional Search Console integration
- `templates/index.html` — the UI
- `tests/test_auditor.py` — test suite (`python tests/test_auditor.py`)

## Notes

- Runs entirely on your machine. The only outbound requests go to the site being audited
  (and to Google if GSC is enabled).
- Reads `oauth_client.json` / `token.json` from the parent folder for GSC.
