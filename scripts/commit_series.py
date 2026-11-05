#!/usr/bin/env python3
"""Read pinned commit-series metadata as JSON; never fetch or change Git state."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def inspect_series(checkout, target, head):
    checkout = Path(checkout).resolve(strict=True)
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_REPLACE_OBJECTS': '1',
           'GIT_NO_LAZY_FETCH': '1', 'GIT_TERMINAL_PROMPT': '0'}

    def git(*args, input=None):
        result = subprocess.run(['git', '--no-lazy-fetch', '--no-pager', '-C', str(checkout), *args],
                                input=input, capture_output=True, env=env)
        if result.returncode:
            detail = result.stderr.decode('utf-8', 'replace').strip()
            raise ValueError(detail or ('No merge base; complete connected history is required'
                                       if args[0] == 'merge-base' else 'Git inspection failed'))
        return result.stdout

    def resolve(value):
        return git('rev-parse', '--verify', '--end-of-options', value+'^{commit}').decode().strip()

    # Git's repository-local environment must not redirect the explicit checkout.
    for name in git('rev-parse', '--local-env-vars').decode().splitlines():
        env.pop(name, None)
    env.pop('GIT_NAMESPACE', None)
    env.update({'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_GRAFT_FILE': os.devnull})
    if git('rev-parse', '--is-shallow-repository').strip() == b'true':
        raise ValueError('Complete history is required; shallow checkout cannot be inspected')
    target_sha, head_sha = resolve(target), resolve(head)
    bases = git('merge-base', '--all', target_sha, head_sha).decode().splitlines()
    if len(bases) != 1:
        raise ValueError('Commit inspection requires a unique merge base')
    base = bases[0]
    commits = git('rev-list', '--topo-order', '--reverse', base+'..'+head_sha).decode().splitlines()
    metadata = {}
    if commits:
        data = git('log', '--no-walk=unsorted', '--no-decorate', '--no-notes',
                   '--no-show-signature', '--encoding=UTF-8', '-z', '--format=%H%x00%P%x00%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI%x00%B',
                   '--stdin', input=('\n'.join(commits)+'\n').encode())
        fields = data.decode('utf-8', 'surrogateescape').split('\0')
        if fields.pop() != '' or len(fields) != 9*len(commits):
            raise ValueError('Unexpected Git metadata framing')
        for offset in range(0, len(fields), 9):
            oid, parents, an, ae, at, cn, ce, ct, message = fields[offset:offset+9]
            paths = git('diff-tree', '--root', '-m', '--no-commit-id', '-r', '--no-renames',
                        '--name-only', '-z', oid, '--').decode('utf-8', 'surrogateescape').split('\0')
            metadata[oid] = {'oid': oid, 'parents': parents.split(), 'message': message,
                'author': {'name': an, 'email': ae, 'date': at},
                'committer': {'name': cn, 'email': ce, 'date': ct},
                'changed_paths': sorted(set(p for p in paths if p))}
        if set(metadata) != set(commits):
            raise ValueError('Incomplete commit metadata')
    return {'schema_version': 1, 'checkout': str(checkout), 'target': target_sha,
            'head': head_sha, 'merge_base': base,
            'reviewed_tree': git('rev-parse', head_sha+'^{tree}').decode().strip(),
            'commits': [metadata[oid] for oid in commits]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--head', required=True)
    args = parser.parse_args()
    try:
        value = inspect_series(args.checkout, args.target, args.head)
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
