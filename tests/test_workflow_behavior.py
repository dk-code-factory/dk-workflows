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


@pytest.mark.parametrize('pin', ['head', 'base', 'preparation_id', 'engineering_id'])
@pytest.mark.parametrize('value', ['', ' \t\n '])
def test_blank_handoff_identity_pins_are_invalid(tmp_path, pin, value):
    trace = fixture_trace()
    next(e for e in trace['events'] if e['kind'] == 'spawn')['handoff']['pins'][pin] = value
    with pytest.raises(evaluator.InvalidTrace, match=f'invalid handoff pin: {pin}'):
        evaluator.evaluate(save_trace(tmp_path, trace))


@pytest.mark.parametrize('field', ['task', 'permitted_actions'])
@pytest.mark.parametrize('value', ['', ' \t\n '])
def test_blank_handoff_task_or_actions_are_rejected(tmp_path, field, value):
    trace = fixture_trace()
    next(e for e in trace['events'] if e['kind'] == 'spawn')['handoff'][field] = value
    report = evaluator.evaluate(save_trace(tmp_path, trace))
    assert report['checks']['handoffs']['status'] == 'failed'
    assert 'handoff_not_narrow_or_pinned' in codes(report)


@pytest.mark.parametrize('scenario', ['ci-head', 'ci-waiter', 'checkpoint-head',
                                     'preparation-id', 'worker-id', 'event-id'])
@pytest.mark.parametrize('value,valid', [('', False), (' \t\n ', False), ('opaque identity:42', True)])
def test_linked_observations_require_nonblank_opaque_identities(tmp_path, scenario, value, valid):
    trace = fixture_trace()
    for event in trace['events']:
        if scenario == 'ci-head' and event['kind'].startswith('ci_'):
            event['head'] = value
        elif scenario == 'ci-waiter' and event['kind'].startswith('ci_'):
            event['waiter'] = value
        elif scenario == 'checkpoint-head' and event['kind'] in ('checkpoint', 'resume'):
            event['head'] = value
        elif scenario == 'preparation-id':
            for field in ('preparation_id', 'previous', 'current', 'preparation'):
                if event.get(field) == 'prep-a':
                    event[field] = value
        elif scenario == 'worker-id' and event.get('agent') == 'fixer-a':
            event['agent'] = value
        elif scenario == 'event-id':
            if event['id'] == 'e36':
                event['id'] = value
            if event.get('checkpoint') == 'e36':
                event['checkpoint'] = value
    path = save_trace(tmp_path, trace)
    if valid:
        assert evaluator.evaluate(path)['status'] == 'passed'
    else:
        with pytest.raises(evaluator.InvalidTrace, match='identity'):
            evaluator.evaluate(path)


@pytest.mark.parametrize('kind,field', [('timeout', 'stage'), ('message', 'agent'),
                                      ('output', 'artifact'), ('update', 'cause'),
                                      ('refresh', 'current'), ('resume', 'preparation')])
@pytest.mark.parametrize('value', ['', ' \t\n '])
def test_blank_observation_identifiers_and_references_are_invalid(tmp_path, kind, field, value):
    trace = fixture_trace()
    next(e for e in trace['events'] if e['kind'] == kind)[field] = value
    with pytest.raises(evaluator.InvalidTrace, match='identity'):
        evaluator.evaluate(save_trace(tmp_path, trace))


@pytest.mark.parametrize('message_type', ['material', 'heartbeat'])
def test_noncompletion_messages_allow_empty_result(tmp_path, message_type):
    trace = fixture_trace()
    next(e for e in trace['events'] if e['kind'] == 'message').update(type=message_type, result='')
    report = evaluator.evaluate(save_trace(tmp_path, trace))
    assert report['status'] == ('failed' if message_type == 'heartbeat' else 'passed')
    assert codes(report) == ({'routine_heartbeat'} if message_type == 'heartbeat' else set())


@pytest.mark.parametrize('message_type', ['automatic_completion', 'completion'])
@pytest.mark.parametrize('result', ['', ' \t\n ', 'opaque result:42', 'preparation-result'])
@pytest.mark.parametrize('duplicate', [False, True])
def test_completion_result_identity_and_duplicate_controls(tmp_path, message_type, result, duplicate):
    trace = fixture_trace()
    original = next(e for e in trace['events'] if e['kind'] == 'message')
    original['type'] = message_type
    event = copy.deepcopy(original) if duplicate else original
    if duplicate:
        event['id'] = 'completion-copy'
        trace['events'].insert(trace['events'].index(original) + 1, event)
    event['result'] = result
    path = save_trace(tmp_path, trace)
    if result in ('', ' \t\n '):
        with pytest.raises(evaluator.InvalidTrace, match='invalid completion result identity'):
            evaluator.evaluate(path)
    else:
        report = evaluator.evaluate(path)
        repeated = duplicate and result == 'preparation-result'
        assert report['status'] == ('failed' if repeated else 'passed')
        assert codes(report) == ({'duplicate_completion'} if repeated else set())


