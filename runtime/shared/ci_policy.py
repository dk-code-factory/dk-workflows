"""Shared exact-head CI decision: every failed test and step needs coverage."""
import time
import subprocess
from shared.ci_deadline import Budget, BoundedAPI, ClassificationTimeout
from shared.flaky_tests import digest, identity, now, require_head
from shared.flaky_actions import collect_snapshot, discover, evidence_file
from shared import ci_exceptions


def evaluate(snapshot, evidence, verification, repo, exceptions=None):
    """Pure evaluation of a pinned snapshot and freshly verified source evidence.

    Call decide() for live decisions. A serialized known_flakes label is never a
    reusable completion credential: settings, issue state and attempts must be read again.
    """
    raw = snapshot.get('raw_result', snapshot.get('result'))
    exceptions = exceptions or {'entries': [], 'configured': False, 'source': None, 'file': None}
    outcome = {'result': raw, 'raw_result': raw, 'head': snapshot.get('head'),
               'checks': snapshot.get('checks', []), 'ignored_failures': [], 'blockers': [],
               'verified_at': verification.get('checked_at'),
               'unavailable_issues': verification.get('unavailable_issues', [])}
    outcome['exception_config'] = exceptions
    def block(reason, **context):
        outcome['blockers'].append({'reason': reason, **context})
    if raw in {'success', 'failure'}:
        try:
            require_head(snapshot.get('head'))
        except ValueError:
            block('CI snapshot lacks a pinned full head SHA')
            outcome['result'] = 'failure'
            return outcome
    if raw != 'failure':
        if raw != 'success':
            block(raw or 'missing CI result')
        elif not snapshot.get('checks') or any(c.get('bucket') not in {'pass', 'skipping'}
                                               for c in snapshot['checks']):
            block('success lacks complete successful checks')
            outcome['result'] = 'failure'
        return outcome
    if (evidence.get('repo') != repo or evidence.get('head') != snapshot.get('head')
            or not verification.get('checked_at')):
        block('missing exact-head repository evidence or fresh issue verification')
        return outcome
    checks = snapshot.get('checks', [])
    if not checks:
        block('missing checks')
    if any(c.get('bucket') not in {'pass', 'skipping', 'fail'} for c in checks):
        block('pending, cancelled, or unsupported checks remain')
    failures = [c for c in checks if c.get('bucket') == 'fail']
    if not failures:
        block('no classifiable failed check')
    entries = [e for e in verification.get('entries', []) if e.get('active') is True
               and e.get('issue_state') == 'open'
               and e.get('checked_at') == verification['checked_at'] and e.get('proof')]
    for check in failures:
        link = check.get('link')
        matching = [item for item in evidence.get('checks', []) if item.get('link') == link]
        if len(matching) != 1 or not link:
            block('missing/ambiguous failed check evidence', link=link)
            continue
        item = matching[0]
        if (item.get('error') or not all(item.get(k) for k in ('run_id', 'attempt', 'job_id', 'workflow', 'job'))
                or item.get('status') != 'completed' or item.get('conclusion') != 'failure'):
            block(item.get('error') or 'job provenance/conclusion is incomplete', link=link)
            continue
        pipelines = ci_exceptions.matching(exceptions['entries'], 'pipeline', item)
        if pipelines:
            outcome['ignored_failures'].append({key: item[key] for key in
                ('link', 'workflow', 'job', 'run_id', 'attempt', 'job_id')} | {
                    'issues': [], 'exceptions': [ci_exceptions.receipt(exceptions, rule) for rule in pipelines]})
            continue
        steps = item.get('steps', [])
        if not steps or len({s.get('number') for s in steps}) != len(steps):
            block('missing/ambiguous steps', link=link)
            continue
        failed_steps = [s for s in steps if s.get('conclusion') == 'failure']
        if not failed_steps:
            block('job failure has no failed step', link=link)
        for step in steps:
            context = {'link': link, 'step': step.get('number')}
            if step.get('status') != 'completed' or step.get('conclusion') not in {'success', 'failure', 'skipped'}:
                block('cancelled, incomplete, or unsupported step', **context)
                continue
            report = step.get('report', {})
            tests = report.get('tests', [])
            failed = [t for t in tests if t.get('outcome') == 'fail']
            if step['conclusion'] == 'failure' and (report.get('complete') is not True or not failed):
                block(report.get('reason') or 'failed step lacks complete named-test evidence', **context)
                continue
            for test in failed:
                test_context = {'workflow': item['workflow'], 'job': item['job'], **test}
                related = [e for e in entries if identity(e) == identity(test_context)]
                configured = ci_exceptions.matching(exceptions['entries'], 'test', test_context)
                if report.get('complete') is not True or not (related or configured):
                    inactive = [{'url': e['issue_url'], 'status': e.get('status')}
                                for e in verification.get('entries', []) if identity(e) == identity(test_context)]
                    block('test failure has no verified open issue', code='unverified_named_test',
                          **context, test=test.get('test'),
                          signature=test.get('signature'), related_issues=inactive)
                else:
                    outcome['ignored_failures'].append(test_context | context |
                        {'run_id': item['run_id'], 'attempt': item['attempt'], 'job_id': item['job_id'],
                         'issues': sorted({e['issue_url'] for e in related}),
                         'exceptions': [ci_exceptions.receipt(exceptions, rule) for rule in configured]})
    if not outcome['blockers'] and outcome['ignored_failures']:
        configured = any(item.get('exceptions') for item in outcome['ignored_failures'])
        outcome['result'] = 'accepted_exceptions' if configured else 'known_flakes'
        outcome['summary'] = 'CI red; accepted configured exceptions' if configured else 'CI red; accepted known flakes'
    return outcome


