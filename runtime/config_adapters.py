"""Typed configuration projections. Destinations and locks are never caller paths."""
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path

import config
import commit_policy
import invocation
import instruction_sources
import preferences
import settings
import skill_config
from shared import ci_exceptions, pr_partition_policy, pr_size
from shared.repository_scope import identities, repository

DOMAINS = {'workflow', 'skill', 'preferences', *settings.DOMAINS,
           'pr-size', 'ci-exceptions', 'pr-partition', 'instruction-sources'}


def normalize(operation):
    if not isinstance(operation, dict) or operation.get('domain') not in DOMAINS:
        raise ValueError('Unsupported configuration domain')
    op = deepcopy(operation)
    domain = op['domain']
    allowed = {'domain', 'action', 'scope'}
    allowed |= {'workflow': {'value'}, 'skill': {'skill', 'value'},
                'preferences': {'proposal'}, 'pr-size': {'value', 'keys'},
                'ci-exceptions': {'value', 'id'},
                'pr-partition': {'reason', 'source', 'instructions'}}.get(domain, {'value'})
    if set(op) - allowed:
        raise ValueError('Unknown operation fields; paths and commands are forbidden')
    actions = {'preferences': {'commit'}, 'ci-exceptions': {'add', 'remove', 'reset'},
               'pr-partition': set(pr_partition_policy.ACTIONS)}.get(domain, {'set', 'reset'})
    if op.get('action') not in actions:
        raise ValueError('Unsupported configuration action')
    scope = op.setdefault('scope', 'catalog' if domain == 'preferences' else 'pr' if domain == 'pr-partition' else 'repo')
    if scope not in ({'catalog'} if domain == 'preferences' else {'pr'} if domain == 'pr-partition' else {'user', 'repo'}):
        raise ValueError('Mutations require an owned user, repository or PR scope')
    if domain == 'instruction-sources' and scope != 'repo':
        raise ValueError('Instruction choices are repository-only')
    required = {'commit': 'proposal', 'set': 'value', 'add': 'value', 'remove': 'id'}.get(op['action'])
    if required and required not in op:
        raise ValueError('Missing operation ' + required)
    if op['action'] == 'reset' and 'value' in op:
        raise ValueError('Reset accepts no value')
    if domain == 'preferences':
        proposal = op['proposal']
        if not isinstance(proposal, dict) or set(proposal) - {'repository', 'revision', 'changes', 'context', 'reused'}:
            raise ValueError('Expected prepared preference proposal')
        proposal.pop('context', None)  # Live admission context is not another snapshot.
        proposal.setdefault('reused', [])
    if domain == 'skill':
        if not isinstance(op.get('skill'), str) or not op['skill'].startswith('dk-workflows:') or not skill_config.directory(op['skill']):
            raise ValueError('Only installed dk-workflows skill options are owned here')
    if domain == 'workflow' and op['action'] == 'set':
        op['value'] = config.validate(op['value'])
    if domain == 'instruction-sources' and op['action'] == 'set':
        op['value'] = instruction_sources.validate(op['value'])
    if domain == 'pr-size' and op['action'] == 'reset':
        op.setdefault('keys', [])
    if domain == 'pr-partition':
        op['source'] = op.get('source') or op.get('reason')
    return op


_CHAIN = ContextVar('configuration_scope_chain', default=None)


def chain(context):
    cached = _CHAIN.get()
    return cached if cached is not None else identities(context['checkout'], context.get('pr'))


@contextmanager
def resolved(context, operations):
    needed = any(op['domain'] in {*settings.DOMAINS, 'pr-size'} for op in operations)
    token = _CHAIN.set(identities(context['checkout'], context.get('pr')) if needed else None)
    try:
        yield
    finally:
        _CHAIN.reset(token)


def lock_paths(context, operations):
    paths = {destination(context, op)[1] for op in operations}
    paths.update(config.path(key).with_suffix('.lock') for key in [None, context.get('upstream'), context['repository']])
    for op in operations:
        domain = op['domain']
        if domain in {*settings.DOMAINS, 'pr-size', 'ci-exceptions'}:
            for key in [*policy_chain(context, domain), 'user']:
                target = (pr_size.path(key) if domain == 'pr-size' else
                          ci_exceptions.config_path(key, 'user' if key == 'user' else 'repo') if domain == 'ci-exceptions' else
                          settings.domain_path(domain, key))
                paths.add(target.with_name(target.name + '.lock') if domain == 'ci-exceptions' else target.with_suffix('.lock'))
    return paths


