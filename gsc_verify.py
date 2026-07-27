"""
Verify the Google Search Console connection (service account or OAuth) and list
the properties the tool can read.

Run:  python gsc_verify.py
"""
import os
import gsc

BASE = os.path.dirname(os.path.abspath(__file__))
CLIENT_FILE = os.path.join(BASE, "..", "oauth_client.json")
TOKEN_FILE = os.path.join(BASE, "..", "token.json")
SERVICE_ACCOUNT_FILE = os.path.join(BASE, "..", "service_account.json")


def main():
    kind = gsc.connection_kind(CLIENT_FILE, TOKEN_FILE, SERVICE_ACCOUNT_FILE)
    print("Connection method:", kind or "NONE — no credentials found")
    if kind == "service_account":
        print("Key file:", os.path.abspath(SERVICE_ACCOUNT_FILE))
    if not kind:
        print("\nNothing to verify. Add service_account.json to the parent folder,")
        print("or run `python gsc_auth.py` for OAuth.")
        return

    try:
        sites = gsc.list_sites(CLIENT_FILE, TOKEN_FILE, SERVICE_ACCOUNT_FILE)
    except Exception as e:
        print("\nERROR talking to Search Console:", str(e)[:300])
        if kind == "service_account":
            print("\nMost likely the service account's email is not yet added as a")
            print("user in Search Console (Settings -> Users and permissions).")
        return

    if not sites:
        print("\nConnected, but this credential can access NO properties.")
        if kind == "service_account":
            print("Add the service account email as a user on your GSC property.")
        return

    print("\nProperties this credential can read:")
    for url, perm in sites:
        print(f"  {url}  |  {perm}")


if __name__ == "__main__":
    main()
