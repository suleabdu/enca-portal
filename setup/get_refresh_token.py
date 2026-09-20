"""
get_refresh_token.py
---------------------
Run this ONCE, on your own computer — never on Render or Netlify.

It opens your browser, asks you to sign in with the Google account that
owns the training spreadsheet (and should own the Drive folder), and asks
you to approve Sheets + Drive access. It then prints a refresh token.

That refresh token — together with the client ID and client secret — lets
the deployed backend act as your account indefinitely, without you signing
in again, the same way the old Apps Script ran as "you" under
"Execute as: Me".

Setup before running:
  1. In Google Cloud Console, create a project (or reuse one) and enable:
       - Google Sheets API
       - Google Drive API
  2. Configure the OAuth consent screen (External, Testing mode is fine)
     and add the Google account you'll sign in with as a Test User.
  3. Create an OAuth Client ID of type "Desktop app" and download its
     JSON file. Save it next to this script as "client_secret.json".
  4. pip install google-auth-oauthlib
  5. python get_refresh_token.py

Copy the three printed values into Render's environment variables:
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  GOOGLE_REFRESH_TOKEN
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        "setup/client_secret.json", SCOPES)
    # access_type=offline + prompt=consent guarantee a refresh token is
    # actually issued (Google sometimes omits it on repeat authorizations).
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    print("\n" + "=" * 70)
    print("Setup complete. Copy these into Render's environment variables:")
    print("=" * 70)
    print(f"GOOGLE_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 70)

    if not creds.refresh_token:
        print(
            "\nWARNING: no refresh token was returned. This usually means the "
            "account already has a prior authorization for this app. Go to "
            "https://myaccount.google.com/permissions, remove access for this "
            "app, and run this script again."
        )


if __name__ == "__main__":
    main()