@pytest.mark.parametrize('helper', ['', ' \t\n ', 'opaque helper:42', 'run-state'])
@pytest.mark.parametrize('duplicate', [False, True])
def test_help_helper_identity_and_repeated_discovery_controls(tmp_path, helper, duplicate):
    trace = fixture_trace()
    original = next(e for e in trace['events'] if e['kind'] == 'output' and e['mode'] == 'help')
    event = copy.deepcopy(original) if duplicate else original
    if duplicate:
        event['id'] = 'help-copy'
        trace['events'].insert(trace['events'].index(original) + 1, event)
    event['helper'] = helper
    path = save_trace(tmp_path, trace)
    if helper in ('', ' \t\n '):
        with pytest.raises(evaluator.InvalidTrace, match='invalid help helper identity'):
            evaluator.evaluate(path)
    else:
        report = evaluator.evaluate(path)
        repeated = duplicate and helper == 'run-state'
        assert report['status'] == ('failed' if repeated else 'passed')
        assert codes(report) == ({'unjustified_full_or_repeated_output'} if repeated else set())


@pytest.mark.parametrize('mode', ['receipt', 'page', 'full_log', 'full_diff', 'full_conversation'])
def test_nonhelp_output_allows_empty_helper(tmp_path, mode):
    trace = fixture_trace()
    if mode in ('receipt', 'page'):
        next(e for e in trace['events'] if e['kind'] == 'output' and e['mode'] == mode)['helper'] = ''
    else:
        output = copy.deepcopy(next(e for e in trace['events'] if e['kind'] == 'output'))
        output.update(id='diagnostic-output', at_ms=trace['events'][-1]['at_ms'], mode=mode, helper='',
                      need='Inspect the retained evidence for the reported failure.', need_event='e10')
        trace['events'].append(output)
    assert evaluator.evaluate(save_trace(tmp_path, trace))['status'] == 'passed'


@pytest.mark.parametrize('source', ['', ' \t\n '])
def test_blank_source_identifier_is_invalid(tmp_path, source):
    trace = fixture_trace()
    path = save_trace(tmp_path, trace)
    trace['sources'][source] = trace['sources'].pop('capture')
    for event in trace['events']:
        event['evidence']['source'] = source
    path.write_text(json.dumps(trace))
    with pytest.raises(evaluator.InvalidTrace, match='invalid source ID'):
        evaluator.evaluate(path)


def test_optional_output_fields_may_remain_empty(tmp_path):
    trace = fixture_trace()
    output = next(e for e in trace['events'] if e['kind'] == 'output')
    output['need_event'] = ''
    output['need'] = output['text'] = ''
    assert evaluator.evaluate(save_trace(tmp_path, trace))['status'] == 'passed'


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


@pytest.mark.parametrize('signal', ['failure', 'decision'])
@pytest.mark.parametrize('text', ['', ' \t\n '])
def test_material_notification_requires_nonblank_report(tmp_path, signal, text):
    trace = fixture_trace()
    notification = next(e for e in trace['events'] if e['kind'] == 'notification' and e['signal'] == signal)
    next(e for e in trace['events'] if e['kind'] == 'update' and e['cause'] == notification['id'])['text'] = text
    report = evaluator.evaluate(save_trace(tmp_path, trace))
    assert report['checks']['communication']['status'] == 'failed'
    assert 'material_report_delayed' in codes(report)


def consolidated_trace(signals, cause_index=0):
    trace = fixture_trace()
    first = next(e for e in trace['events'] if e['id'] == 'e10')
    report = next(e for e in trace['events'] if e['id'] == 'e13')
    batch = []
    for index, signal in enumerate(signals):
        event = copy.deepcopy(first)
        event.update(id=('e10', 'e12')[index] if index < 2 else f'batch-{index}',
                     at_ms=first['at_ms'] + index, signal=signal)
        batch.append(event)
    report.update(at_ms=batch[-1]['at_ms'], cause=batch[cause_index]['id'],
                  text='All reported failures need investigation, and decisions remain pending.')
    start = trace['events'].index(first)
    trace['events'][start:start + 4] = [*batch, report]
    return trace, batch, report


