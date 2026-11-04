#!/usr/bin/env python3
"""Bookkeeping only: status, start, record, invalidate. Agents invoke skills."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
from state import Store, read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True, help='HOST/OWNER/REPO')
    parser.add_argument('--pr', required=True, help='Canonical https PR URL')
    parser.add_argument('--checkout', type=Path, help='Repository whose state must remain external')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('status')
    p.add_argument('--spec', type=Path, action='append', default=[])
    p.add_argument('--compact', action='store_true', help='Replace run progress with counts and references')
    p.add_argument('--explain', action='store_true', help='Add precise captured-field changes and reuse blockers')
    p.add_argument('--run', help='Select a retained run; unfinished claims remain PR-wide')
    p = sub.add_parser('start')
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--recover', help='Explicitly recover this interrupted claim id; reconcile domain writes first')
    p.add_argument('--run', help='Stable iteration run id; retain on resume')
    p.add_argument('--cycle', help='Stable review-cycle id; shared across reviewers and resume')
    p.add_argument('--retry', action='store_true', help='One explicitly eligible same-cycle reviewer retry')
    p.add_argument('--ci-repair', action='store_true')
    p.add_argument('--ci-rerun', action='store_true')
    p.add_argument('--head')
    p = sub.add_parser('record')
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--claim', required=True)
    p.add_argument('--original', type=Path, required=True)
    p.add_argument('--normalized', type=Path, required=True)
    p.add_argument('--status', choices=['complete', 'failed', 'incomplete'], required=True)
    p.add_argument('--progress', type=Path, help='Step status, finding dispositions, exact human acceptances and evidence')
    p = sub.add_parser('invalidate')
    p.add_argument('--key', action='append', required=True)
    p.add_argument('--reason', required=True)
    p.add_argument('--recover', help='Close this stopped claim without invoking; reconcile domain writes first')
    args = parser.parse_args()
    store = Store(args.repository, args.pr, checkout=args.checkout)
    if args.command == 'status':
        result = store.status([read(p) for p in args.spec], compact=args.compact, run=args.run, explain=args.explain)
    elif args.command == 'start':
        result = store.start(read(args.spec), recover=args.recover, run=args.run, cycle=args.cycle,
                             retry=args.retry, ci_repair=args.ci_repair, ci_rerun=args.ci_rerun, head=args.head)
    elif args.command == 'record':
        result = store.record(read(args.spec), args.claim, args.original.read_bytes(), read(args.normalized),
                              args.status, progress=read(args.progress) if args.progress else None)
    else:
        result = store.invalidate(args.key, args.reason, recover=args.recover)
    print(json.dumps(result, sort_keys=True))

if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        sys.exit(2)