def fingerprint(snapshot):
    checks = sorted(snapshot.get('checks', []), key=lambda c: digest(c))
    return digest({'head': snapshot.get('head'), 'checks': checks})


def failure_fingerprint(snapshot, evidence):
    failures = []
    for item in evidence.get('checks', []):
        tests = [identity({'workflow': item.get('workflow'), 'job': item.get('job'), **test})
                 for step in item.get('steps', []) for test in step.get('report', {}).get('tests', [])
                 if test.get('outcome') == 'fail']
        failures.append({'workflow': item.get('workflow'), 'job': item.get('job'),
                         'check_name': item.get('check_name'), 'check_workflow': item.get('check_workflow'),
                         'tests': sorted(tests, key=digest), 'error': item.get('error'),
                         'steps': [{'name': step.get('name'), 'conclusion': step.get('conclusion'),
                                    'reason': step.get('report', {}).get('reason')}
                                   for step in item.get('steps', []) if step.get('conclusion') == 'failure']})
    return digest({'head': snapshot.get('head'), 'failures': sorted(failures, key=digest)})


def discovery_targets(evidence, blockers, budget):
    """Map unknown failures in linear time, checking long reports in batches."""
    missing = {(b.get('link'), b.get('step'), b.get('test'), b.get('signature')) for b in blockers}
    targets = {}
    count = 0
    for item in evidence.get('checks', []):
        for step in item.get('steps', []):
            for test in step.get('report', {}).get('tests', []):
                if count % 128 == 0:
                    budget.remaining()
                count += 1
                if (test.get('outcome') == 'fail' and
                        (item['link'], step['number'], test['test'], test['signature']) in missing):
                    target = {'workflow': item['workflow'], 'job': item['job'], **test}
                    targets[tuple(identity(target))] = target
    budget.remaining()
    return list(targets.values())


