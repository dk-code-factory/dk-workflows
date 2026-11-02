"""Deterministic accounting: no provider calls or reconstructed billed cost."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from shared import usage


def record(identity, provider='codex', raw=None, **extra):
    return {'execution_id': identity, 'provider': provider, 'scope': 'execution',
            'usage': raw if raw is not None else {'input_tokens': 100, 'cached_input_tokens': 70,
                'output_tokens': 20, 'reasoning_output_tokens': 5}, **extra}


def test_mixed_provider_components_are_subsets_and_unknown_is_not_zero():
    records = [record('openai'), record('anthropic', 'claude', {
        'input_tokens': 10, 'cache_read_input_tokens': 70, 'cache_creation_input_tokens': 20,
        'output_tokens': 20, 'total_cost_usd': .25}),
        record('fallback', raw={'total_tokens': 42, 'source': 'codex_cli_summary'}),
        {**record('missing'), 'usage': None}]
    result = usage.report(records)
    assert result['executions'] == 4
    assert result['attempts_with_usage'] == 3
    assert result['missing_attempts'] == 1 and not result['complete']
    assert result['tokens']['total_tokens'] == dict(known_subtotal=282, measured_attempts=3, missing_attempts=1, complete=False)
    assert result['tokens']['input_tokens']['known_subtotal'] == 200
    assert result['tokens']['cached_input_tokens']['known_subtotal'] == 140
    assert result['tokens']['reasoning_output_tokens']['known_subtotal'] == 5
    assert result['tokens']['reasoning_output_tokens']['missing_attempts'] == 3
    assert result['cost']['known_subtotal'] == .25 and result['cost']['missing_attempts'] == 3
    assert result['cost']['sources'] == ['provider_reported']
    assert 'not guaranteed billed cost' in result['cost']['interpretation']
    assert result['requests']['known_count'] is None
    assert result['request_input_tokens']['sample_size'] == 0
    assert result['request_input_tokens']['p95'] is None


def test_deduplicate_every_available_identity_exclude_inherited_and_inclusive_children():
    root = record('root', inclusive=True, response_id='response', tool_call_id='call')
    records = [record('child', parent_execution_id='root'), root,
               record('grandchild', parent_execution_id='child'),
               record('duplicate-execution', response_id='response'),
               record('duplicate-call', tool_call_id='call'), root,
               record('inherited', inherited=True), record('separate')]
    result = usage.report(records)
    assert result['executions'] == 2
    assert result['tokens']['total_tokens']['known_subtotal'] == 240
    assert result['excluded_records'] == dict(duplicates=3, inherited=1, inclusive_children=2)
    # Same identifier in different providers does not collapse separate usage.
    assert usage.report([root, {**root, 'provider': 'command'}])['executions'] == 2
    assert usage.report([root, {**root, 'provider': 'openai'}])['executions'] == 1


def test_request_percentiles_only_use_request_evidence_and_groups_keep_unknown():
    records = [record('a', scope='request', raw={'input_tokens': 10, 'output_tokens': 1},
                      model='m', effort='high', role='root', stage='review'),
               record('b', scope='request', raw={'input_tokens': 30, 'output_tokens': 1},
                      model='m', effort='high', role='child', stage='review'),
               record('c', raw={'input_tokens': 100000, 'output_tokens': 1})]
    result = usage.report(records, ['stage', 'model', 'effort', 'role'])
    assert result['requests'] == dict(known_count=2, unmeasured_executions=1, complete=False)
    assert result['request_input_tokens'] == dict(sample_size=2, min=10, median=20, p90=28, p95=29, max=30)
    assert len(result['groups']) == 3
    assert any(g['key']['model'] is None for g in result['groups'])


def test_resumed_snapshot_fills_missing_usage_and_identity_aliases_are_transitive():
    records = [dict(execution_id='a', response_id='r', usage=None, provider='codex'),
               record('b', response_id='r'),
               dict(execution_id='b', tool_call_id='t', usage=None, provider='codex'),
               dict(tool_call_id='t', usage={'total_tokens': 120}, provider='codex')]
    result = usage.report(records)
    assert result['executions'] == 1
    assert result['tokens']['input_tokens']['known_subtotal'] == 100
    assert result['tokens']['total_tokens']['known_subtotal'] == 120
    assert result['excluded_records']['duplicates'] == 3


def test_complementary_usage_and_cost_snapshots_survive_deduplication():
    records = [record('a', raw={'input_tokens': 10, 'total_cost_usd': .25, 'source': 'first'}),
               record('a', raw={'output_tokens': 2, 'source': 'second'})]
    result = usage.report(records)
    assert result['executions'] == 1
    assert result['tokens']['total_tokens']['known_subtotal'] == 12
    assert result['cost']['known_subtotal'] == .25
    assert result['usage_sources'] == ['first', 'merged_execution_snapshots', 'second']
    assert records[0]['usage'] == {'input_tokens': 10, 'total_cost_usd': .25, 'source': 'first'}
    bridge = usage.report([record('a', response_id='ra'), record('b', response_id='rb'),
                           record('a', response_id='rb')])
    assert bridge['executions'] == 1 and bridge['excluded_records']['duplicates'] == 2


@pytest.mark.parametrize('raw', [None, {}, {'input_tokens': -1}, {'input_tokens': True}, {'output_tokens': float('inf')}])
def test_unavailable_or_invalid_measurement_never_becomes_zero(raw):
    result = usage.report([{'provider': 'codex', 'usage': raw}])
    assert result['tokens']['total_tokens']['known_subtotal'] is None
    assert result['missing_attempts'] == 1
    assert not result['complete']


def test_invalid_subset_does_not_inflate_valid_total_and_claude_missing_cache_is_unknown():
    normalized = usage.normalize({'input_tokens': 5, 'cached_input_tokens': 6,
                                  'output_tokens': 2, 'reasoning_output_tokens': 3}, 'codex')
    assert normalized['total_tokens'] == 7
    assert normalized['cached_input_tokens'] is None
    assert normalized['reasoning_output_tokens'] is None
    assert usage.normalize({'input_tokens': 5, 'output_tokens': 2}, 'claude')['input_tokens'] is None


def test_timestamp_semantics_and_rejected_groups():
    assert usage.timestamp('2026-11-02') == usage.timestamp('2026-11-01T20:00:00-04:00')
    for invalid in ('2026-11-02T12:00:00', 'bad', None):
        with pytest.raises((ValueError, TypeError)):
            usage.timestamp(invalid)
    for invalid in (['wrong'], ['model', 'model']):
        with pytest.raises(ValueError):
            usage.report([], invalid)
