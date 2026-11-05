"""Observed experiment ledger, separate from the complete recurring-cost gate.

Coordinator attribution is by response's primary tool action, not elapsed time.
Mixed preparation/implementation/validation responses remain explicitly mixed.
The active coordinator cannot have a complete terminal inventory at this cutoff.
"""
import argparse
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import dollar_usage
import native_usage


def category(actions):
    text = '\n'.join(actions).lower()
    measurement = any(word in text for word in (
        'dollar_', 'dollar-', 'experiment-', 'native.stderr', 'rollout', 'state_5.sqlite'))
    edit = any(word in text for word in (
        'apply_patch', '.write_text(', 'git commit', 'git add', 'git push', 'gh pr create', 'gh pr edit'))
    validation = any(word in text for word in (
        'pytest', 'scripts/test.py', 'scripts/check.py', 'git diff', 'git show',
        'ci_wait.py', 'rg ', 'sed ', 'cat ', 'head ', 'tail '))
    if measurement and edit and any(word in text for word in ('runtime/', 'scripts/dk.py', 'docs/task-state.md')):
        return 'mixed_implementation_measurement_review'
    if measurement:
        return 'measurement'
    if edit and validation:
        return 'mixed_implementation_review'
    if edit:
        return 'implementation'
    if validation:
        return 'review_validation'
    return 'coordination_unallocated'


def coordinator(path, seen):
    totals = defaultdict(Decimal)
    counts = defaultdict(int)
    actions = []
    cutoff = None
    token_totals = defaultdict(int)
    for line in path.read_text().splitlines():
        row = json.loads(line)
        payload = row.get('payload', {})
        if row['type'] == 'response_item' and payload.get('type') in ('function_call', 'custom_tool_call'):
            actions.append(payload.get('name', '') + ' ' + payload.get('input', payload.get('arguments', '')))
        if row['type'] != 'token_usage_record':
            continue
        response = payload['response_id']
        normalized = native_usage._usage(payload['usage'])
        if response in seen:
            if seen[response] != normalized:
                raise ValueError('Conflicting duplicate response')
            actions = []
            continue
        seen[response] = normalized
        if normalized['cache_write_input_tokens']:
            raise ValueError('Coordinator cache-write TTL is unknown')
        label = category(actions)
        cost = dollar_usage.response_cost(normalized, service_tier='default')
        totals[label] += cost
        counts[label] += 1
        for key, value in normalized.items():
            token_totals[key] += value
        cutoff = row['timestamp']
        actions = []
    return {'observed_costs_by_primary_action_usd': {k:str(v) for k,v in sorted(totals.items())},
            'response_counts_by_primary_action': dict(sorted(counts.items())),
            'observed_total_usd': str(sum(totals.values())), 'observed_tokens': dict(token_totals),
            'last_priced_response_timestamp': cutoff, 'telemetry_complete': False,
            'pricing_assumption': 'Configured gpt-6.1-sol/high/default; coordinator responses lack independent SSE tier inventory.',
            'attribution': 'Whole responses assigned by primary tool action. Mixed actions are not split or disguised as precise time accounting.',
            'raw_rollout_sha256_at_cutoff': hashlib.sha256(path.read_bytes()).hexdigest()}


def ledger(root, root_rollout):
    seen = {}
    experiments = []
    recurring = Decimal(0)
    for directory in sorted(root.glob('experiment-*')):
        if not directory.is_dir():
            continue
        arms = []
        for arm in sorted(directory.iterdir()):
            if not arm.is_dir() or not (arm/'usage.json').exists():
                continue
            usage = json.loads((arm/'usage.json').read_text())
            reference = json.loads((arm/'private-rollout-reference.json').read_text())
            records = native_usage.read_records([Path(reference['path'])])
            total = Decimal(0)
            duplicates = 0
            for response, record in records.items():
                normalized = record['usage']
                if response in seen:
                    if seen[response] != normalized:
                        raise ValueError('Conflicting duplicate response')
                    duplicates += 1
                    continue
                if normalized['cache_write_input_tokens']:
                    raise ValueError('Cache-write TTL requires independent evidence')
                seen[response] = normalized
                total += dollar_usage.response_cost(normalized, service_tier='default')
            price = usage.get('api_equivalent_cost_usd', usage.get('observed_api_equivalent_cost_usd'))
            if price is None:
                # Early telemetry failures were normalized in the retained public
                # report without rewriting the failed raw collector output.
                number = directory.name.removeprefix('experiment-')
                report = json.loads((Path(__file__).resolve().parents[2]/
                    ('docs/evidence/151-experiment-'+number+'.json')).read_text())
                pair, name = arm.name.split('-',1)
                price = report['observed_costs_usd'][name][int(pair)-1]
            expected = Decimal(price)
            if duplicates or total != expected:
                raise ValueError('Arm cost or global response inventory mismatch')
            recurring += total
            arms.append({'arm':arm.name, 'observed_api_equivalent_cost_usd':str(total),
                         'response_count':len(records), 'telemetry_complete':usage['telemetry_complete']})
        experiments.append({'experiment':directory.name, 'arms':arms,
                            'observed_total_usd':str(sum(Decimal(a['observed_api_equivalent_cost_usd']) for a in arms))})
    overhead = coordinator(root_rollout, seen)
    return {'billing_basis':dollar_usage.RATE_CARD['billing_basis'], 'rate_card':dollar_usage.RATE_CARD,
            'native_recurring_trial_spending':experiments,
            'observed_native_trial_total_usd':str(recurring),
            'implementation_review_measurement_overhead':overhead,
            'observed_cumulative_total_usd':str(recurring+Decimal(overhead['observed_total_usd'])),
            'globally_unique_priced_response_count':len(seen),
            'complete_account_billing':False,
            'limitations':[
                'API-equivalent estimates, not actual subscription/account charges. No fixed spending ceiling.',
                'Experiment 1 lacks an independent response inventory and remains observed/inconclusive.',
                'The active coordinator, later CI consumption and final response remain outside this recorded cutoff.',
                'Overhead attribution uses tool-action heuristics with explicit mixed/unallocated buckets; it is not recurring workflow cost.',
                'Native scopes include preparation, waits, result consumption and terminal assessment; no descendants were launched.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--coordinator-rollout', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(ledger(args.root, args.coordinator_rollout), sort_keys=True, indent=2))
