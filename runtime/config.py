"""Process configuration and shared native invocation definitions."""
from copy import deepcopy
from pathlib import Path
import os
import re

import invocation
from state import digest, make_spec, read

CYCLE_PHASES = ('before_cycle', 'after_cycle')
FINALIZER_DEFAULTS = ['dk-commit-split', 'dk-pr-decisions', 'dk-pr-description',
                      'dk-pr-feedback-reply', 'dk-ci', 'dk-pr-publish']
LIMITS = {'review_cycles': 5, 'retries_per_task': 1, 'ci_repairs_per_cycle': 5,
          'ci_reruns_per_head': 3}
ACTIONS = ('push_commits', 'rewrite_commits', 'edit_pr', 'reply_to_feedback',
           'resolve_threads', 'create_draft_prs', 'request_review', 'post_findings', 'approve_ci', 'approve')
FIX_OPERATIONS = ['dk-pr-feedback-read', 'dk-pr-decisions', 'dk-pr-feedback-address', 'dk-pr-split',
              'dk-pr-reviewers-run', 'dk-commit-split', 'dk-commit-message', 'dk-pr-description',
              'dk-pr-feedback-reply', 'dk-ci', 'dk-ci-flakes', 'dk-pr-publish']
DEFAULTS = {
    'schema_version': 1,
    'accounts': {'personal': []},
    'skill_settings': {},
    'processes': {
        'pr-iter': {'run': 'dk-pr-iter', 'routes': {'own_pr': 'pr-fix', 'other_pr': 'pr-review'}},
        'pr-fix': {'run': 'dk-pr-fix', 'operations': FIX_OPERATIONS, 'limits': dict(LIMITS),
                   'finalizers': list(FINALIZER_DEFAULTS),
                   'actions': {**{action: action not in {'post_findings', 'approve_ci', 'approve'} for action in ACTIONS},
                               'merge': {'enabled': False, 'method': 'squash'}},
                   'hooks': {phase: [] for phase in CYCLE_PHASES}},
        'pr-review': {'run': 'dk-pr-review',
                      'verification': {'skill': 'dk-pr-review',
                                       'instructions': 'Independently verify candidate findings against pinned source and governing instructions. Return confirmed, rejected or uncertain findings with evidence. Do not edit or publish.'},
                      'operations': ['dk-pr-feedback-read', 'dk-pr-reviewers-run', 'dk-pr-review-post',
                                 'dk-ci-approve', 'dk-ci', 'dk-ci-flakes', 'dk-pr-publish'],
                      'limits': dict(LIMITS),
                      'actions': {**{action: action in {'post_findings', 'approve_ci'} for action in ACTIONS},
                                  'merge': {'enabled': False, 'method': 'squash'}},
                      'hooks': {phase: [] for phase in CYCLE_PHASES}},
        'commit-message': {'run': 'dk-commit-message'},
        'issue': {'run': 'dk-issue'},
        'pr-reviewers-run': {'run': 'dk-pr-reviewers-run'},
    },
    'extensions': {},
}


def finalizer_groups(finalizers):
    if not isinstance(finalizers, list):
        raise ValueError('Finalizers must be an ordered invocation list')
    groups, seen = [], []
    for entry in finalizers:
        group = entry if isinstance(entry, list) else [entry]
        if not group:
            raise ValueError('Parallel finalizer groups must not be empty')
        for value in group:
            call = invocation.normalize(value)
            reference = call.get('skill')
            if reference:
                if any(invocation.matches(reference, previous) or invocation.matches(previous, reference)
                       for previous in seen):
                    raise ValueError('Finalizer skills must be unique across the entire list')
                seen.append(reference)
        groups.append(deepcopy(group))
    return groups


