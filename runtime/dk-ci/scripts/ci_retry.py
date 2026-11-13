#!/usr/bin/env python3
"""Prepare bounded failed-job recovery after a native evidence-based diagnosis."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'scripts'))
from shared.ci_retry import capture, requests
from shared.flaky_actions import GitHub, pr_repository
from shared.diagnostics import Diagnostic
from ci_wait import observe
import publish


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'prepare'])
    parser.add_argument('--pr', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--decision', type=Path, required=True,
                        help='Current failing ci_wait decision JSON')
    parser.add_argument('--diagnosis', type=Path,
                        help='Native diagnosis with capture and a decision for each failed job')
    parser.add_argument('--automatic-limit', type=int, choices=range(4), default=3)
    args = parser.parse_args()
    decision = json.loads(args.decision.read_text())
    handoff = capture(GitHub(pr_repository(args.pr)), args.pr, args.head, decision,
                      observe(args.pr, args.head))
    if args.action == 'inspect':
        result = handoff
    else:
        if args.diagnosis is None:
            parser.error('prepare requires --diagnosis')
        result = requests(handoff, json.loads(args.diagnosis.read_text()),
                          automatic_limit=args.automatic_limit)
        operations = []
        for request in result.pop('requests'):
            try:
                value = publish.prepare(request)
                operations.append({'run_id': request['run_id'], 'attempt': request['attempt'],
                    'operation': value['operation'], 'status': value['status'],
                    'next_action': 'reconcile' if value['status'] == 'pending' else 'publish'})
            except (ValueError, Diagnostic) as exc:
                result['blockers'].append({'run_id': request['run_id'], 'reason': str(exc)})
        result['operations'] = operations
        result['ci_satisfied'] = False
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        sys.exit(2)
