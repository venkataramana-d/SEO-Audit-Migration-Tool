"""
GSC — optional Google Search Console integration.

Pulls top pages (by clicks) for the property so the report can flag which URLs
must keep their rankings through the migration. Uses the OAuth client the user
created; first call opens a browser to authorize, then caches token.json.
"""

import os
from datetime import date, timedelta

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def available():
    """True if the Google libraries and client file are present."""
    try:
        import google_auth_oauthlib  # noqa
        import googleapiclient       # noqa
    except ImportError:
        return False
    return True


def _service(client_file, token_file, sa_file=None):
    """Build a Search Console service.

    Prefers a SERVICE ACCOUNT key file when present (portable, no browser, never
    expires) and falls back to the OAuth desktop flow otherwise.
    """
    from googleapiclient.discovery import build

    # 1) service account (the durable "key file" method)
    if sa_file and os.path.exists(sa_file):
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            sa_file, scopes=SCOPES)
        return build("searchconsole", "v1", credentials=creds)

    # 2) OAuth desktop flow (cached token)
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_file, "w") as f:
            f.write(creds.to_json())
    return build("searchconsole", "v1", credentials=creds)


def connection_kind(client_file, token_file, sa_file=None):
    """Return which credential the tool will use: 'service_account', 'oauth', or None."""
    if sa_file and os.path.exists(sa_file):
        return "service_account"
    if os.path.exists(token_file):
        return "oauth"
    return None


def list_sites(client_file, token_file, sa_file=None):
    """Return [(siteUrl, permissionLevel), ...] the credential can access."""
    svc = _service(client_file, token_file, sa_file)
    sites = svc.sites().list().execute().get("siteEntry", [])
    return [(s.get("siteUrl"), s.get("permissionLevel")) for s in sites]


def top_pages(site_url, client_file, token_file, days=90, limit=1000,
              path_filter=None, sa_file=None):
    """Return [[page, clicks, impressions, ctr%, position], ...] sorted by clicks."""
    svc = _service(client_file, token_file, sa_file)
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=days)
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["page"],
        "rowLimit": limit,
    }
    if path_filter:
        body["dimensionFilterGroups"] = [{
            "filters": [{"dimension": "page", "operator": "contains",
                         "expression": path_filter}]
        }]
    resp = svc.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows = resp.get("rows", [])
    out = [[r["keys"][0], r["clicks"], r["impressions"],
            round(r["ctr"] * 100, 2), round(r["position"], 1)] for r in rows]
    out.sort(key=lambda x: x[1], reverse=True)
    return out
