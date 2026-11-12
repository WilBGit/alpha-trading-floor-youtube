#!/usr/bin/env python3
"""Reauthorize Alpha Trading Floor YouTube OAuth with analytics scopes."""
import json
import shutil
from datetime import datetime
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

HOME = Path.home()
ENV_PATH = HOME / "money-machines" / ".env"
TOKEN_PATH = HOME / "money-machines" / "shared" / ".google_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def read_env(path: Path) -> dict:
    values = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


env = read_env(ENV_PATH)
client_id = env.get("GOOGLE_CLIENT_ID")
client_secret = env.get("GOOGLE_CLIENT_SECRET")
if not client_id or not client_secret:
    raise RuntimeError("GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET is missing")

client_config = {
    "installed": {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

if TOKEN_PATH.exists():
    backup = TOKEN_PATH.with_name(
        f".google_token.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    shutil.copy2(TOKEN_PATH, backup)
    print(f"Existing token backed up to: {backup}", flush=True)

print("Opening Google authorization in your browser...", flush=True)
print("Requested scopes:", flush=True)
for scope in SCOPES:
    print(f"  - {scope}", flush=True)

flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
credentials = flow.run_local_server(
    host="localhost",
    port=0,
    open_browser=True,
    authorization_prompt_message="Authorize Alpha Trading Floor in the browser window.",
    success_message="Authorization complete. You may close this browser tab.",
    access_type="offline",
    prompt="consent",
    include_granted_scopes="true",
)

TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
TOKEN_PATH.write_text(credentials.to_json())

saved = json.loads(TOKEN_PATH.read_text())
print("AUTHORIZATION_COMPLETE", flush=True)
print("Saved scopes:", flush=True)
for scope in saved.get("scopes", []):
    print(f"  - {scope}", flush=True)
print(f"Token saved to: {TOKEN_PATH}", flush=True)
