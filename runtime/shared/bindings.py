"""Portable bindings resolved from host registries, never by cache enumeration."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

NAME = r'[A-Za-z0-9][A-Za-z0-9_-]*'
ALIASES = {
    'code-review-claude-ad': {'skill': 'code-review-ad:code-review-ad', 'host': 'claude'},
    'code-review-claude-matpocock': {'skill': 'code-review-matpocock:code-review-matpocock', 'host': 'claude'},
    'code-review-claude-pr-review-toolkit': {'skill': 'pr-review-toolkit:review-pr', 'host': 'claude'},
    'code-review-ad': {'skill': 'code-review-ad:code-review-ad', 'host': 'current'},
    'code-review-matpocock': {'skill': 'code-review-matpocock:code-review-matpocock', 'host': 'current'},
    'code-review-qodo': {'skill': 'code-review-qodo:code-review-qodo', 'host': 'current'},
}
ROLES = ('commit_message', 'pr_description', 'issue_description')
FORBIDDEN = {'dk-pr-review', 'dk-pr-iter'}


_REGISTRY_SNAPSHOT = ContextVar('dk_registry_snapshot', default=None)


@contextmanager
def registry_snapshot():
    """Reuse host inventory during one operation; the next operation reads it fresh."""
    if _REGISTRY_SNAPSHOT.get() is not None:
        yield
        return
    token = _REGISTRY_SNAPSHOT.set({})
    try:
        yield
    finally:
        _REGISTRY_SNAPSHOT.reset(token)


def codex_plugins():
    cache = _REGISTRY_SNAPSHOT.get()
    profile = str(Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).resolve())
    key = (profile, shutil.which('codex'))
    if cache is not None and key in cache:
        return cache[key]
    proc = subprocess.run(['codex', 'plugin', 'list', '--json'], text=True, capture_output=True, timeout=30)
    if proc.returncode:
        raise ValueError(proc.stderr.strip() or 'Codex registry lookup failed')
    data = json.loads(proc.stdout)
    if (not isinstance(data, dict) or not isinstance(data.get('installed', []), list)
            or any(not isinstance(record, dict) for record in data.get('installed', []))):
        raise ValueError('Codex registry response must contain an installed array of objects')
    records = data.get('installed', [])
    if cache is not None:
        cache[key] = records
    return records


_PINNED_BINDINGS = ContextVar('dk_pinned_bindings', default={})


def validate_version(registry_version, manifest):
    """A version is optional on either side; compare only declared versions."""
    declared = manifest.get('version')
    if registry_version is not None and 'version' in manifest and registry_version != declared:
        raise ValueError('Installed version and manifest disagree; refresh the host installation')


@contextmanager
def pinned_bindings(descriptors):
    from shared.runtime_bundles import verify, verify_binding
    pinned = {}
    for name, descriptor in descriptors.items():
        value = descriptor.get('execution')
        if value is None:
            continue  # Legacy preparation keeps its original registry validation.
        canonical = key(value['binding'])
        alias = name in ALIASES and normalize(name) == normalize(value['binding'])
        if value.get('executor') == 'command':
            if name != canonical or value.get('identity') != canonical or not value.get('bundles'):
                raise ValueError('invalid retained command binding')
            verify_binding(value)
            pinned[name] = value
            continue
        host = current_host() if value['binding'].get('host', 'current') == 'current' else value['binding']['host']
        if (value.get('executor') != host or (name != canonical and not alias)
                or value.get('identity') != canonical or verify(value['root']) != value.get('bundle_digest')):
            raise ValueError('invalid retained reviewer binding')
        procedure, manifest = manifest_skill(Path(value['root']), value['executor'],
                                             *value['binding']['skill'].split(':'))
        validate_version(value.get('version'), manifest)
        if str(procedure) != value['procedure']:
            raise ValueError('retained reviewer procedure or version changed')
        pinned[name] = value
    token = _PINNED_BINDINGS.set(pinned)
    try:
        yield
    finally:
        _PINNED_BINDINGS.reset(token)


def current_host():
    host = os.environ.get('DK_HOST')
    if host not in ('codex', 'claude'):
        raise ValueError('Set DK_HOST=codex or DK_HOST=claude as instructed by the native entrypoint')
    return host


def normalize(value, *, drafting=False):
    if value == 'builtin' and drafting:
        return 'builtin'
    if isinstance(value, str):
        if value in ALIASES:
            return dict(ALIASES[value])
        value = {'skill': value if ':' in value else 'dk-workflows:' + value, 'host': 'current'}
    if not isinstance(value, dict):
        raise ValueError('Expected a skill binding or argument-vector command binding')
    if set(value) == {'command'}:
        argv = value['command']
        if not isinstance(argv, list) or not argv or any(not isinstance(s, str) or not s or '\0' in s for s in argv):
            raise ValueError('command must be a nonempty argument vector; shell text is not accepted')
        if not any('{handoff}' in arg for arg in argv):
            raise ValueError('command must receive the handoff through a {handoff} argument')
        return {'command': list(argv)}
    if set(value) - {'skill', 'host'} or not re.fullmatch(NAME + ':' + NAME, str(value.get('skill', ''))):
        raise ValueError('skill must be plugin-name:skill-name')
    if value['skill'].split(':')[1] in FORBIDDEN:
        raise ValueError('Recursive review orchestration cannot be a provider')
    host = value.get('host', 'current')
    if host not in ('current', 'codex', 'claude'):
        raise ValueError('host must be current, codex, or claude')
    return {'skill': value['skill'], 'host': host}


def key(value):
    legacy = value if isinstance(value, str) and ':' not in value else None
    value = normalize(value)
    if legacy:
        # Retained in-process operational records can still carry old identities.
        # New configuration normalizes before this point; historical labels stay readable.
        return legacy
    if 'command' in value:
        return 'command-' + hashlib.sha256(json.dumps(value['command']).encode()).hexdigest()[:20]
    return value['skill'] + '@' + value['host']


def selection(values):
    if not isinstance(values, list):
        raise ValueError('reviewers must be an ordered list')
    result = [normalize(v) for v in values]
    if len({key(v) for v in result}) != len(result):
        raise ValueError('reviewers must be unique')
    return result


def manifest_skill(root, host, namespace, name):
    root = root.resolve()
    manifest = root / f'.{host}-plugin/plugin.json'
    data = json.loads(manifest.read_text())
    if data.get('name') != namespace:
        raise ValueError('Installed registry identity does not match plugin manifest')
    directories = data.get('skills', './skills/')
    if isinstance(directories, str):
        directories = [directories]
    entries = []
    for directory in directories:
        path = (root / directory).resolve()
        entries += [path / name / 'SKILL.md', path / 'SKILL.md'] if path.name == name else [path / name / 'SKILL.md']
    if host == 'claude':
        commands = data.get('commands', './commands/')
        if isinstance(commands, str):
            commands = [commands]
        entries += [(root / directory / f'{name}.md').resolve() for directory in commands]
    matches = {p.resolve() for p in entries if p.is_file() and p.resolve().is_relative_to(root)}
    if len(matches) != 1:
        raise ValueError(f'Expected one installed {namespace}:{name} procedure, found {len(matches)}')
    return matches.pop(), data


def resolve(value):
    selected = _PINNED_BINDINGS.get().get(key(value))
    if selected is not None:
        from shared.runtime_bundles import verify_binding
        verify_binding(selected)
        return dict(selected)
    binding = normalize(value)
    if 'command' in binding:
        exe = binding['command'][0]
        if not shutil.which(exe):
            raise ValueError(f'Missing selected command executable: {exe}')
        return {'binding': binding, 'identity': key(binding), 'executor': 'command'}
    host = current_host() if binding['host'] == 'current' else binding['host']
    namespace, name = binding['skill'].split(':')
    if not shutil.which(host):
        raise ValueError(f'Missing selected host executable: {host}')
    matches = []
    if host == 'claude':
        registry = Path(os.environ.get('CLAUDE_CONFIG_DIR', Path.home() / '.claude')) / 'plugins/installed_plugins.json'
        plugins = json.loads(registry.read_text()).get('plugins', {}) if registry.is_file() else {}
        for identity, records in plugins.items():
            if identity.split('@', 1)[0] == namespace:
                for record in records:
                    matches.append((identity, Path(record['installPath']), record))
    else:
        home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
        for record in codex_plugins():
            if record.get('name') == namespace and record.get('enabled', True):
                identity = record['pluginId']
                path = record.get('installedPath') or home / 'plugins/cache' / record['marketplaceName'] / namespace / record['version']
                matches.append((identity, Path(path), record))
    if len(matches) != 1:
        raise ValueError(f'{binding["skill"]} on {host}: expected one registry match, found {len(matches)}. '
                         f'Install the plugin for {host}, or remove ambiguous installations.')
    identity, root, record = matches[0]
    procedure, manifest = manifest_skill(root, host, namespace, name)
    validate_version(record.get('version'), manifest)
    return {'binding': binding, 'identity': key(binding), 'executor': host, 'plugin_id': identity,
            'root': str(root.resolve()), 'procedure': str(procedure), 'version': record.get('version')}


def diagnose(value, source=None):
    try:
        return {**resolve(value), 'source': source, 'missing': []}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        return {'binding': value, 'source': source, 'missing': [str(exc)]}
