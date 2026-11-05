"""Offline accounting checks; no model or native workflow is executed."""
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import pytest


SPEC = importlib.util.spec_from_file_location('native_usage', Path(__file__).parent/'fixtures/native_usage.py')
usage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(usage)


def record(response='r1', thread='parent', turn='t1', *, inputs=100, cached=20,
           writes=10, output=30, reasoning=15):
    return {'type': 'token_usage_record', 'payload': {
        'thread_id': thread, 'turn_id': turn, 'session_id': thread,
        'root_turn_id': 'root-turn', 'response_id': response,
        'usage': {'input_tokens': inputs, 'cached_input_tokens': cached,
                  'cache_write_input_tokens': writes, 'output_tokens': output,
                  'reasoning_output_tokens': reasoning, 'total_tokens': inputs+output},
        # Deliberately larger cumulative totals: these must never be charged.
        'turn_token_usage': {'total_tokens': 99999},
        'thread_token_usage': {'total_tokens': 999999}}}


def participant(thread='parent', parent=None, turn='t1', responses=None, counters=None):
    responses = responses or ['r1']
    return {'thread_id': thread, 'parent_thread_id': parent, 'model': 'gpt-6.1-sol',
            'effort': 'high', 'service_tier': 'default',
            'turns': [{'turn_id': turn, 'complete': True, 'response_ids': responses,
                       'terminal_response_id': responses[-1],
                       'expected_usage': counters or record()['payload']['usage']}]}


def manifest(*participants):
    return {'pins': {'source': 'source-revision', 'guidance': 'guidance-sha',
                     'scenario': 'scenario-sha'}, 'inventory_complete': True,
            'participants': list(participants) or [participant()],
            'coordination_call_ids': ['spawn-1', 'wait-1']}


def rollout(tmp_path, name, rows):
    path = tmp_path/name
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    return path


def test_global_dedup_inherited_history_and_all_descendant_usage(tmp_path):
    original = record()
    child = record('r2', 'child', 't2', inputs=200, cached=100, writes=0, output=40)
    grandchild = record('r3', 'grandchild', 't3', inputs=80, cached=0, writes=0, output=20)
    outside = record('old-response', 'parent', 'old-turn', inputs=50000)
    parent_path = rollout(tmp_path, 'parent.jsonl', [outside, original,
        {'type': 'event_msg', 'payload': {'type': 'token_count', 'total_tokens': 999999}}])
    child_path = rollout(tmp_path, 'child.jsonl', [outside, original, child])
    grandchild_path = rollout(tmp_path, 'grandchild.jsonl', [original, child, grandchild])
    info = manifest(participant(), participant('child', 'parent', 't2', ['r2'], child['payload']['usage']),
                    participant('grandchild', 'child', 't3', ['r3'], grandchild['payload']['usage']))
    result = usage.summarize([parent_path, child_path, grandchild_path], info)
    assert result['tokens'] == {'input_tokens': 380, 'cached_input_tokens': 120,
        'cache_write_input_tokens': 10, 'output_tokens': 90, 'reasoning_output_tokens': 45,
        'total_tokens': 470, 'fresh_input_tokens': 250}
    assert result['response_count'] == result['participant_count'] == 3
    assert result['coordination_calls'] == 2
    assert Decimal(result['estimated_api_cost_usd']) == Decimal('.001437')
    assert result['billing_basis'] == 'API-equivalent estimate; not actual account billing'
    assert str(tmp_path) not in json.dumps(result)
    assert 'old-response' not in json.dumps(result)


def test_conflicting_duplicate_rejected_even_from_inherited_copy(tmp_path):
    path = rollout(tmp_path, 'first.jsonl', [record()])
    conflicting = rollout(tmp_path, 'copy.jsonl', [record(output=31)])
    with pytest.raises(usage.TelemetryError, match='Conflicting duplicate'):
        usage.summarize([path, conflicting], manifest())


