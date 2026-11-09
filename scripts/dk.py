#!/usr/bin/env python3
"""Prepare, finish and resume native tasks; agents invoke skills."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
from state import Store, make_spec, read
import skill_config
from config import (CYCLE_PHASES, effective, finalizer_groups, make_finalizer_spec,
                    make_hook_spec, make_invocation_spec, hook_phase)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True, help='HOST/OWNER/REPO')
    parser.add_argument('--pr', required=True, help='Canonical https PR URL')
    parser.add_argument('--checkout', type=Path, help='Repository whose state must remain external')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare', help='Define, capture and claim native tasks in one call')
    p.add_argument('--file', type=Path, required=True, help='Tasks and retained run/cycle options')
    p.add_argument('--compact', action='store_true', help='Omit known assignments unless invoking new work')
    p = sub.add_parser('resume', help='Recapture saved task definitions and return compact status')
    p.add_argument('--key', action='append', default=[])
    p.add_argument('--run')
    p.add_argument('--explain', action='store_true')
    p = sub.add_parser('result', help='Read one validated saved result section')
    p.add_argument('--key', required=True)
    p.add_argument('--section', choices=['findings', 'normalized', 'original', 'checklist'], default='findings')
    p = sub.add_parser('progress', help='Read a bounded page of retained dispositions and acceptances')
    p.add_argument('--run', required=True)
    p.add_argument('--after', type=int, default=0)
    p.add_argument('--limit', type=int, default=20)
    p = sub.add_parser('finish', help='Record a native result using its retained definition')
    p.add_argument('--key', required=True)
    p.add_argument('--claim', required=True)
    p.add_argument('--round', dest='round_id', help='Reserved completion-check round; retains the primary result')
    p.add_argument('--report', type=Path, required=True, help='Skill report with explicit complete boolean')
    p.add_argument('--original', type=Path, help='Original output; defaults to report bytes')
    p.add_argument('--status', choices=['complete', 'failed', 'incomplete'])
    p.add_argument('--native-evidence', type=Path)
    p.add_argument('--progress', type=Path)
    p.add_argument('--compact', action='store_true', help='Omit the unchanged assignment on rechecks')
    p = sub.add_parser('spec')
    p.add_argument('--definition', type=Path, required=True,
                   help='Explicit make_spec fields, excluding repository and pr')
    p = sub.add_parser('skill-spec', help='Bind an item in a skill-owned list; never launch it')
    p.add_argument('--skill', required=True, help='Fully qualified owning plugin:skill')
    p.add_argument('--kind', choices=['tasks', 'finalizers', 'reviewers'], required=True)
    p.add_argument('--index', type=int, required=True)
    p.add_argument('--definition', type=Path, required=True)
    p.add_argument('--options', type=Path, help='Owning invocation-local options')
    p.add_argument('--upstream')
    p.add_argument('--run', help='Retained run id; required for finalizers')
    p = sub.add_parser('hook-spec', help='Define one configured cycle hook; agents execute it')
    p.add_argument('--definition', type=Path, required=True,
                   help='Resolved skill identity, resources, governing inputs and execution options')
    p.add_argument('--phase', choices=CYCLE_PHASES, required=True)
    p.add_argument('--index', type=int, required=True, help='Zero-based hook position')
    p.add_argument('--process', default='pr-fix')
    p.add_argument('--run', required=True)
    p.add_argument('--cycle', required=True)
    p.add_argument('--upstream')
    p = sub.add_parser('finalizer-plan', help='Read ordered finalizer groups; agents execute them')
    p.add_argument('--process', default='pr-fix')
    p.add_argument('--upstream')
    p = sub.add_parser('finalizer-spec', help='Define one configured finalizer in the retained run')
    p.add_argument('--definition', type=Path, required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--index', type=int, help='Zero-based flattened finalizer position')
    group.add_argument('--finalizer', help='Unique skill name, without a path prefix')
    p.add_argument('--process', default='pr-fix')
    p.add_argument('--run', required=True)
    p.add_argument('--upstream')
    p = sub.add_parser('invocation-spec', help='Bind a configured invocation to a managed native task')
    p.add_argument('--definition', type=Path, required=True)
    p.add_argument('--process', required=True)
    p.add_argument('--kind', choices=['coordinator', 'task', 'reviewer'], default='coordinator')
    p.add_argument('--task', help='Purpose name in the process tasks map; required with --kind task')
    p.add_argument('--index', type=int)
    p.add_argument('--upstream')
    p = sub.add_parser('reconcile-hooks', help='Record reconciled effects before changing a retained hook list')
    p.add_argument('--process', default='pr-fix')
    p.add_argument('--phase', choices=CYCLE_PHASES, required=True)
    p.add_argument('--run', required=True)
    p.add_argument('--cycle', required=True)
    p.add_argument('--upstream')
    p.add_argument('--expected', required=True, help='Observed phase snapshot hash from status')
    p.add_argument('--reason', required=True)
    p = sub.add_parser('status')
    p.add_argument('--spec', type=Path, action='append', default=[])
    p.add_argument('--compact', action='store_true', help='Replace run progress with counts and references')
    p.add_argument('--explain', action='store_true', help='Add precise captured-field changes and reuse blockers')
    p.add_argument('--run', help='Select a retained run; unfinished claims remain PR-wide')
    p = sub.add_parser('continue-run', help='Record explicit user authorization for an exhausted run')
    p.add_argument('--run', required=True, help='Existing run id; preserve its history and all counters')
    p.add_argument('--request-id', required=True, help='Stable user-request identity; retain on replay')
    p.add_argument('--authorization', required=True, help='Exact renewed user instruction, not an inferred resume')
    p.add_argument('--source', required=True, help='Source identifying the explicit user request')
    p.add_argument('--cycles', type=int, default=5, help='Additional review cycles, from one to five (default five)')
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
    if args.command in {'prepare', 'resume', 'finish', 'result', 'progress'}:
        from tasks import Tasks
        tasks = Tasks(args.repository, args.pr, checkout=args.checkout)
        if args.command == 'prepare':
            result = tasks.prepare(read(args.file), compact=args.compact)
        elif args.command == 'resume':
            result = tasks.resume(keys=args.key, run=args.run, explain=args.explain)
        elif args.command == 'result':
            result = tasks.result(args.key, args.section)
        elif args.command == 'progress':
            result = tasks.progress(args.run, after=args.after, limit=args.limit)
        else:
            result = tasks.finish(args.key, args.claim, (args.original or args.report).read_bytes(),
                                  read(args.report), status=args.status,
                                  progress=read(args.progress) if args.progress else None,
                                  native_evidence=args.native_evidence.read_bytes() if args.native_evidence else None,
                                  round_id=args.round_id, compact=args.compact)
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == 'spec':
        result = make_spec(repository=args.repository, pr=args.pr, **read(args.definition))
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == 'hook-spec':
        result = make_hook_spec(repository=args.repository, pr=args.pr, run=args.run,
                                cycle=args.cycle, phase=args.phase, index=args.index, process=args.process,
                                definition=read(args.definition), upstream=args.upstream)
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == 'finalizer-plan':
        loaded = effective(args.repository, args.upstream)
        print(json.dumps({'groups': finalizer_groups(loaded['settings']['processes'][args.process].get('finalizers', [])),
                          'sources': loaded['sources']}, sort_keys=True))
        return
    if args.command == 'finalizer-spec':
        result = make_finalizer_spec(repository=args.repository, pr=args.pr, run=args.run,
                                     finalizer=args.finalizer, index=args.index, process=args.process, definition=read(args.definition),
                                     upstream=args.upstream)
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == 'invocation-spec':
        result = make_invocation_spec(repository=args.repository, pr=args.pr, process=args.process,
                                      kind=args.kind, task=args.task, index=args.index, definition=read(args.definition),
                                      upstream=args.upstream)
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == 'skill-spec':
        result = skill_config.make_spec(repository=args.repository, pr=args.pr, skill=args.skill,
                                        kind=args.kind, index=args.index, definition=read(args.definition),
                                        upstream=args.upstream, run=args.run,
                                        options=read(args.options) if args.options else None)
        print(json.dumps(result, sort_keys=True))
        return
    store = Store(args.repository, args.pr, checkout=args.checkout)
    if args.command == 'reconcile-hooks':
        loaded = effective(args.repository, args.upstream)
        snapshot = hook_phase(loaded['settings'], args.process, args.run, args.cycle, args.phase)
        result = store.reconcile_hooks(snapshot, args.expected, args.reason)
    elif args.command == 'status':
        result = store.status([read(p) for p in args.spec], compact=args.compact, run=args.run, explain=args.explain)
    elif args.command == 'continue-run':
        result = store.continue_run(run=args.run, request_id=args.request_id,
                                    authorization=args.authorization, source=args.source, cycles=args.cycles)
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
