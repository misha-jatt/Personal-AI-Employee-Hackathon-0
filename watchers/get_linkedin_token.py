"""
LinkedIn OAuth 2.0 Token Helper
================================
Run this script, paste your Client ID and Secret when prompted,
then open the URL in your browser and authorize.
The script captures the callback and exchanges the code for an access token.

Prerequisites:
  - Add http://localhost:8585/callback as a redirect URL in your LinkedIn app
  - Request "Share on LinkedIn" and "Sign In with LinkedIn using OpenID Connect" products
"""

import http.server
import urllib.parse
import json
import sys

try:
    import requests
except ImportError:
    print("Installing requests...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

PORT = 8585
REDIRECT_URI = f"http://localhost:{PORT}/callback"
SCOPES = "openid profile w_member_social"

auth_code = None


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family:sans-serif;text-align:center;padding:60px;">
                <h1>Authorization successful!</h1>
                <p>You can close this tab and return to the terminal.</p>
                </body></html>
            """)
        elif "error" in params:
            error = params.get("error", ["unknown"])[0]
            desc = params.get("error_description", ["No description"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"""
                <html><body style="font-family:sans-serif;text-align:center;padding:60px;">
                <h1>Authorization failed</h1>
                <p>Error: {error}</p><p>{desc}</p>
                </body></html>
            """.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress server logs


def main():
    print("=" * 55)
    print("  LinkedIn OAuth 2.0 — Access Token Helper")
    print("=" * 55)
    print()

    client_id = input("Enter your Client ID: ").strip()
    client_secret = input("Enter your Client Secret: ").strip()

    if not client_id or not client_secret:
        print("Error: Both Client ID and Client Secret are required.")
        sys.exit(1)

    auth_url = (
        "https://www.linkedin.com/oauth/v2/authorization?"
        f"response_type=code&client_id={client_id}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&scope={urllib.parse.quote(SCOPES)}"
    )

    print()
    print("Open this URL in your browser and authorize the app:")
    print()
    print(f"  {auth_url}")
    print()

    # Try to auto-open
    try:
        import webbrowser
        webbrowser.open(auth_url)
        print("(Browser should open automatically)")
    except Exception:
        print("(Copy and paste the URL above into your browser)")

    print()
    print(f"Waiting for callback on http://localhost:{PORT}/callback ...")
    print()

    server = http.server.HTTPServer(("localhost", PORT), CallbackHandler)
    server.timeout = 300  # 5 minute timeout

    while auth_code is None:
        server.handle_request()

    server.server_close()

    print("Authorization code received! Exchanging for access token...")
    print()

    resp = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if resp.status_code == 200:
        data = resp.json()
        token = data.get("access_token", "")
        expires_in = data.get("expires_in", "unknown")

        print("=" * 55)
        print("  SUCCESS — Your LinkedIn Access Token")
        print("=" * 55)
        print()
        print(f"  Token: {token[:20]}...{token[-10:]}")
        print(f"  Expires in: {expires_in} seconds (~{int(expires_in)//86400} days)")
        print()

        # Offer to save to .env
        save = input("Save token to watchers/.env? (y/n): ").strip().lower()
        if save == "y":
            env_path = "watchers/.env" if "watchers" not in sys.argv[0] else ".env"
            # Also try the script's directory
            import os
            script_dir = os.path.dirname(os.path.abspath(__file__))
            env_path = os.path.join(script_dir, ".env")

            try:
                with open(env_path, "r") as f:
                    content = f.read()
                new_content = content.replace(
                    "LINKEDIN_ACCESS_TOKEN=your_linkedin_access_token_here",
                    f"LINKEDIN_ACCESS_TOKEN={token}",
                )
                # If placeholder wasn't found, try generic replacement
                if new_content == content:
                    import re
                    new_content = re.sub(
                        r"LINKEDIN_ACCESS_TOKEN=.*",
                        f"LINKEDIN_ACCESS_TOKEN={token}",
                        content,
                    )
                with open(env_path, "w") as f:
                    f.write(new_content)
                print(f"Token saved to {env_path}")
            except FileNotFoundError:
                print(f"Could not find {env_path}. Manually add this to your .env:")
                print(f"  LINKEDIN_ACCESS_TOKEN={token}")
        else:
            print("Add this line to your watchers/.env file:")
            print(f"  LINKEDIN_ACCESS_TOKEN={token}")

        print()
        print("Done! Your LinkedIn integration is ready.")
    else:
        print("ERROR: Token exchange failed.")
        print(f"  Status: {resp.status_code}")
        print(f"  Response: {resp.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