def validate(settings):
    if not isinstance(settings, dict) or set(settings) - (DEFAULTS.keys() | {'$schema'}):
        raise ValueError('Unknown workflow setting; use the process configuration format')
    result = deepcopy(settings)
    if '$schema' in result and not invocation.text(result['$schema']):
        raise ValueError('$schema must be a nonempty schema reference')
    if 'schema_version' in result and (type(result['schema_version']) is not int or result['schema_version'] != 1):
        raise ValueError('Unsupported configuration schema_version')
    accounts = result.get('accounts', {})
    if not isinstance(accounts, dict) or set(accounts) - {'personal'}:
        raise ValueError('Accounts accept a personal account list')
    personal = accounts.get('personal', [])
    if not isinstance(personal, list):
        raise ValueError('Personal accounts must be a list')
    seen = set()
    for account in personal:
        if (not isinstance(account, dict) or set(account) != {'host', 'id'}
                or not isinstance(account['host'], str)
                or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', account['host'])
                or type(account['id']) is not int or account['id'] <= 0):
            raise ValueError('Personal accounts require host and positive numeric id')
        account['host'] = account['host'].lower()
        key = (account['host'], account['id'])
        if key in seen:
            raise ValueError('Personal accounts must be unique per host/account')
        seen.add(key)
    defaults = result.get('skill_settings', {})
    if not isinstance(defaults, dict):
        raise ValueError('skill_settings must be a skill-to-settings object')
    for skill, value in defaults.items():
        invocation.skill_name(skill)
        invocation.validate_settings(value)
    processes = result.get('processes', {})
    if not isinstance(processes, dict):
        raise ValueError('Processes must be an object')
    for name, process in processes.items():
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
            raise ValueError('Invalid process name')
        if not isinstance(process, dict) or set(process) - {'run', 'verification', 'operations', 'finalizers', 'limits', 'actions', 'hooks', 'routes'}:
            raise ValueError('Unknown process setting')
        if 'run' in process:
            invocation.validate(process['run'])
        if 'verification' in process:
            invocation.validate(process['verification'])
        if 'finalizers' in process:
            finalizer_groups(process['finalizers'])
        if not isinstance(process.get('operations', []), list):
            raise ValueError('Process operations must be an invocation list')
        for call in process.get('operations', []):
            invocation.validate(call)
        limits = process.get('limits', {})
        if not isinstance(limits, dict) or set(limits) - LIMITS.keys() or any(
                type(value) is not int or value < 1 or value > LIMITS[key] for key, value in limits.items()):
            raise ValueError('Process limits must be positive integers within the retained runtime ceilings')
        actions = process.get('actions', {})
        if (not isinstance(actions, dict) or set(actions) - (set(ACTIONS) | {'merge'})
                or any(type(value) is not bool for key, value in actions.items() if key != 'merge')):
            raise ValueError('Process actions must be known boolean permissions')
        merging = actions.get('merge', {})
        if (not isinstance(merging, dict) or set(merging) - {'enabled', 'method'}
                or type(merging.get('enabled', False)) is not bool
                or merging.get('method', 'squash') not in {'squash', 'merge'}):
            raise ValueError('Merge policy accepts enabled and squash or merge method')
        hooks = process.get('hooks', {})
        if not isinstance(hooks, dict) or set(hooks) - set(CYCLE_PHASES):
            raise ValueError('Hooks accept before_cycle and after_cycle lists')
        for entries in hooks.values():
            if not isinstance(entries, list):
                raise ValueError('Hooks must be ordered invocation lists')
            for call in entries:
                invocation.validate(call)
        routes = process.get('routes', {})
        if not isinstance(routes, dict) or set(routes) - {'own_pr', 'other_pr'} or any(
                not isinstance(route, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', route)
                for route in routes.values()):
            raise ValueError('Routes must name configured processes')
    if not isinstance(result.get('extensions', {}), dict):
        raise ValueError('Extensions must be an object')
    return result


def path(repository=None):
    base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
    if not base.is_absolute():
        raise ValueError('XDG_CONFIG_HOME must be absolute')
    return base/'dk-workflows/v2'/((digest(repository.lower()) if repository else 'user') + '.json')


def effective(repository, upstream=None, *, expanded=True):
    result, sources = deepcopy(DEFAULTS) if expanded else {}, []
    for repo in [None] + ([upstream] if upstream else []) + [repository]:
        source = path(repo)
        if source.exists():
            settings = validate(read(source))
            result = invocation.merge(result, {key: value for key, value in settings.items() if key != '$schema'})
            sources.append(str(source))
    if expanded:
        for name, process in result['processes'].items():
            if 'run' not in process:
                raise ValueError('Custom process requires a run invocation: ' + name)
            for route in process.get('routes', {}).values():
                if route not in result['processes']:
                    raise ValueError('Route selects an unknown process: ' + route)
    return {'settings': result, 'sources': sources}


def calls(settings, process=None, available=None):
    def visit(value, location, ancestors=()):
        raw = invocation.normalize(value)
        reference = raw.get('skill')
        candidates = [name for name in (available or []) if reference and invocation.matches(reference, name)]
        identity = candidates[0] if len(candidates) == 1 else None
        call = invocation.configured(raw, settings['skill_settings'], identity=identity)
        yield location, call
        reviewers = call.get('options', {}).get('reviewers', [])
        signature = digest(call)
        if reviewers and signature in ancestors:
            raise ValueError('Recursive reviewer selection: ' + str(reference))
        for index, reviewer in enumerate(reviewers):
            yield from visit(reviewer, location + '/options/reviewers/' + str(index), (*ancestors, signature))
    for name, entry in settings['processes'].items():
        if process is not None and name != process:
            continue
        prefix = '/processes/' + name
        yield from visit(entry['run'], prefix + '/run')
        if 'verification' in entry:
            yield from visit(entry['verification'], prefix + '/verification')
        for index, call in enumerate(entry.get('operations', [])):
            yield from visit(call, prefix + '/operations/' + str(index))
        for group_index, group in enumerate(finalizer_groups(entry.get('finalizers', []))):
            for index, call in enumerate(group):
                yield from visit(call, prefix + '/finalizers/' + str(group_index) + '/' + str(index))
        for phase, hooks in entry.get('hooks', {}).items():
            for index, call in enumerate(hooks):
                yield from visit(call, prefix + '/hooks/' + phase + '/' + str(index))


def preflight(settings, available, capabilities=None, *, process=None):
    if process is not None and process not in settings['processes']:
        raise ValueError('Unknown process')
    if not isinstance(available, list) or any(not isinstance(item, str) for item in available):
        raise ValueError('Available skill identities must be a list of strings')
    return invocation.preflight(list(calls(settings, process, available)), available, capabilities)


def reviewers(settings, process):
    entry = settings['processes'][process]
    coordinators = [value for value in [entry['run'], *entry.get('operations', [])]
                    if (invocation.normalize(value).get('skill', '').rsplit(':', 1)[-1] == 'dk-pr-reviewers-run'
                        or 'reviewers' in invocation.configured(value, settings['skill_settings']).get('options', {}))]
    if len(coordinators) != 1:
        raise ValueError('Reviewer selection requires one configured review coordinator')
    return invocation.configured(coordinators[0], settings['skill_settings']).get('options', {}).get('reviewers', [])


def select(settings, process, kind='run', index=None, phase=None):
    if process not in settings['processes']:
        raise ValueError('Unknown process')
    entry = settings['processes'][process]
    if kind in {'run', 'verification'}:
        if kind not in entry:
            raise ValueError('No configured invocation for this selection')
        value = entry[kind]
    else:
        if type(index) is not int or index < 0:
            raise ValueError('Invocation selection requires a nonnegative index')
        if kind == 'operation':
            values = entry.get('operations', [])
        elif kind == 'finalizer':
            values = [value for group in finalizer_groups(entry.get('finalizers', [])) for value in group]
        elif kind == 'hook' and phase in CYCLE_PHASES:
            values = entry.get('hooks', {}).get(phase, [])
        elif kind == 'reviewer':
            values = reviewers(settings, process)
        else:
            raise ValueError('Unknown invocation selection')
        if index >= len(values):
            raise ValueError('No configured invocation at this index')
        value = values[index]
    return invocation.configured(value, settings['skill_settings'])


def make_invocation_spec(*, repository, pr, process, definition, kind='run', index=None,
                         phase=None, upstream=None):
    """Bind the common invocation to explicit native resources without executing it."""
    loaded = effective(repository, upstream)
    call = select(loaded['settings'], process, kind, index, phase)
    if not isinstance(definition, dict):
        raise ValueError('Invocation definition must be an object')
    runner = definition.get('skill_identity')
    if call.get('skill') and (not isinstance(runner, str) or not invocation.matches(call['skill'], runner)):
        raise ValueError('Resolve and supply the configured invocation skill identity')
    if not call.get('skill'):
        owner = invocation.normalize(loaded['settings']['processes'][process]['run']).get('skill')
        if not isinstance(runner, str) or (owner and not invocation.matches(owner, runner)):
            raise ValueError('Instruction-only invocations use the owning process identity')
    call = invocation.configured(call, loaded['settings']['skill_settings'], identity=runner)
    execution = deepcopy(definition)
    instructions = execution.get('instructions', {})
    if not isinstance(instructions, dict):
        raise ValueError('Instructions must be a label-to-resource-path map')
    instructions = dict(instructions)
    resources = execution.get('resources', {})
    if not isinstance(resources, dict):
        raise ValueError('Resources must be a label-to-resource-path map')
    resources = dict(resources)
    for label, resource in {
            'invocation_contract': Path(__file__).with_name('invocation.py'),
            'invocation_schema': Path(__file__).parents[1]/'schemas/invocation.schema.json',
            'configuration_contract': Path(__file__),
            'configuration_schema': Path(__file__).parents[1]/'schemas/config.schema.json'}.items():
        if label in resources:
            raise ValueError('Reserved invocation resource label')
        resources[label] = str(resource)
    if not isinstance(execution.get('options', {}), dict):
        raise ValueError('Invocation execution options must be an object')
    execution.update(instructions=instructions, resources=resources,
        options={'invocation': call, 'configured': call.get('options', {}),
                 'process_limits': loaded['settings']['processes'][process].get('limits', dict(LIMITS)),
                 'execution': execution.get('options', {})})
    if 'instructions' in call:
        execution['assigned_instructions'] = call['instructions']
    spec = make_spec(repository=repository, pr=pr, **execution)
    spec['configuration'] = {
        'sources': [str(path(repo)) for repo in [None] + ([upstream] if upstream else []) + [repository]],
        'process': process, 'kind': kind, 'index': index, 'phase': phase}
    if kind not in {'reviewer', 'verification'}:
        spec['options']['process_actions'] = loaded['settings']['processes'][process].get('actions', {})
    return spec


def check_selection(spec):
    """Check only explicit live configuration inputs, preserving unrelated reuse."""
    selection = spec.get('configuration')
    if selection is None:
        return
    if (not isinstance(selection, dict)
            or set(selection) != {'sources', 'process', 'kind', 'index', 'phase'}
            or not isinstance(selection['sources'], list)
            or any(not isinstance(source, str) or not Path(source).is_absolute() for source in selection['sources'])):
        raise ValueError('Malformed explicit configuration selection')
    settings = deepcopy(DEFAULTS)
    for source in selection['sources']:
        if Path(source).exists():
            value = validate(read(source))
            settings = invocation.merge(settings, {key: item for key, item in value.items() if key != '$schema'})
    try:
        call = select(settings, selection['process'], selection['kind'], selection['index'], selection['phase'])
        call = invocation.configured(call, settings['skill_settings'], identity=spec['skill']['identity'])
        process = settings['processes'][selection['process']]
    except (ValueError, KeyError) as exc:
        raise ValueError('Inputs changed: configured invocation no longer resolves') from exc
    options = spec['options']
    changed = (call != options.get('invocation')
               or process.get('limits', dict(LIMITS)) != options.get('process_limits'))
    if 'process_actions' in options:
        changed = changed or process.get('actions', {}) != options['process_actions']
    if 'hook_phase' in options:
        occurrence = options['hook_phase']['occurrence']
        changed = changed or hook_phase(settings, selection['process'], occurrence['run'],
                                       occurrence['cycle'], selection['phase']) != options['hook_phase']
    if 'finalization' in options:
        changed = changed or process.get('finalizers', []) != options['finalization']['plan']
    if changed:
        raise ValueError('Inputs changed: configured invocation, policy or hook list changed')


def make_finalizer_spec(*, repository, pr, run, definition, finalizer=None, index=None,
                        process='pr-fix', upstream=None):
    if not invocation.text(run):
        raise ValueError('Finalizer specs require the retained run id')
    if not isinstance(definition, dict):
        raise ValueError('Invocation definition must be an object')
    loaded = effective(repository, upstream)
    plan = loaded['settings']['processes'][process].get('finalizers', [])
    flat = [value for group in finalizer_groups(plan) for value in group]
    if finalizer is not None:
        matches = [position for position, value in enumerate(flat)
                   if invocation.normalize(value).get('skill') == finalizer]
        if len(matches) != 1:
            raise ValueError('No configured finalizer with this reference')
        index = matches[0]
    call = select(loaded['settings'], process, 'finalizer', index)
    runner = definition.get('skill_identity') if isinstance(definition, dict) else None
    occurrence = {'run': run, 'skill': runner or call.get('skill') or digest(call)}
    if not call.get('skill'):
        occurrence['index'] = index
    spec = make_invocation_spec(repository=repository, pr=pr, process=process, kind='finalizer',
        index=index, upstream=upstream, definition={**definition, 'responsibility': 'finalizer:' + digest(occurrence)})
    spec['options']['finalization'] = {**occurrence, 'plan': deepcopy(plan)}
    return spec


def hook_phase(settings, process, run, cycle, phase):
    if phase not in CYCLE_PHASES or not invocation.text(run) or not invocation.text(cycle):
        raise ValueError('Hook phase requires retained run and cycle ids')
    if process not in settings['processes']:
        raise ValueError('Unknown process')
    entries = settings['processes'][process].get('hooks', {}).get(phase, [])
    return {'occurrence': {'process': process, 'run': run, 'cycle': cycle, 'phase': phase},
            'hooks': [select(settings, process, 'hook', position, phase) for position in range(len(entries))]}


def make_hook_spec(*, repository, pr, run, cycle, phase, index, definition,
                   process='pr-fix', upstream=None):
    if phase not in CYCLE_PHASES or not invocation.text(run) or not invocation.text(cycle):
        raise ValueError('Hook specs require a phase and retained run and cycle ids')
    if not isinstance(definition, dict) or set(definition) - {
            'skill_identity', 'resources', 'instructions', 'inputs', 'options', 'git',
            'governing_inputs_complete', 'cacheable', 'native_review'}:
        raise ValueError('Hook definitions supply execution resources and inputs')
    loaded = effective(repository, upstream)
    call = select(loaded['settings'], process, 'hook', index, phase)
    occurrence = {'process': process, 'run': run, 'cycle': cycle, 'phase': phase, 'index': index}
    spec = make_invocation_spec(
        repository=repository, pr=pr, process=process, kind='hook', index=index, phase=phase,
        upstream=upstream, definition={**definition, 'responsibility': 'cycle-hook:' + digest(occurrence),
                                     'assigned_instructions': call.get('instructions', 'Execute the configured hook skill and return original evidence and explicit completion.')})
    spec['options']['cycle_hook'] = occurrence
    spec['options']['hook_phase'] = hook_phase(loaded['settings'], process, run, cycle, phase)
    return spec
