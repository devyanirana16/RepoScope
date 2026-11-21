"""Run a local golden evaluation with python3 -B eval_repo_qa.py."""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from repo_qa import SEARCH_RESULT_LIMIT, answer_keywords, answer_question, chunk_issue, chunk_markdown
from repo_qa import chunk_release, retrieve_answer_evidence


# Each topic has a documented current value, a superseded value, and distinct
# issue and release facts. Values and private markers are fictional.
TOPICS = (
    ("retries", "3 attempts", "5 attempts", "retry metrics", "jitter support", "amber"),
    ("timeouts", "20 seconds", "60 seconds", "timeout traces", "deadline propagation", "birch"),
    ("webhooks", "24 hours", "48 hours", "delivery alerts", "signature rotation", "cedar"),
    ("pagination", "50 records", "100 records", "cursor validation", "opaque cursors", "dune"),
    ("retention", "30 days", "90 days", "deletion audits", "scheduled purges", "elm"),
    ("batching", "200 items", "500 items", "batch monitoring", "partial successes", "fern"),
    ("polling", "15 seconds", "5 seconds", "poll telemetry", "conditional requests", "grove"),
    ("exports", "10 files", "25 files", "export monitoring", "compressed downloads", "hazel"),
    ("sessions", "8 hours", "12 hours", "session audits", "token refresh", "iris"),
    ("quotas", "40 requests per minute", "80 requests per minute", "quota alerts", "usage headers", "jade"),
)

TYPES = (
    "easy lookup", "cross-document synthesis", "conflicting sources",
    "stale information", "refusal",
)
CURRENT = date(2026, 8, 14)
OLD = date(2025, 6, 10)
ACCESS = {"maya": {"payments-api"}, "arjun": {"payments-api", "fraud-engine"}}
TOP_K = SEARCH_RESULT_LIMIT


@dataclass(frozen=True)
class Case:
    kind: str
    question: str
    evidence: tuple = ()
    expected_lines: tuple = ()
    user: str = "maya"
    selected_repo: Optional[str] = None
    denied: bool = False


def citation(chunk):
    return f"{chunk.repository}/{chunk.source_ref}"


def golden_dataset():
    index = {"payments-api": [], "fraud-engine": []}
    cases = []
    for number, (topic, current, old, issue_fact, release_fact, marker) in enumerate(TOPICS, 1):
        payment = "payments-api"
        current_line = f"The current {topic} limit is {current}."
        old_line = f"The legacy {topic} limit was {old}."
        issue_line = f"The {topic} issue tracks {issue_fact}."
        release_line = f"The {topic} release adds {release_fact}."
        conflict_line = f"The {topic} rollout notes give a limit of {old}."
        doc, = chunk_markdown(
            payment, f"docs/{topic}.md", f"## {topic} limits\n{current_line}", CURRENT
        )
        legacy, = chunk_markdown(
            payment, f"archive/{topic}.md", f"## {topic} legacy limits\n{old_line}", OLD
        )
        issue, = chunk_issue(payment, number, f"{topic} issue", issue_line, CURRENT, [])
        conflict, = chunk_issue(
            payment, 100 + number, f"{topic} rollout notes", conflict_line, CURRENT, []
        )
        release = chunk_release(payment, f"v2.{number}", release_line, CURRENT)
        private_line = f"The {topic} restricted memo contains memo-{marker}."
        private, = chunk_issue(
            "fraud-engine", number, f"{topic} restricted memo",
            private_line, CURRENT, [], confidential=True,
        )
        index[payment].extend((doc, legacy, issue, conflict, release))
        index["fraud-engine"].append(private)

        if number <= 3:
            cases.append(Case(
                TYPES[0], f"What does the {topic} restricted memo contain?",
                (private,), (private_line,), user="arjun", selected_repo="fraud-engine",
            ))
        else:
            cases.append(Case(TYPES[0], f"What does the {topic} release add?",
                              (release,), (release_line,)))

        cases.extend((
            Case(TYPES[1], f"What does the {topic} issue track and what does the release add?",
                 (issue, release), (issue_line, release_line)),
            Case(TYPES[2], f"What {topic} limit do the current docs and rollout notes give?",
                 (doc, conflict), (current_line, conflict_line)),
            Case(TYPES[3], f"Is the legacy {topic} limit still current?",
                 (doc,), (current_line,)),
        ))
        if number <= 3:
            cases.append(Case(TYPES[4], f"What does the {topic} restricted memo contain?",
                              selected_repo="fraud-engine", denied=True))
        elif number <= 6:
            cases.append(Case(TYPES[4], f"What does the {topic} restricted memo contain?"))
        elif number <= 8:
            cases.append(Case(TYPES[4], f"What does the {topic} restricted memo contain?",
                              user="guest"))
        else:
            cases.append(Case(TYPES[4], f"Who owns the {topic} lunar observatory?"))
    return index, cases


