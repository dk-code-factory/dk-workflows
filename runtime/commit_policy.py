"""Commit policy settings and captured native-provider handoffs; no execution."""
import hashlib
import re
from pathlib import Path

import invocation

from shared.ci_exceptions import data_root
from shared.private_state import read
from state import capture, make_spec


def validate(value):
    if isinstance(value, dict) and 'prompt' in value:
        if set(value) != {'prompt'}:
            raise ValueError('A literal commit policy accepts only prompt')
        if (not isinstance(value['prompt'], str) or not value['prompt'].strip()
                or '\x00' in value['prompt']):
            raise ValueError('Commit policy prompt must be nonempty text without NUL bytes')
        return dict(value)
    return invocation.validate(value)


def provider(value):
    return value is not None and not (isinstance(value, dict) and 'prompt' in value)


def selected_call(value):
    return invocation.normalize(value) if provider(value) else {'skill': 'dk-workflows:dk-commit-split'}


def path(key):
    folder = data_root() if key == 'user' else data_root()/hashlib.sha256(key.encode()).hexdigest()[:24]
    return folder/'commit-policy.json'


def load(chain, scope='effective'):
    if scope not in {'effective', 'repo', 'user'}:
        raise ValueError('Commit policy scope must be effective, repo or user')
    levels = [*chain, 'user'] if scope == 'effective' else ['user' if scope == 'user' else chain[0]]
    for key in levels:
        source = path(key)
        if source.exists():
            return {'value': validate(read(source, None)), 'source': key, 'file': str(source)}
    return {'value': None, 'source': 'default'}


def policy_spec(*, chain, repository, pr, definition):
    """Capture complete caller-supplied context; the host resolves native skills."""
    selected = load(chain)
    if not isinstance(definition, dict) or set(definition) - {
            'skill_identity', 'resources', 'assigned_instructions', 'instructions',
            'inputs', 'options', 'git', 'governing_inputs_complete', 'cacheable', 'native_review'}:
        raise ValueError('Policy definitions require resolved resources and consumed context')
    call = selected_call(selected['value'])
    expected = call.get('skill', 'dk-workflows:dk-commit-split')
    if not invocation.matches(expected, definition.get('skill_identity', '')):
        raise ValueError('Resolve and supply the selected policy skill identity')
    if not definition.get('resources') or definition.get('governing_inputs_complete') is not True:
        raise ValueError('Policy handoffs require resolved resources and complete governing inputs')
    git = definition.get('git', {})
    if not isinstance(git, dict) or git.get('history') is not True or git.get('head_sensitive') is not True:
        raise ValueError('Policy context requires pinned Git history and head_sensitive:true')
    if any(not isinstance(git.get(key), str) or not re.fullmatch(r'(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})', git[key])
           for key in ('target', 'base', 'head')):
        raise ValueError('Policy Git target, base and head must be immutable full SHAs')
    instructions = definition.get('instructions', {})
    options = definition.get('options', {})
    if not isinstance(instructions, dict) or not isinstance(options, dict):
        raise ValueError('Policy instructions and options must be objects')
    if 'commit_policy_configuration' in instructions:
        raise ValueError('Reserved commit policy configuration input label')
    instructions = dict(instructions)
    if 'file' in selected:
        instructions['commit_policy_configuration'] = selected['file']
    execution = dict(definition)
    resources = dict(definition['resources'])
    for label, resource in {
            'invocation_contract': Path(__file__).with_name('invocation.py'),
            'invocation_schema': Path(__file__).parents[1]/'schemas/invocation.schema.json'}.items():
        if label in resources:
            raise ValueError('Reserved invocation resource label')
        resources[label] = str(resource)
    if 'instructions' in call:
        execution['assigned_instructions'] = call['instructions']
    execution.update(responsibility='commit-policy', instructions=instructions,
                     resources=resources,
                     options={'commit_policy': selected, 'execution': options,
                              'invocation': call, 'configured': call.get('options', {})})
    spec = make_spec(repository=repository, pr=pr, **execution)
    spec['policy_fingerprint'] = capture(spec)['fingerprint']
    return spec


def handoff(*, chain, spec, report=None):
    """Check provider completion and live sources before returning instructions."""
    if not isinstance(spec, dict) or any(not isinstance(spec.get(key), dict)
                                        for key in ('task', 'options', 'skill')):
        raise ValueError('Malformed commit policy spec')
    selected = load(chain)
    if (spec.get('task', {}).get('id') != 'commit-policy'
            or spec.get('options', {}).get('commit_policy') != selected):
        raise ValueError('Commit policy configuration changed; refresh the policy spec')
    policy = selected['value']
    expected = selected_call(policy).get('skill', 'dk-workflows:dk-commit-split')
    if not invocation.matches(expected, spec.get('skill', {}).get('identity', '')):
        raise ValueError('Policy spec does not identify the selected provider')
    if provider(policy):
        if (not isinstance(report, dict) or report.get('complete') is not True
                or not isinstance(report.get('findings'), list)
                or not isinstance(report.get('blockers', []), list) or report.get('blockers')
                or not isinstance(report.get('policy_prompt'), str)
                or not report['policy_prompt'].strip() or '\x00' in report['policy_prompt']):
            raise ValueError('Policy provider requires a completed report with nonempty policy_prompt and no blockers')
        prompt = report['policy_prompt']
    else:
        if report is not None:
            raise ValueError('Prompt and default policies do not consume provider reports')
        prompt = (policy or {}).get('prompt')
    captured = capture(spec)
    if captured['fingerprint'] != spec.get('policy_fingerprint'):
        raise ValueError('Policy inputs changed during execution; refresh the policy spec')
    return {'schema_version': 1, 'configuration': selected, 'spec': spec,
            'fingerprint': captured['fingerprint'], 'policy_prompt': prompt, 'report': report}


def verify(*, chain, repository, pr, definition, supplied):
    """Compare a supplied handoff with freshly resolved policy and consumed inputs."""
    if (not isinstance(supplied, dict) or type(supplied.get('schema_version')) is not int
            or supplied['schema_version'] != 1):
        raise ValueError('Malformed commit policy handoff')
    spec = policy_spec(chain=chain, repository=repository, pr=pr, definition=definition)
    current = handoff(chain=chain, spec=spec, report=supplied.get('report'))
    if current != supplied:
        raise ValueError('Commit policy handoff is stale; refresh affected policy and cleanup evidence')
    return current
