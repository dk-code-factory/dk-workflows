#!/usr/bin/env python3
"""Bookkeeping only: generate specs, inspect, claim and record. Agents invoke skills."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
from state import Store, make_spec, read
from config import CYCLE_PHASES, effective, finalizer_groups, make_finalizer_spec, make_hook_spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True, help='HOST/OWNER/REPO')
    parser.add_argument('--pr', required=True, help='Canonical https PR URL')
    parser.add_argument('--checkout', type=Path, help='Repository whose state must remain external')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('spec')
    p.add_argument('--definition', type=Path, required=True,
                   help='Explicit make_spec fields, excluding repository and pr')
    p = sub.add_parser('hook-spec', help='Define one configured cycle hook; agents execute it')
    p.add_argument('--definition', type=Path, required=True,
                   help='Resolved skill identity, resources, governing inputs and execution options')
    p.add_argument('--phase', choices=CYCLE_PHASES, required=True)
    p.add_argument('--hook-id', required=True)
    p.add_argument('--run', required=True)
    p.add_argument('--cycle', required=True)
    p.add_argument('--upstream')
    p = sub.add_parser('finalizer-plan', help='Read ordered finalizer groups; agents execute them')
    p.add_argument('--upstream')
    p = sub.add_parser('finalizer-spec', help='Define one configured finalizer in the retained run')
    p.add_argument('--definition', type=Path, required=True)
    p.add_argument('--finalizer', required=True, help='/skill/PLUGIN:SKILL reference')
    p.add_argument('--run', required=True)
    p.add_argument('--upstream')
    p = sub.add_parser('status')
    p.add_argument('--spec', type=Path, action='append', default=[])
    p.add_argument('--compact', action='store_true', help='Replace run progress with counts and references')
    p.add_argument('--explain', action='store_true', help='Add precise captured-field changes and reuse blockers')
    p.add_argument('--run', help='Select a retained run; unfinished claims remain PR-wide')
    for name in ('start', 'start-batch'):
        p = sub.add_parser(name)
        if name == 'start':
            p.add_argument('--spec', type=Path, required=True)
            p.add_argument('--recover', help='Explicitly recover this interrupted claim id; reconcile domain writes first')
        else:
            p.add_argument('--spec', type=Path, action='append', required=True)
            p.add_argument('--recover', action='append', default=[], metavar='TASK_KEY=CLAIM_ID',
                           help='Explicit task recovery; reconcile domain writes first')
        p.add_argument('--run', help='Stable iteration run id; retain on resume')
        p.add_argument('--cycle', help='Stable review-cycle id; shared across reviewers and resume')
        p.add_argument('--retry', action='store_true', help='One explicitly eligible same-cycle reviewer retry')
        p.add_argument('--ci-repair', action='store_true')
        p.add_argument('--ci-rerun', action='store_true')
        p.add_argument('--head')
    p = sub.add_parser('native-binding', help='Expected binding for native observations; never creates evidence')
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--claim', required=True)
    p = sub.add_parser('record')
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--claim', required=True)
    p.add_argument('--original', type=Path, required=True)
    p.add_argument('--normalized', type=Path, required=True)
    p.add_argument('--status', choices=['complete', 'failed', 'incomplete'], required=True)
    p.add_argument('--native-evidence', type=Path, help='Original coordinator-captured native host observations')
    p.add_argument('--progress', type=Path, help='Step status, finding dispositions, exact human acceptances and evidence')
    p = sub.add_parser('invalidate')
    p.add_argument('--key', action='append', required=True)
    p.add_argument('--reason', required=True)
    p.add_argument('--recover', help='Close this stopped claim without invoking; reconcile domain writes first')
    args = parser.parse_args()
    if args.command == 'spec':
        result = make_spec(repository=args.repository, pr=args.pr, **read(args.definition))
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == 'hook-spec':
        result = make_hook_spec(repository=args.repository, pr=args.pr, run=args.run,
                                cycle=args.cycle, phase=args.phase, hook_id=args.hook_id,
                                definition=read(args.definition), upstream=args.upstream)
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == 'finalizer-plan':
        loaded = effective(args.repository, args.upstream)
        print(json.dumps({'groups': finalizer_groups(loaded['settings']['finalizers'],
                                                   role_skills=loaded['settings']['skills'].values()),
                          'sources': loaded['sources']}, sort_keys=True))
        return
    if args.command == 'finalizer-spec':
        result = make_finalizer_spec(repository=args.repository, pr=args.pr, run=args.run,
                                     finalizer=args.finalizer, definition=read(args.definition),
                                     upstream=args.upstream)
        print(json.dumps(result, sort_keys=True))
        return
    store = Store(args.repository, args.pr, checkout=args.checkout)
    if args.command == 'status':
        result = store.status([read(p) for p in args.spec], compact=args.compact, run=args.run, explain=args.explain)
    elif args.command == 'start':
        result = store.start(read(args.spec), recover=args.recover, run=args.run, cycle=args.cycle,
                             retry=args.retry, ci_repair=args.ci_repair, ci_rerun=args.ci_rerun, head=args.head)
    elif args.command == 'start-batch':
        recover = {}
        for value in args.recover:
            key, separator, claim = value.partition('=')
            if not separator or not key or not claim or key in recover:
                raise ValueError('Batch --recover requires unique TASK_KEY=CLAIM_ID entries')
            recover[key] = claim
        result = store.start_batch([read(path) for path in args.spec], recover=recover,
                                   run=args.run, cycle=args.cycle, retry=args.retry,
                                   ci_repair=args.ci_repair, ci_rerun=args.ci_rerun, head=args.head)
    elif args.command == 'native-binding':
        result = store.native_binding(read(args.spec), args.claim)
    elif args.command == 'record':
        result = store.record(read(args.spec), args.claim, args.original.read_bytes(), read(args.normalized),
                              args.status, progress=read(args.progress) if args.progress else None,
                              native_evidence=args.native_evidence.read_bytes() if args.native_evidence else None)
    else:
        result = store.invalidate(args.key, args.reason, recover=args.recover)
    print(json.dumps(result, sort_keys=True))

if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        sys.exit(2)
