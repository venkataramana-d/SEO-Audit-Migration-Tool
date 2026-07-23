"""
SEO Pre-Migration Audit Tool — local web app.

Run:  python app.py
Open: http://127.0.0.1:5000

Configure the target path, page limit, and options in the UI, run the audit,
watch live progress, then download the color-coded Excel report.
"""

import threading
import uuid
from datetime import datetime

from flask import Flask, render_template, request, jsonify, send_file, abort

import json
import os

import crawler
import auditor
import report
import gsc
import perf

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# in-memory job store: job_id -> state dict
JOBS = {}
CLIENT_FILE = "../oauth_client.json"   # created earlier in the parent folder
TOKEN_FILE = "../token.json"
SETTINGS_FILE = "settings.json"


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_settings(data):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def run_job(job_id, params):
    job = JOBS[job_id]
    try:
        stop = job["stop_flag"]

        def progress(**kw):
            job.update(kw)

        job["phase"] = "crawling"
        c = crawler.Crawler(
            start_url=params["start_url"],
            max_pages=params["max_pages"],
            workers=params["workers"],
            progress_cb=progress,
            stop_flag=stop,
        )
        pages, status_map = c.run()
        if stop.is_set():
            job["phase"] = "stopped"
            job["done"] = True
            return

        job["phase"] = "auditing"
        all_issues, page_index = auditor.audit_all(pages)
        summary = auditor.summarize(page_index, all_issues)
        summary["total_found"] = len(c.seen)          # all in-scope URLs discovered
        summary["sitemap_total"] = c.sitemap_total    # URLs declared across all sitemaps
        summary["sitemap_scope"] = c.sitemap_scope    # sitemap URLs within crawl scope

        # group issues by URL for the interactive explorer
        from collections import defaultdict
        issues_by_url = defaultdict(list)
        for i in all_issues:
            issues_by_url[i["url"]].append(i)
        job["page_index"] = page_index
        job["issues_by_url"] = issues_by_url
        job["pages_by_url"] = {p.get("url"): p for p in pages}

        def _to_map(rows):
            m = {}
            for rank, r in enumerate(sorted(rows, key=lambda x: x[1], reverse=True), 1):
                m[r[0].rstrip("/")] = {
                    "clicks": r[1], "impressions": r[2],
                    "ctr": r[3], "position": r[4], "rank": rank,
                }
            return m

        gsc_map_90, gsc_map_365 = {}, {}
        if params.get("include_gsc"):
            job["phase"] = "gsc"
            try:
                for days, target in ((90, "90"), (365, "365")):
                    rows = gsc.top_pages(
                        site_url=params["gsc_property"],
                        client_file=CLIENT_FILE,
                        token_file=TOKEN_FILE,
                        days=days,
                        path_filter=params.get("path_filter"),
                    )
                    if target == "90":
                        gsc_map_90 = _to_map(rows)
                    else:
                        gsc_map_365 = _to_map(rows)
            except Exception as e:
                job["gsc_error"] = str(e)[:300]
        job["gsc_map"] = gsc_map_90          # 3-month is the default (used by Excel too)
        job["gsc_map_90"] = gsc_map_90
        job["gsc_map_365"] = gsc_map_365

        job["phase"] = "report"
        meta = {"start_url": params["start_url"],
                "date": datetime.now().strftime("%Y-%m-%d %H:%M")}
        # rebuild rows (page, clicks, impressions, ctr, position) from the 3-month map
        gsc_rows = [[u, v["clicks"], v["impressions"], v["ctr"], v["position"]]
                    for u, v in sorted(gsc_map_90.items(),
                                       key=lambda x: -x[1]["clicks"])] or None
        bio = report.build_workbook(summary, page_index, all_issues, meta, gsc_rows)

        job["report_bytes"] = bio.getvalue()
        job["summary"] = summary
        job["phase"] = "complete"
        job["done"] = True
    except Exception as e:
        import traceback
        job["error"] = str(e)
        job["traceback"] = traceback.format_exc()
        job["phase"] = "error"
        job["done"] = True


@app.route("/")
def index():
    return render_template("index.html", gsc_available=gsc.available())


@app.route("/checklist")
def checklist():
    return render_template("checklist.html")