def call(operation, index, case, query, access):
    try:
        result = operation(index, access, case.user, query, case.selected_repo)
    except PermissionError:
        return None, True
    return result, False


def evaluate(index, case, access=ACCESS):
    # Measure the search that answer mode actually uses.
    query = answer_keywords(case.question)
    results, retrieval_denied = call(retrieve_answer_evidence, index, case, query, access)
    answer, answer_denied = call(answer_question, index, case, case.question, access)
    results = results or []
    expected_ids = {chunk.chunk_id for chunk in case.evidence}
    top_ids = {chunk.chunk_id for chunk in results[:TOP_K]}
    allowed = access.get(case.user, set())
    if case.selected_repo is not None:
        allowed = allowed & {case.selected_repo}

    retrieval_permitted = all(chunk.repository in allowed for chunk in results)
    answer_permitted = True
    evidence = answer.evidence if answer is not None else ()
    grounded = cited = bool(evidence)
    sources = {citation(chunk): chunk for chunks in index.values() for chunk in chunks}
    for excerpt, source in evidence:
        chunk = sources.get(source)
        authorized = chunk is not None and chunk.repository in allowed
        cited &= authorized
        if chunk is not None:
            answer_permitted &= authorized
        grounded &= authorized and bool(excerpt) and excerpt in {
            line.strip() for line in chunk.text.splitlines()
        }
        # Look for unique private text even if the answer cites another source.
        for chunk in index["fraud-engine"]:
            if chunk.repository not in allowed:
                marker = chunk.text.rsplit("memo-", 1)[-1].rstrip(".")
                answer_permitted &= f"memo-{marker}" not in excerpt.lower()

    if case.kind == "refusal":
        retrieval_ok = retrieval_denied if case.denied else not results and not retrieval_denied
        answer_ok = answer_denied if case.denied else answer is None and not answer_denied
    else:
        retrieval_ok = expected_ids <= top_ids
        expected_evidence = {
            (line, citation(chunk)) for line, chunk in zip(case.expected_lines, case.evidence)
        }
        answer_ok = (
            grounded
            and expected_evidence <= set(evidence)
            and answer.disagreement == (case.kind == "conflicting sources")
        )

    return (
        retrieval_ok and retrieval_permitted,
        answer_ok and answer_permitted,
        retrieval_permitted and answer_permitted,
        grounded,
        cited,
    )


def evaluate_role_change(index, cases):
    before = next(case for case in cases if case.user == "arjun")
    access = {user: set(repos) for user, repos in ACCESS.items()}
    before_ok = all(evaluate(index, before, access)[:3])
    access[before.user].remove(before.selected_repo)

    after = Case("refusal", before.question, user=before.user)
    after_ok = all(evaluate(index, after, access)[:3])
    scoped = Case(
        "refusal", before.question, user=before.user,
        selected_repo=before.selected_repo, denied=True,
    )
    scoped_ok = all(evaluate(index, scoped, access)[:3])
    return {
        "Private evidence available before the role change": before_ok,
        "Unscoped search and answer refuse after the role change": after_ok,
        "Explicit repository selection denied after the role change": scoped_ok,
    }


def main():
    index, cases = golden_dataset()
    print(f"{len(cases)} golden questions. Retrieval uses all required evidence in the top {TOP_K}.")
    scored = [(case, evaluate(index, case)) for case in cases]
    totals = [0, 0, 0, 0, 0]
    for kind in TYPES:
        group = [(case, score) for case, score in scored if case.kind == kind]
        scores = [score for _, score in group]
        counts = [sum(column) for column in zip(*scores)]
        totals = [total + count for total, count in zip(totals, counts)]
        print(f"{kind:<25} retrieval {counts[0]:2}/{len(group)}  answers {counts[1]:2}/{len(group)}")
        for case, (retrieval, answer, permitted, _, _) in group:
            if not (retrieval and answer and permitted):
                failures = [
                    name for name, passed in (
                        ("retrieval", retrieval), ("answer", answer), ("permissions", permitted)
                    ) if not passed
                ]
                print(f"  {case.user}: {case.question} [{', '.join(failures)}]")

    print(f"Total: retrieval {totals[0]}/{len(cases)}, answers {totals[1]}/{len(cases)}.")
    print(f"Grounded excerpts: {totals[3]}. Authorized citations: {totals[4]}.")
    leaks = len(cases) - totals[2]
    print(f"Permission leaks: {leaks}/{len(cases)}.")
    for user in sorted({case.user for case in cases}):
        checks = [score[2] for case, score in scored if case.user == user]
        print(f"  {user}: {checks.count(False)}/{len(checks)}")
    role_change = evaluate_role_change(index, cases)
    print("Role-change scenario (same index before and after):")
    for check, passed in role_change.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {check}")
    return int(bool(leaks) or not all(role_change.values()))


if __name__ == "__main__":
    raise SystemExit(main())
