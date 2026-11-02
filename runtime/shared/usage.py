"""Provider usage semantics and evidence-only aggregation for local reports."""
import json
import math
from datetime import datetime, timezone

TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens',
                'reasoning_output_tokens', 'total_tokens')
GROUP_FIELDS = ('repository', 'stage', 'provider', 'model', 'effort', 'role')
EXECUTION_FIELDS = ('execution_id', 'parent_execution_id', 'response_id', 'tool_call_id',
                    'inherited', 'inclusive', 'scope', 'started_at', 'observed_at', 'stage', 'role',
                    'prompt_bytes', 'provider', 'model', 'effort', 'usage')


def execution_records(attempts):
    return [{key: item[key] for key in EXECUTION_FIELDS if key in item} for item in attempts]


def timestamp(value):
    """ISO dates mean midnight UTC; timestamps must carry an explicit offset."""
    if not isinstance(value, str):
        raise ValueError('expected an ISO date or offset-aware timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if len(value) == 10:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        raise ValueError('timestamps require an explicit timezone offset')
    return parsed.astimezone(timezone.utc)


def number(value, integer=True):
    return (type(value) is int if integer else type(value) in (int, float)) and value >= 0 and math.isfinite(value)


def normalize(raw, provider):
    """Never infer a missing component; cache/reasoning are subsets, not extras.

    Claude input_tokens excludes read/creation cache tokens. OpenAI input_tokens
    includes cached tokens; output_tokens includes reasoning tokens.
    """
    if not isinstance(raw, dict):
        return None
    result = {key: raw[key] if number(raw.get(key)) else None for key in TOKEN_FIELDS}
    result['source'] = raw.get('source') or ('claude_cli_json' if provider in ('claude', 'anthropic') else 'provider_metadata')
    if provider in ('claude', 'anthropic'):
        components = [raw.get(key) for key in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')]
        result['input_tokens'] = sum(components) if all(number(n) for n in components) else None
        result['cached_input_tokens'] = raw.get('cache_read_input_tokens') if number(raw.get('cache_read_input_tokens')) else None
    components_known = result['input_tokens'] is not None and result['output_tokens'] is not None
    component_total = result['input_tokens'] + result['output_tokens'] if components_known else None
    result['total_tokens_consistent'] = (result['total_tokens'] == component_total
        if result['total_tokens'] is not None and components_known else None)
    if result['total_tokens'] is None:
        result['total_tokens'] = component_total
    result['total_tokens_source'] = ('provider_reported' if number(raw.get('total_tokens'))
                                     else 'derived_input_output' if component_total is not None else None)
    if isinstance(raw.get('total_tokens_source'), str):
        result['total_tokens_source'] = raw['total_tokens_source']
    for child, parent in (('cached_input_tokens', 'input_tokens'), ('reasoning_output_tokens', 'output_tokens')):
        if result[child] is not None and result[parent] is not None and result[child] > result[parent]:
            result[child] = None
    cost = raw.get('total_cost_usd')
    result['cost_usd'] = cost if number(cost, integer=False) else None
    result['cost_source'] = raw.get('cost_source') or ('provider_reported' if result['cost_usd'] is not None else None)
    # No rate table, subscription allocation, or billed-cost inference.
    return result


def provider_identity(record):
    return {'openai': 'codex', 'anthropic': 'claude'}.get(record.get('provider'), record.get('provider'))


def identities(record):
    provider = provider_identity(record)
    return {(provider, key, record[key]) for key in
            ('execution_id', 'response_id', 'tool_call_id') if isinstance(record.get(key), str) and record[key]}


def snapshot_order(record):
    for key in ('observed_at', 'snapshot_at', 'started_at'):
        try:
            return timestamp(record.get(key))
        except (ValueError, TypeError):
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def source_labels(raw):
    labels = raw.get('sources')
    return [label for label in labels if isinstance(label, str)] if isinstance(labels, (list, tuple)) else []


def unique_records(records):
    """Reconcile identities before selecting totals and independent request samples."""
    retained = sorted(records, key=snapshot_order)
    roots, seen = list(range(len(retained))), {}

    def root(index):
        while roots[index] != index:
            roots[index] = roots[roots[index]]
            index = roots[index]
        return index

    for index, record in enumerate(retained):
        for key in identities(record):
            if key in seen:
                roots[root(index)] = root(seen[key])
            seen[key] = index
    snapshots = {}
    for index, record in enumerate(retained):
        snapshots.setdefault(root(index), []).append(record)
    selected, aliases, measured = [], [], []
    for observations in snapshots.values():
        items = [item for item in observations if not item.get('inherited')]
        # Merge in observation order, including through transitive identity bridges.
        # Non-null fields survive incomplete snapshots; raw evidence is untouched.
        merged, raw, sources, keys = {}, {}, set(), set()
        # Inherited observations supply identity and ancestry edges, never usage.
        for item in observations:
            keys.update(identities(item))
            for key in ('execution_id', 'parent_execution_id', 'provider'):
                if item.get(key) is not None:
                    merged[key] = item[key]
        for item in items:
            merged.update({key: value for key, value in item.items() if value is not None})
            value = item.get('usage')
            if isinstance(value, dict):
                raw.update({key: value for key, value in value.items() if value is not None})
                normalized = normalize(value, merged.get('provider'))
                if normalized['total_tokens'] is not None:
                    raw['total_tokens'] = normalized['total_tokens']
                    raw['total_tokens_source'] = normalized['total_tokens_source']
                sources.update(source_labels(value))
                if isinstance(value.get('source'), str):
                    sources.add(value['source'])
        if len(items) > 1 and raw:
            raw.update(source='merged_execution_snapshots', sources=sorted(sources))
        merged['usage'] = raw or None
        merged['inclusive'] = any(item.get('inclusive') for item in items)
        selected.append(merged)
        aliases.append(keys)
        measured.append(bool(items))

    executions, unqualified = {}, {}
    for index, keys in enumerate(aliases):
        for provider, kind, value in keys:
            if kind == 'execution_id':
                executions[provider, value] = index
                unqualified.setdefault(value, set()).add(index)

    def parent_index(record):
        parent = record.get('parent_execution_id')
        if not isinstance(parent, str) or not parent:
            return None
        local = executions.get((provider_identity(record), parent))
        if local is not None:
            return local
        matches = unqualified.get(parent, set())
        return next(iter(matches)) if len(matches) == 1 else None

    totals = []
    normalized = [normalize(record.get('usage'), record.get('provider')) for record in selected]
    for index, record in enumerate(selected):
        if not measured[index]:
            continue
        parent, ancestors = parent_index(record), {index}
        covered = set()
        while parent is not None and parent not in ancestors:
            ancestors.add(parent)
            if selected[parent].get('inclusive') and normalized[parent] is not None:
                covered.update(key for key in (*TOKEN_FIELDS, 'cost_usd')
                               if normalized[parent][key] is not None)
            parent = parent_index(selected[parent])
        known = {key for key in (*TOKEN_FIELDS, 'cost_usd')
                 if normalized[index] is not None and normalized[index][key] is not None}
        if not covered or known - covered:
            totals.append({**record, '_covered_usage_fields': sorted(covered)})
    inherited = sum(bool(r.get('inherited')) for r in records)
    excluded = {'duplicates': len(records) - inherited - sum(measured),
                'inherited': inherited,
                'inclusive_children': sum(measured) - len(totals)}
    return totals, excluded, [r for r, active in zip(selected, measured) if active and r.get('scope') == 'request']


def distribution(values):
    values = sorted(values)
    def percentile(p):
        if not values:
            return None
        pos = (len(values) - 1) * p
        lo, hi = math.floor(pos), math.ceil(pos)
        return values[lo] + (values[hi] - values[lo]) * (pos - lo)
    return {'sample_size': len(values), 'min': values[0] if values else None,
            'median': percentile(.5), 'p90': percentile(.9), 'p95': percentile(.95),
            'max': values[-1] if values else None}


def measurements(records, requests):
    """Known subtotals always carry their denominator and missing count."""
    usages = [normalize(r.get('usage'), r.get('provider')) for r in records]
    for record, value in zip(records, usages):
        if value is not None:
            for key in record.get('_covered_usage_fields', ()):
                value[key] = None
    result = {'executions': len(records), 'attempts_with_usage': sum(u is not None and any(u[k] is not None for k in TOKEN_FIELDS) for u in usages)}
    result['inconsistent_token_totals'] = sum(u is not None and u['total_tokens_consistent'] is False for u in usages)
    result['total_token_sources'] = sorted({u['total_tokens_source'] for u in usages
        if u is not None and u['total_tokens'] is not None and u['total_tokens_source'] is not None})
    result['missing_attempts'] = len(records) - result['attempts_with_usage']
    result['complete'] = bool(records) and result['missing_attempts'] == 0
    result['tokens'] = {}
    for key in (*TOKEN_FIELDS, 'cost_usd'):
        eligible = [u for r, u in zip(records, usages) if key not in r.get('_covered_usage_fields', ())]
        values = [u[key] for u in eligible if u is not None and u[key] is not None]
        metric = {'known_subtotal': sum(values) if values else None,
                  'measured_attempts': len(values), 'missing_attempts': len(eligible) - len(values),
                  'complete': bool(eligible) and len(values) == len(eligible)}
        if key == 'cost_usd':
            result['cost'] = {**metric, 'currency': 'USD', 'interpretation': 'provider-reported evidence; not guaranteed billed cost',
                              'sources': sorted({u['cost_source'] for u in usages if u and u['cost_source']})}
        else:
            result['tokens'][key] = metric
    # CLI turn totals do not establish individual model-request sizes/counts.
    result['requests'] = {'known_count': len(requests) if requests else None,
                          'unmeasured_executions': sum(r.get('scope') != 'request' for r in records),
                          'complete': bool(records) and all(r.get('scope') == 'request' for r in records)}
    result['usage_sources'] = sorted({u['source'] for u in usages if u and isinstance(u['source'], str)} |
        {source for r in records if isinstance(r.get('usage'), dict)
         for source in source_labels(r['usage'])})
    request_usage = [normalize(r.get('usage'), r.get('provider')) for r in requests]
    result['request_input_tokens'] = distribution([u['input_tokens'] for u in request_usage
        if u and u['input_tokens'] is not None])
    result['prompt_bytes'] = distribution([r['prompt_bytes'] for r in records if number(r.get('prompt_bytes'))])
    return result


def report(records, groups=()):
    if any(field not in GROUP_FIELDS for field in groups) or len(set(groups)) != len(groups):
        raise ValueError('invalid or repeated usage grouping field')
    records, excluded, requests = unique_records(records)
    grouped, request_groups = {}, {}
    for collection, target in ((records, grouped), (requests, request_groups)):
        for record in collection:
            key = tuple(record.get(field) for field in groups)
            target.setdefault(key, []).append(record)
    return {**measurements(records, requests), 'excluded_records': excluded,
            'group_by': list(groups), 'groups': [
                {'key': dict(zip(groups, key)), **measurements(grouped.get(key, []), request_groups.get(key, []))}
                for key in sorted(grouped.keys() | request_groups.keys(), key=json.dumps)] if groups else []}
