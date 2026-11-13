"""Failure-triggered flake learning from the PR being handled.

Keep the historical Actions reader unchanged: its content fingerprint and retained
attempt artifacts are shared with explicit repository-wide discovery.
"""
import json
import subprocess

from shared import flaky_actions as actions
from shared.ci_deadline import ClassificationTimeout, check_budget
from shared.flaky_tests import identity, require_head
from shared.github_reads import run as github_run


class GitHub(actions.GitHub):
    def find_issues(self, targets, limit):
        """Search exact test names, sharing a bounded result/query allowance."""
        issues = {}
        for test in sorted({target['test'] for target in targets})[:limit]:
            check_budget()
            remaining = limit - len(issues)
            if remaining <= 0:
                break
            query = json.dumps(test, ensure_ascii=False) + ' in:title,body sort:updated-desc'
            command = ['gh', 'issue', 'list', '--repo', self.repo, '--state', 'open',
                       '--search', query, '--limit', str(remaining),
                       '--json', 'number,url,title,body,updatedAt,state']
            completed = github_run(command, capture_output=True, text=True, timeout=60)
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or 'targeted issue lookup unavailable')
            for issue in json.loads(completed.stdout):
                issues[issue['url']] = {'number': issue['number'], 'html_url': issue['url'],
                    'title': issue['title'], 'body': issue['body'],
                    'updated_at': issue['updatedAt'], 'state': issue['state'].lower()}
        return list(issues.values())


def investigate(api, registry, *, targets, max_issues=500, refresh_targets=None):
    """Look up issues for observed PR failures; never enumerate Actions history."""
    if not 1 <= max_issues <= 500:
        raise ValueError('max_issues must be between 1 and 500')
    report = {'limits': {'open_issues': max_issues}, 'limits_reached': [], 'missing': []}
    try:
        issues = api.find_issues(targets, max_issues)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        issues = []
        report['missing'].append(str(exc))
    if len(issues) == max_issues or len({t['test'] for t in targets}) > max_issues:
        report['limits_reached'].append('open_issues')
    # Retain original query evidence, not a second rendered verification report.
    report['evidence_file'] = actions.evidence_file(registry, {'open_issues': issues})
    report['verification'] = registry.synchronize(issues, api.issue,
        targets=targets if refresh_targets is None else refresh_targets)
    return report


def failure_targets(evidence):
    targets = {}
    for item in evidence.get('checks', []):
        for step in item.get('steps', []):
            for test in step.get('report', {}).get('tests', []):
                check_budget()
                if test.get('outcome') == 'fail':
                    target = {'workflow': item.get('workflow'), 'job': item.get('job'), **test}
                    targets[tuple(identity(target))] = target
    return list(targets.values())


def missing_followup_steps(collected, failed_steps):
    """Check prior failing steps without treating setup logs as test reports."""
    return [f'{job["job_id"]}/{step["number"]}: {step["report"]["reason"]}'
            for job in collected['jobs'] for step in job['steps']
            if (collected['workflow'], job['job'], step['number']) in failed_steps
            and step.get('conclusion') == 'success' and not step['report']['complete']]


def collect_followups(api, registry, snapshot):
    """Collect named passes only after an observed failure on this PR head.

    Current check links select runs; no repository run listing is used. Existing
    attempt caches retain their keys and original artifacts across sessions.
    """
    require_head(snapshot['head'])
    failures = [item for item in registry.load().get('observations', [])
                if item.get('outcome') == 'fail' and item.get('complete') is True
                and item.get('head') == snapshot['head']]
    if not failures:
        return None
    scope = {(item['workflow'], item['job']) for item in failures}
    failed_steps = {(item['workflow'], item['job'], item.get('step')) for item in failures}
    report = {'attempts': [], 'missing': []}
    seen = set()
    for check in snapshot.get('checks', []):
        check_budget()
        if check.get('bucket') != 'pass':
            continue
        try:
            run_id, job_id = actions.check_job(registry.repo, check.get('link'))
            if run_id in seen:
                continue
            seen.add(run_id)
            run = api.request(f'actions/runs/{run_id}')
            if (run.get('id') != run_id or run.get('head_sha') != snapshot['head']
                    or run.get('status') != 'completed'):
                raise ValueError('follow-up head changed or run is pending')
            if not any(workflow == run.get('path') for workflow, job in scope):
                continue
            collected = actions.collect_attempt(api, registry, run, run['run_attempt'],
                                                reuse=True, job_scope=scope)
            missing = missing_followup_steps(collected, failed_steps)
            if collected.get('cached') and missing:
                # Historical caches may contain incomplete successful test steps.
                # Refresh only this attempt, retaining its original artifacts/key.
                collected = actions.collect_attempt(api, registry, run, run['run_attempt'],
                                                    reuse=False, job_scope=scope)
                missing = missing_followup_steps(collected, failed_steps)
            if not any(job['job_id'] == job_id for job in collected['jobs']):
                raise ValueError('follow-up check is not in the observed failing job scope')
            current = api.request(f'actions/runs/{run_id}')
            if any(current.get(key) != run.get(key) for key in ('id', 'head_sha', 'run_attempt', 'status', 'path')):
                raise ValueError('follow-up attempt changed during collection')
            report['missing'].extend(collected['missing'])
            report['missing'].extend(missing)
            report['attempts'].append({'run_id': run_id, 'attempt': run['run_attempt'],
                'source_file': collected['source_file'], 'archive_file': collected.get('archive_file')})
        except ClassificationTimeout:
            raise
        except (RuntimeError, OSError, ValueError, KeyError) as exc:
            # Learning cannot turn missing log evidence into proof or fail green CI.
            report['missing'].append(str(exc))
    return report


def compact(output, *, offset=0, limit=20):
    """Return counts and evidence references without expanding proof/log history."""
    verification = output.get('verification', output)
    entries = verification.get('entries', [])
    counts = {}
    for entry in entries:
        status = entry.get('status', 'candidate')
        counts[status] = counts.get(status, 0) + 1
    unlinked = output.get('unlinked_candidates', [])
    items = [{key: entry.get(key) for key in
              ('workflow', 'job', 'test', 'signature', 'status', 'issue_url')}
             for entry in entries]
    unique_unlinked = {tuple(identity(item)): item for item in unlinked}
    items.extend({**{key: item.get(key) for key in ('workflow', 'job', 'test', 'signature')},
                  'status': 'unlinked'} for item in unique_unlinked.values())
    items.sort(key=lambda item: tuple(item.get(key) or '' for key in
                                     ('workflow', 'job', 'test', 'signature', 'issue_url')))
    end = min(offset + limit, len(items))
    return {**{key: value for key, value in output.items()
               if key not in {'verification', 'entries', 'observations', 'unlinked_candidates',
                              'targets', 'missing', 'issue_errors', 'unavailable_issues'}},
            'counts': counts, 'unlinked_candidates': len(unique_unlinked),
            'entries': items[offset:end], 'total': len(items),
            'next_offset': end if end < len(items) else None,
            'checked_at': verification.get('checked_at'),
            'target_count': len(output.get('targets') or []),
            'missing': output.get('missing', [])[:limit], 'missing_count': len(output.get('missing', [])),
            'unavailable_issues': verification.get('unavailable_issues', [])[:limit],
            'unavailable_issue_count': len(verification.get('unavailable_issues', [])),
            'issue_errors': dict(list(verification.get('issue_errors', {}).items())[:limit]),
            'issue_error_count': len(verification.get('issue_errors', {}))}
