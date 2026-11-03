#!/usr/bin/env python3
"""Check every line of a draft or branch commit message against an 80-character limit."""

import argparse
from pathlib import Path
import subprocess
import sys

MAX_LENGTH = 80


def git(cwd, *args):
    result = subprocess.run(['git', *args], cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or 'git failed')
    return result.stdout


def overlong_lines(message, label):
    return [f'{label}: line {number} has {len(line)} characters (maximum {MAX_LENGTH})'
            for number, line in enumerate(message.splitlines(), 1) if len(line) > MAX_LENGTH]


def validate_commits(commits, cwd):
    errors = []
    for commit in commits:
        message = git(cwd, 'show', '-s', '--format=%B', commit)
        errors.extend(overlong_lines(message, commit[:12]))
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
    args = parser.parse_args()
    if args.file:
        errors = overlong_lines(args.file.read_text(encoding='utf-8'), str(args.file))
        count = 1
    else:
        commits = commits_since(args.base, args.cwd)
        errors = validate_commits(commits, args.cwd)
        count = len(commits)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print(f'validated {count} commit message{"s" if count != 1 else ""}; all lines are at most {MAX_LENGTH} characters')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'commit message check: {exc}', file=sys.stderr)
        sys.exit(1)
