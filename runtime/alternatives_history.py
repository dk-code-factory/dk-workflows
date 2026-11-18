"""Read original alternatives evidence without making it current-code coverage.

No new storage protocol: the existing store validates immutable results, native
observations and the original completion checklist against their original pins.
"""
from copy import deepcopy
import base64
import json

from state import digest


KINDS = {'assessed-alternatives', 'alternatives', 'architecture'}
MANUAL = 'dk-workflows:dk-pr-review-alternatives'


def request(path, *, tasks=None):
    """Bind new comparison authorization to the dedicated direct invocation."""
    from state import read
    value = read(path)
    basic = isinstance(value, dict) and all(isinstance(value.get(field), str) and value[field].strip()
                                          for field in ('id', 'source', 'instruction'))
    bound = False
    if tasks is not None:
        from pathlib import Path
        from state import resource
        current = resource(path)
        for entry in entries(tasks).values():
            inputs = entry['managed']['definition'].get('inputs', {})
            for name in ('alternatives_request', 'alternatives_reassessment'):
                prior = inputs.get(name)
                if prior and Path(prior).absolute() == Path(path).absolute():
                    expected = entry.get('components', {}).get('inputs', {}).get(name)
                    if expected is not None and current != expected:
                        raise ValueError('Original manual request changed; restore its original source')
                    bound = bound or expected == current
    if basic and value.get('skill') == MANUAL and value.get('origin') == 'user':
        return value
    # Older releases captured three-field request artifacts. Their unchanged
    # original binding can resume existing work, but cannot authorize a new id.
    if basic and 'skill' not in value and 'origin' not in value and bound:
        return value
    if not basic or value.get('skill') != MANUAL or value.get('origin') != 'user':
        raise ValueError('Fresh alternatives requires a stable id, original source and exact instruction for the direct user invocation of ' + MANUAL)
    return value


def guard_route(tasks, owner, kind, definition):
    """A downstream automatic call cannot create another initial chain."""
    sources = [definition.get('inputs', {}).get(name) for name in ('alternatives_request', 'alternatives_reassessment')]
    if any(sources):
        for source in filter(None, sources):
            request(source, tasks=tasks)
        return
    values = entries(tasks)
    chains = [value['managed'] for value in values.values()
              if value['managed'].get('stage') != 'assessment']
    if any(item.get('skill', 'dk-workflows:dk-pr-review') != owner
           or ('alternatives' if item['kind'] == 'architecture' else item['kind']) != kind
           for item in chains):
        raise ValueError('Resume the original alternatives chain with the same workflow owner; only a direct dedicated user invocation can start a fresh comparison')


def entries(tasks):
    with tasks.store.locked() as manifest:
        return {key: deepcopy(value) for key, value in manifest['entries'].items()
                if value.get('managed', {}).get('kind') in KINDS}


def accepted(tasks, key):
    """Validate historical acceptance, never current installed resources."""
    with tasks.store.locked() as manifest:
        original = deepcopy(manifest['entries'].get(key))
    if not original or original.get('managed', {}).get('kind') not in KINDS:
        raise ValueError('Original alternatives definition is missing; restore its evidence')
    saved, problem = tasks.store._load_saved(original)
    if problem or saved['status'] != 'complete':
        raise ValueError('Original alternatives evidence is missing, damaged or incomplete; restore it')
    if saved['cacheable'] is not True:
        raise ValueError('Original alternatives lacks a complete reusable input capture; restore its evidence')
    components = original['components']
    if digest(components) != saved['fingerprint']:
        raise ValueError('Original alternatives capture is damaged; restore it')
    captured = {'key': key, 'components': components,
                'fingerprint': saved['fingerprint'], 'cacheable': saved['cacheable']}
    # The original checklist contract, not the current package's generated
    # contract, governs historical acceptance. describe still checks its stored
    # responses, native observations, invalidation and outstanding claim.
    historical = {k: v for k, v in original.items() if k != 'managed'}
    status = tasks.store.describe(captured, historical)
    if not status['coverage_complete'] or not status.get('checklist', {}).get('complete'):
        raise ValueError('Original alternatives evidence needs claim/checklist recovery: ' + status['reason'])
    spec = deepcopy(components)
    if 'git' in original['managed']['definition']:
        spec['git'] = deepcopy(original['managed']['definition']['git'])
        # Commit pins come from the immutable execution receipt, rather than
        # trusting mutable replay definitions after a history rewrite.
        spec['git'].update(saved['provenance']['commits'])
    native = saved.get('native_execution', {}).get('evidence')
    handle = json.loads(base64.b64decode(native))['invocation']['handle'] if native else None
    return {'key': key, 'spec': spec, 'report': saved['normalized'],
            'native_handle': handle,
            'reference': saved['path'], 'managed': original['managed']}


