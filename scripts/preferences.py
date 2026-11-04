#!/usr/bin/env python3
"""Prepare, store, retrieve and render scoped private preferences."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
import preferences


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('list')
    show = commands.add_parser('show')
    show.add_argument('--id', required=True)
    for action in ('prepare', 'commit', 'render'):
        command = commands.add_parser(action)
        command.add_argument('--file', type=Path, required=True)
    remove = commands.add_parser('remove')
    remove.add_argument('--id', action='append', required=True)
    remove.add_argument('--revision', type=int, required=True)
    resolve = commands.add_parser('resolve')
    resolve.add_argument('--mode', choices=preferences.MODES, required=True)
    resolve.add_argument('--paths', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'list':
        result = preferences.list_records(args.checkout)
    elif args.action == 'show':
        result = preferences.show(args.checkout, args.id)
    elif args.action == 'remove':
        result = preferences.remove(args.checkout, args.id, args.revision)
    elif args.action == 'resolve':
        result = preferences.resolve(args.checkout, args.mode, load(args.paths))
    elif args.action == 'render':
        print(preferences.render(load(args.file)), end='')
        return
    else:
        result = getattr(preferences, args.action)(args.checkout, load(args.file))
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        sys.exit(2)