@pytest.mark.parametrize('change,message', [
    (lambda m: m.update(inventory_complete=False), 'descendant inventory'),
    (lambda m: m['participants'][0]['turns'][0].update(complete=False), 'Unfinished host'),
    (lambda m: m['participants'][0]['turns'][0].update(response_ids=['r1', 'missing']), 'Missing expected'),
    (lambda m: m['participants'][0]['turns'][0].update(terminal_response_id='missing'), 'terminal response'),
    (lambda m: m['participants'][0].update(parent_thread_id='missing'), 'exactly one parent root'),
    (lambda m: m['participants'][0].update(effort='unknown'), 'effort'),
    (lambda m: m['participants'][0].update(service_tier='unknown'), 'tier'),
    (lambda m: m['participants'][0].update(model='unknown'), 'model'),
    (lambda m: m['pins'].pop('guidance'), 'source pins'),
    (lambda m: m.pop('coordination_call_ids'), 'Coordination call'),
    (lambda m: m.update(coordination_call_ids=['same', 'same']), 'duplicates'),
])
def test_missing_or_incomplete_manifest_fails_closed(tmp_path, change, message):
    info = manifest()
    change(info)
    with pytest.raises(usage.TelemetryError, match=message):
        usage.summarize([rollout(tmp_path, 'rollout', [record()])], info)


def test_missing_child_cannot_disappear_from_total(tmp_path):
    info = manifest(participant(), participant('child', 'parent', 't2', ['r2']))
    with pytest.raises(usage.TelemetryError, match='Missing expected'):
        usage.summarize([rollout(tmp_path, 'parent', [record()])], info)


def test_expected_usage_detects_missing_interior_even_if_id_inventory_is_incomplete(tmp_path):
    info = manifest()
    info['participants'][0]['turns'][0]['expected_usage'] = {
        key: value*2 for key, value in record()['payload']['usage'].items()}
    with pytest.raises(usage.TelemetryError, match='independently pinned'):
        usage.summarize([rollout(tmp_path, 'parent', [record()])], info)


@pytest.mark.parametrize('rows,message', [
    ([record('extra')], 'Missing expected'),
    ([record(), record('extra')], 'Uninventoried response'),
    ([record(thread='wrong-thread')], 'identity differs'),
    ([{'type': 'event_msg', 'payload': {'type': 'token_count'}}], 'Missing expected'),
])
def test_no_substitution_for_expected_per_response_telemetry(tmp_path, rows, message):
    with pytest.raises(usage.TelemetryError, match=message):
        usage.summarize([rollout(tmp_path, 'rollout', rows)], manifest())


@pytest.mark.parametrize('field,value,message', [
    ('input_tokens', -1, 'invalid token'),
    ('input_tokens', True, 'invalid token'),
    ('total_tokens', 131, 'Inconsistent'),
    ('cached_input_tokens', 101, 'Cache counters'),
    ('reasoning_output_tokens', 31, 'Reasoning exceeds'),
    ('cache_write_input_tokens', None, 'invalid token'),
])
def test_invalid_and_missing_usage_not_estimated(tmp_path, field, value, message):
    row = record()
    if value is None:
        row['payload']['usage'].pop(field)
    else:
        row['payload']['usage'][field] = value
    with pytest.raises(usage.TelemetryError, match=message):
        usage.summarize([rollout(tmp_path, 'rollout', [row])], manifest())


def test_truncated_or_absent_rollout_rejects(tmp_path):
    path = rollout(tmp_path, 'truncated', [record()])
    with path.open('a') as stream:
        stream.write('{"type":')
    with pytest.raises(usage.TelemetryError, match='truncated'):
        usage.summarize([path], manifest())
    with pytest.raises(usage.TelemetryError, match='Unreadable'):
        usage.summarize([tmp_path/'missing'], manifest())


