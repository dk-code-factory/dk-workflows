"""Select prose candidates from pinned Git content; native skills identify prose.

References live in existing managed definitions/results. No registry or runner.
"""
from copy import deepcopy
from pathlib import Path
import re

from checklists import contract as checklist_contract
from task_policy import capture
from task_primitives import git


LABEL = 'prose_previous'
OPTION = 'prose_cleanup'


def commit(checkout, value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value):
        raise ValueError('Prose references require immutable commit IDs')
    resolved = git(checkout, 'rev-parse', '--verify', value + '^{commit}').decode().strip()
    if resolved != value:
        raise ValueError('Prose commit reference changed')
    return value


def tree(checkout, value):
    return git(checkout, 'rev-parse', '--verify', value + '^{tree}').decode().strip()


def patch(checkout, base, head):
    return git(checkout, '-c', 'diff.renames=false', 'diff', '--full-index',
               '--no-color', '--no-ext-diff', '--no-textconv', '--no-relative',
               '--no-renames', '--submodule=short', base, head, '--').decode('utf-8', 'replace')


def wrapped(spec):
    return 'skill_configuration' in spec or 'configuration' in spec


def execution_options(value, *, is_wrapped):
    options = value.get('options', {})
    return options.get('execution', {}) if is_wrapped else options


def completion_descriptor(saved, current):
    retained = deepcopy(saved)
    for field in ('checklist', 'required', 'cacheable'):
        if field in current:
            retained['definition'][field] = deepcopy(current[field])
        else:
            retained['definition'].pop(field, None)
    return retained


def governing(components, *, is_wrapped):
    """Compare guidance and original base/target, excluding only candidate content.

    The prior-result reference and baseline are helper-selected bookkeeping, not
    new governing guidance. All other consumed sources remain freshness inputs.
    """
    value = deepcopy(components)
    value['inputs'].pop(LABEL, None)
    source = value['inputs'].get('git', {})
    source.pop('diff_sha256', None)
    source.get('trees', {}).pop('head', None)
    source.get('commits', {}).pop('head', None)
    execution_options(value, is_wrapped=is_wrapped).pop(OPTION, None)
    return value


