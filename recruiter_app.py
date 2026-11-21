"""Public RepoScope demo. Run locally with python3 recruiter_app.py."""

import json
from pathlib import Path
from urllib.error import HTTPError

from github_readme import search_public_readme
from repo_qa import answer_question, demo_data


WEB = Path(__file__).with_name("web")
ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.css": ("app.css", "text/css"),
    "/app.js": ("app.js", "text/javascript"),
}
SECURITY_HEADERS = [
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Content-Security-Policy",
     "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
]


def permission_demo():
    index, access = demo_data()
    question = "What happened with suspicious payments?"
    # Ask for the same repository before and after revocation, using the same index.
    repository = "fraud-engine"
    before = answer_question(index, access, "arjun", question, repository)
    access["arjun"].remove(repository)
    after = answer_question(index, access, "arjun", question)
    try:
        answer_question(index, access, "arjun", question, repository)
    except PermissionError:
        if before is not None and after is None:
            return {
                "question": question,
                "before": [
                    {"excerpt": excerpt, "citation": citation} for excerpt, citation in before.evidence
                ],
                "after": "Repository access denied. No answer or citations returned.",
            }
    raise ValueError("The permission demo did not pass its access checks.")


def read_search(environ):
    if environ.get("CONTENT_TYPE", "").split(";", 1)[0] != "application/json":
        raise ValueError("Send the search as JSON.")
    length = int(environ.get("CONTENT_LENGTH") or 0)
    if not 0 < length <= 4096:
        raise ValueError("Enter a repository and search terms.")
    fields = json.loads(environ["wsgi.input"].read(length))
    if not isinstance(fields, dict):
        raise ValueError("Enter a repository and search terms.")
    repository, query = fields.get("repository"), fields.get("query")
    if not all(isinstance(value, str) and 0 < len(value.strip()) <= 500 for value in (repository, query)):
        raise ValueError("Enter a repository and search terms.")
    return repository.strip(), query.strip()


def route(environ):
    path, method = environ["PATH_INFO"], environ["REQUEST_METHOD"]
    if method == "GET":
        if path in ASSETS:
            filename, content_type = ASSETS[path]
            return "200 OK", content_type, (WEB / filename).read_bytes()
        if path == "/healthz":
            return "200 OK", "text/plain", b"OK"
        if path == "/api/session":
            return "200 OK", "application/json", {"publicDemo": True, "signInAvailable": False}
        if path == "/api/demo":
            return "200 OK", "application/json", permission_demo()

    if method == "POST" and path == "/api/search":
        repository, query = read_search(environ)
        results = search_public_readme(repository, query)
        return "200 OK", "application/json", {
            "results": [{"citation": citation, "excerpt": excerpt} for citation, excerpt in results]
        }
    return "404 Not Found", "application/json", {"error": "Page unavailable."}


def application(environ, start_response):
    try:
        status, content_type, content = route(environ)
    except HTTPError as error:
        status = "503 Service Unavailable" if error.code in (403, 429) else "502 Bad Gateway"
        content_type = "application/json"
        content = {"error": f"GitHub returned HTTP {error.code}. Try again in a moment."}
    except (PermissionError, ValueError) as error:
        status, content_type, content = "400 Bad Request", "application/json", {"error": str(error)}
    except OSError:
        status, content_type = "502 Bad Gateway", "application/json"
        content = {"error": "GitHub couldn’t be reached. Try again."}

    if content_type == "application/json":
        content = json.dumps(content).encode()
    start_response(status, SECURITY_HEADERS + [
        ("Content-Type", f"{content_type}; charset=utf-8"),
        ("Content-Length", str(len(content))),
    ])
    return [content]


if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    print("RepoScope recruiter preview at http://127.0.0.1:8765", flush=True)
    with make_server("127.0.0.1", 8765, application) as server:
        server.serve_forever()
