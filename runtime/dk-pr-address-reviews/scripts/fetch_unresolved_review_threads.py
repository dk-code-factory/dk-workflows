#!/usr/bin/env python3
"""Fetch every unresolved inline GitHub PR review thread as stable JSON."""

from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from shared.github_reads import run as github_run


THREADS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      id
      url
      headRefOid
      reviewThreads(first: 100, after: $cursor) {
        nodes {
          id
          isResolved
          viewerCanResolve
          isOutdated
          path
          line
          originalLine
          diffSide
          startLine
          comments(first: 100) {
            nodes {
              id
              databaseId
              body
              url
              createdAt
              updatedAt
              pullRequestReview { id databaseId }
              author { login }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


MORE_COMMENTS_QUERY = """
query($threadId: ID!, $cursor: String!) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      comments(first: 100, after: $cursor) {
        nodes {
          id
          databaseId
          body
          url
          createdAt
          updatedAt
          pullRequestReview { id databaseId }
          author { login }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


class FetchError(RuntimeError):
    """Raised when a complete deterministic snapshot cannot be produced."""


def run_gh(arguments: list[str], cwd: Path) -> dict[str, Any]:
    result = github_run(
        ["gh", *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown gh failure"
        raise FetchError(f"gh command failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise FetchError(f"gh returned invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise FetchError("gh returned a non-object JSON payload")
    if payload.get("errors"):
        raise FetchError(f"GitHub GraphQL errors: {json.dumps(payload['errors'], sort_keys=True)}")
    return payload


def graphql(
    query: str,
    variables: dict[str, str | int],
    cwd: Path,
    hostname: str,
) -> dict[str, Any]:
    arguments = ["api", "--hostname", hostname, "graphql", "-f", f"query={query}"]
    for name, value in variables.items():
        flag = "-F" if isinstance(value, int) else "-f"
        arguments.extend([flag, f"{name}={value}"])
    return run_gh(arguments, cwd)


def split_pr_reference(pr):
    """Keep a review or comment anchor while passing a canonical URL to gh."""
    if not pr or not pr.startswith('https://'):
        return pr, None
    parsed = urlparse(pr)
    reference = parsed.fragment or None
    if reference and not re.fullmatch(r'(?:pullrequestreview-\d+|discussion_r\d+)', reference):
        raise FetchError('unsupported PR anchor; use a PR URL, review link, or inline comment link')
    return parsed._replace(fragment='', query='', path=parsed.path.rstrip('/')).geturl(), reference


def selection_for(reference, threads):
    if isinstance(reference, str) and re.fullmatch(r'pullrequestreview-\d+', reference):
        return {'reviewReference': reference,
                'threadReferences': sorted(t['id'] for t in threads if any(
                    c.get('reviewReference') == reference for c in t['comments']))}
    if isinstance(reference, str) and re.fullmatch(r'discussion_r\d+', reference):
        database_id = int(reference.removeprefix('discussion_r'))
        matches = [t['id'] for t in threads if any(
            c.get('databaseId') == database_id for c in t['comments'])]
        if len(matches) != 1:
            raise FetchError(f'selected inline comment must match exactly one thread: {reference}')
        return {'commentReference': reference, 'threadReferences': matches}
    raise FetchError('invalid review or inline comment selection')


def resolve_pull_request(pr: str | None, cwd: Path) -> dict[str, Any]:
    arguments = ["pr", "view"]
    canonical, reference = split_pr_reference(pr)
    if canonical:
        arguments.append(canonical)
    arguments.extend(
        [
            "--json",
            "number,url,state,title,body,headRefName,headRefOid,headRepository,"
            "baseRefName,baseRefOid,closingIssuesReferences,isCrossRepository",
        ]
    )
    metadata = run_gh(arguments, cwd)
    url = require_string(metadata, "url", "pull request metadata")
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 4 or path_parts[2] != "pull":
        raise FetchError(f"cannot derive repository from pull request URL: {url}")
    number = metadata.get("number")
    if not isinstance(number, int):
        raise FetchError("pull request metadata has no integer number")
    return {
        **({("commentReference" if reference.startswith('discussion_r') else "reviewReference"): reference}
           if reference else {}),
        "owner": path_parts[0],
        "repo": path_parts[1],
        "hostname": parsed.hostname or "github.com",
        "number": number,
        "url": url,
        "state": metadata.get("state"),
        "title": require_string(metadata, "title", "pull request metadata"),
        "description": require_text(metadata, "body", "pull request metadata"),
        "headRefName": metadata.get("headRefName"),
        "headRefOid": metadata.get("headRefOid"),
        "headRepository": metadata.get("headRepository"),
        "baseRefName": metadata.get("baseRefName"),
        "baseRefOid": metadata.get("baseRefOid"),
        "closingIssuesReferences": metadata.get("closingIssuesReferences"),
        "isCrossRepository": metadata.get("isCrossRepository"),
    }


def fetch_all_threads(metadata: dict[str, Any], cwd: Path) -> list[dict[str, Any]]:
    threads: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        variables: dict[str, str | int] = {
            "owner": require_string(metadata, "owner", "pull request metadata"),
            "repo": require_string(metadata, "repo", "pull request metadata"),
            "number": require_int(metadata, "number", "pull request metadata"),
        }
        if cursor is not None:
            variables["cursor"] = cursor
        payload = graphql(
            THREADS_QUERY,
            variables,
            cwd,
            require_string(metadata, "hostname", "pull request metadata"),
        )
        pull_request = nested(payload, "data", "repository", "pullRequest")
        if not isinstance(pull_request, dict):
            raise FetchError("pull request was not found in GraphQL response")
        connection = require_dict(pull_request, "reviewThreads", "pull request")
        nodes = require_list(connection, "nodes", "reviewThreads")
        for raw_thread in nodes:
            if not isinstance(raw_thread, dict):
                raise FetchError("reviewThreads contains a non-object node")
            thread_id = require_string(raw_thread, "id", "review thread")
            if thread_id in threads:
                raise FetchError(f"duplicate review thread across pages: {thread_id}")
            comments_connection = require_dict(raw_thread, "comments", f"thread {thread_id}")
            comments = normalize_comments(
                require_list(comments_connection, "nodes", f"thread {thread_id} comments")
            )
            page_info = require_page_info(comments_connection, f"thread {thread_id} comments")
            if page_info["hasNextPage"]:
                comments.extend(
                    fetch_remaining_comments(
                        thread_id,
                        require_cursor(page_info, f"thread {thread_id} comments"),
                        cwd,
                        require_string(metadata, "hostname", "pull request metadata"),
                    )
                )
            raw_thread["comments"] = deduplicate_comments(comments, thread_id)
            threads[thread_id] = raw_thread

        page_info = require_page_info(connection, "reviewThreads")
        if not page_info["hasNextPage"]:
            break
        cursor = require_cursor(page_info, "reviewThreads")
        if cursor in seen_cursors:
            raise FetchError(f"reviewThreads pagination repeated cursor: {cursor}")
        seen_cursors.add(cursor)
    return list(threads.values())


def fetch_remaining_comments(
    thread_id: str,
    first_cursor: str,
    cwd: Path,
    hostname: str,
) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    cursor = first_cursor
    seen_cursors: set[str] = set()
    while True:
        if cursor in seen_cursors:
            raise FetchError(f"comment pagination repeated cursor for {thread_id}: {cursor}")
        seen_cursors.add(cursor)
        payload = graphql(
            MORE_COMMENTS_QUERY,
            {"threadId": thread_id, "cursor": cursor},
            cwd,
            hostname,
        )
        node = nested(payload, "data", "node")
        if not isinstance(node, dict):
            raise FetchError(f"thread disappeared during comment pagination: {thread_id}")
        connection = require_dict(node, "comments", f"thread {thread_id}")
        comments.extend(
            normalize_comments(require_list(connection, "nodes", f"thread {thread_id} comments"))
        )
        page_info = require_page_info(connection, f"thread {thread_id} comments")
        if not page_info["hasNextPage"]:
            return comments
        cursor = require_cursor(page_info, f"thread {thread_id} comments")


def normalize_comments(raw_comments: list[Any]) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    for raw_comment in raw_comments:
        if not isinstance(raw_comment, dict):
            raise FetchError("comments connection contains a non-object node")
        reference = require_string(raw_comment, "id", "review comment")
        author = raw_comment.get("author")
        review = raw_comment.get("pullRequestReview") or {}
        review_id = review.get("databaseId")
        comments.append(
            {
                "reference": reference,
                "databaseId": raw_comment.get("databaseId"),
                "author": author.get("login") if isinstance(author, dict) else None,
                "createdAt": raw_comment.get("createdAt"),
                "updatedAt": raw_comment.get("updatedAt"),
                "reviewNodeId": review.get("id"),
                "reviewReference": f"pullrequestreview-{review_id}" if review_id is not None else None,
                "url": raw_comment.get("url"),
                "text": raw_comment.get("body") if isinstance(raw_comment.get("body"), str) else "",
            }
        )
    return comments


def deduplicate_comments(comments: list[dict[str, Any]], thread_id: str) -> list[dict[str, Any]]:
    by_reference: dict[str, dict[str, Any]] = {}
    for comment in comments:
        reference = require_string(comment, "reference", f"thread {thread_id} comment")
        prior = by_reference.get(reference)
        if prior is not None and prior != comment:
            raise FetchError(f"comment changed during pagination: {reference}")
        by_reference[reference] = comment
    return sorted(
        by_reference.values(),
        key=lambda item: (string_or_empty(item.get("createdAt")), item["reference"]),
    )


def prepare_items(threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for thread in threads:
        if thread.get("isResolved") is not False:
            continue
        comments = thread.get("comments")
        if not isinstance(comments, list) or not comments:
            raise FetchError(f"unresolved thread has no comments: {thread.get('id')}")
        root = comments[0]
        items.append(
            {
                "reference": require_string(thread, "id", "review thread"),
                "commentReference": require_string(root, "reference", "root review comment"),
                "url": root.get("url"),
                "path": thread.get("path"),
                "line": thread.get("line"),
                "originalLine": thread.get("originalLine"),
                "isOutdated": bool(thread.get("isOutdated")),
                "text": root.get("text", ""),
                "conversation": comments,
            }
        )
    items.sort(key=item_sort_key)
    return items


def item_sort_key(item: dict[str, Any]) -> tuple[str, int, str]:
    line = item.get("line")
    if not isinstance(line, int):
        line = item.get("originalLine")
    if not isinstance(line, int):
        line = 2**31 - 1
    return (string_or_empty(item.get("path")), line, require_string(item, "reference", "item"))


def build_output(metadata: dict[str, Any], threads: list[dict[str, Any]]) -> dict[str, Any]:
    items = prepare_items(threads)
    threads = sorted(threads, key=lambda thread: thread["id"])
    canonical_threads = json.dumps(threads, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    canonical_items = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    reference = metadata.get("commentReference") or metadata.get("reviewReference")
    return {
        **({"selection": selection_for(reference, threads)} if reference else {}),
        "schemaVersion": 1,
        "complete": True,
        "repository": f"{metadata['owner']}/{metadata['repo']}",
        "pullRequest": {
            "number": metadata["number"],
            "url": metadata["url"],
            "state": metadata.get("state"),
            "title": metadata["title"],
            "description": metadata["description"],
            "headRefName": metadata.get("headRefName"),
            "headRefOid": metadata.get("headRefOid"),
            "headRepository": metadata.get("headRepository"),
            "baseRefName": metadata.get("baseRefName"),
            "baseRefOid": metadata.get("baseRefOid"),
            "closingIssuesReferences": metadata.get("closingIssuesReferences"),
            "isCrossRepository": metadata.get("isCrossRepository"),
        },
        "itemCount": len(items),
        "itemsSha256": hashlib.sha256(canonical_items.encode("utf-8")).hexdigest(),
        "items": items,
        "threads": threads,
        "threadCount": len(threads),
        "threadsSha256": hashlib.sha256(canonical_threads.encode("utf-8")).hexdigest(),
    }


def fetch_stable_snapshot(pr: str | None, cwd: Path, attempts: int = 3) -> dict[str, Any]:
    prior: dict[str, Any] | None = None
    prior_fingerprint: str | None = None
    for _ in range(attempts):
        metadata = resolve_pull_request(pr, cwd)
        output = build_output(metadata, fetch_all_threads(metadata, cwd))
        fingerprint = json.dumps(
            {
                "repository": output["repository"],
                "pullRequest": output["pullRequest"],
                "itemCount": output["itemCount"],
                "itemsSha256": output["itemsSha256"],
                "threadsSha256": output["threadsSha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if fingerprint == prior_fingerprint:
            return output
        prior = output
        prior_fingerprint = fingerprint
    changed = prior["itemsSha256"] if prior else "unknown"
    raise FetchError(
        f"pull request changed during deterministic fetch; no two consecutive snapshots matched (last hash {changed})"
    )


def nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def require_dict(value: dict[str, Any], key: str, context: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise FetchError(f"{context} has no object field {key}")
    return result


def require_list(value: dict[str, Any], key: str, context: str) -> list[Any]:
    result = value.get(key)
    if not isinstance(result, list):
        raise FetchError(f"{context} has no list field {key}")
    return result


def require_string(value: dict[str, Any], key: str, context: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise FetchError(f"{context} has no non-empty string field {key}")
    return result


def require_text(value: dict[str, Any], key: str, context: str) -> str:
    result = value.get(key)
    if not isinstance(result, str):
        raise FetchError(f"{context} has no string field {key}")
    return result


def require_int(value: dict[str, Any], key: str, context: str) -> int:
    result = value.get(key)
    if not isinstance(result, int):
        raise FetchError(f"{context} has no integer field {key}")
    return result


def require_page_info(connection: dict[str, Any], context: str) -> dict[str, Any]:
    page_info = require_dict(connection, "pageInfo", context)
    if not isinstance(page_info.get("hasNextPage"), bool):
        raise FetchError(f"{context} pageInfo has no boolean hasNextPage")
    return page_info


def require_cursor(page_info: dict[str, Any], context: str) -> str:
    cursor = page_info.get("endCursor")
    if not isinstance(cursor, str) or not cursor:
        raise FetchError(f"{context} says another page exists but has no endCursor")
    return cursor


def string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", help="GitHub pull-request URL or number; default: current branch PR")
    parser.add_argument("--cwd", default=".", help="Repository working directory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cwd = Path(args.cwd).resolve()
    try:
        output = fetch_stable_snapshot(args.pr, cwd)
    except (FetchError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    json.dump(output, sys.stdout, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
