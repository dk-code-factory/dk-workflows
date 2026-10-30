#!/usr/bin/env python3
"""Observe checks for one exact PR head; never start or rerun jobs."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
from shared.flaky_tests import Registry
from shared.flaky_actions import GitHub, pr_repository
from shared.ci_policy import decide
from shared.github_reads import run as github_run

SHA = re.compile(r"^[0-9a-f]{40}$")


def gh_json(args):
    result = github_run(["gh", *args], capture_output=True, text=True, check=False)
    if not result.stdout.strip():
        raise RuntimeError(result.stderr.strip() or f"gh exited {result.returncode}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("invalid gh JSON") from exc


def observe(pr, head, repo=None):
    target = ["--repo", repo] if repo else []
    meta = gh_json(["pr", "view", pr, *target, "--json", "headRefOid,url"])
    actual = meta.get("headRefOid")
    if actual != head:
        return {"result": "head_changed", "head": head, "actual_head": actual, "checks": []}
    checks = gh_json(["pr", "checks", pr, *target, "--json", "name,bucket,state,link,workflow,startedAt,completedAt"])
    if not isinstance(checks, list):
        raise RuntimeError("checks response is not a list")
    # Recheck after reading checks so a concurrent push cannot produce a false result.
    latest = gh_json(["pr", "view", pr, *target, "--json", "headRefOid"]).get("headRefOid")
    if latest != head:
        return {"result": "head_changed", "head": head, "actual_head": latest, "checks": []}
    if not checks:
        state = "no_checks"
    elif any(c.get("bucket") == "pending" for c in checks):
        state = "pending"
    elif any(c.get("bucket") in {"fail", "cancel"} for c in checks):
        state = "failure"
    elif all(c.get("bucket") in {"pass", "skipping"} for c in checks):
        state = "success"
    else:
        raise RuntimeError("unrecognized check bucket")
    return {"result": state, "head": head, "checks": checks, "url": meta.get("url")}


def wait(pr, head, repo=None, timeout=1800, interval=30, no_checks_grace=60,
         clock=time.monotonic, sleep=time.sleep, observer=observe, on_progress=None):
    require_head(head)
    if timeout < 0 or interval <= 0 or no_checks_grace < 0:
        raise ValueError("invalid wait duration")
    start = clock()
    last_reported_elapsed = 0
    last_reported_state = None
    while True:
        result = observer(pr, head, repo)
        elapsed = clock() - start
        if result["result"] in {"head_changed", "failure", "success"}:
            return result | {"elapsed_seconds": round(elapsed, 1)}
        if result["result"] == "no_checks" and elapsed >= no_checks_grace:
            return result | {"elapsed_seconds": round(elapsed, 1)}
        if elapsed >= timeout:
            return result | {"result": "timeout", "last_state": result["result"], "elapsed_seconds": round(elapsed, 1)}
        if on_progress and (result["result"] != last_reported_state or
                            elapsed - last_reported_elapsed >= 60):
            on_progress({"result": result["result"], "head": head,
                         "elapsed_seconds": round(elapsed, 1), "check_count": len(result["checks"])})
            last_reported_elapsed = elapsed
            last_reported_state = result["result"]
        sleep(min(interval, timeout - elapsed))


def require_head(head):
    if not SHA.fullmatch(head):
        raise ValueError("head must be a full lowercase SHA")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--classification-timeout", type=int, default=60)
    parser.add_argument("--no-checks-grace", type=int, default=60)
    args = parser.parse_args()
    if args.classification_timeout <= 0:
        parser.error("classification timeout must be positive")
    try:
        result = wait(args.pr, args.head, args.repo, args.timeout, args.interval,
                      args.no_checks_grace,
                      on_progress=lambda update: print(json.dumps(update, sort_keys=True),
                                                       file=sys.stderr, flush=True))
        if result["result"] in {"success", "failure"}:
            print(json.dumps({"phase": "terminal", "raw_result": result["result"],
                              "head": result["head"], "elapsed_seconds": result.get("elapsed_seconds"),
                              "check_count": len(result["checks"])},
                             sort_keys=True), file=sys.stderr, flush=True)
            repo = pr_repository(result.get("url") or args.pr, args.repo)
            result = decide(GitHub(repo), Registry(repo, Path.cwd(), import_legacy=False), result,
                            lambda: observe(args.pr, args.head, args.repo),
                            classification_timeout=args.classification_timeout,
                            on_progress=lambda update: print(json.dumps(update, sort_keys=True),
                                                             file=sys.stderr, flush=True))
    except (RuntimeError, ValueError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, json.dumps({"result": "error", "message": str(exc)}) + "\n")
    print(json.dumps(result, sort_keys=True))
    if result["result"] not in {"success", "known_flakes", "accepted_exceptions"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
