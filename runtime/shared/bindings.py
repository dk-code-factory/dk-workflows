"""Portable bindings resolved from host registries, never by cache enumeration."""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
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
NESTED_REVIEW_ROLES = {
    'code-review-ad:code-review-ad': ['Toolkit', 'Standards', 'Spec', 'Performance'],
    'code-review-matpocock:code-review-matpocock': ['Standards', 'Spec'],
    'pr-review-toolkit:review-pr': ['code-reviewer', 'pr-test-analyzer', 'comment-analyzer',
                                  'silent-failure-hunter', 'type-design-analyzer', 'code-simplifier'],
}


def validate_nested_roles(value):
    if (not isinstance(value, list)
            or any(not isinstance(role, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.:-]*', role) for role in value)
            or len(set(value)) != len(value)):
        raise ValueError('nested_review_roles must be unique recipient identities')
    return value


def nested_roles(value):
    binding = normalize(value)
    return binding.get('nested_review_roles', NESTED_REVIEW_ROLES.get(binding.get('skill'), []))


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
_EXECUTION_DIRECTORY = ContextVar('dk_execution_directory', default=None)
_PINNED_PROFILE_CHECKS = ContextVar('dk_pinned_profile_checks', default=None)


@contextmanager
def execution_directory(cwd):
    token = _EXECUTION_DIRECTORY.set(str(Path(cwd).resolve()))
    try:
        yield
    finally:
        _EXECUTION_DIRECTORY.reset(token)


def validate_version(registry_version, manifest):
    """A version is optional on either side; compare only declared versions."""
    declared = manifest.get('version')
    if registry_version is not None and 'version' in manifest and registry_version != declared:
        raise ValueError('Installed version and manifest disagree; refresh the host installation')


@contextmanager
def pinned_bindings(descriptors, *, cwd=None, cache_profiles=True):
    with execution_directory(cwd or _EXECUTION_DIRECTORY.get() or Path.cwd()):
        with _pinned_bindings(descriptors, cache_profiles=cache_profiles):
            yield


@contextmanager
def profile_observation(descriptors, *, cwd):
    """Fresh short profile checks without replacing the cohort's frozen bindings."""
    with execution_directory(cwd):
        with _pinned_bindings(descriptors, keep_bindings=True):
            yield


def verify_retained(name, value):
    """Validate the supplied retained provider without changing its registry identity."""
    from shared.runtime_bundles import verify, verify_binding
    canonical = key(value['binding'])
    alias = name in ALIASES and normalize(name) == normalize(value['binding'])
    if value.get('executor') == 'command':
        if name != canonical or value.get('identity') != canonical or not value.get('bundles'):
            raise ValueError('invalid retained command binding')
        verify_binding(value)
        return
    host = current_host() if value['binding'].get('host', 'current') == 'current' else value['binding']['host']
    if (value.get('executor') != host or (name != canonical and not alias)
            or value.get('identity') != canonical or verify(value['root']) != value.get('bundle_digest')):
        raise ValueError('invalid retained reviewer binding')
    procedure, manifest = manifest_skill(Path(value['root']), value['executor'],
                                         *value['binding']['skill'].split(':'))
    validate_version(value.get('version'), manifest)
    if str(procedure) != value['procedure']:
        raise ValueError('retained reviewer procedure or version changed')


@contextmanager
def _pinned_bindings(descriptors, *, cache_profiles=True, keep_bindings=False):
    pinned, checks = {}, {} if cache_profiles else None
    cwd = _EXECUTION_DIRECTORY.get()
    for name, descriptor in descriptors.items():
        value = descriptor.get('execution')
        if value is None:
            continue  # Legacy preparation keeps its original registry validation.
        verify_retained(name, value)
        if 'codex_profile' in value['binding']:
            from shared.codex_profiles import verify_profile
            verify_profile(value, cwd)
            if checks is not None:
                checks[name] = (copy.deepcopy(value), cwd)
        pinned[name] = value
    if keep_bindings:
        current = _PINNED_BINDINGS.get()
        if any(current.get(name) is not value for name, value in pinned.items()):
            raise ValueError('profile observation requires the exact retained binding scope')
        pinned = current
    token = _PINNED_BINDINGS.set(pinned)
    checked = _PINNED_PROFILE_CHECKS.set(checks)
    try:
        yield
    finally:
        _PINNED_PROFILE_CHECKS.reset(checked)
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
    contract = value.get('handoff_contract', 'git-diff-v1')
    if (contract not in ('git-diff-v1', 'git-diff-v2')
            or drafting and 'handoff_contract' in value):
        raise ValueError('Reviewer handoff_contract must be git-diff-v1 or git-diff-v2')
    nested = value.get('nested_review_roles')
    if nested is not None:
        validate_nested_roles(nested)
        if drafting:
            raise ValueError('nested_review_roles is only available for reviewers')
    extra = {'handoff_contract': contract} if contract != 'git-diff-v1' else {}
    if nested is not None:
        extra['nested_review_roles'] = list(nested)
    profile = value.get('codex_profile')
    if 'codex_profile' in value:
        if not isinstance(profile, str) or not profile.strip() or '\0' in profile:
            raise ValueError('codex_profile must be a nonempty profile name')
        if 'command' in value or value.get('host', 'current') == 'claude':
            raise ValueError('codex_profile requires a native Codex executor')
        extra['codex_profile'] = profile
    if set(value) - {'handoff_contract', 'nested_review_roles'} == {'command'}:
        argv = value['command']
        if not isinstance(argv, list) or not argv or any(not isinstance(s, str) or not s or '\0' in s for s in argv):
            raise ValueError('command must be a nonempty argument vector; shell text is not accepted')
        if not any('{handoff}' in arg for arg in argv):
            raise ValueError('command must receive the handoff through a {handoff} argument')
        return {'command': list(argv), **extra}
    if set(value) - {'skill', 'host', 'handoff_contract', 'nested_review_roles', 'codex_profile'} or not re.fullmatch(NAME + ':' + NAME, str(value.get('skill', ''))):
        raise ValueError('skill must be plugin-name:skill-name')
    if value['skill'].split(':')[1] in FORBIDDEN:
        raise ValueError('Recursive review orchestration cannot be a provider')
    host = value.get('host', 'current')
    if host not in ('current', 'codex', 'claude'):
        raise ValueError('host must be current, codex, or claude')
    return {'skill': value['skill'], 'host': host, **extra}


def key(value):
    legacy = value if isinstance(value, str) and ':' not in value else None
    value = normalize(value)
    if legacy:
        # Retained in-process operational records can still carry old identities.
        # New configuration normalizes before this point; historical labels stay readable.
        return legacy
    if 'command' in value:
        return 'command-' + hashlib.sha256(json.dumps(value['command']).encode()).hexdigest()[:20]
    identity = value['skill'] + '@' + value['host']
    if 'codex_profile' in value:
        identity += '#codex-profile-' + hashlib.sha256(value['codex_profile'].encode()).hexdigest()[:20]
    return identity


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
        if (normalize(value).get('handoff_contract', 'git-diff-v1') !=
                selected['binding'].get('handoff_contract', 'git-diff-v1')):
            raise ValueError('Reviewer handoff contract changed; refresh preparation')
        verify_retained(key(value), selected)
        if 'codex_profile' in selected['binding']:
            cwd = _EXECUTION_DIRECTORY.get()
            checks = _PINNED_PROFILE_CHECKS.get()
            if checks is None or checks.get(key(value)) != (selected, cwd):
                from shared.codex_profiles import verify_profile
                verify_profile(selected, cwd)
                if checks is not None:
                    checks[key(value)] = (copy.deepcopy(selected), cwd)
        return dict(selected)
    binding = normalize(value)
    if 'command' in binding:
        exe = binding['command'][0]
        if not shutil.which(exe):
            raise ValueError(f'Missing selected command executable: {exe}')
        return {'binding': binding, 'identity': key(binding), 'executor': 'command'}
    host = current_host() if binding['host'] == 'current' else binding['host']
    if 'codex_profile' in binding and host != 'codex':
        raise ValueError('codex_profile requires a native Codex executor (resolved host: ' + host + ')')
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
    result = {'binding': binding, 'identity': key(binding), 'executor': host, 'plugin_id': identity,
              'root': str(root.resolve()), 'procedure': str(procedure), 'version': record.get('version')}
    if 'codex_profile' in binding:
        from shared.codex_profiles import resolve_profile
        result['codex_execution'] = resolve_profile(binding['codex_profile'], _EXECUTION_DIRECTORY.get())
    return result


def diagnose(value, source=None):
    try:
        return {**resolve(value), 'source': source, 'missing': []}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        return {'binding': value, 'source': source, 'missing': [str(exc)]}
