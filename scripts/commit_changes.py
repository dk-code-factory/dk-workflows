#!/usr/bin/env python3
"""Capture local commit candidates without changing the index or worktree."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def inspect_changes(checkout):
    checkout = Path(checkout).resolve(strict=True)
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}

    def git(*args, missing_ok=False):
        result = subprocess.run(['git', '--no-lazy-fetch', '--no-pager', '-C', str(checkout),
                                 *args], capture_output=True, env=env)
        if missing_ok and result.returncode == 1:
            return None
        if result.returncode:
            raise ValueError(result.stderr.decode('utf-8', 'replace').strip()
                             or 'Git inspection failed')
        return result.stdout.decode('utf-8', 'surrogateescape')

    for name in git('rev-parse', '--local-env-vars').splitlines():
        env.pop(name, None)
    env.pop('GIT_NAMESPACE', None)
    env.update({'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_GRAFT_FILE': os.devnull})
    if git('rev-parse', '--is-inside-work-tree').strip() != 'true':
        raise ValueError('Commit preparation requires a worktree')
    checkout = Path(git('rev-parse', '--show-toplevel').removesuffix('\n'))

    def capture():
        head = git('rev-parse', '--verify', '--quiet', 'HEAD^{commit}', missing_ok=True)
        if head is None:
            git('symbolic-ref', '--quiet', 'HEAD')
            if git('rev-parse', '--verify', '--quiet', 'HEAD', missing_ok=True) is not None:
                raise ValueError('HEAD must reference a commit or an unborn branch')
        else:
            head = head.strip()
        if git('ls-files', '--unmerged', '-z'):
            raise ValueError('Resolve index conflicts before commit preparation')
        flags = ['--no-color', '--no-ext-diff', '--no-textconv', '--no-renames', '--binary',
                 '--full-index', '--ignore-submodules=none', '--no-relative']
        return {'head': head,
                'status': git('-c', 'status.renames=false', 'status', '--porcelain=v1',
                              '-z', '--untracked-files=all').split('\0')[:-1],
                'staged_patch': git('diff', *flags, '--cached', *([head] if head else []), '--'),
                'unstaged_patch': git('diff', *flags, '--'),
                'untracked_paths': git('ls-files', '--others', '--exclude-standard',
                                       '-z').split('\0')[:-1]}

    first = capture()
    if first != capture():
        raise ValueError('Local changes moved during capture; inspect again before selection')
    return {'checkout': str(checkout), **first}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, required=True)
    args = parser.parse_args()
    try:
        value = inspect_changes(args.checkout)
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
