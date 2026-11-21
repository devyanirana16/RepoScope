"""First milestone of the GitHub repository Q&A project. Uses fictional data."""

import argparse
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from math import log


SEARCH_RESULT_LIMIT = 5


@dataclass(frozen=True)
class Answer:
    evidence: tuple
    disagreement: bool = False


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    repository: str
    source_type: str
    source_ref: str
    updated_on: date
    confidential: bool
    text: str


def chunk_markdown(repository, path, markdown_text, updated_on, confidential=False):
    """Split our sample Markdown by level-two headings and cite line numbers."""
    chunks = []
    heading_line = 1
    section_lines = []

    for line_number, line in enumerate(markdown_text.splitlines(), start=1):
        if line.startswith("## "):
            if section_lines:
                chunks.append((heading_line, section_lines))
            heading_line = line_number
            section_lines = []
        if line.strip() or section_lines:
            section_lines.append(line)

    if section_lines:
        chunks.append((heading_line, section_lines))

    return [
        Chunk(
            f"{repository}:{path}:{line_number}",
            repository,
            "markdown_doc",
            f"{path}#L{line_number}",
            updated_on,
            confidential,
            "\n".join(lines).strip(),
        )
        for line_number, lines in chunks
    ]


def chunk_issue(repository, number, title, body, updated_on, comments, confidential=False):
    issue_ref = f"issues/{number}"
    chunks = [
        Chunk(
            f"{repository}:issue-{number}-body",
            repository,
            "github_issue",
            issue_ref,
            updated_on,
            confidential,
            f"{title}\n{body}",
        )
    ]
    for comment in comments:
        chunks.append(
            Chunk(
                f"{repository}:issue-{number}-comment-{comment['id']}",
                repository,
                "github_issue",
                f"{issue_ref}#issuecomment-{comment['id']}",
                comment["updated_on"],
                confidential,
                f"{title}\n{comment['text']}",
            )
        )
    return chunks


def chunk_release(repository, tag, notes, published_on, confidential=False):
    """Keep a short release note together."""
    return Chunk(
        f"{repository}:release-{tag}",
        repository,
        "release_notes",
        f"releases/tag/{tag}",
        published_on,
        confidential,
        f"{tag}\n{notes}",
    )


def repositories_to_search(repo_access, user_id, selected_repo=None):
    allowed = set(repo_access.get(user_id, ()))
    if selected_repo is None:
        return allowed
    if selected_repo not in allowed:
        raise PermissionError(
            "The repository you selected is unavailable or you don’t have access to it."
        )
    return {selected_repo}


def eligible_chunks(index, repo_access, user_id, selected_repo=None):
    """Return the only candidates the next search step may score."""
    scope = repositories_to_search(repo_access, user_id, selected_repo)
    return [chunk for repo in sorted(scope) for chunk in index.get(repo, ())]


def keywords(text):
    """Return the distinct lowercase words in text."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def search_chunks(index, repo_access, user_id, query, selected_repo=None):
    """Rank authorized matches by inverse document frequency."""
    candidates = eligible_chunks(index, repo_access, user_id, selected_repo)
    query_words = keywords(query)
    if not query_words:
        return []

    overlaps = [
        (chunk, query_words & (keywords(chunk.text) | keywords(chunk.source_ref)))
        for chunk in candidates
    ]
    frequencies = Counter(word for _, words in overlaps for word in words)
    weights = {
        word: 1 + log((len(candidates) + 1) / (frequency + 1))
        for word, frequency in frequencies.items()
    }
    matches = [
        (sum(weights[word] for word in sorted(words)), chunk)
        for chunk, words in overlaps
        if words
    ]

    matches.sort(key=lambda match: match[0], reverse=True)
    return [chunk for _, chunk in matches]


def best_matching_line(text, query_words):
    return max(
        text.splitlines(),
        key=lambda line: len(query_words & keywords(line)),
        default="",
    ).strip()


def answer_keywords(question):
    return keywords(question) - {
        "a", "an", "are", "do", "does", "how", "is", "of", "the", "what", "when", "where",
        "which", "who", "why",
    }


def retrieve_answer_evidence(index, repo_access, user_id, question_words, selected_repo=None):
    """Keep ranked chunks with at least two question terms in their text."""
    chunks = search_chunks(index, repo_access, user_id, " ".join(question_words), selected_repo)
    minimum_matches = min(2, len(question_words))
    return [
        chunk for chunk in chunks
        if len(question_words & keywords(chunk.text)) >= minimum_matches
    ]


def conflicting_limits(chunks, question_words):
    """Compare a documented current limit with a rollout limit for the same topic."""
    if "limit" not in question_words:
        return ()

    documented = {}
    rollout = {}
    for chunk in chunks:
        for line in chunk.text.splitlines():
            if chunk.source_type == "markdown_doc":
                match = re.fullmatch(r"The current (.+) limit is (.+)\.", line)
                limits = documented
            elif chunk.source_type == "github_issue":
                match = re.fullmatch(r"The (.+) rollout notes give a limit of (.+)\.", line)
                limits = rollout
            else:
                continue
            if match:
                topic, value = match.groups()
                if keywords(topic) <= question_words:
                    limits.setdefault((chunk.repository, topic), (value, line, chunk.source_ref))

    for key, (value, line, source) in documented.items():
        other = rollout.get(key)
        if other and value.casefold() != other[0].casefold():
            repo, _ = key
            return ((line, f"{repo}/{source}"), (other[1], f"{repo}/{other[2]}"))
    return ()


def issue_and_release_evidence(chunks, question):
    """Find the issue and release details for a two-part topic question."""
    match = re.fullmatch(
        r"what does the (.+?) issue track and what does the release add\??",
        question.strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        return ()

    topic = match[1].casefold()
    prefixes = {
        "github_issue": f"the {topic} issue tracks ",
        "release_notes": f"the {topic} release adds ",
    }
    evidence = {}
    for chunk in chunks:
        prefix = prefixes.get(chunk.source_type)
        if prefix is None:
            continue
        for line in chunk.text.splitlines():
            if line.casefold().startswith(prefix):
                by_type = evidence.setdefault(chunk.repository, {})
                by_type.setdefault(chunk.source_type, (line, f"{chunk.repository}/{chunk.source_ref}"))
                if prefixes.keys() <= by_type.keys():
                    return tuple(by_type[source_type] for source_type in prefixes)
    return ()


def answer_question(index, repo_access, user_id, question, selected_repo=None):
    """Return cited evidence from authorized matches."""
    question_words = answer_keywords(question)
    chunks = retrieve_answer_evidence(index, repo_access, user_id, question_words, selected_repo)
    if not chunks:
        return None

    top_chunks = chunks[:SEARCH_RESULT_LIMIT]
    if {"rollout", "notes"} <= question_words:
        conflict = conflicting_limits(top_chunks, question_words)
        if conflict:
            return Answer(conflict, disagreement=True)

    synthesis = issue_and_release_evidence(top_chunks, question)
    if synthesis:
        return Answer(synthesis)

    chunk = chunks[0]
    text = chunk.text
    if chunk.source_type == "markdown_doc" and text.startswith("## "):
        body = text.partition("\n")[2]
        text = body.strip() or text
    excerpt = best_matching_line(text, question_words)
    if chunk.source_type == "github_issue":
        body_excerpt = best_matching_line(text.partition("\n")[2], question_words)
        if body_excerpt and len(question_words & keywords(body_excerpt)) >= len(
            question_words & keywords(excerpt)
        ):
            excerpt = body_excerpt
    return Answer(((excerpt, f"{chunk.repository}/{chunk.source_ref}"),))


def print_search_results(chunks, query):
    """Print each result's source and its best-matching line."""
    if not chunks:
        print("  No matching chunks.")
        return

    query_words = keywords(query)
    for chunk in chunks:
        line = best_matching_line(chunk.text, query_words)
        print(f"  {chunk.repository}/{chunk.source_ref} [{chunk.source_type}]")
        print(f"    {line}")


