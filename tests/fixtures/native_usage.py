"""Read private native rollout usage; never launch agents or execute a workflow.

``summarize(paths, manifest)`` reads JSONL rollouts and returns normalized numbers.
The manifest contains source/guidance/scenario pins, a complete host participant
inventory, and the expected response IDs for each participating turn. Obtain that
inventory and terminal response IDs from host execution evidence independently
of the usage records: enumerating only records that happened to arrive cannot
detect missing telemetry. This reader checks that inventory, not host honesty.

Manifest shape (all turns and descendants must be listed)::

    {"pins": {"source": "revision", "guidance": "sha256", "scenario": "sha256"},
     "inventory_complete": true,
     "participants": [{"thread_id": "parent", "parent_thread_id": null,
       "model": "gpt-6.1-sol", "effort": "high", "service_tier": "default",
       "turns": [{"turn_id": "turn", "complete": true,
         "response_ids": ["resp_1"], "terminal_response_id": "resp_1",
         "expected_usage": {"input_tokens": 100, "cached_input_tokens": 0,
           "cache_write_input_tokens": 0, "output_tokens": 20,
           "reasoning_output_tokens": 10, "total_tokens": 120}}]}],
     "coordination_call_ids": []}

``expected_usage`` is the independently pinned total for the complete measured
turn, corroborated against the native final token_count delta and successful
terminal host outcome. The final turn_token_usage can detect missing interior
records, but alone cannot prove that the final response was observed. The caller
must verify the host completed and include its final response. Never derive both
the expected total and expected IDs by summing only records that arrived.

Use observed native call IDs for coordination calls, including preparation,
waiting, messaging and result consumption when these are separate host calls.
Keep manifests and raw rollouts private. Output contains no conversations or
local paths. Inherited history is neither charged a second time as old responses
nor subtracted from the input of new responses. Only per-response ``usage`` is
summed; cumulative thread/turn counters and token_count events are ignored.
"""
from decimal import Decimal
import json
from pathlib import Path
from statistics import median


PRICE_SOURCE = 'https://developers.openai.com/api/docs/models/gpt-6.1-sol'
PRICE_DATE = '2026-11-05'
TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                'output_tokens', 'reasoning_output_tokens', 'total_tokens')
TIER_MULTIPLIERS = {'default': Decimal(1), 'standard': Decimal(1),
                    'priority': Decimal(2), 'fast': Decimal(2),
                    'ultrafast': Decimal(6), 'batch': Decimal('.5'),
                    'flex': Decimal('.5')}
EFFORTS = {'low', 'medium', 'high', 'xhigh', 'max'}


class TelemetryError(ValueError):
    """The available evidence cannot establish a complete usage measurement."""


def _require(condition, message):
    if not condition:
        raise TelemetryError(message)


def _identifier(value):
    return isinstance(value, str) and bool(value.strip())


def _identifiers(values, label):
    _require(isinstance(values, list) and all(map(_identifier, values)),
             label+' must be an explicit identifier list')
    _require(len(values) == len(set(values)), label+' contains duplicates')
    return set(values)


def _usage(value):
    _require(isinstance(value, dict), 'Missing response usage')
    _require(all(type(value.get(key)) is int and value[key] >= 0
                 for key in TOKEN_FIELDS), 'Missing or invalid token counters')
    result = {key: value[key] for key in TOKEN_FIELDS}
    _require(result['cached_input_tokens'] + result['cache_write_input_tokens']
             <= result['input_tokens'], 'Cache counters exceed input')
    _require(result['reasoning_output_tokens'] <= result['output_tokens'],
             'Reasoning exceeds output')
    _require(result['input_tokens'] + result['output_tokens'] == result['total_tokens'],
             'Inconsistent response total')
    return result


def response_cost(usage, model, service_tier):
    """API-equivalent USD estimate; not actual subscription/account billing.

    Rates per million: fresh input 2, cached .1, cache writes 2.5, output 10.
    Over 272K input, all input/cache rates double and output costs 1.5x for
    that response. Apply the explicit service multiplier after that surcharge.
    No regional processing premium is assumed; such trials need another price
    basis and must not be passed as one of these supported tiers.
    """
    _require(model == 'gpt-6.1-sol', 'Unknown model price')
    _require(service_tier in TIER_MULTIPLIERS, 'Unknown service tier price')
    usage = _usage(usage)
    fresh = usage['input_tokens']-usage['cached_input_tokens']-usage['cache_write_input_tokens']
    input_cost = (Decimal(fresh)*2 + Decimal(usage['cached_input_tokens'])*Decimal('.1')
                  + Decimal(usage['cache_write_input_tokens'])*Decimal('2.5'))
    output_cost = Decimal(usage['output_tokens'])*10
    if usage['input_tokens'] > 272000:
        input_cost *= 2
        output_cost *= Decimal('1.5')
    return (input_cost+output_cost)*TIER_MULTIPLIERS[service_tier]/1000000