def destination(context, op):
    domain, scope = op['domain'], op['scope']
    if domain in {'workflow', 'skill'}:
        target = config.path(context['repository'] if scope == 'repo' else None)
    elif domain == 'instruction-sources':
        target = instruction_sources.policy_path(context['repository'])
    elif domain == 'preferences':
        target = preferences.catalog_path(context['checkout'])
    elif domain == 'pr-partition':
        target = pr_partition_policy.path(context.get('pr'))
    else:
        identity = owner(context, op)
        target = (pr_size.path(identity) if domain == 'pr-size' else
                  ci_exceptions.config_path(identity, scope) if domain == 'ci-exceptions' else
                  settings.domain_path(domain, identity))
    lock = target.with_name(target.name + '.lock') if domain in {'ci-exceptions', 'pr-partition'} else target.with_suffix('.lock')
    return target, lock


def owner(context, op):
    if op['scope'] == 'user':
        return 'user'
    if op['domain'] == 'ci-exceptions' and context.get('pr'):
        canonical, _ = pr_partition_policy.identity(context['pr'])
        return 'remote:' + canonical.split('https://', 1)[1].split('/pull/')[0]
    return repository(context['checkout']) if op['domain'] == 'ci-exceptions' else chain(context)[0]


def policy_chain(context, domain):
    return [owner(context, {'domain': domain, 'scope': 'repo'})] if domain == 'ci-exceptions' else chain(context)


def encode(value):
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n'


def project(context, op, raw):
    """Validate current evidence and return exact desired text (or deletion)."""
    domain, action = op['domain'], op['action']
    value = deepcopy(op.get('value'))
    if domain == 'instruction-sources':
        if action == 'set':
            instruction_sources.discover(context['checkout'], value)
        return encode(instruction_sources.envelope(context['repository'], value if action == 'set' else None))
    if domain == 'workflow':
        if action == 'set' and 'binding' in op:
            value = op['replacement'] if 'replacement' in op else _patched(raw, op['patch'])
        return encode(config.validate(value)) if action == 'set' else None
    if action == 'reset' and (domain in {*settings.DOMAINS, 'ci-exceptions'} or domain == 'pr-size' and not op.get('keys')):
        return None
    current = json.loads(raw) if raw is not None and domain not in settings.DOMAINS else None
    if domain in {'workflow', 'skill'}:
        saved = config.validate(current) if current is not None else {}
        if action == 'set':
            saved.setdefault('skill_defaults', {}).setdefault(op['skill'], {})['options'] = skill_config.validate(op['skill'], value)
        else:
            entry = saved.get('skill_defaults', {}).get(op['skill'], {})
            entry.pop('options', None)
            if not entry:
                saved.get('skill_defaults', {}).pop(op['skill'], None)
        return encode(config.validate(saved)) if raw is not None or action == 'set' else None
    if domain == 'preferences':
        proposal = op['proposal']
        identity = preferences.repository(context['checkout'])
        if (proposal.get('repository') != identity or type(proposal.get('revision')) is not int
                or proposal['revision'] < 0 or 'changes' not in proposal):
            raise ValueError('Commit requires prepared repository, revision and changes')
        reused = proposal.get('reused', [])
        if (not isinstance(reused, list) or any(not isinstance(item, dict) or set(item) != {'proposed_id', 'id'}
                or any(not isinstance(v, str) or not v for v in item.values()) for item in reused)):
            raise ValueError('Malformed duplicate reuse metadata')
        catalog = preferences._catalog(current) if current is not None else {'version': 1, 'revision': 0, 'records': []}
        if catalog['revision'] != proposal['revision']:
            raise ValueError('Stale catalog revision; repeat preference admission')
        records, _ = preferences._apply(catalog, proposal['changes'], identity)
        if records == sorted(catalog['records'], key=lambda r: r['id']):
            return raw
        return encode({**catalog, 'revision': catalog['revision'] + 1, 'records': records})
    if domain == 'pr-partition':
        pr = context.get('pr')
        saved = pr_partition_policy.validate(current, pr) if current is not None else pr_partition_policy.initial(pr)
        reason = pr_partition_policy.text(op.get('reason'), 'decision reason')
        source = pr_partition_policy.text(op.get('source') or reason, 'decision source')
        previous = saved['history'][-1] if saved['history'] else None
        policy = pr_partition_policy.transition(previous['policy'] if previous else pr_partition_policy.DEFAULT,
                                                action, op.get('instructions'))
        if previous and all(previous[k] == v for k, v in {'action': action, 'policy': policy, 'reason': reason, 'source': source}.items()):
            return raw
        entry = {'revision': len(saved['history']) + 1, 'action': action, 'policy': policy,
                 'reason': reason, 'source': source, 'previous': previous['id'] if previous else None}
        entry['id'] = ci_exceptions.fingerprint(entry)
        saved['history'].append(entry)
        return encode(pr_partition_policy.validate(saved, pr))
    if domain == 'ci-exceptions':
        identity = owner(context, op)
        saved = ci_exceptions.validate(current, identity) if current is not None else {'schema_version': 1, 'identity': identity, 'entries': []}
        if action == 'reset':
            return None
        if action == 'add':
            checked = ci_exceptions.entry(**value)
            saved['entries'] = [v for v in saved['entries'] if v['id'] != checked['id']] + [checked]
        else:
            if not any(v['id'] == op['id'] for v in saved['entries']):
                raise ValueError('Unknown CI exception ID in this scope')
            saved['entries'] = [v for v in saved['entries'] if v['id'] != op['id']]
        return encode(ci_exceptions.validate(saved, identity))
    if domain == 'pr-size':
        identity = owner(context, op)
        if current is not None and (set(current) != {'schema_version', 'identity', 'pr_size'}
                or type(current['schema_version']) is not int or current['schema_version'] != 1 or current['identity'] != identity):
            raise ValueError('Malformed PR size configuration')
        values = pr_size.validate(current['pr_size']) if current is not None else {}
        if action == 'set':
            if not value:
                raise ValueError('Set requires at least one setting')
            values.update(pr_size.validate(value))
        elif not op.get('keys'):
            return None
        else:
            if not isinstance(op['keys'], list) or set(op['keys']) - pr_size.DEFAULTS.keys():
                raise ValueError('Unknown PR size setting')
            for key in op['keys']:
                values.pop(key, None)
        return encode({'schema_version': 1, 'identity': identity, 'pr_size': values})
    if action == 'reset':
        return None
    if domain == 'completion':
        if not invocation.text(value):
            raise ValueError('Nonempty completion prompt text required')
        return value
    if domain == 'commit-policy':
        value = commit_policy.validate(value)
    elif not isinstance(value, dict) or not value:
        raise ValueError('Nonempty domain settings object required')
    if domain == 'drafting':
        for role, selection in value.items():
            if role == '$schema':
                if not invocation.text(selection):
                    raise ValueError('Nonempty $schema reference required')
            else:
                invocation.validate(selection)
    return encode(value)


