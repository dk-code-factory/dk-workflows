"""Repository instruction discovery with an all-source default and optional overrides."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess

from shared.repository_scope import repository
from shared.storage_paths import location


HOST_LIMITATIONS = (
    'The host may already have loaded AGENTS.md, CLAUDE.md or other instructions before this skill. '
    'This policy controls dk-workflows source loading and reuse; it cannot unload host context or '
    'override higher-priority instructions. If excluded rules are already active and conflict with '
    'your choice, stop affected work and use a supported host context configured to exclude them.'
)
AUTOMATION_NAMES = {'agents.md', 'agents.override.md', 'claude.md', 'claude.local.md', 'copilot-instructions.md', '.cursorrules'}
HUMAN_PREFIXES = ('contributing', 'development', 'developing', 'hacking')
RULE_DIRECTORIES = (('.cursor', 'rules'), ('.claude', 'rules'))


def key(identity):
    return identity.removeprefix('remote:').lower() if not identity.startswith('path:') else identity


def policy_path(identity):
    return location('config')/'repositories'/(hashlib.sha256(os.fsencode(key(identity))).hexdigest()[:24] + '-instructions.json')


def relative(value):
    if (not isinstance(value, str) or not value or (os.sep == '\\' and '\\' in value) or '\x00' in value
            or PurePosixPath(value).is_absolute() or '..' in PurePosixPath(value).parts
            or str(PurePosixPath(value)) != value or value == '.'):
        raise ValueError('Instruction sources require normalized repository-relative paths')
    return value


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'mode', 'include', 'exclude'}
            or value['mode'] not in {'human', 'all', 'custom'}):
        raise ValueError('Instruction policy requires mode human, all or custom and include/exclude lists')
    result = {'mode': value['mode']}
    for field in ('include', 'exclude'):
        items = value[field]
        if not isinstance(items, list) or len(set(map(str, items))) != len(items):
            raise ValueError('Instruction source lists must contain unique paths')
        result[field] = sorted(relative(item) for item in items)
    if set(result['include']) & set(result['exclude']):
        raise ValueError('A source cannot be both included and excluded')
    return result


def envelope(identity, value):
    return {'version': 1, 'repository': key(identity), 'policy': validate(value) if value is not None else None}


def load(identity):
    path = policy_path(identity)
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or set(value) != {'version', 'repository', 'policy'}
            or type(value['version']) is not int or value['version'] != 1 or value['repository'] != key(identity)):
        raise ValueError('Malformed repository instruction policy')
    return envelope(identity, value['policy'])


def effective_policy(saved):
    # A missing override or legacy reset marker uses the default without a write.
    return saved['policy'] if saved and saved['policy'] is not None else {
        'mode': 'all', 'include': [], 'exclude': []}


def root(checkout):
    checkout = Path(checkout).resolve()
    result = subprocess.run(['git', '-C', str(checkout), 'rev-parse', '--show-toplevel'],
                            capture_output=True, timeout=15)
    return Path(os.fsdecode(result.stdout.removesuffix(b'\n'))).resolve() if result.returncode == 0 else checkout


def kind(name):
    path = PurePosixPath(name)
    lower, parts = path.name.lower(), tuple(part.lower() for part in path.parts)
    if (lower in AUTOMATION_NAMES or lower.endswith('.instructions.md') or parts[:2] in RULE_DIRECTORIES):
        return 'automation'
    if (lower.startswith(HUMAN_PREFIXES)
            or 'docs' in parts and any(p in {'development', 'developer', 'developers', 'contributing'} for p in parts[:-1])):
        return 'human'
    return None


def discover(checkout, policy=None):
    checkout = root(checkout)
    # Prune ignored trees for ordinary files, then ask only for ignored guidance.
    result = subprocess.run(['git', '-C', str(checkout), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            capture_output=True, timeout=15)
    names = (set(os.fsdecode(result.stdout).split('\x00')) - {''} if result.returncode == 0 else
             {p.relative_to(checkout).as_posix() for p in checkout.rglob('*') if p.is_file() and '.git' not in p.parts})
    if result.returncode == 0:
        patterns = [f':(glob,icase)**/{name}' for name in (
            *sorted(AUTOMATION_NAMES), '*.instructions.md', *(prefix+'*' for prefix in HUMAN_PREFIXES))]
        patterns.extend(':(glob,icase)'+'/'.join(parts)+'/**' for parts in RULE_DIRECTORIES)
        patterns.append(':(glob,icase)**/docs/**')
        ignored = subprocess.run(['git', '-C', str(checkout), 'ls-files', '-z', '--others', '--ignored',
                                  '--exclude-standard', '--', *patterns], capture_output=True, timeout=15, check=True)
        names.update(os.fsdecode(ignored.stdout).split('\x00'))
        names.discard('')
    if policy:
        names.update(policy['include'])
        names.update(policy['exclude'])
    excluded = {(checkout/name).resolve() for name in policy['exclude']} if policy else set()
    sources = []
    for name in sorted(names):
        source_kind = kind(name)
        if source_kind is None and not (policy and name in policy['include'] + policy['exclude']):
            continue
        path = checkout/name
        # Never follow repository links into host/global instructions.
        safe = not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(checkout)
        if not safe:
            if policy and name in policy['include']:
                raise ValueError('Selected instruction source is missing or unsafe: ' + name)
            if not path.is_symlink() and not (policy and name in policy['exclude']):
                continue
        selected = bool(safe and policy and path.resolve() not in excluded and (
            name in policy['include'] or policy['mode'] == 'all' or policy['mode'] == 'human' and source_kind == 'human'))
        sources.append({'path': name, 'kind': source_kind or 'custom', 'selected': selected})
    return sources


def inspect(checkout, identity=None):
    identity = identity or repository(checkout)
    checkout = root(checkout)
    saved = load(identity)
    policy = effective_policy(saved)
    return {'repository': key(identity), 'initialized': True,
            'status': 'ready', 'policy': policy,
            'sources': discover(checkout, policy), 'host_limitations': HOST_LIMITATIONS}


def read_source(checkout, source, *, discovery=False, identity=None):
    identity = identity or repository(checkout)
    checkout = root(checkout)
    source = relative(source)
    path = checkout/source
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(checkout):
        raise ValueError('Instruction source is missing or unsafe: ' + source)
    context = inspect(checkout, identity)
    found = next((item for item in context['sources'] if item['path'] == source), None)
    if found is None and discovery:
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(checkout):
            found = {'path': source, 'kind': kind(source) or 'custom', 'selected': False}
    if found is None or not (found['selected'] or discovery):
        raise ValueError('Source is not adopted; discovery reads are evidence only')
    return {**found, 'adopted': found['selected'], 'text': (checkout/source).read_text()}


def binding(checkout, identity):
    saved = load(identity)
    policy = effective_policy(saved)
    checkout = root(checkout)
    sources = discover(checkout, policy)
    if not sources and (saved is None or saved['policy'] is None):
        return None  # Preserve legacy fingerprints when no repository guidance exists.
    selected = {item['path']: hashlib.sha256((checkout/item['path']).read_bytes()).hexdigest()
                for item in sources if item['selected']}
    encoded = json.dumps({'policy': policy, 'sources': selected}, sort_keys=True).encode()
    return {'checkout': str(checkout), 'fingerprint': hashlib.sha256(encoded).hexdigest()}


def check_paths(checkout, identity, paths):
    context = inspect(checkout, identity)
    checkout = root(checkout)
    selected = {(checkout/item['path']).resolve() for item in context['sources'] if item['selected']}
    selected_names = {(checkout/item['path']).absolute() for item in context['sources'] if item['selected']}
    unselected = {(checkout/item['path']).parent.resolve()/Path(item['path']).name: item['path']
                  for item in context['sources'] if not item['selected']}
    excluded = {path.resolve(): name for path, name in unselected.items() if path.resolve() not in selected}
    if not unselected:
        return
    pending, visited = [(Path(value), frozenset()) for value in paths], set()
    while pending:
        item, ancestors = pending.pop()
        path = item.resolve()
        directory = item.is_dir()
        unselected_name = unselected.get(item.parent.resolve()/item.name)
        if unselected_name is not None and item.absolute() not in selected_names:
            raise ValueError('Excluded repository instruction source in handoff: ' + unselected_name)
        found = next((source for source in excluded if source == path or directory and source.is_relative_to(path)), None)
        if found is not None:
            raise ValueError('Excluded repository instruction source in handoff: ' + excluded[found])
        if item.is_symlink() and not directory:
            pending.append((item.parent/item.readlink(), ancestors))
        if directory and path not in ancestors and item.absolute() not in visited:
            visited.add(item.absolute())
            pending.extend((child, ancestors | {path}) for child in item.iterdir()
                           if child.name not in {'.git', '__pycache__', '.pytest_cache'})
