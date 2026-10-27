#!/usr/bin/env python3
"""Prepare, publish and reconcile reviewed PR title/body edits."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import json
import subprocess

from shared.review_loop import require_complete
from shared import publication, workflow_storage
from shared.private_state import atomic_text
from shared.diagnostics import Diagnostic, emit, preparation_failure
from shared.github_reads import run
from shared.publication_github import identity


def snapshot(pr):
    identity(pr)
    proc = run(['gh', 'pr', 'view', pr, '--json', 'url,headRefOid,title,body'],
               capture_output=True, text=True, check=True)
    value = json.loads(proc.stdout)
    if (not isinstance(value, dict) or not isinstance(value.get('url'), str) or value['url'].lower() != pr.lower()
            or not all(isinstance(value.get(k), str) for k in ('headRefOid', 'title', 'body'))):
        raise ValueError('incomplete PR snapshot')
    return value


def prepare(pr, token, title_file=None, body_file=None):
    require_complete(token, 'dk-pr-description')
    fields = {}
    if title_file:
        title = Path(title_file).read_text().strip()
        if not title or '\n' in title or '\r' in title:
            raise ValueError('a single-line title is required')
        fields['title'] = title
    if body_file:
        fields['body'] = Path(body_file).read_text()
    if not fields:
        raise ValueError('at least one of --title-file or --body-file is required')
    expected = snapshot(pr)
    directory = workflow_storage.allocate(repository=Path.cwd())
    body_artifact = directory / 'reviewed-body.txt' if 'body' in fields else None
    if body_artifact:
        atomic_text(body_artifact, fields['body'])
    return publication.prepare('edit-pr', pr, expected,
        {'fields': fields, 'hashes': {k: publication.digest(v) for k, v in fields.items()},
         'description_review_state': token, 'body_artifact': str(body_artifact) if body_artifact else None},
        [sys.executable, Path(__file__).resolve(), '--reconcile'], directory=directory)


def publish(path, reconcile=False):
    def desired(value):
        request = value['request']
        return {**request['expected'], **request['approved']['fields']}

    def check(value, verified):
        approved = value['request']['approved']
        require_complete(approved['description_review_state'], 'dk-pr-description')
        if approved['hashes'] != {k: publication.digest(v) for k, v in approved['fields'].items()}:
            raise ValueError('reviewed output changed; prepare again')
        if not verified and 'body' in approved['fields']:
            body = Path(approved['body_artifact'])
            if body.resolve().parent != Path(path).resolve().parent:
                raise ValueError('captured body must stay with the prepared operation')
            if not body.exists():
                # Rebuild a missing transport file from the captured approved text.
                atomic_text(body, approved['fields']['body'])
            if body.read_text() != approved['fields']['body']:
                raise ValueError('captured body artifact changed; fresh preparation required')
        live = snapshot(value['request']['target'])
        if live != (desired(value) if verified else value['request']['expected']):
            raise ValueError('PR head, title or body changed; fresh preparation required')

    def send(value):
        request = value['request']
        argv = ['gh', 'pr', 'edit', request['target']]
        fields = request['approved']['fields']
        if 'title' in fields:
            argv += ['--title', fields['title']]
        if 'body' in fields:
            argv += ['--body-file', request['approved']['body_artifact']]
        result = subprocess.run(argv, capture_output=True, text=True, check=True)
        return {'stdout': result.stdout, 'stderr': result.stderr}

    def observe(value):
        live = snapshot(value['request']['target'])
        if live != desired(value):
            raise ValueError('live PR does not exactly match the prepared edit')
        return live

    return publication.execute(path, 'edit-pr', check=check, send=send, observe=observe, reconcile=reconcile)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-state')
    parser.add_argument('--title-file', type=Path)
    parser.add_argument('--body-file', type=Path)
    parser.add_argument('--prepare', action='store_true', help='Retain reviewed inputs without editing')
    parser.add_argument('--operation', help='Prepared edit returned by --prepare')
    parser.add_argument('--reconcile', action='store_true', help='Read-only verification; never repeat an edit')
    parser.add_argument('pr', nargs='?', help='full PR URL')
    args = parser.parse_args()
    if args.operation:
        if args.prepare or args.title_file or args.body_file or args.review_state or args.pr:
            raise ValueError('a retained operation owns its inputs; omit draft files and PR')
        result = publish(args.operation, args.reconcile)
    else:
        if args.reconcile or not args.pr or not args.review_state:
            raise ValueError('prepare requires PR and --review-state; reconciliation requires --operation')
        result = prepare(args.pr, args.review_state, args.title_file, args.body_file)
        if not args.prepare:
            result = publish(result['operation'])
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Diagnostic as exc:
        emit(exc)
        sys.exit(1)
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        emit(preparation_failure(exc, sys.argv))
        print(f'pr edit: {exc}', file=sys.stderr)
        sys.exit(1)
