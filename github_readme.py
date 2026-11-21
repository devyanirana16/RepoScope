"""Search the README of one GitHub repository."""

import argparse
import base64
import json
import os
import re
import sys
from datetime import date
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from repo_qa import best_matching_line, chunk_markdown, keywords, search_chunks


class SameOriginRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl)[:2] != urlsplit(req.full_url)[:2]:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request_json(request):
    with build_opener(SameOriginRedirects()).open(request, timeout=15) as response:
        return json.load(response)


def github_json(path, token=None):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "RepoScope"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"https://api.github.com{path}",
        headers=headers,
    )
    return request_json(request)


def accessible_repositories(token):
    """List repositories visible to this GitHub App user token."""
    def pages(path, key):
        page = 1
        while True:
            response = github_json(f"{path}?per_page=100&page={page}", token)
            items = response[key]
            yield from items
            if len(items) < 100 or page * 100 >= response["total_count"]:
                return
            page += 1

    names = set()
    for installation in pages("/user/installations", "installations"):
        path = f"/user/installations/{installation['id']}/repositories"
        names.update(repo["full_name"] for repo in pages(path, "repositories"))
    return sorted(names, key=str.casefold)


def device_request(path, **fields):
    return request_json(Request(
        f"https://github.com{path}",
        data=urlencode(fields).encode("ascii"),
        headers={"Accept": "application/json"},
    ))


def start_device_sign_in(client_id):
    device = device_request("/login/device/code", client_id=client_id)
    if "error" in device:
        raise ValueError(f"GitHub could not start sign-in ({device['error']}).")
    return device


def poll_device_sign_in(client_id, device_code):
    return device_request(
        "/login/oauth/access_token",
        client_id=client_id,
        device_code=device_code,
        grant_type="urn:ietf:params:oauth:grant-type:device_code",
    )


def sign_in(client_id):
    device = start_device_sign_in(client_id)
    print(f"Enter code {device['user_code']} at https://github.com/login/device", file=sys.stderr)
    interval = device["interval"]
    deadline = monotonic() + device["expires_in"]
    while monotonic() + interval < deadline:
        sleep(interval)
        response = poll_device_sign_in(client_id, device["device_code"])
        if "access_token" in response:
            return response["access_token"]
        error = response.get("error")
        if error == "slow_down":
            interval = response.get("interval", interval + 5)
        elif error != "authorization_pending":
            raise ValueError(f"GitHub sign-in failed ({error or 'unexpected response'}).")
    raise ValueError("GitHub sign-in expired. Run the command again to get a new code.")


def search_readme(repository, query, token=None):
    """Return cited excerpts from a README visible to the requesting user."""
    if repository.startswith("https://"):
        url = urlsplit(repository)
        if url.netloc.lower() != "github.com":
            raise ValueError("Use a github.com repository URL or OWNER/REPO.")
        repository = "/".join(url.path.strip("/").split("/")[:2])
        if repository.endswith(".git"):
            repository = repository[:-4]
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Use a GitHub repository URL or OWNER/REPO.")

    repo_path = f"/repos/{repository}"
    user_id = github_json("/user", token)["login"] if token is not None else "public"
    try:
        metadata = github_json(repo_path, token)
    except HTTPError as error:
        if error.code in (403, 404):
            raise PermissionError("The repository is unavailable or you don’t have access to it.") from error
        raise
    if token is None and metadata.get("visibility") != "public":
        raise PermissionError("This command requires a public repository.")

    repository = metadata["full_name"]
    repo_path = f"/repos/{repository}"
    commits = github_json(f"{repo_path}/commits?per_page=1", token)
    if not commits:
        raise ValueError("The repository has no commits.")

    commit = commits[0]
    sha = commit["sha"]
    updated_on = date.fromisoformat(commit["commit"]["committer"]["date"][:10])
    readme = github_json(f"{repo_path}/readme?ref={quote(sha, safe='')}", token)
    if readme["encoding"] != "base64":
        raise ValueError("GitHub did not return an inline README.")

    text = base64.b64decode(readme["content"]).decode("utf-8-sig")
    chunks = chunk_markdown(repository, readme["path"], text, updated_on)
    results = search_chunks({repository: chunks}, {user_id: {repository}}, user_id, query)
    query_words = keywords(query)
    base_url = f"https://github.com/{repository}/blob/{sha}"
    return [
        (
            f"{base_url}/{quote(readme['path'])}#{chunk.source_ref.rsplit('#', 1)[-1]}",
            best_matching_line(chunk.text, query_words),
        )
        for chunk in results
    ]


def search_public_readme(repository, query):
    return search_readme(repository, query)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", help="Repository in OWNER/REPO format")
    parser.add_argument("query", help="Words to search for in the README")
    parser.add_argument("--sign-in", action="store_true", help="Sign in through a GitHub App")
    args = parser.parse_args()
    client_id = os.environ.get("GITHUB_APP_CLIENT_ID")
    if args.sign_in and not client_id:
        parser.error("--sign-in requires GITHUB_APP_CLIENT_ID")
    try:
        token = sign_in(client_id) if args.sign_in else None
        results = search_readme(args.repository, args.query, token)
    except KeyboardInterrupt:
        parser.exit(130, "\nCancelled.\n")
    except HTTPError as error:
        parser.exit(1, f"GitHub returned HTTP {error.code} ({error.reason}).\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")

    if not results:
        print("No matching chunks.")
    for citation, excerpt in results:
        print(excerpt)
        print(f"Source: {citation}")


if __name__ == "__main__":
    main()
