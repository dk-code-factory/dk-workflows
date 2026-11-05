"""Workflow settings managed alongside PR policies by the dk-config skill."""
from pathlib import Path
import os
import re
from state import digest, make_spec, read

CYCLE_PHASES = ('before_cycle', 'after_cycle')
FINALIZER_ROLES = ('cleanup', 'decisions', 'description', 'reply', 'ci', 'publish')


def finalizer_groups(finalizers, *, role_skills=()):
    """Validate groups, preserving supplied role identities in derived defaults."""
    if not isinstance(finalizers, list):
        raise ValueError('Finalizers must be an ordered list')
    role_references = {'/skill/' + skill for skill in role_skills}
    groups, seen = [], set()
    for entry in finalizers:
        group = entry if isinstance(entry, list) else [entry]
        if not group:
            raise ValueError('Parallel finalizer groups must not be empty')
        for reference in group:
            if (not isinstance(reference, str) or (reference not in role_references and not re.fullmatch(
                    r'/skill/[A-Za-z0-9][A-Za-z0-9_.-]*:[A-Za-z0-9][A-Za-z0-9_.-]*', reference))):
                raise ValueError('Finalizers require /skill/PLUGIN:SKILL strings; groups cannot nest')
            if reference in seen:
                raise ValueError('Finalizer skills must be unique across the entire list')
            seen.add(reference)
        groups.append(list(group))
    return groups

DEFAULTS = {
    'reviewers': [],
    'skills': {
        'feedback': 'dk-workflows:dk-pr-feedback-address',
        'decisions': 'dk-workflows:dk-pr-decisions',
        'description': 'dk-workflows:dk-pr-description',
        'reply': 'dk-workflows:dk-pr-feedback-reply',
        'partition': 'dk-workflows:dk-pr-split',
        'cleanup': 'dk-workflows:dk-commit-split',
        'ci': 'dk-workflows:dk-ci',
        'ci_approve': 'dk-workflows:dk-ci-approve',
        'flaky_tests': 'dk-workflows:dk-ci-flakes',
        'publish': 'dk-workflows:dk-pr-publish',
        'read_feedback': 'dk-workflows:dk-pr-feedback-read',
        'run_reviewers': 'dk-workflows:dk-pr-reviewers-run',
        'post_review': 'dk-workflows:dk-pr-review-post',
    },
    'iteration': {'max_cycles': 5, 'same_cycle_retries': 1, 'ci_repairs_per_cycle': 5,
                  'ci_reruns_per_head': 3},
    'domain_settings': {},
    'cycle_hooks': {phase: [] for phase in CYCLE_PHASES},
    'identities': [],
    'incoming_review': {'post': True, 'approve_ci': True, 'approve': False, 'merge': False,
                        'merge_method': 'squash'},
}
DEFAULTS['finalizers'] = ['/skill/' + DEFAULTS['skills'][role] for role in FINALIZER_ROLES]