def handoff(tasks, descriptor):
    """Return candidate diff and a prepare request; never claim or edit work."""
    descriptor = deepcopy(descriptor)
    definition = descriptor['definition']
    source = definition.get('git', {})
    if set(source) - {'checkout', 'target', 'base', 'head', 'head_sensitive', 'history'} or not source:
        raise ValueError('Prose cleanup requires pinned Git inputs')
    if source.get('history'):
        raise ValueError('Prose cleanup uses content, not history-sensitive inputs')
    checkout = source['checkout']
    for name in ('target', 'base', 'head'):
        commit(checkout, source[name])
    # Callers supply the full original PR base on every pass. Delta selection is
    # ephemeral; never replace the managed Git base with an accepted output.
    definition.setdefault('inputs', {}).pop(LABEL, None)
    definition.setdefault('options', {}).pop(OPTION, None)
    # Bind helper semantics for both bundled and custom providers.
    resources = definition.setdefault('resources', {})
    resources['prose_runtime'] = str(Path(__file__).resolve())
    resources['prose_contract'] = str(Path(__file__).resolve().parents[1]/'docs/prose-cleanup.md')
    candidate_spec = tasks.spec(descriptor)
    candidate = capture(candidate_spec)
    # Spec construction can normalize and replace the retained definition.
    definition = descriptor['definition']
    with tasks.store.locked() as manifest:
        entry = deepcopy(manifest['entries'].get(candidate['key']))
        pending = [deepcopy(item) for item in manifest['entries'].values()
                   if item.get('claim') and item.get('components', {}).get('task', {}).get('id')
                   == candidate['components']['task']['id']]
    if pending:
        old = pending[0]
        if not old.get('managed'):
            raise ValueError('Prose recovery requires a retained managed definition')
        return {'action': 'recover_required', 'key': old['key'],
                'claim': old['claim'], 'request': request(old['managed']),
                'reason': 'Reconcile original execution and effects before selecting new candidates'}
    mode, baseline, previous = 'full', source['base'], None
    same_policy = bool(entry and candidate['components'].get('instruction_policy')
                       == entry['components'].get('instruction_policy'))
    if entry:
        saved = entry.get('managed')
        if not saved:
            raise ValueError('Prose recovery requires a retained managed definition')
        if entry.get('result'):
            _, problem = tasks.store._load_saved(entry)
            if problem:
                raise ValueError('Retained prose result unavailable: ' + problem)
        # Historical policy bindings cannot be replayed under newly adopted
        # guidance. The freshly captured explicit descriptor governs a full pass.
        if same_policy:
            prior_spec = tasks.spec(saved, replay=True)
            prior_capture = capture(prior_spec)
            status = tasks.store.describe(prior_capture, entry)
            same_governing = (governing(candidate['components'], is_wrapped=wrapped(candidate_spec))
                              == governing(prior_capture['components'], is_wrapped=wrapped(prior_spec)))
            retained = completion_descriptor(saved, definition)
            if candidate['cacheable'] and same_governing and status.get('analysis_complete'):
                current_checks = checklist_contract(prior_spec, retained['definition'])
                if (not status.get('checklist', {}).get('accepted')
                        or current_checks != entry.get('checklist', {}).get('contract')):
                    return {'action': 'recheck', 'key': candidate['key'], 'request': request(retained),
                            'reason': 'Finish current completion checks without repeating primary analysis'}
            if status['reusable'] and candidate['cacheable'] and same_governing:
                result, problem = tasks.store._load_saved(entry)
                if problem:
                    raise ValueError('Accepted prose result unavailable: ' + problem)
                prior = result['normalized'].get(OPTION)
                if prior is None:
                    raise ValueError('Accepted prose result lacks input/output references')
                baseline = commit(checkout, prior['output'])
                previous = result['path']
                if tree(checkout, baseline) == tree(checkout, source['head']):
                    return {'action': 'reuse', 'key': candidate['key'], 'result': previous,
                            'output': baseline, 'patch': '', 'request': request(retained)}
                mode = 'delta'
    definition['options'][OPTION] = {'baseline': baseline, 'head': source['head']}
    if previous:
        definition['inputs'][LABEL] = previous
    return {'action': 'prepare', 'mode': mode, 'baseline': baseline, 'head': source['head'],
            'previous': previous, 'patch': patch(checkout, baseline, source['head']),
            'request': request(descriptor)}


def request(descriptor):
    return {'tasks': [descriptor], **({'run': descriptor['run']} if 'run' in descriptor else {})}


def validate_report(spec, report):
    """Validate deterministic references; semantic editing stays with the skill."""
    execution = execution_options(spec, is_wrapped=wrapped(spec))
    expected = execution.get(OPTION) if isinstance(execution, dict) else None
    if expected is None or report.get('complete') is not True:
        return
    for field in ('changed_locations', 'validation', 'unresolved_ambiguities', 'reference_verification_limits'):
        if not isinstance(report.get(field), list):
            raise ValueError('Complete prose report requires a ' + field + ' list')
    if not report['validation']:
        raise ValueError('Complete prose report requires validation evidence or limitations')
    value = report.get(OPTION)
    if not isinstance(value, dict) or set(value) != {'baseline', 'head', 'output'}:
        raise ValueError('Complete prose report requires baseline, head and output references')
    if any(value.get(key) != expected[key] for key in ('baseline', 'head')):
        raise ValueError('Prose input references differ from the prepared handoff')
    source = spec['git']
    for pin in value.values():
        commit(source['checkout'], pin)
    if source['head'] != value['head']:
        raise ValueError('Prose head differs from the pinned assignment')
    # Output is an existing commit, created through ordinary authorized stages.
    # The helper never writes the index, objects or branch to capture edits.
    head = git(source['checkout'], 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
    if tree(source['checkout'], head) != tree(source['checkout'], value['output']):
        raise ValueError('Prose output must match the current committed tree')
