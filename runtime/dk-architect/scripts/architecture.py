#!/usr/bin/env python3
"""Inspect architecture catalogs, export scoped packets and evaluate exceptions."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared import architecture as api
from shared.repository_scope import identities


def json_file(path):
    return json.loads(Path(path).read_text())


def reference(path):
    value = json_file(path)
    return value if set(value) == {'path', 'fingerprint'} else {
        'path': str(Path(path).resolve()), 'fingerprint': api.unseal(value)['fingerprint']}


def dispatch(arguments=None, *, cwd='.', pr=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cwd', default=cwd)
    parser.add_argument('--pr', default=pr)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'refresh'):
        command = sub.add_parser(name)
        command.add_argument('--baseline', help='Pinned target SHA; local default requires clean HEAD')
    for name in ('inspect', 'list'):
        sub.add_parser(name)
    command = sub.add_parser('edit')
    command.add_argument('--file', required=True, help='JSON rule record')
    command.add_argument('--user-file', required=True, help='Exact user instruction and source')
    command.add_argument('--expected', required=True, help='Inspected catalog fingerprint')
    command.add_argument('--resolve-conflicts', action='store_true', help='Apply the explicit user resolution to outstanding conflicts')
    command = sub.add_parser('delete')
    command.add_argument('id')
    command.add_argument('--user-file', required=True)
    command.add_argument('--expected', required=True)
    command.add_argument('--resolve-conflicts', action='store_true')
    command = sub.add_parser('packet', aliases=['export'])
    command.add_argument('--agent', required=True)
    command.add_argument('--paths-file', help='JSON array of intended paths; omission means unknown scope')
    command.add_argument('--task', required=True)
    command.add_argument('--head')
    command.add_argument('--scope-file', help='Complete task/PR scope and Decisions JSON')
    command = sub.add_parser('validate-packet')
    command.add_argument('--packet', required=True)
    command.add_argument('--agent', required=True)
    command.add_argument('--paths-file', required=True)
    command = sub.add_parser('request')
    command.add_argument('--packet', required=True)
    command.add_argument('--file', required=True, help='rule_id, deviation, paths, evidence, rationale JSON')
    command = sub.add_parser('enforce')
    command.add_argument('--request', required=True)
    command.add_argument('--user-file', help='Explicit approval from the user; never infer from a timeout')
    command = sub.add_parser('prepare')
    command.add_argument('--context', required=True)
    command.add_argument('--decisions', required=True)
    command.add_argument('--governing-file', help='JSON source-slot to path map for issue, instruction and other governing evidence')
    command = sub.add_parser('handoff')
    command.add_argument('--preparation', required=True)
    command.add_argument('--context', required=True)
    command.add_argument('--agent', required=True)
    command.add_argument('--paths-file')
    command = sub.add_parser('review')
    command.add_argument('--preparation', required=True)
    command.add_argument('--context', required=True)
    command.add_argument('--decisions', required=True)
    command.add_argument('--exceptions-file', help='JSON array of enforcer approval references')
    args = parser.parse_args(arguments)
    root = Path(args.cwd).resolve()
    # PR lookup uses existing head-first identity resolution. Local use shares
    # dk-config's normalized remote/path key without extra network discovery.
    identity = identities(root, args.pr)[0] if args.pr else None
    if args.command in ('init', 'refresh'):
        if args.pr and not args.baseline:
            parser.error('PR architecture initialization requires the pinned target --baseline')
        result = api.ensure(root, args.baseline, identity=identity, force=args.command == 'refresh')
    elif args.command in ('inspect', 'list'):
        result = api.current(root, identity)
    elif args.command in ('edit', 'delete'):
        result = api.edit(root, json_file(args.file) if args.command == 'edit' else None,
                          delete=args.id if args.command == 'delete' else None,
                          user=json_file(args.user_file), expected=args.expected, identity=identity,
                          resolve_conflicts=args.resolve_conflicts)
    elif args.command in ('packet', 'export'):
        result = api.packet(root, args.agent, json_file(args.paths_file) if args.paths_file else None,
                            task=args.task, head=args.head, identity=identity,
                            scope=json_file(args.scope_file) if args.scope_file else None)
    elif args.command == 'validate-packet':
        result = api.validate_packet(reference(args.packet), root, agent=args.agent,
                                     paths=json_file(args.paths_file))
    elif args.command == 'request':
        result = api.exception_request(reference(args.packet), root, **json_file(args.file))
    elif args.command == 'enforce':
        result = api.enforce(reference(args.request), root,
                             user=json_file(args.user_file) if args.user_file else None)
    elif args.command == 'prepare':
        decisions = json_file(args.decisions)
        result = api.prepare(json_file(args.context), decisions.get('data', decisions),
            governing=api.governing_sources(json_file(args.governing_file)) if args.governing_file else None)
    elif args.command == 'handoff':
        prepared = json_file(args.preparation)
        result = api.handoff(prepared.get('architecture', prepared), json_file(args.context), args.agent,
                             json_file(args.paths_file) if args.paths_file else None)
    else:
        prepared, decisions = json_file(args.preparation), json_file(args.decisions)
        result = api.review(prepared.get('architecture', prepared), json_file(args.context),
                            decisions.get('data', decisions),
                            exceptions=json_file(args.exceptions_file) if args.exceptions_file else ())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    os.umask(0o077)
    try:
        raise SystemExit(dispatch())
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
