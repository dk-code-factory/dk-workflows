#!/usr/bin/env python3
"""Capture and verify commit policy handoffs. Native agents invoke providers."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
import commit_policy
from shared.repository_scope import identities
from state import identity, read


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cwd', default='.')
    parser.add_argument('--repository', required=True)
    parser.add_argument('--pr', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('spec')
    p.add_argument('--definition', type=Path, required=True)
    p = sub.add_parser('handoff')
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--report', type=Path)
    p = sub.add_parser('verify')
    p.add_argument('--handoff', type=Path, required=True)
    p.add_argument('--definition', type=Path, required=True)
    args = parser.parse_args(arguments)
    subject = identity(args.repository, args.pr)
    chain = identities(args.cwd, args.pr)
    if args.command == 'handoff':
        spec = read(args.spec)
        if not isinstance(spec, dict) or any(spec.get(key) != value for key, value in subject.items()):
            raise ValueError('Policy spec must identify the current canonical PR')
        return commit_policy.handoff(chain=chain, spec=spec,
                                     report=read(args.report) if args.report else None)
    context = {'chain': chain, 'repository': args.repository, 'pr': args.pr,
               'definition': read(args.definition)}
    if args.command == 'spec':
        return commit_policy.policy_spec(**context)
    return commit_policy.verify(**context, supplied=read(args.handoff))


if __name__ == '__main__':
    try:
        print(json.dumps(main(), sort_keys=True))
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        sys.exit(2)