def demo_data():
    payments = "payments-api"
    fraud = "fraud-engine"
    updated_on = date(2026, 8, 14)
    index = {
        payments: [
            *chunk_markdown(
                payments,
                "docs/payment-status.md",
                "## Pending\nThe payment is still processing.\n\n"
                "## Under Review\nThe payment is waiting for manual review.",
                updated_on,
            ),
            *chunk_issue(
                payments, 18, "Add a review-completed webhook",
                "Merchants have to keep checking the API to learn when a review finishes.",
                updated_on,
                [{"id": 101, "text": "Send a webhook when the review completes.",
                  "updated_on": updated_on}],
            ),
            chunk_release(
                payments, "v2.4", "Added the payment.review_completed webhook.", updated_on,
            ),
        ],
        fraud: chunk_issue(
            fraud, 42, "Lower the fraud review threshold",
            "Too many suspicious payments were being approved.",
            updated_on, [], confidential=True,
        ),
    }
    return index, {"maya": {payments}, "arjun": {payments, fraud}}


def demo(query="review webhook", user=None, selected_repo=None, *, answer=False):
    index, repo_access = demo_data()
    fraud = "fraud-engine"

    if user is not None:
        if answer:
            result = answer_question(index, repo_access, user, query, selected_repo)
            if result is None:
                print("No matching evidence found.")
            else:
                if result.disagreement:
                    print("The sources disagree.")
                for excerpt, citation in result.evidence:
                    print(excerpt)
                    print(f"Source: {citation}")
            return
        chunks = search_chunks(index, repo_access, user, query, selected_repo)
        print_search_results(chunks, query)
        return

    for user_id in ("maya", "arjun"):
        print(f"{user_id.title()}'s eligible chunks")
        for chunk in eligible_chunks(index, repo_access, user_id):
            print(f"  {chunk.chunk_id} ({chunk.source_ref})")

    for user_id in ("maya", "arjun"):
        print(f"\n{user_id.title()}'s search results for {query!r}")
        print_search_results(search_chunks(index, repo_access, user_id, query), query)

    repo_access["arjun"].remove(fraud)
    print("\nArjun's scope after revocation:", sorted(repositories_to_search(repo_access, "arjun")))
    print(f"Arjun's search results for {query!r} after revocation")
    print_search_results(search_chunks(index, repo_access, "arjun", query), query)

    try:
        search_chunks(index, repo_access, "arjun", query, selected_repo=fraud)
    except PermissionError as error:
        print(error)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Search the sample repository data.")
    parser.add_argument("query", nargs="?", default="review webhook", help="Words to search for")
    parser.add_argument("--user", choices=("maya", "arjun"), help="Run one search as this sample user")
    parser.add_argument("--repo", help="Limit the search to this repository")
    parser.add_argument("--answer", action="store_true", help="Show a cited excerpt for the question")
    args = parser.parse_args()
    if args.repo is not None and args.user is None:
        parser.error("--repo requires --user")
    if args.answer and args.user is None:
        parser.error("--answer requires --user")
    try:
        demo(args.query, args.user, args.repo, answer=args.answer)
    except PermissionError as error:
        parser.exit(1, f"{error}\n")