@pytest.mark.parametrize('signals', [('failure', 'decision'), ('failure', 'decision', 'failure'),
                                    ('result', 'failure', 'decision', 'completion')])
@pytest.mark.parametrize('cause_index', [0, -1])
def test_contiguous_notifications_allow_one_immediate_report(tmp_path, signals, cause_index):
    trace, _, _ = consolidated_trace(signals, cause_index)
    assert evaluator.evaluate(save_trace(tmp_path, trace))['status'] == 'passed'


@pytest.mark.parametrize('mutation', ['blank', 'whitespace', 'output', 'query', 'timeout',
                                     'unrelated-cause', 'outside-batch', 'omitted',
                                     'no-following-action', 'nonupdate'])
def test_consolidated_reports_preserve_promptness_and_batch_causality(tmp_path, mutation):
    trace, batch, report = consolidated_trace(('failure', 'decision'))
    if mutation in ('blank', 'whitespace'):
        report['text'] = '' if mutation == 'blank' else ' \t\n '
    elif mutation == 'unrelated-cause':
        report['cause'] = 'e2'
    elif mutation == 'outside-batch':
        prior = copy.deepcopy(batch[0])
        prior.update(id='earlier-notification', signal='result')
        boundary = {'id': 'earlier-action', 'kind': 'timeout', 'at_ms': prior['at_ms'], 'stage': 'preparation'}
        start = trace['events'].index(batch[0])
        trace['events'][start:start] = [prior, boundary]
        report['cause'] = prior['id']
    elif mutation == 'omitted':
        trace['events'].remove(report)
    elif mutation == 'no-following-action':
        trace['events'] = trace['events'][:trace['events'].index(report)]
    else:
        action = {'id': 'intervening-action', 'kind': mutation, 'at_ms': report['at_ms']}
        if mutation == 'output':
            action = copy.deepcopy(next(e for e in trace['events'] if e['kind'] == 'output'))
            action.update(id='intervening-action', at_ms=report['at_ms'])
        elif mutation == 'query':
            action.update(category='git', cause=batch[0]['id'])
        elif mutation == 'timeout':
            action['stage'] = 'preparation'
        else:
            action.update(kind='transition', stage='feedback', cause=batch[0]['id'])
        index = trace['events'].index(report)
        if mutation == 'nonupdate':
            trace['events'][index] = action
        else:
            trace['events'].insert(index, action)
    result = evaluator.evaluate(save_trace(tmp_path, trace))
    assert result['checks']['communication']['status'] == 'failed'
    assert 'material_report_delayed' in codes(result)


@pytest.mark.parametrize('premature', [False, True])
def test_ci_cadence_restarts_with_each_waiter_session(tmp_path, premature):
    trace = fixture_trace()
    start = copy.deepcopy(next(e for e in trace['events'] if e['kind'] == 'ci_start'))
    at = trace['events'][-1]['at_ms']
    start.update(id='restart', at_ms=at)
    trace['events'].extend([start,
        {'id': 'restart-initial', 'kind': 'ci_observe', 'at_ms': at, 'head': start['head'], 'waiter': start['waiter']}])
    if premature:
        trace['events'].append({'id': 'restart-extra', 'kind': 'ci_observe', 'at_ms': at + 1,
                                'head': start['head'], 'waiter': start['waiter']})
    trace['events'].append({'id': 'restart-end', 'kind': 'ci_end', 'at_ms': at + 2,
                            'head': start['head'], 'waiter': start['waiter']})
    report = evaluator.evaluate(save_trace(tmp_path, trace))
    assert report['status'] == ('failed' if premature else 'passed')
    assert ('ci_cadence_accelerated' in codes(report)) is premature


@pytest.mark.parametrize('mutation', ['terminal', 'no-deadline', 'early', 'no-end', 'later-observation'])
def test_only_terminal_deadline_observation_may_shorten_ci_interval(tmp_path, mutation):
    trace = fixture_trace()
    start = next(e for e in trace['events'] if e['kind'] == 'ci_start')
    observations = [e for e in trace['events'] if e['kind'] == 'ci_observe']
    start['deadline_ms'] = start['at_ms'] + 35000
    observations[-1]['at_ms'] = start['deadline_ms']
    if mutation == 'no-deadline':
        del start['deadline_ms']
    elif mutation == 'early':
        observations[-1]['at_ms'] -= 1
    elif mutation == 'no-end':
        trace['events'] = [e for e in trace['events'] if e['kind'] != 'ci_end']
    elif mutation == 'later-observation':
        extra = copy.deepcopy(observations[-1])
        extra.update(id='post-deadline', at_ms=extra['at_ms'] + 1)
        trace['events'].insert(trace['events'].index(observations[-1]) + 1, extra)
    report = evaluator.evaluate(save_trace(tmp_path, trace))
    assert report['status'] == ('passed' if mutation == 'terminal' else 'failed')
    assert ('ci_cadence_accelerated' in codes(report)) is (mutation != 'terminal')


