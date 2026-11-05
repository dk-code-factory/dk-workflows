"""Dollar gate for new runtime experiments; historical native_usage is unchanged.

Usage parsing and completeness verification are imported from PR #145 at
c9c2a181e72f7a3673013e9a6c901d5347918240. This module deliberately permits
different pinned runtime/guidance/resources between arms. It never launches work.
"""
from decimal import Decimal
from statistics import median
import native_usage as historical


RATE_CARD = {
    'date': '2026-11-05',
    'source': 'https://developers.openai.com/api/docs/models/gpt-6.1-sol',
    'pricing_source': 'https://developers.openai.com/api/docs/pricing',
    'model': 'gpt-6.1-sol',
    'per_million_usd': {'input': '2', 'cache_read': '.1',
                        'cache_write_30m': '2.5', 'cache_write_12h': '5', 'output': '10'},
    'long_context_threshold': 272000,
    'long_context_input_multiplier': '2', 'long_context_output_multiplier': '1.5',
    'tiers': {k: str(v) for k, v in historical.TIER_MULTIPLIERS.items()},
    'regional_multiplier': '1.1',
    'billing_basis': 'API-equivalent cost; not actual account billing',
}
PIN_FIELDS = ('runtime', 'guidance', 'resources', 'assignment', 'scenario', 'conditions')


def response_cost(usage, *, service_tier, cache_write_12h_tokens=0, regional=False):
    """Reasoning is already included in output; split cache TTLs explicitly."""
    usage = historical._usage(usage)
    historical._require(type(cache_write_12h_tokens) is int and
                        0 <= cache_write_12h_tokens <= usage['cache_write_input_tokens'],
                        'Invalid cache-write duration counters')
    historical._require(type(regional) is bool, 'Unknown regional processing')
    cost = historical.response_cost(usage, RATE_CARD['model'], service_tier)
    extra = Decimal(cache_write_12h_tokens) * Decimal('2.5')
    if usage['input_tokens'] > RATE_CARD['long_context_threshold']:
        extra *= 2
    cost += extra * historical.TIER_MULTIPLIERS[service_tier] / 1000000
    return cost * (Decimal('1.1') if regional else 1)


def evaluate_pairs(pairs, *, correctness_passed):
    """Exactly three frozen alternating pairs; missing evidence is inconclusive."""
    reasons = []
    try:
        historical._require(isinstance(pairs, list) and len(pairs) == 3,
                            'Exactly three pairs required')
        historical._require([p['order'] for p in pairs] == [
            ['baseline', 'candidate'], ['candidate', 'baseline'], ['baseline', 'candidate']],
            'Incorrect trial order')
        frozen = {}
        costs = {'baseline': [], 'candidate': []}
        response_ids = set()
        for pair in pairs:
            for arm in ('baseline', 'candidate'):
                result = pair[arm]
                historical._require(result['telemetry_complete'] is True,
                                    'Incomplete telemetry')
                historical._usage(result['tokens'])
                pins = result['pins']
                historical._require(all(historical._identifier(pins.get(k)) for k in PIN_FIELDS),
                                    'Missing experiment pins')
                settings = result['settings']
                historical._require(isinstance(settings, list) and bool(settings), 'Missing settings')
                for setting in settings:
                    historical._require(set(setting) == {'model', 'effort', 'service_tier'} and
                        setting['model'] == RATE_CARD['model'] and setting['effort'] in historical.EFFORTS
                        and setting['service_tier'] in historical.TIER_MULTIPLIERS, 'Invalid settings')
                pin = ({k: pins[k] for k in PIN_FIELDS}, settings)
                historical._require(arm not in frozen or frozen[arm] == pin,
                                    'Arm changed across repeated trials')
                frozen[arm] = pin
                historical._require(result['rate_card'] == RATE_CARD, 'Unmatched rate card')
                ids = historical._identifiers(result['response_ids'], 'Measured response IDs')
                historical._require(bool(ids) and not ids.intersection(response_ids),
                                    'Reused response across trial arms')
                response_ids.update(ids)
                cost = Decimal(result['api_equivalent_cost_usd'])
                historical._require(cost.is_finite() and cost >= 0, 'Invalid dollar cost')
                costs[arm].append(cost)
            for key in ('assignment', 'scenario', 'conditions'):
                historical._require(pair['baseline']['pins'][key] == pair['candidate']['pins'][key],
                                    'Unmatched '+key)
            historical._require(pair['baseline']['settings'] == pair['candidate']['settings'],
                                'Unmatched execution settings')
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        return {'accepted': False, 'decision': 'inconclusive', 'reasons': [str(error)]}
    if correctness_passed is not True:
        reasons.append('Correctness did not pass')
    b, c = costs['baseline'], costs['candidate']
    if median(c) > Decimal('.95') * median(b):
        reasons.append('Median dollar savings below 5%')
    if sum(c) >= sum(b):
        reasons.append('Total candidate dollar cost did not decrease')
    improved = sum(after < before for before, after in zip(b, c))
    if improved < 2:
        reasons.append('Fewer than two improving dollar pairs')
    return {'accepted': not reasons, 'decision': 'declined' if reasons else 'accepted',
            'reasons': reasons, 'improved_pairs': improved,
            'baseline_usd': list(map(str, b)), 'candidate_usd': list(map(str, c)),
            'median_baseline_usd': str(median(b)), 'median_candidate_usd': str(median(c)),
            'sum_baseline_usd': str(sum(b)), 'sum_candidate_usd': str(sum(c)),
            'rate_card': RATE_CARD,
            'limitation': 'Three pairs give bounded evidence, not a general savings guarantee'}
