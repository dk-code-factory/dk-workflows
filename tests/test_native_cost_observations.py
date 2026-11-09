"""Missing usage cannot be excused by enumerating the surviving records."""
from copy import deepcopy
import json

import pytest

import native_cost_observations as observations


def usage(n=1):
    return {'input_tokens': 100*n, 'cached_input_tokens': 0, 'cache_write_input_tokens': 0,
            'output_tokens': 10*n, 'reasoning_output_tokens': 5*n, 'total_tokens': 110*n}


def seed():
    rows = [
        {'type': 'turn_context', 'payload': {'turn_id': 'turn', 'model': 'gpt-6.1-sol', 'effort': 'high'}},
        {'type': 'event_msg', 'payload': {'type': 'thread_settings_applied', 'thread_settings': {
            'model': 'gpt-6.1-sol', 'reasoning_effort': 'high', 'service_tier': 'default'}}},
        {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'turn'}},
    ]
    for i in range(1, 4):
        rows.extend([
            {'type': 'token_usage_record', 'payload': {'thread_id': 'thread', 'turn_id': 'turn',
                'session_id': 'thread', 'root_turn_id': 'turn', 'response_id': 'response-'+str(i),
                'usage': usage()}},
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                'total_token_usage': usage(i), 'last_token_usage': usage()}}},
        ])
    rows.insert(-2, {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
        'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': 'Complete native task.'}]}})
    rows.append({'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'turn',
                                               'last_agent_message': 'Complete native task.'}})
    return rows


def collect(tmp_path, rows, **kwargs):
    path = tmp_path/'rollout.jsonl'
    path.write_text('\n'.join(map(json.dumps, rows))+'\n')
    return observations.collect(path, 'thread', expected_turns=['turn'], descendants=[], **kwargs)


def test_independent_inventory_and_cumulative_events_establish_response_scope(tmp_path):
    rows = seed()
    result = collect(tmp_path, rows)
    assert result['telemetry_complete'] and result['response_count'] == 3
    assert result['tokens']['total_tokens'] == 330
    assert result['api_equivalent_cost_usd'] == '0.0009'
    rows.insert(-1, deepcopy(rows[-2]))  # A repeated cumulative event is not another response.
    repeated = collect(tmp_path, rows)
    assert repeated.pop('raw_rollout_sha256') != result.pop('raw_rollout_sha256')
    assert repeated == result


@pytest.mark.parametrize('response', [1, 2, 3])
def test_missing_initial_interior_or_terminal_response_is_incomplete(tmp_path, response):
    rows = [row for row in seed() if not (row['type'] == 'token_usage_record' and
            row['payload']['response_id'] == 'response-'+str(response))]
    with pytest.raises(ValueError, match='inventory'):
        collect(tmp_path, rows)


@pytest.mark.parametrize('mutation', ['terminal', 'counter', 'context', 'tier', 'cache-write', 'duplicate'])
def test_missing_or_conflicting_host_evidence_fails_closed(tmp_path, mutation):
    rows = seed()
    if mutation == 'terminal': rows.pop()
    if mutation == 'counter': rows.pop(4)
    if mutation == 'context': rows.pop(0)
    if mutation == 'tier': rows[1]['payload']['thread_settings'].pop('service_tier')
    if mutation == 'cache-write':
        for row in rows:
            if row['type'] == 'token_usage_record': row['payload']['usage']['cache_write_input_tokens'] = 10
            if row['type'] == 'event_msg' and row['payload']['type'] == 'token_count':
                info = row['payload']['info']
                info['last_token_usage']['cache_write_input_tokens'] = 10
                info['total_token_usage']['cache_write_input_tokens'] = info['total_token_usage']['input_tokens']//10
    if mutation == 'duplicate': rows.insert(4, deepcopy(rows[3]))
    with pytest.raises(ValueError):
        collect(tmp_path, rows)


def test_descendants_require_complete_explicit_inventories(tmp_path):
    path = tmp_path/'rollout.jsonl'
    path.write_text('\n'.join(map(json.dumps, seed()))+'\n')
    with pytest.raises(ValueError, match='descendants'):
        observations.collect(path, 'thread', expected_turns=['turn'], descendants=['unexpected'])


def test_settings_may_arrive_after_start_but_cannot_change_within_the_turn(tmp_path):
    rows = seed()
    rows[1], rows[2] = rows[2], rows[1]
    assert collect(tmp_path, rows)['telemetry_complete']
    changed = deepcopy(rows[2])
    changed['payload']['thread_settings']['service_tier'] = 'fast'
    rows.insert(5, changed)
    with pytest.raises(ValueError, match='Settings changed'):
        collect(tmp_path, rows)


def test_starting_context_does_not_normalize_semantic_instruction_drift(tmp_path):
    path = tmp_path/'rollout.jsonl'
    rows = [
        {'type': 'session_meta', 'payload': {'base_instructions': {'text': 'Same host'},
            'cli_version': '1', 'model_provider': 'openai'}},
        {'type': 'turn_context', 'payload': {'model': 'gpt-6.1-sol', 'effort': 'high'}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'developer',
            'content': [{'text': 'Same rules for /root/baseline'}]}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
            'content': [{'text': 'Work inside /trial/baseline'}]}},
    ]
    path.write_text('\n'.join(map(json.dumps, rows))+'\n')
    baseline = observations.starting_context(path, replacements={
        '/root/baseline': 'AGENT', '/trial/baseline': 'ARM'})
    rows[2]['payload']['content'][0]['text'] = 'Same rules for /root/candidate'
    rows[3]['payload']['content'][0]['text'] = 'Work inside /trial/candidate'
    path.write_text('\n'.join(map(json.dumps, rows))+'\n')
    replacements = {'/root/candidate': 'AGENT', '/trial/candidate': 'ARM'}
    assert observations.starting_context(path, replacements=replacements) == baseline
    rows[0]['payload']['base_instructions']['text'] = 'Changed host rules'
    path.write_text('\n'.join(map(json.dumps, rows))+'\n')
    assert observations.starting_context(path, replacements=replacements) != baseline


@pytest.mark.parametrize('mutation', ['final-usage-and-counter', 'final-output', 'conflicting-output'])
def test_terminal_inventory_is_bound_to_original_output(tmp_path, mutation):
    rows = seed()
    if mutation == 'final-usage-and-counter':
        rows = [row for row in rows if not (
            row['type'] == 'token_usage_record' and row['payload']['response_id'] == 'response-3' or
            row['type'] == 'event_msg' and row['payload'].get('type') == 'token_count' and
            row['payload']['info']['total_token_usage'] == usage(3))]
    elif mutation == 'final-output':
        rows = [row for row in rows if row['type'] != 'response_item']
    else:
        rows[-1]['payload']['last_agent_message'] = 'A different terminal output.'
    with pytest.raises(ValueError, match='[Tt]erminal'):
        collect(tmp_path, rows)