def validate(settings):
    if not isinstance(settings, dict) or set(settings) - DEFAULTS.keys():
        raise ValueError('Unknown workflow setting')
    if not isinstance(settings.get('reviewers', []), list):
        raise ValueError('Reviewers must be a list')
    for key in ('skills', 'domain_settings'):
        if not isinstance(settings.get(key, {}), dict):
            raise ValueError(key + ' must be an object')
    import copy
    settings = copy.deepcopy(settings)
    if 'finalizers' in settings:
        finalizer_groups(settings['finalizers'])
    for reviewer in settings.get('reviewers', []):
        if (not isinstance(reviewer, dict) or set(reviewer) - {'skill', 'required', 'options'}
                or not isinstance(reviewer.get('skill'), str) or not reviewer['skill']
                or type(reviewer.get('required', True)) is not bool
                or not isinstance(reviewer.get('options', {}), dict)):
            raise ValueError('Reviewer requires skill, optional required boolean and execution options')
    reviewers = settings.get('reviewers', [])
    if not isinstance(reviewers, list) or len({x['skill'] for x in reviewers}) != len(reviewers):
        raise ValueError('Reviewers must be a list of unique skills')
    for role, skill in settings.get('skills', {}).items():
        if role not in DEFAULTS['skills'] or not isinstance(skill, str) or not skill:
            raise ValueError('Unknown role or empty selected skill')
    if 'iteration' in settings and settings['iteration'] != DEFAULTS['iteration']:
        raise ValueError('Iteration limits are fixed')
    for owner, reference in settings.get('domain_settings', {}).items():
        if not isinstance(owner, str) or not isinstance(reference, str) or not reference:
            raise ValueError('Domain settings must be owner-to-reference strings')
    hooks = settings.get('cycle_hooks', {})
    if not isinstance(hooks, dict) or set(hooks) - set(CYCLE_PHASES):
        raise ValueError('Cycle hooks require before_cycle or after_cycle lists')
    for phase, entries in hooks.items():
        if not isinstance(entries, list):
            raise ValueError(phase + ' must be an ordered list')
        ids = set()
        for hook in entries:
            if (not isinstance(hook, dict)
                    or set(hook) - {'id', 'instructions', 'skill', 'options'}
                    or not isinstance(hook.get('id'), str)
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', hook['id'])
                    or hook['id'] in ids
                    or not isinstance(hook.get('instructions'), str)
                    or not hook['instructions'].strip() or '\x00' in hook['instructions']
                    or ('skill' in hook and (not isinstance(hook['skill'], str)
                                            or not hook['skill'].strip()))
                    or not isinstance(hook.get('options', {}), dict)):
                raise ValueError('Cycle hooks require unique ids, original instructions, '
                                 'an optional skill and object options')
            ids.add(hook['id'])
    identities = settings.get('identities', [])
    if (not isinstance(identities, list) or any(
            not isinstance(identity, dict) or set(identity) != {'host', 'id'}
            or not isinstance(identity['host'], str)
            or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', identity['host'])
            or type(identity['id']) is not int or identity['id'] <= 0
            for identity in identities)):
        raise ValueError('Personal identities require a GitHub host and positive numeric account id')
    for identity in identities:
        identity['host'] = identity['host'].lower()
    if len({(v['host'], v['id']) for v in identities}) != len(identities):
        raise ValueError('Personal identities must be unique per host/account')
    policy = settings.get('incoming_review', {})
    if (not isinstance(policy, dict) or set(policy) - DEFAULTS['incoming_review'].keys()
            or any(type(value) is not bool for key, value in policy.items() if key != 'merge_method')
            or policy.get('merge_method', 'squash') not in {'squash', 'merge'}):
        raise ValueError('Incoming-review policy requires boolean actions and squash or merge method')
    return settings


def path(repository=None):
    base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
    if not base.is_absolute():
        raise ValueError('XDG_CONFIG_HOME must be absolute')
    return base/'dk-workflows/v2'/((digest(repository.lower()) if repository else 'user') + '.json')


def effective(repository, upstream=None):
    import copy
    result = copy.deepcopy(DEFAULTS)
    sources = []
    explicit_finalizers = False
    chain = [None] + ([upstream] if upstream else []) + [repository]
    for repo in chain:
        source = path(repo)
        if not source.exists():
            continue
        settings = validate(read(source))
        explicit_finalizers |= 'finalizers' in settings
        for key, value in settings.items():
            if key in {'skills', 'domain_settings', 'incoming_review', 'cycle_hooks'}:
                result[key].update(value)
            else:
                result[key] = value
        sources.append(str(source))
    if not explicit_finalizers:
        # Existing role overrides continue to select the default ending. A
        # shared implementation runs once with all of its assigned duties.
        result['finalizers'] = list(dict.fromkeys(
            '/skill/' + result['skills'][role] for role in FINALIZER_ROLES))
    finalizer_groups(result['finalizers'], role_skills=result['skills'].values())
    return {'settings': result, 'sources': sources}


def preflight(settings, available, *, workflow='fix'):
    """The agent supplies native resolved identities; no host registry inspection."""
    if not isinstance(available, list) or any(not isinstance(x, str) for x in available):
        raise ValueError('Available skill identities must be a list of strings')
    if workflow not in {'fix', 'review'}:
        raise ValueError('Preflight workflow must be fix or review')
    selected = set(settings['skills'].values()) | {r['skill'] for r in settings['reviewers']}
    selected.update(hook['skill'] for entries in settings.get('cycle_hooks', {}).values()
                    for hook in entries if 'skill' in hook)
    if workflow == 'fix':
        selected.update(reference.removeprefix('/skill/')
                        for group in finalizer_groups(settings.get('finalizers', DEFAULTS['finalizers']),
                                                      role_skills=settings['skills'].values())
                        for reference in group)
    missing = sorted(selected - set(available))
    return {'ready': not missing, 'missing_selected_skills': missing}


