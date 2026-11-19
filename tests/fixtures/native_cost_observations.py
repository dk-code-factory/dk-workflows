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
    result = collect_thread(path, thread, expected_turns=expected_turns)
    result.pop('turns')
    result['descendants_observed'] = 0
    return result


def own_thread_rows(rows, thread, *, ancestors=None):
    """Exclude only a corroborated, usage-free copied ancestor prefix in memory.

    Some host forks replay parent events at the child's creation timestamp.
    Timestamps cannot establish ownership. The caller supplies ancestor turns
    from the independently inventoried host graph and validated parent traces.
    """
    if len(rows) < 2 or rows[1]['type'] != 'session_meta':
        return rows, 0
    ancestors = ancestors or {}
    require(rows[0]['type'] == 'session_meta' and rows[0]['payload']['id'] == thread,
            'Missing own fork identity')
    parent = rows[0]['payload'].get('parent_thread_id')
    require(parent in ancestors and rows[1]['payload']['id'] == parent,
            'Uncorroborated copied parent identity')
    boundary = next((i for i, row in enumerate(rows[2:], 2)
                     if row['type'] == 'event_msg' and
                     row['payload'].get('type') == 'thread_settings_applied'), None)
    require(boundary is not None, 'Missing own fork settings boundary')
    foreign_turns = {turn for turns in ancestors.values() for turn in turns}
    for row in rows[1:boundary]:
        payload = row['payload']
        if row['type'] == 'session_meta':
            require(payload['id'] in ancestors, 'Unknown copied ancestor identity')
        if row['type'] == 'token_usage_record':
            require(payload['thread_id'] in ancestors,
                    'Own or unknown usage in copied ancestor prefix')
        if row['type'] == 'event_msg' and payload.get('type') == 'token_count':
            require(not payload.get('info'), 'Counter evidence in copied ancestor prefix')
        if row['type'] == 'turn_context' or (row['type'] == 'event_msg' and
                payload.get('type') in ('task_started', 'task_complete', 'task_completed')):
            require(payload['turn_id'] in foreign_turns,
                    'Unknown turn in copied ancestor prefix')
    own_rows = [rows[0], *rows[boundary:]]
    require(not any(row['type'] == 'session_meta' for row in own_rows[1:]),
            'Unexpected session identity after own fork boundary')
    require(not any(row['type'] == 'event_msg' and
                    row['payload'].get('type') == 'task_started' and
                    row['payload']['turn_id'] in foreign_turns for row in own_rows),
            'Ancestor turn after own fork boundary')
    return own_rows, boundary-1


def collect_thread(path, thread, *, expected_turns, ancestors=None):
    """Collect one thread; its caller must independently account for descendants."""
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows, inherited = own_thread_rows(rows, thread, ancestors=ancestors)
    records = native_usage.read_records([path])
    records = {key: value for key, value in records.items() if value['thread_id'] == thread}
    controls, contexts, starts, terminals = {}, {}, [], {}
    counters, observed = defaultdict(list), defaultdict(list)
    zero = {key: 0 for key in native_usage.TOKEN_FIELDS}
    previous = dict(zero)
    active = None
    final_outputs, last_usage_positions = {}, {}
    for position, row in enumerate(rows):
        payload = row['payload']
        if row['type'] == 'turn_context':
            require(payload['turn_id'] not in contexts, 'Duplicate turn context')
            contexts[payload['turn_id']] = payload
        if (row['type'] == 'response_item' and payload.get('type') == 'message'
                and payload.get('role') == 'assistant' and payload.get('phase') == 'final_answer'):
            final_outputs[active] = (position, ''.join(
                item.get('text', '') for item in payload.get('content', [])
                if item.get('type') == 'output_text'))
        if row['type'] == 'token_usage_record' and payload['thread_id'] == thread:
            require(active == payload['turn_id'], 'Usage outside the active host turn')
            observed[active].append(payload['response_id'])
            last_usage_positions[active] = position
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
            output_position, output = final_outputs.get(turn, (-1, None))
            require(output == payload['last_agent_message'] and output_position >= 0,
                    'Missing or conflicting original terminal output')
            require(last_usage_positions.get(turn, -1) > output_position,
                    'Terminal response missing from host usage inventory')
            terminals[turn] = dict(controls)
            active = None
    require(active is None and starts == expected_turns and set(terminals) == set(starts),
            'Incomplete or unexpected host turn inventory')
    require(set(contexts) == set(starts), 'Incomplete or unexpected turn settings')
    totals = dict(zero)
    cost = Decimal(0)
    settings = []
    response_ids = []
    turns = []
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
        turn_cost = Decimal(0)
        turn_totals = dict(zero)
        for response, host_usage in zip(ids, counters[turn]):
            usage = records[response]['usage']
            require(usage == host_usage, 'Response usage differs from independent host event')
            require(usage['cache_write_input_tokens'] == 0,
                    'Cache writes need independently observed TTLs')
            for key in totals:
                totals[key] += usage[key]
                turn_totals[key] += usage[key]
            turn_cost += dollar_usage.response_cost(usage, service_tier=control['service_tier'])
        cost += turn_cost
        turns.append({'turn_id': turn, 'response_ids': ids, 'tokens': turn_totals,
                      'settings': settings[-1], 'api_equivalent_cost_usd': str(turn_cost)})
        response_ids.extend(ids)
    require(set(response_ids) == set(records), 'Usage outside the measured scope')
    totals['fresh_input_tokens'] = totals['input_tokens'] - totals['cached_input_tokens']
    result = {'telemetry_complete': True, 'tokens': totals, 'response_ids': response_ids,
            'response_count': len(response_ids), 'settings': settings,
            'api_equivalent_cost_usd': str(cost), 'rate_card': dollar_usage.RATE_CARD,
            'raw_rollout_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'inventory_basis': 'Advancing host token_count events, cumulative deltas and terminal outcomes',
            'turns': turns}
    if inherited:
        result['inherited_prefix_rows'] = inherited
    return result


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
