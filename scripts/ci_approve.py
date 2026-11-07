#!/usr/bin/env python3
"""Approve only fork workflow gates after current incoming-review evidence checks."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
import config
import skill_config
import publish
from shared.ci_approval import Approval
from shared.diagnostics import Diagnostic
from state import digest, read, resource, write


def request(path):
    evidence = read(Path(path))
    return {key: evidence[key] for key in ('pr', 'head', 'base')} | {'evidence': str(Path(path).resolve())}


def snapshot(path):
    """Capture at inspection; refresh only after reassessing changed review inputs."""
    value = read(Path(path))
    approval = Approval(publish.handler(request(path)))
    value['settings'] = config.effective(value['repository'], value.get('upstream'))
    if value.get('configuration') == 'skill-owned':
        value['skill_settings_fingerprints'] = {
            name: digest(skill_config.effective('dk-workflows:' + name, value['repository'], value.get('upstream'),
                                                options=value.get(field, {}))['options'])
            for name, field in [('dk-pr-review', 'options'), ('dk-pr-reviewers-run', 'reviewer_options')]}
        value.pop('skill_settings', None)
    value['instruction_fingerprints'] = {key: resource(source) for key, source in value['instructions'].items()}
    value['context'] = approval.context()
    write(Path(path), value)
    return {'evidence': str(Path(path).resolve()), 'status': 'captured'}


def approve(path):
    bound = request(path)
    approval = Approval(publish.handler(bound))
    # Reconcile retained intent before listing: a lost response may already have
    # removed the gate from discovery. Never lose that attempt's operation path.
    from shared import publication
    from shared.workflow_storage import operations_root
    scope = operations_root() / publication.digest(bound['pr'])
    receipts = []
    for operation in sorted(scope.glob('*/*.json')):
        retained = publication.load(operation)
        if retained['status'] == 'pending':
            if retained['request']['kind'] != 'ci-approve':
                raise ValueError('Reconcile the pending publication first: ' + str(operation))
            receipts.append(publish.publish(operation, reconcile=True))
    approval.evidence()
    eligible = approval.discover()
    for run in eligible:
        prepared = publish.prepare({**bound, 'kind': 'ci-approve', 'run_id': run['id'], 'attempt': run['run_attempt']})
        receipts.append(publish.publish(prepared['operation']))
    # Verify a no-op against fresh readiness and a second complete enumeration.
    approval.evidence()
    remaining = approval.discover()
    if remaining:
        raise ValueError('Fork workflow approval gates remain; retain receipts and refresh discovery')
    return {'status': 'verified', 'noop': not receipts, 'pr': bound['pr'], 'head': bound['head'],
            'operations': receipts, 'eligible_runs_remaining': 0, 'ci_success': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('snapshot', 'approve'))
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps((snapshot if args.action == 'snapshot' else approve)(args.evidence), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Diagnostic as exc:
        print(json.dumps(exc.envelope()))
        sys.exit(2)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        sys.exit(2)