def make_finalizer_spec(*, repository, pr, run, finalizer, definition, upstream=None):
    """Capture one configured finalizer; native agents execute the plan."""
    if not isinstance(run, str) or not run.strip():
        raise ValueError('Finalizer specs require the retained run id')
    loaded = effective(repository, upstream)
    groups = finalizer_groups(loaded['settings']['finalizers'],
                             role_skills=loaded['settings']['skills'].values())
    if not any(finalizer in group for group in groups):
        raise ValueError('No configured finalizer with this reference')
    skill = finalizer.removeprefix('/skill/')
    if (not isinstance(definition, dict) or set(definition) - {
            'skill_identity', 'resources', 'assigned_instructions', 'instructions', 'inputs',
            'options', 'git', 'governing_inputs_complete', 'cacheable'}):
        raise ValueError('Finalizer definitions supply resolved resources, assignment and execution inputs')
    if definition.get('skill_identity') != skill:
        raise ValueError('Resolve and supply the configured finalizer skill identity')
    if not isinstance(definition.get('options', {}), dict):
        raise ValueError('Finalizer execution options must be an object')
    instructions = definition.get('instructions', {})
    if not isinstance(instructions, dict):
        raise ValueError('Finalizer instructions must be a label-to-resource-path map')
    instructions = dict(instructions)
    for index, source in enumerate(loaded['sources']):
        label = 'finalizer_configuration_' + str(index)
        if label in instructions:
            raise ValueError('Reserved finalizer configuration input label')
        instructions[label] = source
    # Identity follows the skill across reorderings. Plan/context changes stale
    # its evidence without hiding an unfinished claim behind a new task id.
    occurrence = {'run': run, 'skill': skill}
    execution = dict(definition)
    execution.update(
        responsibility='finalizer:' + digest(occurrence), instructions=instructions,
        options={'finalization': {**occurrence, 'plan': loaded['settings']['finalizers']},
                 'execution': definition.get('options', {})})
    return make_spec(repository=repository, pr=pr, **execution)


def make_hook_spec(*, repository, pr, run, cycle, phase, hook_id, definition, upstream=None):
    """Define one native hook occurrence; the caller claims and invokes it normally."""
    if (phase not in CYCLE_PHASES or not isinstance(run, str) or not run.strip()
            or not isinstance(cycle, str) or not cycle.strip()):
        raise ValueError('Hook specs require a phase and retained run and cycle ids')
    loaded = effective(repository, upstream)
    hook = next((entry for entry in loaded['settings']['cycle_hooks'][phase]
                 if entry['id'] == hook_id), None)
    if hook is None:
        raise ValueError('No configured hook for this phase and id')
    if not isinstance(definition, dict) or set(definition) - {
            'skill_identity', 'resources', 'instructions', 'inputs', 'options', 'git',
            'governing_inputs_complete', 'cacheable'}:
        raise ValueError('Hook definitions supply execution resources and inputs; '
                         'identity and instructions come from the configured occurrence')
    runner = definition.get('skill_identity')
    if hook.get('skill'):
        if runner != hook['skill']:
            raise ValueError('Resolve and supply the configured hook skill identity')
    elif runner not in {'dk-workflows:dk-pr-fix', 'dk-workflows:dk-pr-review'}:
        raise ValueError('Instruction hooks run in the owning PR workflow agent')
    if not isinstance(definition.get('options', {}), dict):
        raise ValueError('Hook execution options must be an object')
    instructions = definition.get('instructions', {})
    if not isinstance(instructions, dict):
        raise ValueError('Hook instructions must be a label-to-resource-path map')
    instructions = dict(instructions)
    for index, source in enumerate(loaded['sources']):
        label = 'cycle_hook_configuration_' + str(index)
        if label in instructions:
            raise ValueError('Reserved hook configuration input label')
        instructions[label] = source
    occurrence = {'run': run, 'cycle': cycle, 'phase': phase, 'id': hook_id}
    execution = dict(definition)
    execution.update(
        responsibility='cycle-hook:' + digest(occurrence),
        assigned_instructions=hook['instructions'], instructions=instructions,
        options={'cycle_hook': occurrence, 'configured': hook.get('options', {}),
                 'execution': definition.get('options', {})})
    return make_spec(repository=repository, pr=pr, **execution)
