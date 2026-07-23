"""
One-time Google Search Console authorization.

Run once:  python gsc_auth.py
It opens a browser to sign in, saves ../token.json, then lists the GSC
properties you have access to (so we use the exact property string).
"""
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
CLIENT_FILE = "../oauth_client.json"
TOKEN_FILE = "../token.json"


def main():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, SCOPES)
            print("Opening browser for Google sign-in...", flush=True)
            creds = flow.run_local_server(port=0, open_browser=True,
                                          authorization_prompt_message=
                                          "AUTH URL: {url}")
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print("TOKEN SAVED to", os.path.abspath(TOKEN_FILE), flush=True)

    # confirm which properties this account can access
    svc = build("searchconsole", "v1", credentials=creds)
    sites = svc.sites().list().execute().get("siteEntry", [])
    print("PROPERTIES:", flush=True)
    for s in sites:
        print("  ", s.get("siteUrl"), "|", s.get("permissionLevel"), flush=True)


if __name__ == "__main__":
    main()