def _decide(api, registry, snapshot, refresh_snapshot, auto_discover, budget):
    """One-shot decision after the sole waiter settles checks; never poll/rerun."""
    try:
        exceptions = ci_exceptions.load('remote:' + registry.repo)
    except (ValueError, OSError) as exc:
        decision = {'result': 'failure', 'raw_result': snapshot['result'], 'head': snapshot['head'],
                    'checks': snapshot.get('checks', []), 'ignored_failures': [],
                    'blockers': [{'reason': 'invalid CI exception configuration: ' + str(exc)}]}
        return decision
    budget.progress('collect_current', raw_result=snapshot['result'])
    evidence = collect_snapshot(api, registry, snapshot, exceptions['entries']) if snapshot['result'] == 'failure' else {}
    # New observations can prove a pass or match an existing issue.
    verification = registry.synchronize([], api.issue)
    decision = evaluate(snapshot, evidence, verification, registry.repo, exceptions)
    discovery = None
    # History cannot repair malformed reports, missing provenance or unsettled CI.
    # Only wholly classifiable named failures with missing issue proof can benefit.
    unknown = decision['blockers']
    eligible = (auto_discover and snapshot['result'] == 'failure' and unknown
                and all(b.get('code') == 'unverified_named_test' for b in unknown))
    key = failure_fingerprint(snapshot, evidence) if eligible else None
    targets = (discovery_targets(evidence, unknown, budget) if eligible
               and key not in registry.load().get('scans', {}) else [])
    if targets:
        def claim(data):
            scans = data.setdefault('scans', {})
            if key in scans:
                return False
            scans[key] = {'head': snapshot['head'], 'started_at': now(), 'status': 'running', 'targets': targets}
            return True
        if registry.update(claim):
            try:
                budget.progress('discover_unknown', target_count=len(targets))
                discovery = discover(api, registry, targets=targets)
            except ClassificationTimeout:
                registry.update(lambda data: data['scans'][key].update(finished_at=now(), status='timed_out'))
                raise
            registry.update(lambda data: data['scans'][key].update(
                finished_at=now(), status='completed', evidence_file=discovery['evidence_file']))
            verification = registry.synchronize([], api.issue)
            decision = evaluate(snapshot, evidence, verification, registry.repo, exceptions)
    # A rerun changes classification even if the PR SHA is unchanged. Read the
    # attempt after collection/discovery, and compare the entire check snapshot.
    for item in evidence.get('checks', []):
        if item.get('error') or not item.get('run_id'):
            continue
        try:
            run = api.request(f'actions/runs/{item["run_id"]}')
            if (run.get('head_sha') != snapshot['head'] or run.get('run_attempt') != item['attempt']
                    or run.get('status') != 'completed'):
                raise RuntimeError('head or run attempt changed during classification')
        except (RuntimeError, OSError, ValueError) as exc:
            decision['blockers'].append({'reason': str(exc), 'link': item['link']})
    budget.progress('verify_current')
    latest = refresh_snapshot()
    budget.remaining()
    if fingerprint(latest) != fingerprint(snapshot) or latest.get('result') != snapshot['result']:
        decision['blockers'].append({'reason': 'CI snapshot or head changed during classification'})
        if latest.get('result') == 'head_changed':
            decision['result'] = 'head_changed'
    try:
        if ci_exceptions.load('remote:' + registry.repo)['fingerprint'] != exceptions['fingerprint']:
            raise ValueError('CI exception configuration changed during classification')
    except (ValueError, OSError) as exc:
        decision['blockers'].append({'reason': str(exc)})
    if decision['blockers'] and decision['result'] in {'success', 'known_flakes', 'accepted_exceptions'}:
        decision['result'] = 'failure'
    decision['evidence_file'] = evidence.get('evidence_file')
    if discovery:
        decision['discovery'] = {k: discovery[k] for k in ('limits', 'limits_reached', 'missing', 'evidence_file')}
    return decision


def decide(api, registry, snapshot, refresh_snapshot, auto_discover=True, *,
           classification_timeout=60, clock=time.monotonic, on_progress=None):
    """Bound live classification; timeout always leaves terminal CI unaccepted."""
    budget = Budget(classification_timeout, clock, on_progress)
    try:
        budget.progress('classification', raw_result=snapshot['result'], head=snapshot['head'])
        with budget.activate():
            decision = _decide(BoundedAPI(api, budget), registry, snapshot, refresh_snapshot,
                               auto_discover, budget)
        budget.remaining()
        timed_out = False
    except (ClassificationTimeout, subprocess.TimeoutExpired) as exc:
        timed_out = True
        decision = {'result': 'failure', 'raw_result': snapshot['result'], 'head': snapshot['head'],
                    'checks': snapshot.get('checks', []), 'ignored_failures': [],
                    'blockers': [{'reason': 'CI classification deadline exceeded: ' + str(exc)}]}
    decision['classification'] = {'timeout_seconds': classification_timeout,
        'elapsed_seconds': round(clock() - budget.started, 1), 'phase': budget.phase,
        'timed_out': timed_out}
    if timed_out:
        decision['classification']['next_action'] = (
            'Inspect retained evidence; run explicit narrowed flake discovery or repair current report parsing, '
            'then recheck live CI. An unchanged automatic scan is not retried.')
    decision['decision_file'] = evidence_file(registry, decision)
    return decision
