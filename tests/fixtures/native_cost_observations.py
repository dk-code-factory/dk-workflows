"""Read native leaf-task costs using independent host counter events.

Test-only reader: no agent launcher, model requests or production state. A response
inventory comes from advancing host token_count events, not the usage records
being counted. Ordered per-response counters, cumulative deltas, terminal outcomes
and actual settings must all agree before telemetry is called complete.
"""
from collections import defaultdict
from decimal import Decimal
import hashlib
import json

import dollar_usage
import native_usage


def require(value, message):
    if not value:
        raise ValueError(message)


def collect(path, thread, *, expected_turns, descendants):
    """Collect complete explicitly leaf-only turns; preserve failures externally."""
    require(descendants == [], 'Unexpected descendants need their own inventories')
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    records = native_usage.read_records([path])
    records = {key: value for key, value in records.items() if value['thread_id'] == thread}
    controls, contexts, starts, terminals = {}, {}, [], {}
    counters, observed = defaultdict(list), defaultdict(list)
    zero = {key: 0 for key in native_usage.TOKEN_FIELDS}
    previous = dict(zero)
    active = None
    for row in rows:
        payload = row['payload']
        if row['type'] == 'turn_context':
            require(payload['turn_id'] not in contexts, 'Duplicate turn context')
            contexts[payload['turn_id']] = payload
        if row['type'] == 'token_usage_record' and payload['thread_id'] == thread:
            require(active == payload['turn_id'], 'Usage outside the active host turn')
            observed[active].append(payload['response_id'])
        if row['type'] != 'event_msg':
            continue
        kind = payload.get('type')
        if kind == 'thread_settings_applied':
            settings = payload['thread_settings']
            current = {key: settings.get(key) for key in
                       ('model', 'reasoning_effort', 'service_tier')}
            require(active is None or not controls or current == controls,
                    'Settings changed during a host turn')
            controls = current
        elif kind == 'task_started':
            require(active is None, 'Overlapping native turns')
            active = payload['turn_id']
            starts.append(active)
        elif kind == 'token_count' and payload.get('info'):
            info = payload['info']
            total = native_usage._usage(info['total_token_usage'])
            if total != previous:
                require(active is not None, 'Counter advanced outside a host turn')
                delta = {key: total[key] - previous[key] for key in zero}
                last = native_usage._usage(info['last_token_usage'])
                require(delta == last, 'Host cumulative delta differs from response counters')
                counters[active].append(last)
                previous = total
        elif kind in ('task_complete', 'task_completed'):
            turn = payload['turn_id']
            require(active == turn and turn not in terminals, 'Unmatched terminal host outcome')
            require(bool(payload.get('last_agent_message', '').strip()), 'Missing terminal output')
            terminals[turn] = dict(controls)
            active = None
    require(active is None and starts == expected_turns and set(terminals) == set(starts),
            'Incomplete or unexpected host turn inventory')
    require(set(contexts) == set(starts), 'Incomplete or unexpected turn settings')
    totals = dict(zero)
    cost = Decimal(0)
    settings = []
    response_ids = []
    for turn in starts:
        ids = observed[turn]
        require(len(ids) == len(set(ids)), 'Duplicate response observation')
        require(bool(ids) and len(ids) == len(counters[turn]),
                'Independent host response inventory differs from usage records')
        context, control = contexts[turn], terminals[turn]
        require(context['model'] == control['model'] == dollar_usage.RATE_CARD['model'],
                'Unmatched or unsupported actual model')
        require(context['effort'] == control['reasoning_effort'] and
                context['effort'] in native_usage.EFFORTS, 'Unmatched actual effort')
        require(control['service_tier'] in native_usage.TIER_MULTIPLIERS,
                'Missing actual service tier')
        settings.append({'model': control['model'], 'effort': context['effort'],
                         'service_tier': control['service_tier']})
        for response, host_usage in zip(ids, counters[turn]):
            usage = records[response]['usage']
            require(usage == host_usage, 'Response usage differs from independent host event')
            require(usage['cache_write_input_tokens'] == 0,
                    'Cache writes need independently observed TTLs')
            for key in totals:
                totals[key] += usage[key]
            cost += dollar_usage.response_cost(usage, service_tier=control['service_tier'])
        response_ids.extend(ids)
    require(set(response_ids) == set(records), 'Usage outside the measured scope')
    totals['fresh_input_tokens'] = totals['input_tokens'] - totals['cached_input_tokens']
    return {'telemetry_complete': True, 'tokens': totals, 'response_ids': response_ids,
            'response_count': len(response_ids), 'settings': settings,
            'api_equivalent_cost_usd': str(cost), 'rate_card': dollar_usage.RATE_CARD,
            'raw_rollout_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'inventory_basis': 'Advancing host token_count events, cumulative deltas and terminal outcomes',
            'descendants_observed': 0}


def starting_context(path, *, replacements):
    """Normalize only declared arm paths and host-assigned agent identities."""
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    meta = next(row['payload'] for row in rows if row['type'] == 'session_meta')
    context = next(row['payload'] for row in rows if row['type'] == 'turn_context')
    messages = []
    for row in rows:
        message = row['payload']
        if row['type'] != 'response_item' or message.get('type') != 'message':
            continue
        if message['role'] == 'assistant':
            break
        if message['role'] in ('system', 'developer', 'user'):
            messages.append({'role': message['role'], 'content': message['content']})
    require(meta.get('base_instructions') and any(m['role'] == 'developer' for m in messages),
            'Missing native starting instructions')
    controls = {key: context.get(key) for key in
                ('disabled_plugin_ids', 'approval_policy', 'approvals_reviewer', 'sandbox_policy',
                 'permission_profile', 'model', 'comp_hash', 'collaboration_mode',
                 'multi_agent_version', 'realtime_active', 'effort')}
    controls.update(cli_version=meta['cli_version'], model_provider=meta['model_provider'])
    values = {'base_instructions': meta['base_instructions'], 'messages': messages, 'controls': controls}
    normalized = json.dumps(values, sort_keys=True)
    for original, replacement in replacements.items():
        normalized = normalized.replace(original, replacement)
    return hashlib.sha256(normalized.encode()).hexdigest()