def input_paths(context, operations):
    """References only; discover live inherited settings and governing instructions."""
    paths = {Path(__file__), Path(__file__).with_name('config_requests.py')}
    root = Path(__file__).resolve().parents[1]
    paths.update(root/name for name in ('DESIGN.md', 'AGENTS.md', 'docs/configuration-agent.md',
                 'docs/configuration-requests.md', 'docs/configuration.md', 'docs/preferences.md', 'docs/instruction-sources.md',
                 'docs/skill-configuration.md', 'docs/pr/configuration.md', 'docs/commit-policy.md',
                 'docs/invocations.md', 'docs/private-reporting.md', 'scripts/config.py', 'scripts/pr_config.py',
                 'scripts/preferences.py', 'scripts/config_requests.py'))
    paths.update(root/'runtime'/name for name in ('config.py', 'preferences.py', 'skill_config.py', 'settings.py', 'commit_policy.py', 'invocation.py'))
    paths.update((root/'runtime/shared').glob('*.py'))
    paths.update((root/'schemas').glob('*.json'))
    for name in ('dk-config', 'dk-preferences'):
        paths.add(root/'codex/skills'/name/'SKILL.md')
    for folder in (root/'codex/skills').iterdir():
        paths.update([folder/'config.json', folder/'config.schema.json'])
    paths.update(config.path(key) for key in [None, context.get('upstream'), context['repository']])
    checkout = instruction_sources.root(context['checkout'])
    selection = next((op for op in operations if op['domain'] == 'instruction-sources'), None)
    saved = (instruction_sources.envelope(context['repository'], selection.get('value')) if selection else
             instruction_sources.load(context['repository']))
    paths.add(instruction_sources.policy_path(context['repository']))
    paths.add(root/'runtime/instruction_sources.py')
    paths.add(root/'scripts/instruction_sources.py')
    if saved is None:
        paths.update(parent/'AGENTS.md' for parent in [checkout, *checkout.parents])
        paths.update(checkout.rglob('AGENTS.md'))
    else:
        paths.update(checkout/item['path'] for item in instruction_sources.discover(checkout, saved['policy'])
                     if item['selected'])
    if any(op['domain'] == 'preferences' for op in operations):
        paths.add(preferences.catalog_path(checkout))
    for op in operations:
        paths.add(destination(context, op)[0])
        domain = op['domain']
        if domain in {*settings.DOMAINS, 'pr-size', 'ci-exceptions'}:
            for key in [*policy_chain(context, domain), 'user']:
                paths.add(pr_size.path(key) if domain == 'pr-size' else
                          ci_exceptions.config_path(key, 'user' if key == 'user' else 'repo') if domain == 'ci-exceptions' else
                          settings.domain_path(domain, key))
    return paths