def route(tasks, owner):
    """Choose retained work before constructing another route's initial plan."""
    values = entries(tasks)
    # A shared purpose assessment alone is not a second whole-problem plan.
    candidates = [value for value in values.values()
                  if value['managed'].get('stage') != 'assessment']
    if not candidates:
        candidates = list(values.values())
    if not candidates:
        return None
    # Existing requested-route work stays authoritative. Otherwise resume the
    # route that owns the retained chain, including an unfinished initial claim.
    selected = next((value for value in candidates if value['managed'].get('skill') == owner), candidates[0])
    managed = selected['managed']
    return {'owner': managed.get('skill', 'dk-workflows:dk-pr-review'),
            'kind': 'alternatives' if managed['kind'] == 'architecture' else managed['kind']}


def legacy_gate(tasks, owner, *, consumer=None, review_operation=None, analysis_only=False):
    """Resume historical whole-problem chains without inventing an inventory."""
    import alternatives
    consumer = consumer or owner
    values = entries(tasks)
    stages = {value['managed']['stage']: value for value in values.values()
              if value['managed'].get('skill', alternatives.OWNER) == owner
              and value['managed']['kind'] in {'alternatives', 'architecture'}
              and value['managed']['stage'] != 'impact'}
    refs, retained = {}, {}
    for stage in ('assessment', 'comparison', 'selection', 'alignment'):
        original = stages.get(stage)
        dependencies = {name: refs[name] for name in alternatives.REQUIRES[stage]}
        if original is None:
            return {'outcome': 'review_required', 'release_reviewers': False, 'stage': stage,
                    'references': refs, 'next_task': {'kind': 'alternatives', 'skill': owner,
                                                    'stage': stage, 'dependencies': dependencies}}
        task = original['key']
        try:
            value = alternatives.accepted(tasks, task, settled=True)
        except ValueError as exc:
            return {'outcome': 'recovery_required', 'release_reviewers': False, 'stage': stage,
                    'references': refs, 'next_task': {'key': task}, 'reason': str(exc)}
        if any(value['managed'].get('dependencies', {}).get(name) != ref for name, ref in dependencies.items()):
            raise ValueError('Original alternatives dependency chain changed; restore its evidence')
        refs[stage], retained[stage] = task, value
        if stage == 'assessment' and not value['report']['required']:
            return context({'outcome': 'skipped', 'release_reviewers': True, 'references': refs,
                            'governing_evidence': [value['reference']]})
        if stage == 'selection' and not value['report'].get('choice'):
            return {'outcome': 'choice_pending', 'release_reviewers': False, 'references': refs,
                    'next_task': {'key': task}}
    alignment = retained['alignment']
    result = alignment['report']['alignment']
    governing = [value['reference'] for stage, value in retained.items()
                 if consumer == alternatives.FIX_OWNER or stage != 'assessment']
    if result == 'aligned' or consumer == alternatives.FIX_OWNER and result == 'mismatch':
        return context({'outcome': 'planned', 'release_reviewers': True, 'references': refs,
                        'governing_evidence': governing, 'required_changes': alignment['report'].get('required_changes')})
    if result == 'mismatch':
        # Rejection is an external action. Keep its independent verification and
        # reconciled publication boundary; historical reuse grants no permission.
        verification = stages.get('verification')
        if verification is None:
            return {'outcome': 'review_required', 'release_reviewers': False, 'stage': 'verification',
                    'references': refs, 'next_task': {'kind': 'alternatives', 'skill': owner,
                        'stage': 'verification', 'dependencies': {'alignment': alignment['key']}}}
        verified = alternatives.accepted(tasks, verification['key'], settled=True)
        if (verified['managed'].get('dependencies', {}).get('alignment') != alignment['key']
                or not verified['native_handle'] or not alignment['native_handle']
                or verified['native_handle'] == alignment['native_handle']):
            raise ValueError('Original mismatch requires linked independent verification')
        if verified['report']['disposition'] == 'confirmed':
            head = alignment['spec']['git']['head']
            if not review_operation:
                return {'outcome': 'publication_blocked' if analysis_only else 'mismatch_verified',
                        'release_reviewers': False, 'references': refs, 'head': head,
                        'required_changes': verified['report']['required_changes']}
            operation = alternatives.journal(review_operation, tasks, kind='post-review', head=head)
            approved = operation['request']['approved']
            if (approved['event'] != 'REQUEST_CHANGES' or approved.get('comments')
                    or approved.get('body') != verified['report']['required_changes']):
                raise ValueError('Original rejection requires its independently verified design changes')
            if alternatives.live_pr(tasks.store.subject['pr'])['headRefOid'] != head:
                return context({'outcome': 'planned', 'release_reviewers': True, 'references': refs,
                                'governing_evidence': [*governing, verified['reference']]})
            return {'outcome': 'alternatives_rejected', 'release_reviewers': False,
                    'complete': True, 'ordinary_review_complete': False, 'references': refs}
    return {'outcome': 'alignment_blocked', 'release_reviewers': False, 'references': refs}


def context(result):
    """Explicitly distinguish historical decisions from live alignment coverage."""
    return {**result, 'initial_review': True, 'design_context': True, 'current_alignment_required': True}