def test_parent_inventory_cannot_have_cycles_or_multiple_roots(tmp_path):
    path = rollout(tmp_path, 'rollout', [record()])
    cyclic = manifest(participant(), participant('a', 'b', 't2', ['r2']),
                      participant('b', 'a', 't3', ['r3']))
    with pytest.raises(usage.TelemetryError, match='cyclic'):
        usage.summarize([path], cyclic)
    with pytest.raises(usage.TelemetryError, match='exactly one'):
        usage.summarize([path], manifest(participant(), participant('child', None, 't2', ['r2'])))


@pytest.mark.parametrize('inputs,multiplier', [(272000, Decimal(1)), (272001, Decimal(2))])
def test_long_context_rates_apply_to_full_response_not_aggregate(inputs, multiplier):
    counters = record(inputs=inputs)['payload']['usage']
    input_cost = (Decimal(inputs-30)*2 + Decimal(20)*Decimal('.1') + Decimal(10)*Decimal('2.5'))
    output_cost = Decimal(300) * (Decimal('1.5') if inputs > 272000 else 1)
    expected = (input_cost*multiplier+output_cost)/1000000
    assert usage.response_cost(counters, 'gpt-6.1-sol', 'default') == expected
    assert usage.response_cost(counters, 'gpt-6.1-sol', 'priority') == expected*2
    assert usage.response_cost(counters, 'gpt-6.1-sol', 'flex') == expected/2


def test_combined_short_responses_do_not_get_long_context_surcharge(tmp_path):
    rows = [record('r1', inputs=200000), record('r2', inputs=200000)]
    counters = {key: sum(row['payload']['usage'][key] for row in rows) for key in usage.TOKEN_FIELDS}
    result = usage.summarize([rollout(tmp_path, 'rollout', rows)],
                             manifest(participant(responses=['r1', 'r2'], counters=counters)))
    expected = usage.response_cost(rows[0]['payload']['usage'], 'gpt-6.1-sol', 'default')*2
    assert Decimal(result['estimated_api_cost_usd']) == expected


def paired_trials(tmp_path):
    pairs = []
    for index, (before, after) in enumerate([(1000, 800), (1200, 900), (800, 750)]):
        arms = {}
        for name, tokens in [('baseline', before), ('candidate', after)]:
            row = record(inputs=tokens-30)
            path = rollout(tmp_path, name+str(index), [row])
            arms[name] = usage.summarize([path], manifest(participant(counters=row['payload']['usage'])))
        pairs.append({'order': ['baseline', 'candidate'] if index % 2 == 0
                      else ['candidate', 'baseline'], **arms})
    return pairs


def gate(pairs, **options):
    return usage.evaluate_pairs(pairs, correctness_passed=options.get('correctness_passed', True),
                               review_complete=options.get('review_complete', True),
                               actionable_findings=options.get('actionable_findings', 0),
                               effort_comparison=options.get('effort_comparison', False))


def set_total(summary, total):
    summary['tokens'] = {key: 0 for key in usage.TOKEN_FIELDS}
    summary['tokens'].update(input_tokens=total, total_tokens=total, fresh_input_tokens=total)


def test_gate_uses_total_input_plus_output_not_reasoning_twice(tmp_path):
    result = gate(paired_trials(tmp_path))
    assert result['accepted'] is True
    assert Decimal(result['median_total_token_savings_fraction']) == Decimal('.2')
    assert result['improved_pairs'] == 3
    assert result['paired_token_changes'][0] == {
        'total_tokens': -200, 'fresh_input_tokens': -200, 'output_tokens': 0}