def validate_observed(context, op, raw):
    """Re-admit an applied value without replaying an old revision/append action."""
    if raw is None:
        return
    domain = op['domain']
    value = json.loads(raw) if domain != 'completion' else raw
    if domain == 'instruction-sources':
        instruction_sources.load(context['repository'])
    elif domain in {'workflow', 'skill'}:
        config.validate(value)
    elif domain == 'preferences':
        preferences._catalog(value)
    elif domain == 'pr-partition':
        pr_partition_policy.validate(value, context['pr'])
    elif domain == 'ci-exceptions':
        ci_exceptions.validate(value, owner(context, op))
    elif domain == 'pr-size':
        if (not isinstance(value, dict) or set(value) != {'schema_version', 'identity', 'pr_size'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['identity'] != owner(context, op)):
            raise ValueError('Malformed PR size configuration')
        pr_size.validate(value['pr_size'])
    else:
        project(context, {**op, 'action': 'set', 'value': value}, raw)


def _patch(before, after, prefix=()):
    """Sparse object edits; unchanged settings never enter request history."""
    result = []
    for key in sorted(before.keys() | after.keys()):
        path = [*prefix, key]
        if key not in after:
            result.append({'path': path, 'present': False})
        elif key not in before:
            result.append({'path': path, 'present': True, 'value': after[key]})
        elif isinstance(before[key], dict) and isinstance(after[key], dict):
            result.extend(_patch(before[key], after[key], path))
        elif before[key] != after[key]:
            result.append({'path': path, 'present': True, 'value': after[key]})
    return result


def _patched(raw, patch):
    saved = json.loads(raw) if raw is not None else {}
    for edit in patch:
        target = saved
        for key in edit['path'][:-1]:
            target = target[key]
        if edit['present']:
            target[edit['path'][-1]] = deepcopy(edit['value'])
        else:
            target.pop(edit['path'][-1])
    return saved


def compact(op, raw):
    if op['domain'] != 'workflow' or op['action'] != 'set':
        return deepcopy(op)
    from config_requests import fingerprint
    try:
        before = json.loads(raw) if raw is not None else {}
    except ValueError:
        before = None
    if not isinstance(before, dict):
        # Repairing a malformed document changes the entire requested value.
        patch = None
    else:
        patch = _patch(before, op['value'])
    return {**{k: v for k, v in op.items() if k != 'value'}, 'binding': fingerprint(op),
            **({'patch': patch} if patch is not None else {'replacement': op['value']})}


def matches(saved, submitted):
    from config_requests import fingerprint
    return saved['binding'] == fingerprint(submitted) if 'binding' in saved else saved == submitted


def expand(context, op, progress):
    if 'binding' not in op:
        return deepcopy(op)
    from config_requests import digest
    target = destination(context, op)[0]
    raw = target.read_text() if target.exists() else None
    observed = digest(raw)
    value = (json.loads(raw) if observed == progress['after'] else
             op['replacement'] if 'replacement' in op else _patched(raw, op['patch']))
    result = {k: v for k, v in op.items() if k not in {'binding', 'patch', 'replacement'}}
    result['value'] = config.validate(value)
    if observed not in {progress['before'], progress['after']} or not matches(op, result):
        raise ValueError('Retained configuration operation changed; recovery blocked')
    return result
