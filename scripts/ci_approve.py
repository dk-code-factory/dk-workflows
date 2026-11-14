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
                                                options=skill_config.validate('dk-workflows:' + name,
                                                                              value.get(field, {})))['options'])
            for name, field in [('dk-pr-review', 'options'), ('dk-pr-reviewers-run', 'reviewer_options')]}
        value.pop('skill_settings', None)
    value['instruction_fingerprints'] = {key: resource(source) for key, source in value['instructions'].items()}
    value['context'] = approval.context()
    write(Path(path), value)
    return {'evidence': str(Path(path).resolve()), 'status': 'captured'}


def bridge(path):
    """Plan the native evidence/approval handoff for a retained merge request; no writes."""
    from shared import publication
    from shared.github_publications import sha
    from shared.pr_ownership import classify
    from shared.workflow_storage import operations_root

    bound = read(Path(path))
    if (not isinstance(bound, dict) or bound.get('kind') != 'merge-pr'
            or bound.get('analysis_only') is not False):
        raise ValueError('Bridge requires an authorized merge request with analysis_only:false')
    for key in ('head', 'base'):
        sha(bound.get(key))
    if not isinstance(bound.get('pr'), str):
        raise ValueError('Bridge requires a canonical PR URL')
    options = (skill_config.validate('dk-workflows:dk-pr-review', bound['review_options'])
               if 'review_options' in bound else {})
    if 'evidence' in bound and (not isinstance(bound['evidence'], str) or not bound['evidence']):
        raise ValueError('Bridge evidence must reference an incoming-review document path')
    approval = Approval(publish.handler(bound))
    repository = approval.handler.host + '/' + approval.handler.root.removeprefix('repos/')
    # Validate the complete current invocation before any early handoff. Schema
    # validation alone cannot enforce rules involving merged default/scope values.
    current = skill_config.effective('dk-workflows:dk-pr-review', repository, bound.get('upstream'),
                                     options=options)['options']
    continuation = {'request': str(Path(path).resolve()), 'ci_success': None,
                    'after_approval': ['dk-workflows:dk-ci', 'dk-workflows:dk-pr-publish']}
    # A prior approval may have removed a gate despite a lost response. Discover
    # uncertain intent first, without refreshing or replacing its original bytes.
    pending = [str(operation) for operation in sorted(
        (operations_root() / publication.digest(bound['pr'])).glob('*/*.json'))
        if publish.blocks(publication.load(operation), bound)]
    if pending:
        return {**continuation, 'action': 'reconcile', 'operations': pending}
    context = approval.context()
    if (context['html_url'] != bound['pr'] or context['head']['sha'] != bound['head']
            or context['target_tip'] != bound['base'] or context['state'] != 'open'
            or context['draft'] is not False):
        raise ValueError('Merge bridge requires the current open non-draft PR at the reviewed head and target')
    eligible = approval.discover()
    if not eligible:
        return {**continuation, 'action': 'observe-ci', 'eligible_runs': []}
    settings = config.effective(repository, bound.get('upstream'))
    actor = approval.handler.api(bound['pr'], 'user')
    if classify(approval.handler.host, context['user'], actor,
                settings['settings']['accounts']['personal'])['ownership'] != 'others':
        raise ValueError('Fork CI approval requires another author\'s PR')
    result = {**continuation, 'eligible_runs': [
        {'run_id': run['id'], 'attempt': run['run_attempt']} for run in eligible]}
    if bound.get('evidence'):
        try:
            evidence = read(Path(bound['evidence']))
            if isinstance(evidence, dict) and (
                    evidence.get('upstream') != bound.get('upstream')
                    or 'review_options' in bound and (
                        evidence.get('configuration') != 'skill-owned' or evidence.get('options') != options)):
                raise ValueError('Incoming-review invocation scope changed; reassess affected evidence')
            approval.evidence()
        except (ValueError, KeyError, TypeError, OSError) as exc:
            reason = str(exc)
        else:
            return {**result, 'action': 'approve', 'skill': 'dk-workflows:dk-ci-approve',
                    'evidence': bound['evidence']}
    else:
        reason = 'Standalone review has no current incoming-review evidence document'
    # Retained overrides govern only fully accepted evidence. Rejected evidence
    # must not redirect recovery scope, policy or task selection; use the current
    # explicit invocation, or current defaults when no overrides were supplied.
    if not current['permissions']['approve_fork_ci']:
        raise ValueError('Fork CI execution approval is disabled by current incoming-review policy')
    return {**result, 'action': 'review', 'skill': 'dk-workflows:dk-pr-review',
            'review_options': options, 'reason': reason}


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
        if publish.blocks(retained, {**bound, 'kind': 'ci-approve'}):
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
    parser.add_argument('action', choices=('snapshot', 'approve', 'bridge'))
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--request', type=Path, help='Retained authorized merge request for the read-only bridge')
    args = parser.parse_args()
    if args.action == 'bridge':
        if args.request is None or args.evidence is not None:
            parser.error('bridge requires --request and does not accept --evidence')
        result = bridge(args.request)
    else:
        if args.evidence is None or args.request is not None:
            parser.error(args.action + ' requires --evidence and does not accept --request')
        result = (snapshot if args.action == 'snapshot' else approve)(args.evidence)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Diagnostic as exc:
        print(json.dumps(exc.envelope()))
        sys.exit(2)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        sys.exit(2)
