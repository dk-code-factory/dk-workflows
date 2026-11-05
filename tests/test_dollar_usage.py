"""New dollar acceptance boundaries, independent of the historical token gate."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).parent/'fixtures'))
import dollar_usage as dollars


def pairs(baseline, candidate):
    result = []
    for i, (b, c) in enumerate(zip(baseline, candidate)):
        item = {'order': ['candidate', 'baseline'] if i == 1 else ['baseline', 'candidate']}
        for arm, cost in [('baseline', b), ('candidate', c)]:
            item[arm] = {'telemetry_complete': True,
                'tokens': {'input_tokens': 100, 'cached_input_tokens': 0,
                           'cache_write_input_tokens': 0, 'output_tokens': 20,
                           'reasoning_output_tokens': 10, 'total_tokens': 120},
                'pins': {k: arm if k in ('runtime', 'guidance', 'resources') else 'same'
                         for k in dollars.PIN_FIELDS},
                'settings': [{'model': 'gpt-6.1-sol', 'effort': 'high', 'service_tier': 'default'}],
                'rate_card': deepcopy(dollars.RATE_CARD),
                'response_ids': [f'{arm}-{i}'], 'api_equivalent_cost_usd': str(cost)}
        result.append(item)
    return result


@pytest.mark.parametrize('b,c,accepted', [
    ([100]*3, [95]*3, True),
    ([100]*3, ['95.000001']*3, False),
    ([100]*3, [100]*3, False),
    ([100]*3, [90, 90, 121], False),
    ([100, 200, 300], [101, 100, 301], False),
    ([100]*3, [90]*3, True),
])
def test_dollar_boundaries(b, c, accepted):
    assert dollars.evaluate_pairs(pairs(b, c), correctness_passed=True)['accepted'] is accepted


@pytest.mark.parametrize('change', [
    lambda p: p[0]['candidate'].update(telemetry_complete=False),
    lambda p: p[0]['candidate']['pins'].pop('assignment'),
    lambda p: p[1]['candidate']['pins'].update(runtime='drift'),
    lambda p: p[0]['candidate']['pins'].update(scenario='different'),
    lambda p: p[0]['candidate']['settings'][0].update(effort='low'),
    lambda p: p[0]['candidate'].update(api_equivalent_cost_usd='NaN'),
    lambda p: p[1]['candidate'].update(response_ids=['candidate-0']),
    lambda p: p[0]['candidate'].pop('rate_card'),
])
def test_missing_or_changed_evidence_is_inconclusive(change):
    value = pairs([100]*3, [90]*3)
    change(value)
    assert dollars.evaluate_pairs(value, correctness_passed=True)['decision'] == 'inconclusive'


def test_correctness_blocks_savings():
    assert not dollars.evaluate_pairs(pairs([100]*3, [90]*3), correctness_passed=False)['accepted']


def test_cache_context_tier_and_reasoning_accounting():
    usage = {'input_tokens': 272000, 'cached_input_tokens': 100000,
             'cache_write_input_tokens': 20000, 'output_tokens': 1000,
             'reasoning_output_tokens': 800, 'total_tokens': 273000}
    ordinary = dollars.response_cost(usage, service_tier='default')
    assert ordinary == Decimal('.374')
    assert dollars.response_cost(usage, service_tier='priority') == ordinary * 2
    assert dollars.response_cost(usage, service_tier='default', cache_write_12h_tokens=20000) == ordinary + Decimal('.05')
    usage.update(input_tokens=272001, total_tokens=273001)
    assert dollars.response_cost(usage, service_tier='default') == Decimal('.743004')
