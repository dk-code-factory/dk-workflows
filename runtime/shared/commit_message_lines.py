#!/usr/bin/env python3
"""Check draft or branch message lines using the owning skill's fallback limit."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import skill_config

MAX_LENGTH = skill_config.defaults('dk-workflows:dk-commit-message')['max_line_length']


def git(cwd, *args):
    result = subprocess.run(['git', *args], cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or 'git failed')
    return result.stdout


def overlong_lines(message, label, max_length=MAX_LENGTH):
    return [f'{label}: line {number} has {len(line)} characters (maximum {max_length})'
            for number, line in enumerate(message.splitlines(), 1) if len(line) > max_length]


def validate_commits(commits, cwd, max_length=MAX_LENGTH):
    errors = []
    for commit in commits:
        message = git(cwd, 'show', '-s', '--format=%B', commit)
        errors.extend(overlong_lines(message, commit[:12], max_length))
    return errors


def commits_since(base, cwd):
    base_sha = git(cwd, 'rev-parse', '--verify', f'{base}^{{commit}}').strip()
    ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', base_sha, 'HEAD'],
                              cwd=cwd, capture_output=True)
    if ancestor.returncode:
        raise ValueError('base must be an ancestor of HEAD')
    commits = git(cwd, 'rev-list', '--reverse', f'{base_sha}..HEAD').splitlines()
    if not commits:
        raise ValueError('no commits between the base and HEAD')
    return commits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--file', type=Path, help='Draft commit message file')
    source.add_argument('--base', help='Check every commit from BASE (exclusive) through HEAD')
    parser.add_argument('--cwd', type=Path, default=Path('.'))
    parser.add_argument('--pr')
    parser.add_argument('--options', type=Path, help='Invocation-local message options JSON')
    parser.add_argument('--upstream')
    parser.add_argument('--max-length', type=int, help='Explicit policy or repository limit')
    args = parser.parse_args()
    local = json.loads(args.options.read_text()) if args.options else {}
    if args.max_length is not None:
        local['max_line_length'] = args.max_length
    settings = skill_config.for_context('dk-workflows:dk-commit-message', cwd=args.cwd, pr=args.pr,
                                         upstream=args.upstream, options=local)['options']
    max_length = settings['max_line_length']
    if args.file:
        errors = overlong_lines(args.file.read_text(encoding='utf-8'), str(args.file), max_length)
        count = 1
    else:
        commits = commits_since(args.base, args.cwd)
        errors = validate_commits(commits, args.cwd, max_length)
        count = len(commits)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print(f'validated {count} commit message{"s" if count != 1 else ""}; all lines are at most {max_length} characters')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'commit message check: {exc}', file=sys.stderr)
        sys.exit(1)
