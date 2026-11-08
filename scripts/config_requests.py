#!/usr/bin/env python3
"""Manage private configuration requests; native agents own semantic admission."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
from config_requests import Requests


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    start = sub.add_parser('start')
    start.add_argument('--ask-file', type=Path, required=True)
    start.add_argument('--checkout', default='.')
    start.add_argument('--repository')
    start.add_argument('--upstream')
    start.add_argument('--pr')
    for name in ('prepare', 'clarify', 'apply', 'cancel', 'resume', 'show'):
        command = sub.add_parser(name)
        command.add_argument('--request-id', required=True)
        if name in {'prepare', 'clarify'}:
            command.add_argument('--file', type=Path, required=True)
        if name == 'cancel':
            command.add_argument('--reason', required=True)
        if name == 'show':
            command.add_argument('--section', choices=['status', 'ask', 'context', 'operations', 'decisions', 'assessment', 'progress'], default='status')
    history = sub.add_parser('history')
    history.add_argument('--repository')
    history.add_argument('--domain')
    history.add_argument('--status')
    history.add_argument('--limit', type=int, default=20)
    args = vars(parser.parse_args(arguments))
    command = args.pop('command')
    service = Requests()
    if command == 'start':
        ask = args.pop('ask_file').read_text()
        return service.start(ask, {k: v for k, v in args.items() if v is not None})
    if command == 'prepare':
        return service.prepare(args['request_id'], json.loads(args['file'].read_text()))
    if command == 'clarify':
        return service.clarify(args['request_id'], **json.loads(args['file'].read_text()))
    return getattr(service, command)(**args)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        print(json.dumps(main(), sort_keys=True, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        sys.exit(2)
