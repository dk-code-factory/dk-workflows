#!/usr/bin/env python3
"""Guard failed-job reruns and final-head reviewer requests with retained attempts."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import json
import subprocess

import run_state
from completion import reviewer_candidates
from shared import publication
from shared.private_state import mutate, read
from shared.publication_github import api, pages, identity, pr_snapshot
from shared.diagnostics import Diagnostic, emit, preparation_failure


def run_snapshot(pr, run_id):
    host, repo, _ = identity(pr)
    value = api(host, f'repos/{repo}/actions/runs/{run_id}')
    if (not isinstance(value, dict) or str(value.get('id')) != str(run_id)
            or type(value.get('run_attempt')) is not int or not value.get('head_sha')):
        raise ValueError('incomplete Actions run snapshot')
    return value


def rerun_ready(pr, head, run_id, attempt):
    if pr_snapshot(pr)['head']['sha'] != head:
        raise ValueError('PR head changed')
    value = run_snapshot(pr, run_id)
    if (value['head_sha'] != head or value['run_attempt'] != attempt
            or value.get('status') != 'completed' or value.get('conclusion') not in {'failure', 'timed_out'}):
        raise ValueError('run/head/attempt is not the completed failed attempt')
    host, repo, _ = identity(pr)
    jobs = pages(host, f'repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs', 'jobs')
    if not jobs or not any(j.get('conclusion') in {'failure', 'timed_out'} for j in jobs):
        raise ValueError('failed jobs are missing')
    return value


def request_snapshot(pr):
    host, repo, number = identity(pr)
    meta = pr_snapshot(pr)
    reviews = pages(host, f'repos/{repo}/pulls/{number}/reviews')
    pending = pages(host, f'repos/{repo}/pulls/{number}/requested_reviewers', 'users', counted=False)
    if not isinstance(pending, list) or any(not r.get('login') for r in pending):
        raise ValueError('incomplete requested-reviewer list')
    return meta, reviews, [r['login'] for r in pending]


def completion_ready(pr, head, cwd, arguments, record_path=None):
    if not isinstance(arguments, list) or not all(isinstance(x, str) for x in arguments):
        raise ValueError('completion arguments must be an argument array')
    if record_path:
        # Bind the fresh gate to the same budget/run record being mutated.
        selected = []
        for index, argument in enumerate(arguments):
            if argument == '--run-record' and index + 1 < len(arguments):
                selected.append(arguments[index + 1])
            elif argument.startswith('--run-record='):
                selected.append(argument.split('=', 1)[1])
        if len(selected) != 1 or Path(selected[0]).resolve() != Path(record_path).resolve():
            raise ValueError('completion gate must use the active publication run record')
    argv = [sys.executable, str(Path(__file__).with_name('completion.py')),
            '--cwd', cwd, '--pr', pr, 'gate', *arguments]
    result = subprocess.run(argv, capture_output=True, text=True, check=True)
    value = json.loads(result.stdout)
    if not value.get('completion_ready') or value.get('gate') != 'clean' or value.get('ci', {}).get('head') != head:
        raise ValueError('fresh final-head completion gate is not clean')
    return value


def prepare(kind, pr, head, record_path, *, cycle=0, run_id=None, attempt=None,
            reviewer=None, gate_arguments=None, cwd=None):
    run_state.require_head(head)
    host, repo, number = identity(pr)
    record_path = Path(record_path).resolve()
    from shared.repository_scope import inside_checkout
    if inside_checkout(record_path, Path.cwd()):
        raise ValueError('run record must be outside the repository')
    if not record_path.is_file():
        raise ValueError('existing iteration run record required')
    key = publication.digest([kind, pr, head, run_id, attempt, reviewer and reviewer.lower()])
    def reserve(record):
        prepared = run_state.work_gate(record, head)
        if prepared['identity']['pr_url'] != pr:
            raise ValueError('preparation PR differs from publication target')
        if record.get('pr') != number:
            raise ValueError('iteration PR differs from publication target')
        prior = record.setdefault('publication_attempts', {}).get(key)
        if prior:
            publication.load(prior, kind)
            return {'operation': prior, 'reused': True}
        if kind == 'ci-rerun':
            expected = rerun_ready(pr, head, run_id, attempt)
            approved = {'head': head, 'run_id': run_id, 'attempt': attempt}
        else:
            completion_ready(pr, head, str(cwd), gate_arguments, record_path)
            meta, reviews, pending = request_snapshot(pr)
            requested = [x['reviewer'] for x in record.get('review_requests', []) if x['head'] == head]
            if reviewer.lower() not in {r.lower() for r in reviewer_candidates(
                    reviews, pending, meta['user']['login'], head, requested)} or meta['head']['sha'] != head:
                raise ValueError('reviewer is not eligible on the final head')
            expected = {'head': head, 'reviewer': reviewer, 'pending': pending}
            approved = {'cwd': str(cwd), 'gate_arguments': gate_arguments}
        approved['run_record'] = str(record_path)
        value = publication.prepare(kind, pr, expected, approved,
            [sys.executable, Path(__file__).resolve(), kind, 'reconcile'])
        if kind == 'ci-rerun':
            item = run_state.attempt(record, 'rerun', cycle, head, str(run_id))
            item.update(attempt=attempt, operation=value['operation'])
        else:
            record.setdefault('review_requests', []).append(
                {'head': head, 'reviewer': reviewer, 'operation': value['operation']})
        record['publication_attempts'][key] = value['operation']
        return value
    return mutate(record_path, {}, reserve)


def publish(kind, path, reconcile=False):
    def check(value, verified):
        request = value['request']
        pr, expected, approved = request['target'], request['expected'], request['approved']
        head = approved['head'] if kind == 'ci-rerun' else expected['head']
        record = read(Path(approved['run_record']), {})
        prepared = run_state.work_gate(record, head)
        if prepared['identity']['pr_url'] != pr:
            raise ValueError('preparation PR differs from publication target')
        if str(Path(path).resolve()) not in record.get('publication_attempts', {}).values():
            raise ValueError('publication budget reservation is missing')
        if pr_snapshot(pr)['head']['sha'] != head:
            raise ValueError('PR head changed')
        if kind == 'ci-rerun' and not verified:
            rerun_ready(pr, head, approved['run_id'], approved['attempt'])
        if kind == 'request-review':
            completion_ready(pr, head, approved['cwd'], approved['gate_arguments'], approved['run_record'])
            meta, reviews, pending = request_snapshot(pr)
            if not verified and expected['reviewer'].lower() not in {
                    r.lower() for r in reviewer_candidates(reviews, pending, meta['user']['login'], head)}:
                raise ValueError('reviewer eligibility changed')

    def send(value):
        request = value['request']
        host, repo, number = identity(request['target'])
        if kind == 'ci-rerun':
            return api(host, f'repos/{repo}/actions/runs/{request["approved"]["run_id"]}/rerun-failed-jobs', method='POST')
        return api(host, f'repos/{repo}/pulls/{number}/requested_reviewers',
                   payload={'reviewers': [request['expected']['reviewer']]}, method='POST')

    def observe(value):
        request = value['request']
        if kind == 'ci-rerun':
            approved = request['approved']
            live = run_snapshot(request['target'], approved['run_id'])
            if live['head_sha'] != approved['head'] or live['run_attempt'] != approved['attempt'] + 1:
                raise ValueError('the new Actions attempt has not been observed unambiguously')
            return {'run_id': live['id'], 'head': live['head_sha'], 'attempt': live['run_attempt']}
        meta, reviews, pending = request_snapshot(request['target'])
        if meta['head']['sha'] != request['expected']['head']:
            raise ValueError('PR head changed before the review request was confirmed')
        reviewer = request['expected']['reviewer']
        # A reviewer can respond before reconciliation; that submitted review is
        # equally authoritative evidence that no repeat notification is needed.
        answered = any(r.get('user', {}).get('login', '').lower() == reviewer.lower()
                       and r.get('commit_id') == request['expected']['head']
                       and r.get('state') in {'APPROVED', 'CHANGES_REQUESTED', 'COMMENTED'} for r in reviews)
        if reviewer.lower() not in {r.lower() for r in pending} and not answered:
            raise ValueError('review request is not confirmed; inspect retained attempt')
        return {'head': request['expected']['head'], 'reviewer': reviewer, 'answered': answered}
    return publication.execute(path, kind, check=check, send=send, observe=observe, reconcile=reconcile)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pr')
    p.add_argument('--cwd', default='.')
    p.add_argument('kind', choices=('ci-rerun', 'request-review'))
    sub = p.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--head', required=True)
    prep.add_argument('--run-record', required=True)
    prep.add_argument('--cycle', type=int, default=0)
    prep.add_argument('--run-id')
    prep.add_argument('--attempt', type=int)
    prep.add_argument('--reviewer')
    prep.add_argument('--gate-arguments', help='JSON array of native completion gate arguments')
    for name in ('publish', 'reconcile'):
        sub.add_parser(name).add_argument('--operation', required=True)
    args = p.parse_args()
    if args.command == 'prepare':
        if not args.pr or args.kind == 'ci-rerun' and (not args.run_id or not args.attempt):
            p.error('prepare requires --pr; ci-rerun also requires --run-id and --attempt')
        if args.kind == 'request-review' and (not args.reviewer or not args.gate_arguments):
            p.error('request-review requires --reviewer and --gate-arguments')
        result = prepare(args.kind, args.pr, args.head, args.run_record, cycle=args.cycle,
            run_id=args.run_id, attempt=args.attempt, reviewer=args.reviewer,
            gate_arguments=json.loads(Path(args.gate_arguments).read_text()) if args.gate_arguments else None,
            cwd=str(Path(args.cwd).resolve()))
    else:
        publication.require_target(args.operation, args.kind, args.pr)
        result = publish(args.kind, args.operation, args.command == 'reconcile')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Diagnostic as exc:
        emit(exc); sys.exit(1)
    except (ValueError, TypeError, RuntimeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        emit(preparation_failure(exc, sys.argv))
        print(str(exc), file=sys.stderr); sys.exit(1)
