"""
One-time Spotify authorization helper.

Add this redirect URI to your Spotify app before running:
    http://127.0.0.1:8888/callback
"""

import getpass
import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = [
    "user-top-read",
    "user-read-recently-played",
    "user-read-currently-playing",
    "user-library-read",
    "user-follow-read",
    "playlist-read-private",
    "playlist-read-collaborative",
]
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"


class CallbackHandler(BaseHTTPRequestHandler):
    result = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        CallbackHandler.result = {
            "code": params.get("code", [None])[0],
            "state": params.get("state", [None])[0],
            "error": params.get("error", [None])[0],
        }

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<h2>Spotify authorization received.</h2>"
            b"<p>You can close this tab and return to the terminal.</p>"
        )

    def log_message(self, format, *args):
        pass


def main():
    client_id = input("Spotify Client ID: ").strip()
    client_secret = getpass.getpass("Spotify Client Secret: ").strip()

    state = secrets.token_urlsafe(24)

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": state,
        "show_dialog": "true",
    }

    auth_url = AUTH_URL + "?" + urllib.parse.urlencode(params)
    server = HTTPServer(("127.0.0.1", 8888), CallbackHandler)

    print("Opening Spotify authorization in your browser...")
    print(auth_url)
    webbrowser.open(auth_url)

    while CallbackHandler.result is None:
        server.handle_request()
    server.server_close()

    result = CallbackHandler.result

    if result["error"]:
        raise RuntimeError(f"Spotify authorization failed: {result['error']}")
    if result["state"] != state:
        raise RuntimeError("OAuth state mismatch")
    if not result["code"]:
        raise RuntimeError("No authorization code returned")

    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": REDIRECT_URI,
        },
        auth=(client_id, client_secret),
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("No refresh token returned")

    print()
    print("Add these as GitHub repository secrets:")
    print()
    print("SPOTIFY_CLIENT_ID =", client_id)
    print("SPOTIFY_CLIENT_SECRET =", client_secret)
    print("SPOTIFY_REFRESH_TOKEN =", refresh_token)
    print()
    print("Do not commit the secret or refresh token to Git.")


if __name__ == "__main__":
    main()
