#!/usr/bin/env python3
"""Retain branch/rebase recovery and publish an exact candidate with a lease."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import json
import subprocess
import uuid
from urllib.parse import urlparse

import run_state
from shared import publication, workflow_storage
from shared.private_state import read, write, mutate
from shared.publication_github import pr_snapshot
from shared.diagnostics import Diagnostic, emit, task, preparation_failure


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


def remote_identity(url):
    if '://' not in url and ':' in url and not url.startswith('/'):
        host, path = url.split(':', 1)
        host = host.rsplit('@', 1)[-1]
    else:
        parsed = urlparse(url)
        host, path = parsed.hostname, parsed.path
    parts = path.strip('/').removesuffix('.git').split('/')
    if not host or len(parts) != 2 or not all(parts):
        raise ValueError('remote must identify one exact GitHub repository')
    return host.lower(), '/'.join(parts).lower()


def remote(cwd, name, expected):
    fetch = git(cwd, 'remote', 'get-url', name)
    urls = git(cwd, 'remote', 'get-url', '--push', '--all', name).splitlines()
    if len(urls) != 1 or any(remote_identity(url) != expected for url in [fetch, *urls]):
        raise ValueError('remote identity does not match the PR repository')
    return {'fetch': fetch, 'push': urls[0]}


def tip(cwd, url, branch):
    lines = git(cwd, 'ls-remote', '--heads', url, 'refs/heads/' + branch).splitlines()
    if len(lines) != 1 or lines[0].split()[1] != 'refs/heads/' + branch:
        raise ValueError('remote branch is missing or ambiguous')
    return lines[0].split()[0]


def clean(cwd, *artifacts):
    if git(cwd, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise Diagnostic('local_changes', 'Local changes need engineering assessment before rebasing.',
            task('Assess staged, unstaged and untracked changes against PR scope and Decisions. '
                 'Preserve unique work and produce a clean prepared branch before continuing.', *artifacts))


def validate(state, candidate=False):
    cwd = state['cwd']
    clean(cwd)
    if git(cwd, 'symbolic-ref', '--short', 'HEAD') != state['branch']:
        raise ValueError('prepared local branch changed')
    live = pr_snapshot(state['pr'])
    expected = state['snapshot']
    for field in ('title', 'body', 'head', 'base'):
        if field in ('head', 'base'):
            if live[field]['repo']['full_name'].lower() != expected[field]['repo']['full_name'].lower():
                raise ValueError('PR repository identity changed')
            for key in ('ref', 'sha'):
                if live[field][key] != expected[field][key]:
                    raise ValueError('PR head or target tip changed; refresh preflight')
        elif live[field] != expected[field]:
            raise ValueError('PR scope changed; refresh preparation')
    for field, side in [('head_remote', 'head'), ('base_remote', 'base')]:
        selected = state[field]
        identity = (urlparse(state['pr']).hostname.lower(), expected[side]['repo']['full_name'].lower())
        if remote(cwd, selected['name'], identity) != selected['urls']:
            raise ValueError('remote URL changed')
        url = selected['urls']['push' if side == 'head' else 'fetch']
        if tip(cwd, url, expected[side]['ref']) != expected[side]['sha']:
            raise ValueError('remote head or target tip changed')
    prepared = run_state.work_gate(read(Path(state['run_record']), {}), expected['head']['sha'])
    if prepared['identity']['pr_url'] != state['pr']:
        raise ValueError('preparation PR differs from branch publication target')
    if candidate and git(cwd, 'rev-parse', 'HEAD') != state['candidate']:
        raise ValueError('candidate changed; prepare again')


def prepare(pr, cwd, record, head_remote='origin', base_remote='origin'):
    clean(cwd)
    live = pr_snapshot(pr)
    host = urlparse(pr).hostname.lower()
    remotes = {side: remote(cwd, name, (host, live[side]['repo']['full_name'].lower()))
               for side, name in [('head', head_remote), ('base', base_remote)]}
    original = git(cwd, 'rev-parse', 'HEAD')
    branch = git(cwd, 'symbolic-ref', '--short', 'HEAD')
    if branch != live['head']['ref']:
        raise ValueError('checkout must be the PR head branch')
    directory = workflow_storage.allocate(repository=cwd)
    prefix = 'refs/dk-recovery/' + uuid.uuid4().hex
    state = {'schema_version': 1, 'pr': pr, 'cwd': str(cwd), 'branch': branch,
             'snapshot': live, 'original_head': original, 'target_tip': live['base']['sha'],
             'run_record': str(Path(record).resolve()), 'status': 'prepared',
             'head_remote': {'name': head_remote, 'urls': remotes['head']},
             'base_remote': {'name': base_remote, 'urls': remotes['base']},
             'recovery_refs': {'original': prefix + '/original', 'target': prefix + '/target',
                               'candidate': prefix + '/candidate'}}
    validate(state)
    for side in ('head', 'base'):
        git(cwd, 'fetch', '--no-tags', remotes[side]['fetch'], 'refs/heads/' + live[side]['ref'])
    git(cwd, 'merge-base', '--is-ancestor', live['head']['sha'], original)
    git(cwd, 'update-ref', state['recovery_refs']['original'], original)
    git(cwd, 'update-ref', state['recovery_refs']['target'], state['target_tip'])
    path = directory / 'branch.json'
    write(path, state)
    return {'state': str(path), **state}


def rebase(path, recover=False):
    path = Path(path).resolve()
    workflow_storage.owned(path)
    state = read(path, {})
    cwd = state['cwd']
    if state['status'] == 'prepared' and not recover:
        validate(state)
        if git(cwd, 'rev-parse', 'HEAD') != state['original_head']:
            raise ValueError('local head changed before rebase')
        state['status'] = 'rebasing'
        write(path, state)
        try:
            git(cwd, 'rebase', state['target_tip'])
        except subprocess.CalledProcessError as exc:
            raise Diagnostic('rebase_conflicts', 'Rebase needs engineering resolution: ' + (exc.stdout or '') + (exc.stderr or ''),
                task('Resolve conflicts against PR scope and Decisions, validate the result, finish the rebase, '
                     'then run branch-publish recover --state ' + str(path), path)) from exc
    elif state['status'] not in {'rebasing', 'rebased'}:
        raise ValueError('no interrupted rebase to recover')
    for directory in ('rebase-merge', 'rebase-apply'):
        if Path(git(cwd, 'rev-parse', '--path-format=absolute', '--git-path', directory)).exists():
            raise Diagnostic('rebase_conflicts', 'Rebase is still in progress.',
                task('Resolve and validate the rebase, then recover the retained branch state.', path))
    validate(state)
    candidate = git(cwd, 'rev-parse', 'HEAD')
    git(cwd, 'merge-base', '--is-ancestor', state['target_tip'], candidate)
    git(cwd, 'update-ref', state['recovery_refs']['candidate'], candidate)
    if state.get('operation'):
        if state.get('candidate') != candidate:
            raise ValueError('candidate differs from retained publication')
        return {'state': str(path), 'operation': state['operation']}
    state.update(status='rebased', candidate=candidate)
    value = publication.prepare('branch-publish', state['pr'], state,
        {'candidate': candidate, 'tree': git(cwd, 'rev-parse', 'HEAD^{tree}')},
        [sys.executable, Path(__file__).resolve(), 'reconcile'], directory=path.parent)
    def register(record):
        record.setdefault('publication_attempts', {})[value['operation_id']] = value['operation']
    mutate(Path(state['run_record']), {}, register)
    state['operation'] = value['operation']
    write(path, state)
    return {'state': str(path), **value}


def publish(path, reconcile=False):
    def check(value, verified):
        state = value['request']['expected']
        record = read(Path(state['run_record']), {})
        if str(Path(path).resolve()) not in record.get('publication_attempts', {}).values():
            raise ValueError('branch publication intent is not registered in the run record')
        if not verified:
            validate(state, True)
        else:
            # The published head needs its own fresh preparation before subsequent
            # work; receipt verification retains the original prepublication gate.
            live = pr_snapshot(state['pr'])
            record = read(Path(state['run_record']), {})
            prepared_head = record.get('preparation', {}).get('head', state['snapshot']['head']['sha'])
            if prepared_head not in {state['snapshot']['head']['sha'], state['candidate']}:
                raise ValueError('current preparation no longer covers the published branch')
            prepared = run_state.work_gate(record, prepared_head)
            if prepared['identity']['pr_url'] != state['pr']:
                raise ValueError('preparation PR differs from branch publication target')
            if (live['head']['sha'] != state['candidate'] or live['base']['sha'] != state['target_tip']
                    or any(live[side]['ref'] != state['snapshot'][side]['ref']
                           or live[side]['repo']['full_name'].lower() != state['snapshot'][side]['repo']['full_name'].lower()
                           for side in ('head', 'base'))
                    or live['body'] != state['snapshot']['body'] or live['title'] != state['snapshot']['title']):
                raise ValueError('published head, target or scope changed')
    def send(value):
        state = value['request']['expected']
        result = git(state['cwd'], 'push',
            '--force-with-lease=refs/heads/' + state['snapshot']['head']['ref'] + ':' + state['snapshot']['head']['sha'],
            state['head_remote']['urls']['push'], state['candidate'] + ':refs/heads/' + state['snapshot']['head']['ref'])
        return {'stdout': result}
    def observe(value):
        state = value['request']['expected']
        remote_sha = tip(state['cwd'], state['head_remote']['urls']['push'], state['snapshot']['head']['ref'])
        if remote_sha != state['candidate']:
            raise ValueError('remote candidate is not confirmed')
        return {'head': remote_sha, 'target_tip': state['target_tip'], 'recovery_refs': state['recovery_refs']}
    return publication.execute(path, 'branch-publish', check=check, send=send, observe=observe, reconcile=reconcile)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pr')
    p.add_argument('--cwd', default='.')
    sub = p.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--run-record', required=True)
    prep.add_argument('--head-remote', default='origin')
    prep.add_argument('--base-remote', default='origin')
    for name in ('rebase', 'recover'):
        sub.add_parser(name).add_argument('--state', required=True)
    for name in ('publish', 'reconcile'):
        sub.add_parser(name).add_argument('--operation', required=True)
    args = p.parse_args()
    if args.command == 'prepare':
        if not args.pr:
            p.error('prepare requires --pr')
        value = prepare(args.pr, Path(args.cwd).resolve(), args.run_record, args.head_remote, args.base_remote)
    elif args.command in ('rebase', 'recover'):
        if args.pr and read(Path(args.state), {}).get('pr', '').lower() != args.pr.lower():
            raise ValueError('common PR context conflicts with the retained rebase target')
        value = rebase(args.state, args.command == 'recover')
    else:
        publication.require_target(args.operation, 'branch-publish', args.pr)
        value = publish(args.operation, args.command == 'reconcile')
    print(json.dumps(value, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Diagnostic as exc:
        emit(exc); sys.exit(1)
    except (ValueError, TypeError, RuntimeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        emit(preparation_failure(exc, sys.argv))
        print(str(exc), file=sys.stderr); sys.exit(1)
