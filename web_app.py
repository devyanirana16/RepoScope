"""Run the local RepoScope UI with python3 -B web_app.py."""

import json
import os
import secrets
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError

from github_readme import accessible_repositories, github_json, poll_device_sign_in
from github_readme import search_readme, start_device_sign_in
from recruiter_app import permission_demo


HOST = "127.0.0.1"
PORT = 8765
ORIGIN = f"http://{HOST}:{PORT}"
WEB = Path(__file__).with_name("web")
ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.css": ("app.css", "text/css"),
    "/app.js": ("app.js", "text/javascript"),
}
CLIENT_ID = os.environ.get("GITHUB_APP_CLIENT_ID")
SESSIONS = {}


class Handler(BaseHTTPRequestHandler):
    def send_content(self, status, content, content_type, cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        if cookie is not None:
            self.send_header("Set-Cookie", f"reposcope={cookie}; Path=/; HttpOnly; SameSite=Strict")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, status, data, cookie=None):
        self.send_content(status, json.dumps(data).encode(), "application/json", cookie)

    def session(self):
        for key, state in list(SESSIONS.items()):
            if state["expires"] <= monotonic():
                del SESSIONS[key]
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", "")).get("reposcope")
        except CookieError:
            cookie = None
        session_id = cookie.value if cookie else ""
        return session_id, SESSIONS.get(session_id, {})

    def trusted(self, post=False):
        if self.headers.get("Host") != f"{HOST}:{PORT}" or (
            post and self.headers.get("Origin") != ORIGIN
        ):
            self.send_json(403, {"error": "Open RepoScope from its local address."})
            return False
        return True

    def do_GET(self):
        if not self.trusted():
            return
        if self.path == "/api/session":
            _, state = self.session()
            self.send_json(200, {"login": state.get("login"), "signInAvailable": bool(CLIENT_ID)})
        elif self.path == "/api/demo":
            self.send_json(200, permission_demo())
        elif self.path in ASSETS:
            filename, content_type = ASSETS[self.path]
            self.send_content(200, (WEB / filename).read_bytes(), content_type)
        else:
            self.send_json(404, {"error": "Page unavailable."})

    def do_POST(self):
        if not self.trusted(post=True):
            return
        session_id, state = self.session()
        try:
            if self.path == "/api/search":
                self.search(state)
            elif self.path == "/api/repositories":
                if "token" not in state:
                    raise PermissionError("Sign in to browse your repositories.")
                self.send_json(200, {"repositories": accessible_repositories(state["token"])})
            elif self.path == "/api/sign-in":
                self.begin_sign_in(session_id)
            elif self.path == "/api/sign-in/poll":
                self.poll_sign_in(session_id, state)
            elif self.path == "/api/sign-out":
                SESSIONS.pop(session_id, None)
                self.send_json(200, {}, cookie="")
            else:
                self.send_json(404, {"error": "Page unavailable."})
        except HTTPError as error:
            self.send_json(502, {"error": f"GitHub returned HTTP {error.code}. Check access and try again."})
        except (PermissionError, ValueError) as error:
            self.send_json(400, {"error": str(error)})
        except OSError:
            self.send_json(502, {"error": "GitHub couldn’t be reached. Try again."})

    def search(self, state):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 4096:
            raise ValueError("Enter a repository and a search query.")
        fields = json.loads(self.rfile.read(length))
        if not isinstance(fields, dict):
            raise ValueError("Enter a repository and a search query.")
        repository, query = fields.get("repository"), fields.get("query")
        if not isinstance(repository, str) or not isinstance(query, str) or not query.strip():
            raise ValueError("Enter a repository and a search query.")
        if fields.get("signedIn") and "token" not in state:
            raise PermissionError("Your session expired. Sign in again to search with your account.")
        results = search_readme(repository.strip(), query.strip(), state.get("token"))
        self.send_json(200, {"results": [
            {"citation": citation, "excerpt": excerpt} for citation, excerpt in results
        ]})

    def begin_sign_in(self, previous_session):
        if not CLIENT_ID:
            raise ValueError("Set GITHUB_APP_CLIENT_ID before starting RepoScope.")
        device = start_device_sign_in(CLIENT_ID)
        interval = max(1, device["interval"])
        session_id = secrets.token_urlsafe(32)
        SESSIONS.pop(previous_session, None)
        SESSIONS[session_id] = {
            "device": device["device_code"],
            "interval": interval,
            "next_poll": monotonic() + interval,
            "expires": monotonic() + device["expires_in"],
        }
        self.send_json(200, {"code": device["user_code"], "retryAfter": interval}, cookie=session_id)

    def poll_sign_in(self, session_id, state):
        if "device" not in state:
            raise ValueError("The GitHub code expired. Start sign-in again.")
        interval = state["interval"]
        if monotonic() < state["next_poll"]:
            self.send_json(200, {"retryAfter": interval})
            return
        response = poll_device_sign_in(CLIENT_ID, state["device"])
        if "access_token" in response:
            token = response["access_token"]
            login = github_json("/user", token)["login"]
            new_id = secrets.token_urlsafe(32)
            SESSIONS.pop(session_id, None)
            SESSIONS[new_id] = {
                "login": login, "token": token,
                "expires": monotonic() + min(response.get("expires_in", 3600), 3600),
            }
            self.send_json(200, {"login": login}, cookie=new_id)
            return
        error = response.get("error")
        if error == "slow_down":
            interval = max(interval + 5, response.get("interval", 0))
        elif error != "authorization_pending":
            SESSIONS.pop(session_id, None)
            raise ValueError(f"GitHub sign-in failed ({error or 'unexpected response'}).")
        state.update(interval=interval, next_poll=monotonic() + interval)
        self.send_json(200, {"retryAfter": interval})


if __name__ == "__main__":
    with HTTPServer((HOST, PORT), Handler) as server:
        print(f"RepoScope is available at {ORIGIN}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
