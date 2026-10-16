#!/usr/bin/env python3
"""Fetch a stable, complete snapshot of all inline PR review threads."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

FETCHER = next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir()) / "dk-pr-address-reviews" / "scripts" / "fetch_unresolved_review_threads.py"
spec = importlib.util.spec_from_file_location("review_thread_fetcher", FETCHER)
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)


def snapshot(pr, cwd, attempts=3):
    previous = None
    for _ in range(attempts):
        metadata = fetcher.resolve_pull_request(pr, cwd)
        threads = fetcher.fetch_all_threads(metadata, cwd)
        canonical = json.dumps({"metadata": metadata, "threads": threads}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        if previous == digest:
            return {"complete": True, "pullRequest": metadata, "threadCount": len(threads),
                    "threadsSha256": digest, "threads": threads,
                    **({"selection": fetcher.selection_for(metadata["reviewReference"], threads)}
                       if metadata.get("reviewReference") else {})}
        previous = digest
    raise fetcher.FetchError("all-thread snapshot changed during pagination; no two consecutive snapshots matched")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True)
    parser.add_argument("--cwd", default=".")
    args = parser.parse_args()
    try:
        print(json.dumps(snapshot(args.pr, Path(args.cwd).resolve()), ensure_ascii=False, sort_keys=True))
    except (fetcher.FetchError, OSError) as exc:
        parser.exit(1, f"completion_threads: {exc}\n")


if __name__ == "__main__":
    main()
