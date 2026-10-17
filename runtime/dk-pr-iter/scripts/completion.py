#!/usr/bin/env python3
"""Private completion prompt configuration and pure safety decisions."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
import sys
import importlib.util

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
from shared.discovery_cache import repository
from shared.repository_scope import identities
from shared import series_assessment
from shared import review_coverage

_spec = importlib.util.spec_from_file_location('completion_preparation',
    next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir()) / 'dk-pr-decisions/scripts/preparation.py')
preparation_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preparation_api)
_state_spec = importlib.util.spec_from_file_location('completion_run_state',
    Path(__file__).resolve().with_name('run_state.py'))
run_state_api = importlib.util.module_from_spec(_state_spec)
_state_spec.loader.exec_module(run_state_api)

DEFAULT = Path(__file__).resolve().parents[1] / "references" / "default-completion.md"


def live_preparation_scope(pr, cwd):
    return json.loads(subprocess.check_output(
        ['gh', 'pr', 'view', pr, '--json', 'body,headRefOid'], text=True, cwd=cwd))


def current_preparation(path, run_record, pr_url, head, body):
    prepared = preparation_api.gate(path, pr_url=pr_url, head_sha=head, body=body)
    if prepared['mode'] != 'iteration':
        raise ValueError('completion requires iteration preparation')
    record = json.loads(Path(run_record).read_text(encoding='utf-8'))
    if not isinstance(record, dict):
        raise ValueError('iteration run record must be an object')
    current = run_state_api.work_gate(record, head)
    if (current['preparation_id'] != prepared['preparation_id']
            or current['identity']['pr_url'] != pr_url):
        raise ValueError('completion preparation differs from the active run record')
    return prepared


def data_root():
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    if not base.is_absolute():
        raise ValueError("XDG_DATA_HOME must be absolute")
    return base / "dk-pr-iter" / "completion-prompts"


def override_path(scope, cwd, identity=None):
    base = data_root()
    if scope == "user":
        return base / "user.txt"
    if scope != "repo":
        raise ValueError("scope must be repo or user")
    if identity is None:
        _, identity = repository(cwd)
    return base / "repositories" / (hashlib.sha256(identity.encode()).hexdigest()[:24] + ".txt")


def valid_prompt(value):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("completion prompt must be nonempty UTF-8 text without NUL bytes")
    return value


def read_override(path):
    if not path.exists():
        return None
    try:
        return valid_prompt(path.read_text(encoding="utf-8"))
    except UnicodeError as exc:
        raise ValueError(f"malformed completion prompt at {path}: {exc}") from exc


def selected(cwd, pr=None):
    root, _ = repository(cwd)
    for index, identity in enumerate(identities(root, pr)):
        value = read_override(override_path("repo", root, identity))
        if value is not None:
            return ("repo" if index == 0 else "upstream"), value
    value = read_override(override_path("user", root))
    if value is not None:
        return "user", value
    return "default", valid_prompt(DEFAULT.read_text(encoding="utf-8"))


def set_override(path, value):
    valid_prompt(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".completion-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def completion_gate(start_head, final_head, live_head, review_complete, actionable, ci_result,
                    ci_verifier=None, *, stage='complete', assessment=None, series_snapshot=None,
                    preparation=None, pr_url=None, body=None, verification=None, run_record=None):
    if stage not in {'review', 'complete'}:
        raise ValueError('stage must be review or complete')
    if not all(isinstance(x, str) and len(x) == 40 and all(c in "0123456789abcdef" for c in x)
               for x in (start_head, final_head, live_head)):
        raise ValueError("heads must be full lowercase SHAs")
    if live_head != final_head:
        return "head_changed"
    if not preparation or not pr_url:
        return 'preparation_missing'
    if not run_record:
        return 'run_record_missing'
    try:
        prepared = current_preparation(preparation, run_record, pr_url, final_head, body)
    except (ValueError, KeyError, TypeError, OSError):
        return 'preparation_invalid'
    if not verification:
        return 'engineering_verification_missing'
    try:
        preparation_api.verify_completion(verification, prepared)
    except (ValueError, KeyError, TypeError, OSError):
        return 'engineering_verification_invalid'
    if not review_complete:
        return "review_incomplete"
    try:
        review_coverage.verify(run_record, prepared)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        return 'review_coverage_invalid'
    if actionable:
        return "actionable_work"
    if isinstance(ci_result, dict):
        if ci_result.get('result') in {'known_flakes', 'accepted_exceptions'} or ci_verifier is not None:
            if ci_verifier is None:
                return "ci_not_green"
            ci_result = ci_verifier()
        if (ci_result.get('head') != final_head or ci_result.get('blockers') or
                ci_result.get('result') not in {'success', 'known_flakes', 'accepted_exceptions'}):
            return "ci_not_green"
        if ci_result.get('result') in {'known_flakes', 'accepted_exceptions'} and (
                ci_result.get('raw_result') != 'failure' or not ci_result.get('ignored_failures')
                or not ci_result.get('verified_at')):
            return "ci_not_green"
    elif ci_result != "success":
        return "ci_not_green"
    if stage == 'complete':
        return series_assessment.check(assessment, series_snapshot, final_head)
    return "clean"


def thread_action(thread, fully_answered, response_needed, proposed_reply, own_login):
    """Classify a thread using a verified reply reference in the legacy proposed_reply slot."""
    if thread.get("isResolved") is True:
        return "complete"
    if thread.get("isResolved") is not False:
        raise ValueError("thread resolution state is missing")
    conversation = thread.get("comments")
    if not isinstance(conversation, list) or not conversation:
        raise ValueError("thread conversation is incomplete")
    if not fully_answered:
        return "leave_open"
    # A semantic assessment supplies a verified URL and the exact normalized reply
    # text. Proposed wording (or mere comment presence) cannot prove an answer exists.
    if (isinstance(proposed_reply, dict) and isinstance(proposed_reply.get('url'), str) and
            proposed_reply['url'].startswith('https://')):
        matches = [c for c in conversation[1:] if c.get('url') == proposed_reply.get('url') and
                   c.get('text', c.get('body')) == proposed_reply.get('text') and
                   isinstance(proposed_reply.get('text'), str) and proposed_reply['text'].strip()]
        if len(matches) == 1:
            comment = matches[0]
            later = conversation[conversation.index(comment) + 1:]
            assessments = proposed_reply.get('laterResponses', [])
            covered = {r.get('source') for r in assessments if r.get('disposition') == 'no_objection' and
                       isinstance(r.get('reason'), str) and r['reason'].strip()}
            if all(c.get('reference') and c['reference'] in covered for c in later):
                return 'resolve'
    return 'reply_then_resolve'


def reviewer_candidates(reviews, pending, pr_author, final_head, requested_at_head=()):
    """Return unique eligible reviewer logins from complete submitted-review data."""
    if not isinstance(reviews, list) or not isinstance(pending, (list, tuple, set)):
        raise ValueError("reviews and pending reviewers must be complete lists")
    blocked = {str(x).lower() for x in pending} | {str(x).lower() for x in requested_at_head} | {pr_author.lower()}
    submitted = {}
    for review in reviews:
        user = review.get("user") or {}
        login = user.get("login")
        if review.get("state") not in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"} or not login:
            continue
        key = login.lower()
        entry = submitted.setdefault(key, {"login": login, "on_final_head": False})
        entry["on_final_head"] |= review.get("commit_id") == final_head
    return [entry["login"] for key, entry in submitted.items() if key not in blocked and not entry["on_final_head"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--pr", help="PR URL for fork then upstream completion settings")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    gate = sub.add_parser("gate", help="fresh one-shot CI and completion check after the waiter exits")
    gate.add_argument('--start-head', required=True)
    gate.add_argument('--final-head', required=True)
    gate.add_argument('--review-complete', action='store_true')
    gate.add_argument('--actionable', action='store_true')
    gate.add_argument('--stage', choices=('review', 'complete'), default='complete')
    gate.add_argument('--series-assessment', help='Verified cleanup artifact outside the repository')
    gate.add_argument('--preparation-file', required=True)
    gate.add_argument('--verification-file', required=True)
    gate.add_argument('--run-record', required=True, help='Current mutable iteration run record')
    for name in ("set", "reset"):
        command = sub.add_parser(name)
        command.add_argument("scope", choices=("repo", "user"))
        if name == "set":
            command.add_argument("--file", required=True, help="UTF-8 prompt file; '-' reads stdin")
    args = parser.parse_args()
    try:
        cwd = Path(args.cwd).resolve()
        if args.command == 'gate':
            if not args.pr:
                raise ValueError('--pr is required for the live completion gate')
            live_scope = live_preparation_scope(args.pr, cwd)
            current_preparation(args.preparation_file, args.run_record, args.pr,
                                live_scope['headRefOid'], live_scope['body'])
            from ci_wait import observe
            from shared.flaky_actions import GitHub, pr_repository
            from shared.flaky_tests import Registry
            from shared.ci_policy import decide
            assessment = None
            if args.stage == 'complete':
                if not args.series_assessment:
                    raise ValueError('--series-assessment is required for completion')
                assessment = series_assessment.load(args.series_assessment, cwd)
                reviewed_tree = series_assessment.command(
                    cwd, 'git', 'rev-parse', f"{assessment['reviewed_head']}^{{tree}}")
                if reviewed_tree != assessment['reviewed_tree']:
                    raise ValueError('series assessment reviewed tree is incorrect')
            snapshot = observe(args.pr, args.final_head)
            repo = pr_repository(args.pr)
            verify = lambda: decide(GitHub(repo), Registry(repo, cwd),
                                    observe(args.pr, args.final_head),
                                    lambda: observe(args.pr, args.final_head))
            # Red CI needs live verification, never the saved effective label.
            ci_result = {'result': 'known_flakes'} if snapshot['result'] == 'failure' else snapshot
            decision = {}
            def verified():
                decision.update(verify())
                return decision
            result = completion_gate(args.start_head, args.final_head,
                                     snapshot.get('actual_head', snapshot['head']),
                                     args.review_complete, args.actionable, ci_result, verified,
                                     stage='review', preparation=args.preparation_file,
                                     pr_url=args.pr, body=live_scope['body'], verification=args.verification_file,
                                     run_record=args.run_record)
            if result == 'clean' and args.stage == 'complete':
                # Observe series after the CI policy's live provenance checks.
                live = series_assessment.live_snapshot(args.pr, cwd)
                result = series_assessment.check(assessment, live, args.final_head)
            output = {'gate': result, 'ci': decision or snapshot}
            if result in {'clean', 'review_coverage_invalid'}:
                try:
                    prepared = current_preparation(args.preparation_file, args.run_record, args.pr,
                                                   live_scope['headRefOid'], live_scope['body'])
                    output['review_coverage'] = review_coverage.verify(args.run_record, prepared)
                except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
                    output['review_limitation'] = str(exc)
                    result = output['gate'] = 'review_coverage_invalid'
            print(json.dumps(output, sort_keys=True))
            if result != 'clean':
                raise SystemExit(1)
            return
        identity = identities(cwd, args.pr)[0] if args.pr else None
        if args.command == "set":
            content = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
            set_override(override_path(args.scope, cwd, identity), content)
        elif args.command == "reset":
            override_path(args.scope, cwd, identity).unlink(missing_ok=True)
        scope, prompt = selected(cwd, args.pr)
        print(json.dumps({"source": scope, "prompt": prompt}, ensure_ascii=False))
    except (OSError, UnicodeError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        parser.exit(2, f"completion: {exc}\n")


if __name__ == "__main__":
    main()