@app.route("/settings", methods=["GET", "POST"])
def settings():
    s = load_settings()
    saved = False
    if request.method == "POST":
        s["pagespeed_api_key"] = (request.form.get("pagespeed_api_key") or "").strip()
        save_settings(s)
        saved = True
    gsc_connected = os.path.exists(TOKEN_FILE)
    return render_template("settings.html", settings=s, saved=saved,
                           gsc_connected=gsc_connected,
                           client_exists=os.path.exists(CLIENT_FILE))


@app.route("/page")
def page_detail():
    job_id = request.args.get("job", "")
    url = request.args.get("url", "")
    job = JOBS.get(job_id)
    if not job or not job.get("pages_by_url"):
        return render_template("detail.html", not_found=True, url=url), 404
    page = job["pages_by_url"].get(url)
    if not page:
        return render_template("detail.html", not_found=True, url=url), 404
    issues = job.get("issues_by_url", {}).get(url, [])
    sev_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    issues = sorted(issues, key=lambda i: sev_rank.get(i["severity"], 9))
    gsc_stats = job.get("gsc_map", {}).get(url.rstrip("/"))
    return render_template("detail.html", not_found=False, job_id=job_id,
                           page=page, issues=issues, gsc=gsc_stats,
                           canon_status=auditor.canonical_status(page),
                           has_perf_key=bool(load_settings().get("pagespeed_api_key")))


@app.route("/api/perf")
def api_perf():
    url = request.args.get("url", "")
    strategy = request.args.get("strategy", "mobile")
    if not url.startswith("http"):
        return jsonify({"error": "invalid url"}), 400
    key = load_settings().get("pagespeed_api_key", "")
    return jsonify(perf.pagespeed(url, api_key=key, strategy=strategy))


@app.route("/api/start", methods=["POST"])
def start():
    data = request.get_json(force=True)
    start_url = (data.get("start_url") or "").strip()
    if not start_url.startswith("http"):
        return jsonify({"error": "Enter a valid URL starting with http/https."}), 400

    try:
        max_pages = max(1, min(int(data.get("max_pages", 500)), 15000))
    except (TypeError, ValueError):
        max_pages = 500

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "phase": "starting", "crawled": 0, "found": 0, "current": "",
        "done": False, "error": None, "stop_flag": threading.Event(),
    }
    # derive GSC property (origin) and path filter from the URL entered, so GSC
    # data matches whatever site/section the user crawls (not a hardcoded /in/).
    from urllib.parse import urlparse
    pu = urlparse(start_url)
    origin = f"{pu.scheme}://{pu.netloc}/"
    scope_path = pu.path if pu.path and pu.path != "/" else None
    params = {
        "start_url": start_url,
        "max_pages": max_pages,
        "workers": int(data.get("workers", 8)),
        "include_gsc": bool(data.get("include_gsc")),
        "gsc_property": data.get("gsc_property") or origin,
        "path_filter": data.get("path_filter") or scope_path,
    }
    t = threading.Thread(target=run_job, args=(job_id, params), daemon=True)
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/api/progress/<job_id>")
def progress(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify({
        "phase": job.get("phase"),
        "crawled": job.get("crawled", 0),
        "found": job.get("found", 0),
        "current": job.get("current", ""),
        "done": job.get("done", False),
        "error": job.get("error"),
        "gsc_error": job.get("gsc_error"),
        "summary": job.get("summary"),
        "has_report": bool(job.get("report_bytes")),
    })


@app.route("/api/results/<job_id>")
def results(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify({
        "summary": job.get("summary"),
        "pages": job.get("page_index", []),
        "issues_by_url": job.get("issues_by_url", {}),
        "gsc_map": job.get("gsc_map", {}),
        "gsc_map_90": job.get("gsc_map_90", {}),
        "gsc_map_365": job.get("gsc_map_365", {}),
    })


@app.route("/api/stop/<job_id>", methods=["POST"])
def stop(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    job["stop_flag"].set()
    return jsonify({"ok": True})


@app.route("/api/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or not job.get("report_bytes"):
        abort(404)
    from io import BytesIO
    bio = BytesIO(job["report_bytes"])
    bio.seek(0)
    fname = f"SEO-Audit-{datetime.now().strftime('%Y%m%d-%H%M')}.xlsx"
    return send_file(
        bio, as_attachment=True, download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    print("\n  SEO Pre-Migration Audit Tool")
    print("  Open http://127.0.0.1:5000 in your browser\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
