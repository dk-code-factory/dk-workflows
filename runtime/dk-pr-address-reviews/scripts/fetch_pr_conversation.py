#!/usr/bin/env python3
"""Fetch complete top-level PR reviews and comments with stable feedback references."""

from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


class FetchError(RuntimeError):
    pass


def gh_json(args: list[str], cwd: Path) -> object:
    result = subprocess.run(["gh", *args], cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise FetchError(result.stderr.strip() or result.stdout.strip() or "gh failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise FetchError(f"gh returned invalid JSON: {error}") from error


def pages(endpoint: str, hostname: str, cwd: Path) -> list[dict]:
    result = gh_json(
        ["api", "--hostname", hostname, "--method", "GET", "--paginate", "--slurp",
         endpoint, "-F", "per_page=100"], cwd,
    )
    if not isinstance(result, list) or any(not isinstance(page, list) for page in result):
        raise FetchError(f"incomplete paginated response for {endpoint}")
    items = [item for page in result for item in page]
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), int) for item in items):
        raise FetchError(f"invalid item in {endpoint}")
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)):
        raise FetchError(f"duplicate item across pages in {endpoint}")
    return sorted(items, key=lambda item: item["id"])


def normalize(items: list[dict], *, review: bool) -> list[dict]:
    result = []
    for item in items:
        user = item.get("user")
        result.append({
            "id": item["id"],
            "reference": ("pullrequestreview-" if review else "issuecomment-") + str(item["id"]),
            "kind": "review" if review else "issue_comment",
            "replyKind": "unclassified" if review else "issue_comment",
            "resolvable": False,
            "author": user.get("login") if isinstance(user, dict) else None,
            "text": item.get("body") if isinstance(item.get("body"), str) else "",
            "url": item.get("html_url"),
            "createdAt": item.get("submitted_at") if review else item.get("created_at"),
            **({"state": item.get("state"), "commitId": item.get("commit_id")}
               if review else {"updatedAt": item.get("updated_at")}),
        })
    return result


def fetch(snapshot: dict, cwd: Path, attempts: int = 3) -> dict:
    if snapshot.get("complete") is not True or not isinstance(snapshot.get("items"), list):
        raise FetchError("complete thread snapshot required")
    canonical_items = json.dumps(snapshot["items"], ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"))
    if (snapshot.get("itemCount") != len(snapshot["items"]) or
            snapshot.get("itemsSha256") != hashlib.sha256(canonical_items.encode()).hexdigest()):
        raise FetchError("thread snapshot hash or count is invalid")
    pr = snapshot.get("pullRequest")
    if not isinstance(pr, dict) or not isinstance(pr.get("url"), str):
        raise FetchError("thread snapshot lacks PR identity")
    parsed = urlparse(pr["url"])
    parts = parsed.path.strip("/").split("/")
    if parsed.scheme != "https" or not parsed.hostname or len(parts) != 4 or parts[2] != "pull":
        raise FetchError("invalid PR URL in thread snapshot")
    if not parts[3].isdigit() or int(parts[3]) != pr.get("number"):
        raise FetchError("PR number disagrees with snapshot URL")
    endpoint = f"repos/{parts[0]}/{parts[1]}"
    prior_fingerprint = None
    for _ in range(attempts):
        reviews = normalize(pages(f"{endpoint}/pulls/{parts[3]}/reviews", parsed.hostname, cwd),
                            review=True)
        comments = normalize(pages(f"{endpoint}/issues/{parts[3]}/comments", parsed.hostname, cwd),
                             review=False)
        live = gh_json(["pr", "view", pr["url"], "--json", "headRefOid"], cwd)
        if not isinstance(live, dict) or live.get("headRefOid") != pr.get("headRefOid"):
            raise FetchError("PR head changed since thread snapshot")
        result = {"complete": True, "pullRequest": {"url": pr["url"],
                  "headRefOid": pr["headRefOid"]}, "reviewCount": len(reviews),
                  "commentCount": len(comments), "reviews": reviews, "comments": comments,
                  "items": reviews + comments, "itemCount": len(reviews) + len(comments)}
        fingerprint = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                                 separators=(",", ":")).encode()).hexdigest()
        if fingerprint == prior_fingerprint:
            result["sha256"] = fingerprint
            return result
        prior_fingerprint = fingerprint
    raise FetchError("top-level PR conversation changed during fetch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-file", required=True)
    parser.add_argument("--cwd", default=".")
    args = parser.parse_args()
    try:
        snapshot = json.loads(Path(args.snapshot_file).read_text())
        result = fetch(snapshot, Path(args.cwd).resolve())
    except (OSError, ValueError, FetchError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
