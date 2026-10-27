"""Engineering compatibility is computed, never trusted from a caller's ID."""
import copy
import hashlib
import json
from pathlib import Path

from shared.preparation_graph import digest


def descriptor(value):
    value = copy.deepcopy(value)
    replacements = {}
    execution = value.get('execution') or {}
    for key in ('root', 'origin_root'):
        if execution.get(key):
            replacements[execution[key]] = '<provider-package>'
    for index, bundle in enumerate(execution.get('bundles', [])):
        replacements[bundle['root']] = '<command-package:' + str(index) + '>'
    for index, source in enumerate(value.get('sources', [])):
        replacements[source['path']] = '<instruction:' + str(index) + '>'
    # Known paths also appear in the adapter's declared effective prompt.
    def normalize(item):
        if isinstance(item, str):
            for path, token in sorted(replacements.items(), key=lambda v: -len(v[0])):
                item = item.replace(path, token).replace(json.dumps(path)[1:-1], token)
            return item
        if isinstance(item, list):
            return [normalize(v) for v in item]
        if isinstance(item, dict):
            return {k: normalize(v) for k, v in item.items()}
        return item
    return normalize(value)


def engineering_id(prepared):
    if prepared.get('preparation_version', 1) < 3:
        return prepared['preparation_id']
    # Only these declared fields are operational, derived, or verified lineage.
    excluded = {'preparation_id', 'engineering_id', 'sources', 'reviewer_paths',
                'parent', 'refresh', 'retired_ids', 'workflow_dir', 'workspace',
                'identity', 'inputs', 'reviewers', 'recovery_manifest', 'recovery_context'}
    content = {k: v for k, v in prepared.items() if k not in excluded}
    graph = prepared.get('graph', {})
    content['graph'] = {node['location']: {**node,
        'depends_on': sorted(graph[key]['location'] for key in node['depends_on'])}
        for node in graph.values()}
    content['identity'] = {k: v for k, v in prepared['identity'].items()
                           if k not in {'head_sha', 'decisions_hash'}}
    content['inputs'] = {**prepared['inputs'], 'sources': {
        key: {**value, 'identity': key if prepared['sources'][key].get('mode') == 'capture' else value['identity']}
        for key, value in prepared['inputs']['sources'].items() if not key.startswith('prior_preparation/')}}
    # Preserve explicit order independently of JSON object key ordering.
    order = prepared.get('reviewer_order', list(prepared['reviewers']))
    content['reviewers'] = [descriptor(prepared['reviewers'][name]) for name in order]
    content['source_contracts'] = {key: {k: v for k, v in source.items()
                                       if k not in {'path', 'snapshot', 'sha256', 'origin_identity'}}
                                   for key, source in prepared['sources'].items()
                                   if not key.startswith('prior_preparation/')}
    # Only proven ancestors and the declared absence fields are bookkeeping.
    # Unrelated prior preparations and unknown absence fields still govern.
    lineage, parent = set(), prepared.get('parent')
    while isinstance(parent, dict) and {'path', 'sha256', 'preparation_id'} <= parent.keys():
        if parent['preparation_id'] in lineage:
            raise ValueError('cyclic preparation lineage')
        try:
            raw = Path(parent['path']).read_bytes()
            ancestor = json.loads(raw)
            if (hashlib.sha256(raw).hexdigest() != parent['sha256']
                    or ancestor.get('preparation_id') != parent['preparation_id']):
                raise ValueError('preparation parent fingerprint mismatch')
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            raise ValueError('invalid selected parent preparation: ' + str(exc)) from exc
        lineage.add(parent['preparation_id'])
        parent = ancestor.get('parent')
    prior_inputs = {}
    for key, source in prepared['sources'].items():
        if not key.startswith('prior_preparation/'):
            continue
        prior = json.loads(Path(source['snapshot']).read_bytes())
        value = {k: v for k, v in source.items()
                 if k not in {'path', 'snapshot', 'sha256', 'origin_identity', 'mode'}}
        if 'analysis' in prior:
            if prior.get('preparation_id') not in lineage:
                value['preparation'] = engineering_id(prior)
        else:
            value['extensions'] = {k: v for k, v in prior.items() if k not in {'verified_absence', 'reason'}}
            if not value['extensions']:
                value.pop('extensions')
        if value:
            prior_inputs[key] = value
    content['prior_governing'] = prior_inputs
    return digest(content)


def compatible_reference(item, prepared):
    return (item.get('engineering_id') == engineering_id(prepared)
            if prepared.get('preparation_version', 1) >= 3
            else item.get('preparation_id') == prepared['preparation_id'])