def read_records(paths):
    """Deduplicate response records globally, including copied parent history."""
    records = {}
    _require(bool(paths), 'No rollout files')
    for path in paths:
        try:
            with Path(path).open() as stream:
                for line in stream:
                    row = json.loads(line)
                    _require(isinstance(row, dict), 'Invalid rollout row')
                    if row.get('type') != 'token_usage_record':
                        continue
                    payload = row.get('payload')
                    _require(isinstance(payload, dict), 'Missing usage payload')
                    fields = ('thread_id', 'turn_id', 'session_id', 'root_turn_id', 'response_id')
                    _require(all(_identifier(payload.get(key)) for key in fields),
                             'Missing response identity')
                    record = {key: payload[key] for key in fields}
                    record['usage'] = _usage(payload.get('usage'))
                    response = record['response_id']
                    _require(response not in records or records[response] == record,
                             'Conflicting duplicate response: '+response)
                    records[response] = record
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise TelemetryError('Unreadable or truncated rollout') from error
    return records


def summarize(paths, manifest):
    """Validate explicit complete scopes, then sum parent and descendant usage."""
    _require(isinstance(manifest, dict), 'Missing measurement manifest')
    pins = manifest.get('pins')
    _require(isinstance(pins, dict) and all(_identifier(pins.get(key))
             for key in ('source', 'guidance', 'scenario')), 'Missing source pins')
    _require(manifest.get('inventory_complete') is True,
             'Incomplete descendant inventory')
    participants = manifest.get('participants')
    _require(isinstance(participants, list) and bool(participants), 'No participants')
    by_thread = {}
    scopes = {}
    expected_usage = {}
    expected = {}
    roots = []
    for participant in participants:
        _require(isinstance(participant, dict), 'Invalid participant')
        thread = participant.get('thread_id')
        _require(_identifier(thread) and thread not in by_thread,
                 'Invalid or duplicate participant')
        _require('parent_thread_id' in participant, 'Missing parent linkage')
        if participant['parent_thread_id'] is None:
            roots.append(thread)
        _require(participant.get('model') == 'gpt-6.1-sol', 'Unpinned or unsupported model')
        _require(participant.get('effort') in EFFORTS, 'Unpinned or unsupported effort')
        _require(participant.get('service_tier') in TIER_MULTIPLIERS, 'Unpinned service tier')
        turns = participant.get('turns')
        _require(isinstance(turns, list) and bool(turns), 'Missing participant turns')
        by_thread[thread] = participant
        for turn in turns:
            _require(isinstance(turn, dict), 'Invalid turn')
            turn_id = turn.get('turn_id')
            _require(_identifier(turn_id) and (thread, turn_id) not in scopes,
                     'Invalid or duplicate turn scope')
            _require(turn.get('complete') is True, 'Unfinished host turn')
            ids = _identifiers(turn.get('response_ids'), 'Expected responses')
            _require(bool(ids), 'Turn has no response telemetry')
            _require(turn.get('terminal_response_id') in ids, 'Missing terminal response')
            scopes[thread, turn_id] = ids
            expected_usage[thread, turn_id] = _usage(turn.get('expected_usage'))
            for response in ids:
                _require(response not in expected, 'Response assigned to multiple scopes')
                expected[response] = (thread, turn_id)
    _require(len(roots) == 1, 'Inventory must have exactly one parent root')
    for thread, participant in by_thread.items():
        visited = {thread}
        parent = participant['parent_thread_id']
        while parent is not None:
            _require(_identifier(parent) and parent in by_thread and parent not in visited,
                     'Missing or cyclic parent linkage')
            visited.add(parent)
            parent = by_thread[parent]['parent_thread_id']
    calls = _identifiers(manifest.get('coordination_call_ids'), 'Coordination call IDs')
    records = read_records(paths)
    _require(set(expected) <= records.keys(), 'Missing expected response telemetry')
    selected = {}
    for response, record in records.items():
        scope = record['thread_id'], record['turn_id']
        if response in expected:
            _require(scope == expected[response], 'Response identity differs from inventory')
        if scope in scopes:
            _require(response in expected, 'Uninventoried response in measured turn')
            selected[response] = record
    totals = {key: 0 for key in TOKEN_FIELDS}
    measured_usage = {scope: {key: 0 for key in TOKEN_FIELDS} for scope in scopes}
    cost = Decimal(0)
    for record in selected.values():
        for key in TOKEN_FIELDS:
            totals[key] += record['usage'][key]
            measured_usage[record['thread_id'], record['turn_id']][key] += record['usage'][key]
        participant = by_thread[record['thread_id']]
        cost += response_cost(record['usage'], participant['model'], participant['service_tier'])
    _require(measured_usage == expected_usage,
             'Response sums differ from independently pinned turn usage')
    totals['fresh_input_tokens'] = (totals['input_tokens']-totals['cached_input_tokens']
                                    -totals['cache_write_input_tokens'])
    return {'telemetry_complete': True, 'pins': dict(pins), 'tokens': totals,
            'response_count': len(selected), 'participant_count': len(participants),
            'coordination_calls': len(calls), 'estimated_api_cost_usd': str(cost),
            'price_source': PRICE_SOURCE, 'price_date': PRICE_DATE,
            'billing_basis': 'API-equivalent estimate; not actual account billing',
            'settings': [{'model': p['model'], 'effort': p['effort'],
                          'service_tier': p['service_tier']} for p in participants]}


