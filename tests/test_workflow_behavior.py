"""Synthetic policy/plumbing controls, never actual agent-behavior measurements."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/workflow_behavior'
SCRIPT = ROOT / 'scripts/evaluate_workflow_trace.py'
spec = importlib.util.spec_from_file_location('workflow_trace_evaluator', SCRIPT)
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


def fixture_trace():
    return json.loads((FIXTURES / 'representative.json').read_text())


def save_trace(tmp_path, trace):
    # Regenerate synthetic raw observations when deliberately changing a control.
    raw = ''.join(json.dumps({k: v for k, v in event.items() if k != 'evidence'}, sort_keys=True) + '\n'
                  for event in trace['events']).encode()
    capture = tmp_path / 'capture.jsonl'
    capture.write_bytes(raw)
    trace['sources']['capture'] = {'path': capture.name, 'sha256': hashlib.sha256(raw).hexdigest()}
    for index, event in enumerate(trace['events']):
        event['evidence'] = {'source': 'capture', 'line': index + 1}
    path = tmp_path / 'trace.json'
    path.write_text(json.dumps(trace))
    return path


def codes(report):
    return {finding['code'] for check in report['checks'].values() for finding in check['findings']}


ENTRYPOINT_CASES = json.loads((FIXTURES / 'entrypoints.json').read_text())


@pytest.mark.parametrize('entrypoint', ENTRYPOINT_CASES)
def test_synthetic_policy_example_for_shared_entrypoints(tmp_path, entrypoint):
    trace = fixture_trace()
    case = ENTRYPOINT_CASES[entrypoint]
    trace['entrypoint'] = entrypoint
    trace['events'] = [e for e in trace['events']
        if (case['ci'] or not e['kind'].startswith('ci_'))
        and (e['kind'] not in ('spawn', 'reuse_worker') or e['role'] in case['roles'])]
    # A real failure/result justifies a query; an unchanged timeout cannot.
    result = next(e for e in trace['events'] if e['kind'] == 'notification' and e['signal'] == 'result')
    trace['events'].insert(trace['events'].index(result) + 1,
        {'id': 'source-needed', 'kind': 'query', 'at_ms': result['at_ms'], 'category': 'github', 'cause': result['id']})
    for family in case['unknown']:
        trace['coverage'][family] = 'unavailable'
    report = evaluator.evaluate(save_trace(tmp_path, trace))
    assert report['status'] == ('unknown' if case['unknown'] else 'passed')
    for family, check in report['checks'].items():
        assert check['status'] == ('unknown' if family in case['unknown'] else 'passed')
    assert report['agent_behavior_verified'] is False
    assert report['authority'] == 'diagnostic-only'
    trace['events'][0] = {**trace['events'][0], 'kind': 'query', 'category': 'github',
                          'cause': result['id']}
    # A future cause is invalid rather than accepted as a claimed justification.
    del trace['events'][0]['stage']
    with pytest.raises(evaluator.InvalidTrace):
        evaluator.evaluate(save_trace(tmp_path, trace))


CONTROLS = json.loads((FIXTURES / 'negative-controls.json').read_text())


@pytest.mark.parametrize('control', CONTROLS, ids=lambda c: c['name'])
def test_synthetic_negative_controls(tmp_path, control):
    trace = fixture_trace()
    target = next(e for e in trace['events'] if e['id'] == control['event'])
    target.update(copy.deepcopy(control['set']))
    if control['name'] == 'late-host-update':
        update = next(e for e in trace['events'] if e['kind'] == 'update' and e['cause'] == target['id'])
        update['at_ms'] += 1
    report = evaluator.evaluate(save_trace(tmp_path, trace))
    assert report['status'] == 'failed'
    assert control['code'] in codes(report)


@pytest.mark.parametrize('category', ['agent_status', 'file', 'git', 'github', 'provider', 'ci'])
def test_every_timeout_driven_evidence_query_is_rejected(tmp_path, category):
    trace = fixture_trace()
    trace['events'].insert(1, {'id': 'poll', 'at_ms': 0, 'kind': 'query', 'category': category,
                               'cause': trace['events'][0]['id']})
    assert 'timeout_or_commentary_query' in codes(evaluator.evaluate(save_trace(tmp_path, trace)))



@pytest.mark.parametrize('justification', ['query', 'timeout_query', 'output', 'preparation'])
def test_chained_or_irrelevant_query_justification_is_invalid(tmp_path, justification):
    trace = fixture_trace()
    if justification in ('query', 'timeout_query'):
        notification = next(e for e in trace['events'] if e['kind'] == 'notification' and e['signal'] == 'result')
        anchor = {'id': 'first-query', 'kind': 'query', 'at_ms': notification['at_ms'], 'category': 'github',
                  'cause': notification['id'] if justification == 'query' else trace['events'][0]['id']}
        trace['events'].insert(trace['events'].index(notification) + 1, anchor)
    else:
        anchor = next(e for e in trace['events'] if e['kind'] == justification)
    trace['events'].insert(trace['events'].index(anchor) + 1,
        {'id': 'unjustified-query', 'kind': 'query', 'at_ms': anchor['at_ms'],
         'category': 'github', 'cause': anchor['id']})
    with pytest.raises(evaluator.InvalidTrace, match='unsupported query cause'):
        evaluator.evaluate(save_trace(tmp_path, trace))


def test_duplicate_waiter_completion_and_repeated_help_controls(tmp_path):
    trace = fixture_trace()
    for kind in ('ci_start', 'message', 'output'):
        event = next(e for e in trace['events'] if e['kind'] == kind and (kind != 'output' or e['mode'] == 'help'))
        duplicate = copy.deepcopy(event)
        duplicate['id'] += '-duplicate'
        trace['events'].insert(trace['events'].index(event) + 1, duplicate)
    report = evaluator.evaluate(save_trace(tmp_path, trace))
    assert {'duplicate_ci_waiter', 'duplicate_completion', 'unjustified_full_or_repeated_output'} <= codes(report)


def test_commentary_cannot_add_ci_queries(tmp_path):
    trace = fixture_trace()
    observation = next(e for e in trace['events'] if e['kind'] == 'ci_observe')
    trace['events'].insert(trace['events'].index(observation) + 1,
        {'id': 'extra-ci', 'at_ms': observation['at_ms'], 'kind': 'query', 'category': 'ci', 'cause': 'e2'})
    assert 'extra_ci_query' in codes(evaluator.evaluate(save_trace(tmp_path, trace)))


def test_concrete_diagnostic_need_allows_full_output_and_second_help(tmp_path):
    trace = fixture_trace()
    for event in trace['events']:
        if event['kind'] == 'output' and event['mode'] == 'page':
            event.update(mode='full_log', need='Inspect the retained traceback to classify the reported failure.',
                         need_event=next(e['id'] for e in trace['events'] if e['kind'] == 'notification' and e['signal'] == 'failure'))
    # Keep the bounded page needed by the representative coverage contract.
    page = next(e for e in fixture_trace()['events'] if e['kind'] == 'output' and e['mode'] == 'page')
    page.update(id='bounded-page', at_ms=trace['events'][-1]['at_ms'])
    trace['events'].append(page)
    assert evaluator.evaluate(save_trace(tmp_path, trace))['status'] == 'passed'


@pytest.mark.parametrize('mutation', ['unavailable', 'missing', 'empty', 'partial'])
def test_unobserved_policies_never_pass(tmp_path, mutation):
    trace = fixture_trace()
    if mutation == 'unavailable':
        trace['coverage']['handoffs'] = 'unavailable'
    elif mutation == 'empty':
        trace['events'] = []
    elif mutation == 'partial':
        trace['events'] = trace['events'][:1]
    path = save_trace(tmp_path, trace)
    if mutation == 'missing':
        (tmp_path / 'capture.jsonl').unlink()
    report = evaluator.evaluate(path)
    assert report['status'] != 'passed'
    assert any(check['status'] == 'unknown' for check in report['checks'].values())
    if mutation == 'missing':
        assert {'unsafe_checkpoint_resume', 'missing_complete_output'} <= codes(report)


@pytest.mark.parametrize('mutation', ['hash', 'unknown-kind', 'pass-label', 'bad-cause', 'bad-time', 'bad-ref', 'duplicate', 'bad-type'])
def test_invalid_observations_fail_visibly(tmp_path, mutation):
    trace = fixture_trace()
    if mutation == 'unknown-kind':
        trace['events'][0]['kind'] = 'passed'
    elif mutation == 'pass-label':
        trace['events'][0]['passed'] = True
    elif mutation == 'bad-cause':
        next(e for e in trace['events'] if e['kind'] == 'update')['cause'] = 'not-observed'
    elif mutation == 'bad-time':
        trace['events'][0]['at_ms'] = -1
    elif mutation == 'duplicate':
        trace['events'][1]['id'] = trace['events'][0]['id']
    elif mutation == 'bad-type':
        trace['events'][0]['at_ms'] = True
    path = save_trace(tmp_path, trace)
    if mutation == 'hash':
        (tmp_path / 'capture.jsonl').write_text('tampered evidence')
    elif mutation == 'bad-ref':
        trace['events'][0]['evidence']['line'] = 100000
        path.write_text(json.dumps(trace))
    with pytest.raises(evaluator.InvalidTrace):
        evaluator.evaluate(path)
    report_file = tmp_path / 'invalid-report.json'
    result = subprocess.run([sys.executable, str(SCRIPT), str(path), '--report', str(report_file)],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)['status'] == 'invalid'
    assert json.loads(report_file.read_text())['error']


def test_cli_bounded_private_report_and_no_overwrite(tmp_path):
    report = tmp_path / 'report.json'
    command = [sys.executable, str(SCRIPT), str(FIXTURES / 'representative.json'), '--report', str(report)]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    receipt = json.loads(result.stdout)
    assert len(result.stdout) < 1024 and receipt['status'] == 'passed'
    assert os.stat(report).st_mode & 0o777 == 0o600
    saved = report.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode == 2
    assert report.read_bytes() == saved
    inside_repo = subprocess.run([*command[:-1], str(ROOT / 'never-create-behavior-report.json')], capture_output=True)
    assert inside_repo.returncode == 2
    assert not (ROOT / 'never-create-behavior-report.json').exists()
