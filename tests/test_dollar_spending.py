"""Spending diagnostics never replace complete acceptance telemetry."""
from decimal import Decimal
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent/'fixtures'))
import dollar_spending


def usage_record(response='r1', output=2):
    return {'timestamp':'2026-11-05T00:00:00Z', 'type':'token_usage_record',
            'payload':{'response_id':response, 'usage':{
                'input_tokens':100, 'cached_input_tokens':0, 'cache_write_input_tokens':0,
                'output_tokens':output, 'reasoning_output_tokens':1, 'total_tokens':100+output}}}


def test_overhead_deduplicates_and_preserves_cutoff_and_mixed_attribution(tmp_path):
    path = tmp_path/'rollout.jsonl'
    call = {'type':'response_item', 'payload':{'type':'custom_tool_call', 'name':'exec',
        'input':'apply_patch runtime/thing.py; python3 dollar_trials.py; pytest'}}
    path.write_text('\n'.join(json.dumps(r) for r in [call, usage_record(), usage_record()]))
    seen = {}
    result = dollar_spending.coordinator(path, seen)
    assert result['observed_total_usd'] == str(Decimal('.00022'))
    assert result['response_counts_by_primary_action'] == {'mixed_implementation_measurement_review':1}
    assert len(seen) == 1
    assert result['telemetry_complete'] is False
    assert result['last_priced_response_timestamp'] == '2026-11-05T00:00:00Z'
    path.write_text(path.read_text()+'\n'+json.dumps(usage_record(output=3)))
    with pytest.raises(ValueError, match='Conflicting duplicate'):
        dollar_spending.coordinator(path, {})


@pytest.mark.parametrize('actions,expected', [
    (['apply_patch runtime/state.py'], 'implementation'),
    (['pytest tests/test_state.py'], 'review_validation'),
    (['python3 dollar_report.py experiment-2'], 'measurement'),
    (['apply_patch runtime/state.py; pytest'], 'mixed_implementation_review'),
    ([], 'coordination_unallocated'),
])
def test_primary_action_labels_keep_costs_separate(actions, expected):
    assert dollar_spending.category(actions) == expected


def test_ledger_rejects_reused_responses_across_trials_and_mismatched_costs(tmp_path):
    raw = tmp_path/'native.jsonl'
    record = usage_record()
    record['payload'].update(thread_id='native', turn_id='turn', session_id='native', root_turn_id='turn')
    raw.write_text(json.dumps(record)+'\n')
    coordinator = tmp_path/'coordinator.jsonl'
    coordinator.write_text(json.dumps(usage_record('overhead'))+'\n')
    def arm(name, cost='.00022'):
        path = tmp_path/'experiment-1'/name
        path.mkdir(parents=True)
        (path/'usage.json').write_text(json.dumps({'api_equivalent_cost_usd':cost, 'telemetry_complete':True}))
        (path/'private-rollout-reference.json').write_text(json.dumps({'path':str(raw)}))
    arm('1-baseline')
    result = dollar_spending.ledger(tmp_path, coordinator)
    assert result['observed_cumulative_total_usd'] == '0.00044'
    arm('1-candidate')
    with pytest.raises(ValueError, match='global response inventory mismatch'):
        dollar_spending.ledger(tmp_path, coordinator)
    (tmp_path/'experiment-1/1-candidate/usage.json').write_text(json.dumps({
        'api_equivalent_cost_usd':'.00023', 'telemetry_complete':True}))
    (tmp_path/'experiment-1/1-baseline/usage.json').unlink()
    with pytest.raises(ValueError, match='Arm cost'):
        dollar_spending.ledger(tmp_path, coordinator)