def evaluate_pairs(pairs, *, correctness_passed, review_complete, actionable_findings,
                   effort_comparison=False):
    """Apply the three-pair gate; declare effort_comparison only in experiment 5."""
    reasons = []
    if correctness_passed is not True:
        reasons.append('Correctness checks are incomplete or failed')
    if review_complete is not True or type(actionable_findings) is not int or actionable_findings != 0:
        reasons.append('Independent review is incomplete or has actionable findings')
    if not isinstance(pairs, list) or len(pairs) != 3:
        return {'accepted': False, 'reasons': reasons+['Exactly three paired trials required']}
    if not all(isinstance(pair, dict) for pair in pairs):
        return {'accepted': False, 'reasons': reasons+['Invalid paired trial']}
    orders = [pair.get('order') for pair in pairs]
    alternating = ([['baseline', 'candidate'], ['candidate', 'baseline'], ['baseline', 'candidate']],
                   [['candidate', 'baseline'], ['baseline', 'candidate'], ['candidate', 'baseline']])
    if orders not in alternating:
        reasons.append('Trial order was not alternated')
    try:
        pinned_arms = {}
        for pair in pairs:
            for arm in ('baseline', 'candidate'):
                _require(pair[arm]['telemetry_complete'] is True, 'Incomplete telemetry')
                tokens = _usage(pair[arm]['tokens'])
                _require(tokens['total_tokens'] > 0, 'Empty trial usage')
                fresh = pair[arm]['tokens'].get('fresh_input_tokens')
                _require(type(fresh) is int and fresh >= 0 and
                         fresh == tokens['input_tokens']-tokens['cached_input_tokens']
                         -tokens['cache_write_input_tokens'], 'Invalid fresh-input counter')
                pins = pair[arm]['pins']
                _require(isinstance(pins, dict) and all(_identifier(pins.get(key))
                         for key in ('source', 'guidance', 'scenario')), 'Missing source pins')
                settings = pair[arm]['settings']
                _require(isinstance(settings, list) and bool(settings), 'Missing model settings')
                for setting in settings:
                    _require(isinstance(setting, dict)
                             and set(setting) == {'model', 'effort', 'service_tier'}
                             and setting['model'] == 'gpt-6.1-sol'
                             and setting['effort'] in EFFORTS
                             and setting['service_tier'] in TIER_MULTIPLIERS,
                             'Unpinned model, effort or service tier')
                settings_pin = sorted({(s['model'], s['effort'], s['service_tier']) for s in settings})
                arm_pin = ({key: pair[arm]['pins'][key]
                            for key in ('source', 'guidance', 'scenario')}, settings_pin)
                _require(arm not in pinned_arms or pinned_arms[arm] == arm_pin,
                         'Source, guidance or settings changed across repeated trials')
                pinned_arms[arm] = arm_pin
            for pin in ('source', 'scenario'):
                _require(pair['baseline']['pins'][pin] == pair['candidate']['pins'][pin],
                         'Unmatched source or scenario')
            def settings_key(arm):
                return {(s['model'], s['service_tier']) if effort_comparison is True
                        else (s['model'], s['effort'], s['service_tier'])
                        for s in pair[arm]['settings']}
            _require(settings_key('baseline') == settings_key('candidate'),
                     'Unmatched model, effort or service tier')
        baseline = [pair['baseline']['tokens']['total_tokens'] for pair in pairs]
        candidate = [pair['candidate']['tokens']['total_tokens'] for pair in pairs]
        baseline_cost = [Decimal(pair['baseline']['estimated_api_cost_usd']) for pair in pairs]
        candidate_cost = [Decimal(pair['candidate']['estimated_api_cost_usd']) for pair in pairs]
        _require(all(cost.is_finite() and cost >= 0 for cost in baseline_cost+candidate_cost),
                 'Invalid estimated costs')
        deltas = [{key: pair['candidate']['tokens'][key]-pair['baseline']['tokens'][key]
                   for key in ('total_tokens', 'fresh_input_tokens', 'output_tokens')}
                  for pair in pairs]
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        return {'accepted': False, 'reasons': reasons+['Incomplete paired evidence: '+str(error)]}
    savings = Decimal(1)-Decimal(median(candidate))/Decimal(median(baseline))
    improved = sum(after < before for before, after in zip(baseline, candidate))
    if savings < Decimal('.1'):
        reasons.append('Median total-token savings below 10%')
    if improved < 2:
        reasons.append('Fewer than two pairs improved')
    if median(candidate_cost) > median(baseline_cost):
        reasons.append('Median estimated API cost increased')
    return {'accepted': not reasons, 'reasons': reasons,
            'median_total_token_savings_fraction': str(savings),
            'improved_pairs': improved, 'paired_token_changes': deltas,
            'median_baseline_estimated_api_cost_usd': str(median(baseline_cost)),
            'median_candidate_estimated_api_cost_usd': str(median(candidate_cost)),
            'limitation': 'Three trials provide bounded evidence, not a general performance guarantee'}
