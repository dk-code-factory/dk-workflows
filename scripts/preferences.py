#!/usr/bin/env python3
"""Prepare, store, retrieve and render scoped private preferences."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
import preferences
from config_requests import apply_bound


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
        if action == 'commit':
            command.add_argument('--request-id')
    remove = commands.add_parser('remove')
    remove.add_argument('--id', action='append', required=True)
    remove.add_argument('--revision', type=int, required=True)
    remove.add_argument('--request-id')
    resolve = commands.add_parser('resolve')
    resolve.add_argument('--mode', choices=preferences.MODES, required=True)
    resolve.add_argument('--paths', type=Path, required=True)
    handoff = commands.add_parser('handoff', help='Build inputs and reuse unchanged condition decisions')
    handoff.add_argument('--file', type=Path, required=True, help='Mode, paths, condition_context and new decisions')
    handoff.add_argument('--output', type=Path, required=True, help='Stable private task input; reused on resume')
    args = parser.parse_args()
    if args.action == 'list':
        result = preferences.list_records(args.checkout)
    elif args.action == 'show':
        result = preferences.show(args.checkout, args.id)
    elif args.action == 'remove':
        proposal = {'repository': preferences.repository(args.checkout), 'revision': args.revision,
                    'changes': {'remove': args.id}}
        apply_bound(args.request_id, {'domain': 'preferences', 'action': 'commit', 'proposal': proposal},
                    {'checkout': str(args.checkout)})
        result = preferences.list_records(args.checkout)
    elif args.action == 'resolve':
        result = preferences.resolve(args.checkout, args.mode, load(args.paths))
    elif args.action == 'handoff':
        from shared.repository_scope import checkout_roots
        from shared.private_state import write
        output = args.output.resolve()
        probe = output.parent
        while not probe.exists():
            probe = probe.parent
        roots = [Path(__file__).resolve().parents[1], *checkout_roots(args.checkout), *checkout_roots(probe)]
        if any(output.is_relative_to(root) for root in roots):
            raise ValueError('Preference handoff must remain outside repositories and the plugin')
        request = load(args.file)
        if (not isinstance(request, dict) or set(request) - {'mode', 'paths', 'condition_context', 'decisions'}
                or 'condition_context' not in request):
            raise ValueError('Handoff requires mode, paths and complete consumed condition_context')
        previous = load(output) if output.exists() else None
        built = preferences.handoff(args.checkout, request['mode'], request['paths'], request['condition_context'],
                                    previous=previous, decisions=request.get('decisions', []))
        unchanged = previous == built['handoff']
        if built['complete'] and not unchanged:
            write(output, built['handoff'])
        result = {'complete': built['complete'], 'path': str(output) if built['complete'] else None,
                  'unchanged': built['complete'] and unchanged, 'pending': built['pending']}
    elif args.action == 'render':
        print(preferences.render(load(args.file)), end='')
        return
    elif args.action == 'commit':
        apply_bound(args.request_id, {'domain': 'preferences', 'action': 'commit', 'proposal': load(args.file)},
                    {'checkout': str(args.checkout)})
        result = preferences.list_records(args.checkout)
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
