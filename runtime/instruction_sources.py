"""Repository instruction discovery and explicit adoption, without saved source text."""
import hashlib
import json
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


def key(identity):
    return identity.removeprefix('remote:').lower() if not identity.startswith('path:') else identity


def policy_path(identity):
    return location('config')/'repositories'/(hashlib.sha256(key(identity).encode()).hexdigest()[:24] + '-instructions.json')


def relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or '\x00' in value
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


def root(checkout):
    checkout = Path(checkout).resolve()
    result = subprocess.run(['git', '-C', str(checkout), 'rev-parse', '--show-toplevel'],
                            capture_output=True, text=True, timeout=15)
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else checkout


def kind(name):
    path = PurePosixPath(name)
    lower, parts = path.name.lower(), tuple(part.lower() for part in path.parts)
    if (lower in {'agents.md', 'agents.override.md', 'claude.md', 'claude.local.md', 'copilot-instructions.md', '.cursorrules'}
            or lower.endswith('.instructions.md') or parts[:2] in {('.cursor', 'rules'), ('.claude', 'rules')}):
        return 'automation'
    if (lower.startswith(('contributing', 'development', 'developing', 'hacking'))
            or 'docs' in parts and any(p in {'development', 'developer', 'developers', 'contributing'} for p in parts[:-1])):
        return 'human'
    return None


def discover(checkout, policy=None):
    checkout = root(checkout)
    # Git performs ignored-directory pruning, including large dependency trees.
    result = subprocess.run(['git', '-C', str(checkout), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            capture_output=True, timeout=15)
    names = (set(result.stdout.decode().split('\x00')) - {''} if result.returncode == 0 else
             {p.relative_to(checkout).as_posix() for p in checkout.rglob('*') if p.is_file() and '.git' not in p.parts})
    if policy:
        names.update(policy['include'])
        names.update(policy['exclude'])
    sources = []
    for name in sorted(names):
        source_kind = kind(name)
        if source_kind is None and not (policy and name in policy['include']):
            continue
        path = checkout/name
        # Never follow repository links into host/global instructions.
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(checkout):
            if policy and name in policy['include']:
                raise ValueError('Selected instruction source is missing or unsafe: ' + name)
            continue
        selected = bool(policy and name not in policy['exclude'] and (
            name in policy['include'] or policy['mode'] == 'all' or policy['mode'] == 'human' and source_kind == 'human'))
        sources.append({'path': name, 'kind': source_kind or 'custom', 'selected': selected})
    return sources


def inspect(checkout, identity=None):
    checkout = root(checkout)
    identity = identity or repository(checkout)
    saved = load(identity)
    policy = saved['policy'] if saved else None
    return {'repository': key(identity), 'initialized': policy is not None,
            'status': 'ready' if policy is not None else 'choice-needed', 'policy': policy,
            'sources': discover(checkout, policy), 'host_limitations': HOST_LIMITATIONS}


def read_source(checkout, source, *, discovery=False, identity=None):
    checkout = root(checkout)
    source = relative(source)
    context = inspect(checkout, identity)
    found = next((item for item in context['sources'] if item['path'] == source), None)
    if found is None and discovery:
        path = checkout/source
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(checkout):
            found = {'path': source, 'kind': kind(source) or 'custom', 'selected': False}
    if found is None or not (found['selected'] or discovery):
        raise ValueError('Source is not adopted; discovery reads are evidence only')
    return {**found, 'adopted': found['selected'], 'text': (checkout/source).read_text()}


def binding(checkout, identity):
    saved = load(identity)
    if saved is None:
        return None  # Preserve legacy task identities; native initialization still asks.
    if saved['policy'] is None:
        raise ValueError('Repository instruction choice needed after reset; initialize through dk-config')
    checkout = root(checkout)
    sources = discover(checkout, saved['policy'])
    selected = {item['path']: hashlib.sha256((checkout/item['path']).read_bytes()).hexdigest()
                for item in sources if item['selected']}
    encoded = json.dumps({'policy': saved['policy'], 'sources': selected}, sort_keys=True).encode()
    return {'checkout': str(checkout), 'fingerprint': hashlib.sha256(encoded).hexdigest()}


def check_paths(checkout, identity, paths):
    saved = load(identity)
    if saved is None:
        return
    context = inspect(checkout, identity)
    checkout = root(checkout)
    excluded = {(checkout/item['path']).resolve() for item in context['sources'] if not item['selected']}
    for value in paths:
        path = Path(value).resolve()
        found = next((source for source in excluded if source == path or path.is_dir() and source.is_relative_to(path)), None)
        if found is not None:
            raise ValueError('Excluded repository instruction source in handoff: ' + str(found.relative_to(checkout)))