@pytest.mark.parametrize('mutation,message', [
    (lambda pairs: pairs.pop(), 'Exactly three'),
    (lambda pairs: pairs[1].update(order=['baseline', 'candidate']), 'not alternated'),
    (lambda pairs: pairs[0]['candidate'].update(telemetry_complete=False), 'Incomplete paired'),
    (lambda pairs: pairs[0]['candidate']['pins'].update(source='different'), 'Unmatched'),
    (lambda pairs: pairs[0]['candidate'].pop('estimated_api_cost_usd'), 'Incomplete paired'),
    (lambda pairs: pairs[1]['candidate']['pins'].update(guidance='different'), 'changed across'),
    (lambda pairs: pairs[1]['candidate']['settings'][0].update(effort='medium'), 'changed across'),
    (lambda pairs: pairs[0]['candidate'].update(estimated_api_cost_usd='NaN'), 'Invalid estimated'),
    (lambda pairs: pairs[0]['candidate']['tokens'].update(total_tokens=50.5), 'invalid token'),
    (lambda pairs: pairs[0]['candidate']['tokens'].update(total_tokens=True), 'invalid token'),
    (lambda pairs: pairs[0]['candidate']['tokens'].pop('cached_input_tokens'), 'invalid token'),
    (lambda pairs: pairs[0]['candidate']['tokens'].update(fresh_input_tokens=-100), 'fresh-input'),
    (lambda pairs: pairs[0]['candidate']['tokens'].update(fresh_input_tokens=0), 'fresh-input'),
    (lambda pairs: pairs[0]['candidate']['tokens'].update(fresh_input_tokens=True), 'fresh-input'),
    (lambda pairs: pairs[0]['candidate']['pins'].update(source='  '), 'source pins'),
    (lambda pairs: pairs[0]['candidate']['pins'].update(guidance=None), 'source pins'),
    (lambda pairs: pairs[0]['candidate']['settings'][0].update(unpinned='value'), 'Unpinned model'),
])
def test_gate_rejects_incomplete_and_unmatched_trials(tmp_path, mutation, message):
    pairs = paired_trials(tmp_path)
    mutation(pairs)
    result = gate(pairs)
    assert result['accepted'] is False
    assert any(message in reason for reason in result['reasons'])


def test_gate_rejects_partial_summaries_even_with_plausible_savings(tmp_path):
    pairs = paired_trials(tmp_path)
    for pair in pairs:
        pair['candidate']['tokens'] = {
            'total_tokens': 50.5, 'fresh_input_tokens': -100, 'output_tokens': 1000}
    assert gate(pairs)['accepted'] is False


@pytest.mark.parametrize('option,value', [('correctness_passed', False),
    ('review_complete', False), ('actionable_findings', 1)])
def test_savings_cannot_override_correctness_or_review(tmp_path, option, value):
    assert gate(paired_trials(tmp_path), **{option: value})['accepted'] is False


def test_gate_rejects_higher_cost_or_small_savings(tmp_path):
    pairs = paired_trials(tmp_path)
    for pair in pairs:
        pair['candidate']['estimated_api_cost_usd'] = '100'
    assert 'Median estimated API cost increased' in gate(pairs)['reasons']
    for pair in pairs:
        set_total(pair['candidate'], pair['baseline']['tokens']['total_tokens']-1)
    assert 'Median total-token savings below 10%' in gate(pairs)['reasons']


def test_gate_requires_two_pairs_to_improve_even_if_median_falls(tmp_path):
    pairs = paired_trials(tmp_path)
    for pair, before, after in zip(pairs, [10, 1000, 2000], [11, 1001, 100]):
        set_total(pair['baseline'], before)
        set_total(pair['candidate'], after)
    result = gate(pairs)
    assert result['improved_pairs'] == 1
    assert result['accepted'] is False


def test_different_effort_needs_explicit_reasoning_experiment_declaration(tmp_path):
    pairs = paired_trials(tmp_path)
    for pair in pairs:
        pair['candidate']['settings'][0]['effort'] = 'medium'
    assert gate(pairs)['accepted'] is False
    assert gate(pairs, effort_comparison=True)['accepted'] is True


def test_added_descendants_do_not_require_equal_participant_counts(tmp_path):
    pairs = paired_trials(tmp_path)
    for pair in pairs:
        pair['candidate']['participant_count'] += 1
        pair['candidate']['settings'].append(dict(pair['candidate']['settings'][0]))
    assert gate(pairs)['accepted'] is True