@pytest.mark.parametrize('deadline', [-1, True, '35000', None])
def test_malformed_ci_deadline_is_invalid(tmp_path, deadline):
    trace = fixture_trace()
    next(e for e in trace['events'] if e['kind'] == 'ci_start')['deadline_ms'] = deadline
    with pytest.raises(evaluator.InvalidTrace, match='CI deadline|ci_start field type'):
        evaluator.evaluate(save_trace(tmp_path, trace))


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
    help_event = copy.deepcopy(next(e for e in trace['events'] if e['kind'] == 'output' and e['mode'] == 'help'))
    help_event.update(id='second-help', at_ms=page['at_ms'],
                      need='Inspect help options to diagnose the reported failure.',
                      need_event=next(e['id'] for e in trace['events'] if e['kind'] == 'notification' and e['signal'] == 'failure'))
    trace['events'].append(help_event)
    assert evaluator.evaluate(save_trace(tmp_path, trace))['status'] == 'passed'
    help_event['need'] = ' '
    assert 'unjustified_full_or_repeated_output' in codes(evaluator.evaluate(save_trace(tmp_path, trace)))


@pytest.mark.parametrize('key', ['tree', 'base', 'scope', 'runtime'])
@pytest.mark.parametrize('value', ['', ' \t ', [], {}, None, 12, True])
def test_incomplete_governing_identity_cannot_authorize_reuse(tmp_path, key, value):
    trace = fixture_trace()
    for event in trace['events']:
        if event['kind'] == 'preparation':
            event['governing'][key] = value
    with pytest.raises(evaluator.InvalidTrace, match='governing identities'):
        evaluator.evaluate(save_trace(tmp_path, trace))


@pytest.mark.parametrize('key', sorted(evaluator.GOVERNING))
def test_missing_governing_field_is_invalid(tmp_path, key):
    trace = fixture_trace()
    for event in trace['events']:
        if event['kind'] == 'preparation':
            del event['governing'][key]
    with pytest.raises(evaluator.InvalidTrace, match='governing evidence'):
        evaluator.evaluate(save_trace(tmp_path, trace))


@pytest.mark.parametrize('value', [[], {}, ['identity-a'], {'identity-a': 'hash-a'}])
def test_empty_or_identified_decisions_and_rules_allow_compatible_reuse(tmp_path, value):
    trace = fixture_trace()
    for event in trace['events']:
        if event['kind'] == 'preparation':
            event['governing'].update(decisions=copy.deepcopy(value), rules=copy.deepcopy(value))
    assert evaluator.evaluate(save_trace(tmp_path, trace))['status'] == 'passed'


@pytest.mark.parametrize('key,value', [('reviewers', ''), ('reviewers', [' ']), ('reviewers', [{}]),
                                     ('decisions', ['']), ('rules', {'rule': ''}), ('rules', False)])
def test_malformed_governing_collections_are_invalid(tmp_path, key, value):
    trace = fixture_trace()
    next(e for e in trace['events'] if e['kind'] == 'preparation')['governing'][key] = value
    with pytest.raises(evaluator.InvalidTrace, match='governing identities'):
        evaluator.evaluate(save_trace(tmp_path, trace))


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


def test_excessive_json_nesting_retains_invalid_report(tmp_path):
    path = tmp_path / 'deep.json'
    # Python 3.14's C parser can exceed the Python recursion limit.
    depth = 100000
    path.write_text('[' * depth + '0' + ']' * depth)
    with pytest.raises(evaluator.InvalidTrace, match='excessive JSON nesting'):
        evaluator.evaluate(path)
    report_file = tmp_path / 'deep-report.json'
    result = subprocess.run([sys.executable, str(SCRIPT), str(path), '--report', str(report_file)],
                            capture_output=True, text=True)
    assert result.returncode == 2 and not result.stderr
    assert json.loads(result.stdout)['status'] == 'invalid'
    report = json.loads(report_file.read_text())
    assert report['status'] == 'invalid' and report['error'] == 'excessive JSON nesting'
    assert os.stat(report_file).st_mode & 0o777 == 0o600
