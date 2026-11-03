#!/usr/bin/env python3
"""Check PR size; prepare, verify, publish or reconcile a PR partition."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
from shared import pr_partition
from shared.diagnostics import Diagnostic, emit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cwd', default='.')
    parser.add_argument('--pr')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'assess', 'prepare', 'verify', 'publish', 'reconcile', 'cancel', 'resume'):
        command = sub.add_parser(name)
        if name in ('check', 'assess', 'prepare'):
            command.add_argument('--evidence-file', required=name != 'check', type=Path)
            command.add_argument('--preparation-file', type=Path)
        if name == 'assess':
            command.add_argument('--assessment-file', required=True, type=Path)
        if name == 'prepare':
            command.add_argument('--plan-file', required=True, type=Path)
            command.add_argument('--remote', required=True)
        if name in ('verify', 'publish', 'reconcile', 'cancel', 'resume'):
            command.add_argument('--state', required=True, type=Path)
        if name == 'resume':
            command.add_argument('--candidate-hash', required=True)
            command.add_argument('--assessment-file', type=Path)
            command.add_argument('--decisions-file', type=Path)
        if name == 'reconcile':
            command.add_argument('--operation', type=Path, help='Exact journal operation from the recovery action')
        if name == 'publish':
            command.add_argument('--decisions-file', type=Path)
            command.add_argument('--review-state')
            command.add_argument('--description-review-state')
            command.add_argument('--candidate-hash', help='Verified candidate set reviewed by both self-review loops')
    args = parser.parse_args()
    cwd = Path(args.cwd).resolve()
    def read(path):
        return json.loads(path.read_text())
    if args.command in ('check', 'assess', 'prepare'):
        if not args.pr:
            raise ValueError('check, assess and prepare require a full PR URL')
        evidence = read(args.evidence_file) if args.evidence_file else None
        if args.command == 'check':
            result = pr_partition.check(cwd, args.pr, evidence, preparation_file=args.preparation_file)
        elif args.command == 'assess':
            result = pr_partition.assess_policy(cwd, args.pr, evidence, read(args.assessment_file),
                                                preparation_file=args.preparation_file)
        else:
            result = pr_partition.prepare(cwd, args.pr, evidence, read(args.plan_file), args.remote,
                                          preparation_file=args.preparation_file)
    else:
        data = pr_partition.load(args.state)
        if args.pr and args.pr != data['snapshot']['binding']['pr']:
            raise ValueError('PR context conflicts with retained partition')
        if cwd != Path(data['cwd']):
            raise ValueError('checkout context conflicts with retained partition')
        if getattr(args, 'operation', None) and str(args.operation.resolve()) not in data['operations'].values():
            raise ValueError('reconciliation operation is not in this partition journal')
        if args.command == 'verify':
            pr_partition.verify(args.state)
            result = {'state': str(args.state), 'outcome': 'verified'}
        elif args.command == 'cancel':
            result = pr_partition.cancel_policy(args.state)
        elif args.command == 'resume':
            result = pr_partition.resume_policy(args.state, candidate_hash=args.candidate_hash,
                assessment=read(args.assessment_file) if args.assessment_file else None,
                decisions=read(args.decisions_file) if args.decisions_file else None)
        else:
            result = pr_partition.publish(args.state,
                decisions=read(args.decisions_file) if getattr(args, 'decisions_file', None) else None,
                review_state=getattr(args, 'review_state', None),
                description_review_state=getattr(args, 'description_review_state', None),
                candidate_hash=getattr(args, 'candidate_hash', None),
                operation_path=getattr(args, 'operation', None),
                reconcile=args.command == 'reconcile')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Diagnostic as exc:
        emit(exc)
        sys.exit(2)
    except (ValueError, RuntimeError, OSError) as exc:
        print('partition-prs: ' + str(exc), file=sys.stderr)
        sys.exit(2)
