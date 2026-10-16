#!/usr/bin/env python3
"""Direct a bounded, stateless self-review of a local PR or commit draft."""
import argparse
import base64
import json
import sys

KINDS = ('split', 'commit-message', 'dk-pr-description', 'decisions')
MAX_PASSES = 5


def pass_limit(kind):
    return 3 if kind == 'decisions' else MAX_PASSES


def review_instruction(kind, updated=False):
    if kind == 'decisions':
        return ('Review decision language again. ' if updated else 'Review decision language. ') + (
            'Keep it short, plain, complete, and precise. Remove vague wording and double meanings. '
            'Preserve the choice, rationale, scope, conditions, exceptions, and exact sources. '
            'Do not invent details to resolve ambiguity. Report whether you updated the decision text.')
    return ('Review the updated result again and report whether you updated it.' if updated else
            'Review the current result and report whether you updated it.')


def encode(kind, review_pass, complete=False):
    payload = {'version': 1, 'kind': kind, 'pass': review_pass, 'complete': complete}
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def decode(token):
    try:
        raw = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
        state = json.loads(raw)
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid review state token') from exc
    if (type(state) is not dict or set(state) != {'version', 'kind', 'pass', 'complete'}
            or state['version'] != 1 or type(state['version']) is not int
            or state['kind'] not in KINDS or type(state['pass']) is not int
            or not 1 <= state['pass'] <= pass_limit(state['kind']) or type(state['complete']) is not bool
            or encode(state['kind'], state['pass'], state['complete']) != token):
        raise ValueError('invalid review state token')
    return state


def require_complete(token, kind):
    state = decode(token)
    if state['kind'] != kind or not state['complete']:
        raise ValueError(f'completed {kind} review loop required')
    return state


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    start = sub.add_parser('start')
    start.add_argument('--kind', choices=KINDS, required=True)
    report = sub.add_parser('report')
    report.add_argument('--state', required=True)
    report.add_argument('--updated', choices=('yes', 'no'), required=True)
    check = sub.add_parser('check')
    check.add_argument('--state', required=True)
    check.add_argument('--kind', choices=KINDS, required=True)
    args = parser.parse_args()

    if args.command == 'start':
        result = {'kind': args.kind, 'pass': 1, 'state': encode(args.kind, 1),
                  'complete': False, 'next': 'review',
                  'max_passes': pass_limit(args.kind),
                  'instruction': review_instruction(args.kind)}
    elif args.command == 'report':
        state = decode(args.state)
        if state['complete']:
            raise ValueError('review loop is already complete')
        review_pass = state['pass']
        complete = args.updated == 'no' or review_pass == pass_limit(state['kind'])
        next_pass = review_pass if complete else review_pass + 1
        result = {'kind': state['kind'], 'pass': next_pass,
                  'state': encode(state['kind'], next_pass, complete),
                  'complete': complete,
                  'updated': args.updated == 'yes',
                  'max_passes': pass_limit(state['kind']),
                  'stop_reason': ('unchanged' if args.updated == 'no' else 'pass_limit') if complete else None,
                  'next': 'stop' if complete else 'review',
                  'instruction': ('Stop reviewing. Run deterministic validation on the final result; '
                                  'continue only with actions the user requested.' if complete else
                                  review_instruction(state['kind'], updated=True))}
    else:
        state = require_complete(args.state, args.kind)
        result = {'kind': state['kind'], 'pass': state['pass'], 'complete': True, 'next': 'proceed'}
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except ValueError as exc:
        print(f'review loop: {exc}', file=sys.stderr)
        sys.exit(1)
