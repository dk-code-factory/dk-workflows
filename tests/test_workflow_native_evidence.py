"""Host command receipts remain separate from pass-reported driver outcomes."""
import json

import pytest

from test_workflow_cost import host
import workflow_cost
import workflow_native_evidence as evidence


ARGV = ['python3', '/provider/aspect-driver.py', '--pass', 'spec', '--head', 'abc']


def rows(command, *, status='completed', exit_code=0):
    return [
        {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 'child',
            'turn_id': 'own', 'item': {'type': 'CommandExecution', 'id': 'actual-command',
                                     'command': command, 'status': status, 'exit_code': exit_code}}},
        {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'own',
            'last_agent_message': evidence.MARKER+json.dumps([
                {'argv': ARGV, 'state': 'completed', 'exit_code': 0}])}},
    ]


@pytest.mark.parametrize('command', [ARGV, ['/bin/sh', '-c', ' '.join(ARGV)]])
def test_direct_or_single_plain_command_has_independent_terminal_status(command):
    result = evidence.inspect_thread(rows(command), 'child', {'own'})
    driver = result['reported_drivers'][0]
    observed = driver['command_observations'][0]
    assert driver['reported_exit_code'] == 0
    assert observed['individual_exit_proven'] and observed['individual_exit_code'] == 0


def test_failed_command_cannot_be_replaced_by_a_successful_report():
    result = evidence.inspect_thread(rows(ARGV, exit_code=1), 'child', {'own'})
    driver = result['reported_drivers'][0]
    assert driver['reported_exit_code'] == 0
    assert driver['command_observations'][0]['individual_exit_code'] == 1


def test_whole_shell_success_does_not_prove_an_individual_command_ran():
    command = ['/bin/sh', '-c', 'exit 0\n'+' '.join(ARGV)]
    observed = evidence.inspect_thread(rows(command), 'child', {'own'})['reported_drivers'][0][
        'command_observations'][0]
    assert observed['host_command_exit_code'] == 0
    assert observed['command_contains_reported_argv']
    assert not observed['individual_exit_proven'] and observed['individual_exit_code'] is None


def test_printed_marker_or_copied_parent_command_is_not_a_driver_receipt():
    printed = ['/bin/sh', '-c', "printf '%s\\n' '"+json.dumps(ARGV)+"'"]
    assert evidence.inspect_thread(rows(printed), 'child', {'own'})['reported_drivers'][0][
        'command_observations'] == []
    foreign = rows(ARGV)
    foreign[0]['payload']['thread_id'] = 'parent'
    assert evidence.inspect_thread(foreign, 'child', {'own'})['reported_drivers'][0][
        'command_observations'] == []


def test_format_only_completion_retains_both_original_marker_references():
    source = rows(ARGV)
    original = {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'own',
                                               'last_agent_message': evidence.MARKER+json.dumps([ARGV])}}
    source.insert(1, original)
    result = evidence.inspect_thread(source, 'child', {'own'})
    driver = result['reported_drivers'][0]
    assert driver['marker_lines'] == [2, 3] and driver['reported_state'] == 'completed'
    assert len(driver['command_observations']) == 1


def test_collector_requires_original_graph_and_unchanged_raw_evidence(tmp_path):
    database = host(tmp_path)
    roots = {'initial': 'workflow-initial'}
    accounting = workflow_cost.collect(database, roots)
    assert evidence.collect(database, roots, accounting) == {}
    with (tmp_path/'initial.jsonl').open('a') as out:
        out.write(json.dumps({'type': 'response_item', 'payload': {}})+'\n')
    with pytest.raises(ValueError, match='evidence changed'):
        evidence.collect(database, roots, accounting)
