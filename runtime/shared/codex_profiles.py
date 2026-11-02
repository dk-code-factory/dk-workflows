"""Private Codex profile provenance, resolved without launching a model turn."""
import copy
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from urllib.parse import urlsplit

# Native configured values and resolved model metadata are separate evidence.
SETTINGS = ('model', 'model_provider', 'model_reasoning_effort', 'model_reasoning_summary',
            'model_verbosity', 'model_context_window', 'model_auto_compact_token_limit',
            'model_auto_compact_token_limit_scope', 'model_max_output_tokens',
            'model_supports_reasoning_summaries', 'model_instructions_file',
            'model_catalog_json', 'compact_prompt')
LOWER = {'packagedDefaults', 'mdm', 'system', 'enterpriseManaged'}
HIGHER = {'project', 'sessionFlags', 'legacyManagedConfigTomlFromFile',
          'legacyManagedConfigTomlFromMdm'}


def toml_value(value):
    """Encode native --config values, preserving nested tables and quoted keys."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return str(value).lower()
    if type(value) is int:
        if not -(2**63) <= value < 2**63:
            raise ValueError('Codex configuration integer is outside signed-i64 range')
        return str(value)
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else ('nan' if math.isnan(value) else str(value))
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, list):
        return '[' + ','.join(map(toml_value, value)) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(key, ensure_ascii=False) + '=' + toml_value(item)
                              for key, item in value.items()) + '}'
    raise ValueError('Unsupported native Codex configuration value')


def config_flags(values):
    return [arg for key, value in values.items()
            for arg in ('-c', key + '=' + toml_value(value))]


def merge(base, overlay):
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        result[key] = merge(result[key], value) if (
            isinstance(result.get(key), dict) and isinstance(value, dict)) else copy.deepcopy(value)
    return result


def native_path(value, base):
    """Match AbsolutePathBuf lexical normalization, without resolving symlinks."""
    if not isinstance(value, str):
        raise ValueError('Codex governing file reference must be a path string')
    if value == '~' or value.startswith('~/'):
        value = str(Path.home()) + value[1:]
    return Path(os.path.normpath(os.path.join(str(base), value)))


# This local holder reserves its group-leader PID until parent cleanup. The
# watcher starts before the native child so parent death also bounds active work.
_NATIVE_HOLDER = r"""import os, signal, subprocess, sys, threading
status, control = map(int, sys.argv[1:3])
def watch_owner():
    try:
        while os.read(control, 1):
            pass
    finally:
        os.killpg(os.getpgrp(), signal.SIGKILL)
owner = threading.Thread(target=watch_owner, daemon=True)
owner.start()
try:
    try:
        child = subprocess.Popen(sys.argv[3:], close_fds=True)
        code = child.wait()
        os.write(status, (str(code) + "\n").encode("ascii"))
    except OSError:
        pass
    finally:
        os.close(status)
    owner.join()
finally:
    os.killpg(os.getpgrp(), signal.SIGKILL)
"""


def stop_native(process):
    """Signal the owned group before any operation can reap its leader."""
    if os.name == 'posix':
        if process.returncode is not None:
            raise ValueError('Native Codex helper group ownership was released before cleanup')
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.returncode is None:
        process.kill()
    process.wait(timeout=2)


def run_native(argv, cwd, stdout, *, env=None):
    child_env = dict(os.environ if env is None else env)
    if os.name != 'posix':
        process = subprocess.Popen(argv, cwd=cwd, stdout=stdout, stderr=subprocess.DEVNULL, env=child_env)
        try:
            return subprocess.CompletedProcess([], process.wait(timeout=30))
        finally:
            stop_native(process)
    status_read = status_write = control_read = control_write = None
    process = None
    try:
        status_read, status_write = os.pipe()
        control_read, control_write = os.pipe()
        process = subprocess.Popen([sys.executable, '-I', '-S', '-c', _NATIVE_HOLDER,
                                    str(status_write), str(control_read), *argv], cwd=cwd,
            stdout=stdout, stderr=subprocess.DEVNULL, env=child_env, start_new_session=True,
            pass_fds=(status_write, control_read), close_fds=True)
        os.close(status_write)
        status_write = None
        os.close(control_read)
        control_read = None
        payload, deadline = bytearray(), time.monotonic() + 30
        with selectors.DefaultSelector() as selector:
            selector.register(status_read, selectors.EVENT_READ)
            while b'\n' not in payload:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise subprocess.TimeoutExpired([], 30)
                block = os.read(status_read, 33 - len(payload))
                if not block:
                    raise ValueError('Native Codex helper exit status unavailable')
                payload.extend(block)
                if len(payload) > 32:
                    raise ValueError('Native Codex helper exit status exceeded its bound')
        if re.fullmatch(rb'-?[0-9]{1,10}\n', payload) is None:
            raise ValueError('Native Codex helper returned malformed exit status')
        return subprocess.CompletedProcess([], int(payload))
    finally:
        try:
            if process is not None:
                stop_native(process)
        finally:
            for descriptor in (status_read, status_write, control_read, control_write):
                if descriptor is not None:
                    os.close(descriptor)


def profile_flags(name):
    return ['--profile=' + name] if name.startswith('-') else ['--profile', name]


def validate_profile(name, cwd):
    # Unlike projected app-server overrides, this runs the actual selected-profile
    # loader at below-project precedence, including permission and legacy checks.
    # Debug prompt-input builds context but starts no model turn. Discard its
    # prompt and diagnostics: either can contain private configuration/headers.
    try:
        result = run_native(['codex', *profile_flags(name), '-C', str(cwd), '--sandbox',
                             'read-only', 'debug', 'prompt-input'], cwd, subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('Native Codex selected-profile validation unavailable (' +
                         type(exc).__name__ + ')') from None
    if result.returncode:
        raise ValueError('Native Codex selected-profile validation failed; inspect the profile '
                         'with codex --profile NAME debug prompt-input privately')


def read_config(cwd, overrides=None, *, models=False, requirements=False, env=None):
    """Keep startup, pipe and cleanup failures inside the named native contract."""
    try:
        return _read_config(cwd, overrides, models=models, requirements=requirements, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('Native Codex app-server transport unavailable (' +
                         type(exc).__name__ + ')') from None


def _read_config(cwd, overrides=None, *, models=False, requirements=False, env=None):
    """Ask the native loader for enabled layers under one shared time budget."""
    deadline = time.monotonic() + 30
    process = subprocess.Popen(['codex', *config_flags(overrides or {}), 'app-server'], cwd=cwd, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=(os.name == 'posix'),
                               env=dict(os.environ if env is None else env))
    buffered = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            def request(identity, method, params):
                process.stdin.write((json.dumps({'id': identity, 'method': method,
                                                 'params': params}) + '\n').encode())
                process.stdin.flush()
                while True:
                    while b'\n' in buffered:
                        line, _, rest = buffered.partition(b'\n')
                        buffered[:] = rest
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeError):
                            raise ValueError('Codex ' + method + ' returned malformed JSON') from None
                        if not isinstance(event, dict):
                            raise ValueError('Codex ' + method + ' returned a non-object event')
                        if event.get('id') == identity:
                            if 'error' in event:
                                raise ValueError('Codex native metadata request failed: ' + method)
                            if 'result' not in event:
                                raise ValueError('Codex ' + method + ' returned a malformed native metadata response')
                            return event['result']
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ValueError('Codex ' + method + ' exceeded the shared 30-second metadata budget')
                    block = os.read(process.stdout.fileno(), 65536)
                    if not block:
                        raise ValueError('Codex ' + method + ' native metadata ended without a response')
                    buffered.extend(block)
                    if len(buffered) > 16 * 1024 * 1024:
                        raise ValueError('Codex ' + method + ' response exceeds 16 MiB')
            initialized = request(1, 'initialize', {
                'clientInfo': {'name': 'codex_exec', 'version': '1'},
                'capabilities': {'experimentalApi': True}})
            if not isinstance(initialized, dict):
                raise ValueError('Codex initialize returned an unsupported response')
            process.stdin.write(b'{"method":"initialized","params":{}}\n')
            process.stdin.flush()
            result = request(2, 'config/read', {'cwd': str(cwd), 'includeLayers': True})
            if not isinstance(result, dict):
                raise ValueError('Codex returned an unsupported configuration result')
            if requirements:
                policy = request(3, 'configRequirements/read', {})
                if not isinstance(policy, dict) or 'requirements' not in policy:
                    raise ValueError('Codex returned unsupported requirements metadata')
                result['_requirements'] = policy['requirements'] or {}
            if models:
                listing = {'data': []}
                cursor, seen, size, identity = None, set(), 0, 4
                while True:
                    params = {'includeHidden': True, **({'cursor': cursor} if cursor is not None else {})}
                    page = request(identity, 'model/list', params)
                    if not isinstance(page, dict) or not isinstance(page.get('data'), list):
                        raise ValueError('Native model/list returned an unsupported page')
                    size += len(json.dumps(page))
                    if size > 16 * 1024 * 1024:
                        raise ValueError('Native model/list pages exceed 16 MiB')
                    listing['data'].extend(page['data'])
                    cursor = page.get('nextCursor')
                    if cursor is None:
                        break
                    if not isinstance(cursor, str) or not cursor or cursor in seen:
                        raise ValueError('Native model/list returned an invalid or repeated cursor')
                    seen.add(cursor)
                    identity += 1
                return result, initialized.get('userAgent'), listing
            return result, initialized.get('userAgent')
    finally:
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass  # Preserve the original startup/protocol failure.
        process.stdout.close()
        stop_native(process)

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     default=str).encode()).hexdigest()


def _owned_path(value, cwd, paths):
    """Capture lexical/canonical ownership now; later comparisons need no live aliases."""
    if not isinstance(value, str) or not Path(value).is_absolute():
        return value
    lexical = Path(os.path.normpath(value))
    try:
        canonical = lexical.resolve()
    except (OSError, RuntimeError):
        return value
    try:
        relative = lexical.relative_to(cwd).as_posix()
        target = canonical.relative_to(cwd).as_posix()
    except ValueError:
        paths[value] = {'external_target': str(canonical)}
        return value  # External lexical/target relationships remain exact.
    paths[value] = {'relative': relative, 'target': target}
    return {'owned_workspace_path': relative, 'target': target}


def _config_identity(config, cwd, paths, *, requirements=False):
    value = copy.deepcopy(config)
    if not isinstance(value, dict):
        return value
    catalog = 'modelCatalogJson' if requirements else 'model_catalog_json'
    for field in (catalog,) if requirements else (catalog, 'model_instructions_file'):
        if field in value:
            value[field] = _owned_path(value[field], cwd, paths)
    providers = value.get('modelProviders' if requirements else 'model_providers')
    if isinstance(providers, dict):
        for provider in providers.values():
            auth = provider.get('auth') if isinstance(provider, dict) else None
            if isinstance(auth, dict) and 'cwd' in auth:
                auth['cwd'] = _owned_path(auth['cwd'], cwd, paths)
    return value


def _source_identity(source, cwd, paths):
    value = copy.deepcopy(source)
    for field in ('file', 'dotCodexFolder'):
        if field in value:
            value[field] = _owned_path(value[field], cwd, paths)
    return value


def _layer_identity(layer, cwd, paths):
    config = layer['config']
    projected = _config_identity(config, cwd, paths)
    _source_identity(layer['name'], cwd, paths)
    source = layer['name'].get('file')
    if layer['name'].get('type') == 'project':
        source = str(Path(layer['name']['dotCodexFolder']) / 'config.toml')
    try:
        source_hash = hashlib.sha256(Path(source).read_bytes()).hexdigest() if isinstance(source, str) else None
    except OSError:
        source_hash = None
    target = None
    if isinstance(source, str):
        _owned_path(source, cwd, paths)
        try:
            target = str(Path(source).resolve())
        except (OSError, RuntimeError):
            target = None
        if target is not None:
            _owned_path(target, cwd, paths)
    return {'name': copy.deepcopy(layer['name']), 'version': layer['version'],
            'source_path': source, 'source_target': target,
            'config_sha256': fingerprint(config), 'comparison_sha256': fingerprint(projected),
            'source_sha256': source_hash, 'projected': projected != config}


def _source_comparison(item, layers, path):
    result = copy.deepcopy(item)
    if not isinstance(result, dict) or not isinstance(result.get('name'), dict):
        return result
    for layer in layers:
        if (isinstance(layer, dict) and layer.get('name') == item['name'] and
                layer.get('version') == item.get('version') and layer.get('projected') is True
                and isinstance(layer.get('source_sha256'), str)):
            # Native versions hash the complete normalized JSON. Public
            # layer JSON plus raw file SHA also covers redaction/type loss.
            result['version'] = {'native_config': layer['comparison_sha256'],
                                 'source': layer['source_sha256']}
            if 'config_sha256' in result:
                result['config_sha256'] = layer['comparison_sha256']
            break
    for field in ('file', 'dotCodexFolder'):
        if field in result['name']:
            result['name'][field] = path(result['name'][field])
    for field in ('source_path', 'source_target'):
        if field in result:
            result[field] = path(result[field])
    return result


def profile_identity(snapshot):
    """Project only captured typed ownership; opaque values and unknown extensions govern."""
    value = copy.deepcopy(snapshot)
    proof = value.get('identity_evidence')
    if (not isinstance(proof, dict) or set(proof) != {'version', 'workspace', 'paths', 'layers',
            'provider', 'baseline', 'exact_sha256'} or proof['version'] != 1 or
            proof['exact_sha256'] != fingerprint({key: item for key, item in value.items()
                                                 if key != 'identity_evidence'})):
        return value
    workspace, paths = proof['workspace'], proof['paths']
    if not isinstance(workspace, str) or not isinstance(paths, dict) or not isinstance(proof['layers'], list):
        return value
    if any(not isinstance(item, dict) or not (
            set(item) == {'external_target'} and isinstance(item['external_target'], str) or
            set(item) == {'relative', 'target'} and all(isinstance(field, str) and
                not Path(field).is_absolute() and '..' not in Path(field).parts for field in item.values()))
            for item in paths.values()):
        return value
    def path(item):
        association = paths.get(item) if isinstance(item, str) else None
        if not isinstance(association, dict) or set(association) != {'relative', 'target'}:
            return item
        for field in association.values():
            if (not isinstance(field, str) or Path(field).is_absolute() or '..' in Path(field).parts):
                return item
        if str(Path(workspace) / association['relative']) != item:
            return item
        return {'owned_workspace_path': association['relative'], 'target': association['target']}
    def source(item):
        return _source_comparison(item, proof['layers'], path)
    for field in ('model_catalog_json', 'model_instructions_file'):
        if field in value.get('settings', {}):
            value['settings'][field] = path(value['settings'][field])
    for reference in value.get('setting_sources', {}).values():
        if isinstance(reference, dict):
            for field in ('path', 'resolved_path'):
                if field in reference:
                    reference[field] = path(reference[field])
    value['layers'] = [source(item) for item in value.get('layers', [])]
    value['origins'] = {key: source(item) for key, item in value.get('origins', {}).items()}
    value['profile_source'] = source(value.get('profile_source'))
    provider = proof['provider']
    if (isinstance(provider, dict) and set(provider) == {'exact', 'comparison'} and
            value.get('provider', {}).get('definition_sha256') == provider['exact']):
        value['provider']['definition_sha256'] = provider['comparison']
        proof['provider'] = {'exact': provider['comparison'], 'comparison': provider['comparison']}
    baseline = proof['baseline']
    if isinstance(baseline, dict) and set(baseline) == {'config', 'comparison', 'origins', 'comparison_origins'}:
        baseline['config'], baseline['origins'] = baseline['comparison'], baseline['comparison_origins']
    proof['layers'] = [source(item) for item in proof['layers']]
    proof['workspace'] = '<owned-execution-workspace>'
    proof['paths'] = sorted([({'relative': item['relative'], 'target': item['target']}
                              if 'relative' in item else {'path': original, **item})
                             for original, item in paths.items()], key=lambda item: fingerprint(item))
    proof['exact_sha256'] = fingerprint({key: item for key, item in value.items() if key != 'identity_evidence'})
    return value


def selected_layers(name, cwd):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('codex_profile must be a plain native profile name')
    home = Path(os.path.abspath(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    path = home / (name + '.config.toml')
    try:
        content = path.read_bytes()
        profile = tomllib.loads(content.decode())
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f'Cannot read selected Codex profile {name!r} ({type(exc).__name__})') from None
    source = {'name': {'type': 'user', 'file': str(path), 'profile': name},
              'version': 'sha256:' + hashlib.sha256(content).hexdigest()}
    # These are native pre-project discovery/sanitation inputs, not a substitute
    # for authentic below-project validation. Do not project arbitrary profile
    # values at CLI precedence: that could mask a genuine project override.
    discovery = {key: profile[key] for key in ('projects', 'project_root_markers') if key in profile}
    features = profile.get('features', {})
    if isinstance(features, dict):
        trusted = {key: features[key] for key in ('network_proxy', 'credential_masking') if key in features}
        if trusted:
            discovery['features'] = trusted
    result, agent = read_config(cwd, discovery, requirements=True)
    if (not isinstance(result, dict) or not isinstance(result.get('config'), dict)
            or not isinstance(result.get('origins'), dict) or not isinstance(result.get('layers'), list)):
        raise ValueError('Codex config/read did not return effective settings and layers')
    version = re.search(r'(?:/|\s)(\d+)\.(\d+)\.(\d+)', agent if isinstance(agent, str) else '')
    if not version or tuple(map(int, version.groups())) < (0, 134, 0):
        raise ValueError('codex_profile requires modern profile-v2 native metadata facilities')
    layers = result['layers']
    for layer in layers:
        if (not isinstance(layer, dict) or not isinstance(layer.get('name'), dict)
                or not isinstance(layer['name'].get('type'), str)
                or not isinstance(layer.get('config'), dict) or not isinstance(layer.get('version'), str)):
            raise ValueError('Codex config/read returned an unsupported layer shape')
        required = {'project': ('dotCodexFolder',), 'user': ('file',), 'system': ('file',),
                    'packagedDefaults': ('file',), 'legacyManagedConfigTomlFromFile': ('file',)}
        if any(not isinstance(layer['name'].get(field), str)
               for field in required.get(layer['name']['type'], ())):
            raise ValueError('Codex config/read returned an unsupported layer shape')
    users = [i for i, layer in enumerate(layers) if layer['name'].get('type') == 'user']
    if len(users) != 1:
        raise ValueError('Codex config/read returned ambiguous user/profile layers')
    index = users[0]
    for i, layer in enumerate(layers):
        allowed = HIGHER if i < index else LOWER if i > index else {'user'}
        if layer['name'].get('type') not in allowed:
            raise ValueError('Codex config/read returned unsupported layer precedence')
    base = layers[index]
    if not isinstance(base['name'].get('file'), str):
        raise ValueError('Codex config/read returned an unsupported layer shape')
    if (Path(base['name']['file']).resolve() != (home / 'config.toml').resolve()
            or base['name'].get('profile') is not None or base.get('disabledReason')):
        raise ValueError('Codex config/read did not resolve the expected base user configuration')
    # Native normalizes each file before merging. Preserve lexical selected
    # pathname semantics even when the profile itself is a symlink.
    for field in ('model_catalog_json', 'model_instructions_file'):
        overridden = any(not layer.get('disabledReason') and field in layer['config']
                         and layer['name']['type'] != 'sessionFlags' for layer in layers[:index])
        if field in profile and not overridden:
            profile[field] = str(native_path(profile[field], path.parent))
    # Explicit provider auth.cwd is also an AbsolutePathBuf in native profile
    # loading. Normalize benign strings before the recursive merge; ignored
    # malformed lower leaves remain for authentic final validation to assess.
    providers = profile.get('model_providers')
    if isinstance(providers, dict):
        for provider in providers.values():
            auth = provider.get('auth') if isinstance(provider, dict) else None
            if isinstance(auth, dict) and isinstance(auth.get('cwd'), str):
                auth['cwd'] = str(native_path(auth['cwd'], path.parent))
    merged, origins = copy.deepcopy(result['config']), copy.deepcopy(result['origins'])
    ordered = [*reversed(layers[index:]), {'name': source['name'], 'version': source['version'],
               'config': profile}, *reversed(layers[:index])]
    for layer in ordered:
        # The only session flags in this read are our discovery projection.
        if layer.get('disabledReason') or layer['name']['type'] == 'sessionFlags':
            continue
        merged = merge(merged, layer['config'])
        origins.update({field: {key: layer[key] for key in ('name', 'version')}
                        for field in layer['config']})
    policy = result.get('_requirements', {})
    if not isinstance(policy, dict):
        raise ValueError('Codex returned unsupported requirements metadata')
    merged['_helper_baseline'] = copy.deepcopy(result['config'])
    merged['_helper_discovery'] = copy.deepcopy(discovery)
    merged['_requirements'] = policy
    merged['_native_version'] = version.group(0).strip('/ ')
    settings = {field: merged.get(field) for field in SETTINGS}
    if policy.get('modelCatalogJson') is not None:
        settings['model_catalog_json'] = policy['modelCatalogJson']
        origins['model_catalog_json'] = {'name': {'type': 'requirements'},
                                        'version': 'sha256:' + fingerprint(policy)}
    references = {}
    for field in ('model_catalog_json', 'model_instructions_file'):
        value = settings[field]
        if value is None:
            continue
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('Native Codex returned an unnormalized governing file reference')
        target = Path(value)
        try:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            real = str(target.resolve(strict=True))
        except OSError as exc:
            raise ValueError(f'Cannot read governing Codex {field} file ({type(exc).__name__})') from None
        references[field] = {'path': value, 'resolved_path': real, 'sha256': digest}
    provenance = [{**{key: layer[key] for key in ('name', 'version')},
                   'disabled': bool(layer.get('disabledReason'))} for layer in layers]
    paths = {}
    identities = [_layer_identity(layer, Path(cwd), paths) for layer in layers]
    _config_identity(merged, Path(cwd), paths)
    _source_identity(source['name'], Path(cwd), paths)
    _config_identity(policy, Path(cwd), paths, requirements=True)
    for reference in references.values():
        for field in ('path', 'resolved_path'):
            _owned_path(reference[field], Path(cwd), paths)
    merged['_identity_paths'] = paths
    baseline_config = _config_identity(result['config'], Path(cwd), paths)
    baseline_origins = {key: _source_comparison(value, identities,
                        lambda item: _owned_path(item, Path(cwd), paths))
                        for key, value in result['origins'].items()}
    baseline = {'config': fingerprint(result['config']), 'comparison': fingerprint(baseline_config),
                'origins': fingerprint(result['origins']), 'comparison_origins': fingerprint(baseline_origins)}
    return {'profile': name, 'profile_source': source, 'codex_version': version.group(0).strip('/ '),
            'settings': settings, 'origins': {field: origins.get(field) for field in SETTINGS},
            'setting_sources': references, 'layers': provenance,
            'requirements_sha256': fingerprint(policy),
            'identity_evidence': {'version': 1, 'workspace': str(cwd), 'paths': paths, 'layers': identities, 'baseline': baseline}}, merged


# Native correspondence: openai/codex a09812981de703f18f38015cd5d500ab373f616c,
# models-manager/{manager,model_info}.rs, protocol/openai_models{,/reasoning_effort}.rs.
# Version is retained, not treated as proof that future native algorithms match.
METADATA_KEYS = ('model_provider', 'model_providers', 'model_catalog_json', 'openai_base_url',
                 'chatgpt_base_url', 'features', 'cli_auth_credentials_store',
                 'forced_login_method', 'forced_chatgpt_workspace_id')
MODEL_FIELDS = ('slug', 'default_reasoning_level', 'supported_reasoning_levels',
                'multi_agent_reasoning_effort', 'context_window', 'max_context_window',
                'auto_compact_token_limit', 'effective_context_window_percent')
I64_MIN, I64_MAX = -(2**63), 2**63 - 1


def i64(value, field):
    if type(value) is not int or not I64_MIN <= value <= I64_MAX:
        raise ValueError('Invalid signed-i64 native model metadata: ' + field)
    return value


def divide(value, denominator):
    # Rust integer division truncates toward zero, including negative metadata.
    return (abs(value) // denominator) * (-1 if value < 0 else 1)


def effective_settings(config, model, provider, metadata):
    selected = config.get('model_reasoning_effort')
    effort = selected if selected is not None else metadata.get('default_reasoning_level')
    if effort is not None and (not isinstance(effort, str) or not effort):
        raise ValueError('Invalid native model reasoning effort')
    if effort == 'persistent':
        effort = 'disabled'
    elif effort == 'ultra':
        supported = [entry['effort'] for entry in metadata.get('supported_reasoning_levels', [])
                     if isinstance(entry, dict) and isinstance(entry.get('effort'), str)]
        preferred = metadata.get('multi_agent_reasoning_effort')
        effort = (preferred if preferred != 'ultra' and preferred in supported else
                  'max' if 'max' in supported else
                  next((value for value in reversed(supported) if value != 'ultra'), 'medium'))
    window, maximum = metadata.get('context_window'), metadata.get('max_context_window')
    for field, value in (('context_window', window), ('max_context_window', maximum)):
        if value is not None:
            i64(value, field)
    if config.get('model_context_window') is not None:
        requested = i64(config['model_context_window'], 'model_context_window')
        window = min(requested, maximum) if maximum is not None else requested
    if window is None:
        window = maximum
    percent = i64(metadata.get('effective_context_window_percent'), 'effective_context_window_percent')
    usable = None if window is None else divide(max(I64_MIN, min(I64_MAX, window * percent)), 100)
    limit = config.get('model_auto_compact_token_limit')
    if limit is None:
        limit = metadata.get('auto_compact_token_limit')
    if limit is not None:
        i64(limit, 'auto_compact_token_limit')
    if window is not None:
        # Native release i64 multiplication wraps; usable-context multiplication
        # above explicitly saturates instead. Keep the two operations distinct.
        product = ((window * 9 - I64_MIN) % (2**64)) + I64_MIN
        cap = divide(product, 10)
        limit = cap if limit is None else min(limit, cap)
    scope = config.get('model_auto_compact_token_limit_scope') or 'total'
    if scope not in ('total', 'body_after_prefix'):
        raise ValueError('Unsupported native auto-compaction scope')
    if scope == 'body_after_prefix' and config.get('model_auto_compact_token_limit') is not None:
        limit = config['model_auto_compact_token_limit']
    return {'model': model, 'model_provider': provider, 'model_reasoning_effort': effort,
            'model_context_window': window, 'model_usable_context_window': usable,
            'model_auto_compact_token_limit': limit, 'model_auto_compact_token_limit_scope': scope}


def model_metadata(model, catalog, route):
    rows = catalog['models']
    if route == 'explicit_endpoint':
        candidate = next((row for row in rows if row['slug'] == model), None)
    else:
        def longest(name):
            matching = [row for row in rows if name.startswith(row['slug'])]
            return max(matching, key=lambda row: len(row['slug'])) if matching else None
        candidate = longest(model)
        if candidate is None and model.count('/') == 1:
            namespace, suffix = model.split('/', 1)
            if re.fullmatch(r'[A-Za-z0-9_-]+', namespace):
                candidate = longest(suffix)
    if candidate is None:
        # Public native synthetic ModelInfo, not a private user model/threshold.
        # models-manager/src/model_info.rs::model_info_from_slug.
        candidate = {'slug': model, 'default_reasoning_level': None,
                     'supported_reasoning_levels': [], 'multi_agent_reasoning_effort': None,
                     'context_window': 272000, 'max_context_window': 272000,
                     'auto_compact_token_limit': None, 'effective_context_window_percent': 95}
        origin = 'native_synthetic'
    else:
        origin = 'active_catalog'
    if any(field not in candidate for field in ('slug', 'default_reasoning_level', 'context_window',
                                                'max_context_window', 'effective_context_window_percent')):
        raise ValueError('Native active catalog lacks required model metadata')
    metadata = {key: candidate.get(key) for key in MODEL_FIELDS}
    metadata['supported_reasoning_levels'] = [
        {'effort': item['effort']} for item in candidate.get('supported_reasoning_levels', [])
        if isinstance(item, dict) and isinstance(item.get('effort'), str)]
    return metadata, fingerprint(candidate), origin


def read_catalog(cwd, projection, *, env=None):
    # Never retain native diagnostics or secret-bearing override arguments.
    with tempfile.TemporaryFile() as output:
        try:
            result = run_native(['codex', *config_flags(projection), 'debug', 'models'], cwd, output, env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError('Native active catalog discovery unavailable (' + type(exc).__name__ + ')') from None
        if result.returncode:
            raise ValueError('Native active catalog discovery failed')
        output.seek(0)
        payload = output.read(16 * 1024 * 1024 + 1)
    if len(payload) > 16 * 1024 * 1024:
        raise ValueError('Native active catalog exceeds 16 MiB')
    try:
        catalog = json.loads(payload)
    except (ValueError, UnicodeError):
        raise ValueError('Native active catalog is not valid JSON') from None
    if (not isinstance(catalog, dict) or not isinstance(catalog.get('models'), list)
            or any(not isinstance(row, dict) or not isinstance(row.get('slug'), str)
                   or not row['slug'] for row in catalog['models'])):
        raise ValueError('Native active catalog has an unsupported shape')
    return catalog


def provider_environment(definition, *, env=None):
    names = {'CODEX_INTERNAL_ORIGINATOR_OVERRIDE', 'CODEX_API_KEY', 'OPENAI_API_KEY',
             'OPENAI_ORGANIZATION', 'OPENAI_PROJECT', 'CODEX_OSS_PORT', 'CODEX_OSS_BASE_URL',
             'GOOGLE_CLOUD_PROJECT', 'GOOGLE_CLOUD_LOCATION', 'GOOGLE_APPLICATION_CREDENTIALS',
             'AWS_PROFILE', 'AWS_REGION', 'AWS_DEFAULT_REGION',
             'OPENAI_FEDERATION_RULE_ID', 'OPENAI_IDENTITY_TOKEN_FILE'}
    if isinstance(definition.get('env_key'), str):
        names.add(definition['env_key'])
    headers = definition.get('env_http_headers') or {}
    if isinstance(headers, dict):
        names.update(value for value in headers.values() if isinstance(value, str))
    env = os.environ if env is None else env
    return fingerprint({name: env.get(name) for name in sorted(names)})


_MISSING = object()
_AMBIGUOUS = object()
_PROVIDER_FIELDS = {'name', 'base_url', 'model_catalog_url', 'env_key', 'env_key_instructions',
    'experimental_bearer_token', 'auth', 'gateway_oauth', 'aws', 'wire_api', 'query_params',
    'http_headers', 'env_http_headers', 'request_max_retries', 'stream_max_retries',
    'stream_idle_timeout_ms', 'websocket_connect_timeout_ms', 'requires_openai_auth',
    'supports_websockets', 'supports_standalone_web_search', 'capabilities', 'project_id',
    'location', 'credential_file', 'include_internal_metadata'}


def _delta(baseline, desired):
    """Only required leaves under native recursive table-merge semantics."""
    if isinstance(baseline, dict) and isinstance(desired, dict):
        result = {}
        for key, value in desired.items():
            difference = _delta(baseline.get(key, _MISSING), value)
            if difference is not _MISSING:
                result[key] = difference
        return result if result else _MISSING
    return _MISSING if baseline == desired else copy.deepcopy(desired)


def _provider_fields(provider, definition):
    # These configured replacements are ignored by the native builtin merge.
    if provider == 'openai':
        return {key: definition[key] for key in ('model_catalog_url',) if key in definition}
    return {} if provider in {'ollama', 'lmstudio'} else copy.deepcopy(definition)


def _header_name(value):
    return (isinstance(value, str) and len(value) <= 65535 and
            re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", value) is not None)


def _header_value(value):
    if not isinstance(value, str):
        return False
    try:
        return all(byte == 9 or byte >= 32 and byte != 127 for byte in value.encode('utf-8'))
    except UnicodeError:
        return False


def _headers(definition, env, *, ignored=(), ambiguous=False):
    static, dynamic = {}, {}
    for name, value in (definition.get('http_headers') or {}).items():
        if _header_name(name) and _header_value(value):
            static.setdefault(name.lower(), set()).add(value)
    for name, variable in (definition.get('env_http_headers') or {}).items():
        value = env.get(variable) if isinstance(variable, str) else None
        if _header_name(name) and _header_value(value) and value.strip():
            dynamic.setdefault(name.lower(), set()).add(value)
    result = {}
    for name in static.keys() | dynamic.keys():
        if name in ignored:
            continue
        values = dynamic.get(name) or static[name]  # Native literal pass, then env pass.
        if len(values) != 1:
            if not ambiguous:
                raise ValueError('Native metadata verification unavailable: ambiguous case-insensitive headers')
            result[name] = _AMBIGUOUS
        else:
            result[name] = next(iter(values))
    return result


def _bridge_variable(env, value, reserved=()):
    while True:
        name = 'DK_CODEX_METADATA_' + secrets.token_hex(16).upper()
        if name not in env and name not in reserved:
            env[name] = value
            return name


def _verify_url_transport(field, value):
    try:
        parsed = urlsplit(value)
        private = bool(parsed.username or parsed.password)
    except (TypeError, ValueError, AttributeError):
        raise ValueError('Native metadata verification unavailable: unsupported ' + field + ' URL') from None
    if private:
        raise ValueError('Native metadata credential transport unavailable: ' + field)


def _safe_provider_delta(provider, baseline, desired, *, static_catalog, configured_model, env):
    baseline, desired = _provider_fields(provider, baseline), _provider_fields(provider, desired)
    difference = _delta(baseline, desired)
    if difference is _MISSING:
        return {}
    unknown = set(difference) - _PROVIDER_FIELDS
    if unknown:
        raise ValueError('Native metadata verification unavailable: unsupported selected provider field')
    if 'base_url' in difference:
        _verify_url_transport('base_url', difference['base_url'])
    reserved = {item for definition in (baseline, desired)
                for item in [definition.get('env_key'), *(definition.get('env_http_headers') or {}).values()]
                if isinstance(item, str)}
    if static_catalog:
        # Static raw rows and picker filtering do not consult inference headers,
        # query values, catalogue endpoints or bearer/AWS credentials. The
        # explicit catalogue selects StaticModelsManager before endpoint use.
        # Default-model filtering only
        # needs the provider command-auth presence, not its literal arguments.
        # Preserve factory name/base-url (including Bedrock GovCloud selection).
        for key in ('http_headers', 'env_http_headers', 'experimental_bearer_token', 'env_key',
                    'query_params', 'model_catalog_url', 'aws'):
            difference.pop(key, None)
        if configured_model:
            difference.pop('auth', None)
        elif isinstance(difference.get('auth'), dict):
            difference['auth'].pop('args', None)
            if not difference['auth']:
                difference.pop('auth')
        return difference
    if 'model_catalog_url' in difference:
        raise ValueError('Native metadata credential transport unavailable: model_catalog_url')
    for field in ('query_params',):
        if difference.get(field):
            raise ValueError('Native metadata credential transport unavailable: ' + field)
    auth = difference.get('auth')
    if isinstance(auth, dict) and auth.get('args'):
        raise ValueError('Native metadata credential transport unavailable: auth.args')
    aws = difference.get('aws')
    if isinstance(aws, dict):
        for field in ('credential_export', 'auth_refresh'):
            if isinstance(aws.get(field), dict) and aws[field].get('args'):
                raise ValueError('Native metadata credential transport unavailable: aws.' + field + '.args')
    # Raw header values are never copied just because a different leaf changed.
    if 'http_headers' in difference or 'env_http_headers' in difference:
        ignored = {'authorization'} if (desired.get('env_key') is not None or
                                           desired.get('experimental_bearer_token') is not None) else set()
        wanted = _headers(desired, env, ignored=ignored)
        existing = _headers(baseline, env, ignored=ignored, ambiguous=True)
        difference.pop('http_headers', None)
        difference.pop('env_http_headers', None)
        for name in wanted.keys() | existing.keys():
            value = wanted.get(name, _MISSING)
            if value == existing.get(name, _MISSING):
                continue
            if provider in {'amazon-bedrock', 'amazon-bedrock-runtime', 'gcp'}:
                raise ValueError('Native metadata credential transport unavailable: builtin http_headers')
            literal_names = {key for key in (baseline.get('http_headers') or {})
                             if _header_name(key) and key.lower() == name}
            env_names = {key for key in (baseline.get('env_http_headers') or {})
                         if _header_name(key) and key.lower() == name}
            if value is not _MISSING and value.strip():
                variable = _bridge_variable(env, value, reserved)
                # Native recursive merge retains inherited dynamic case variants.
                for key in env_names | {name}:
                    difference.setdefault('env_http_headers', {})[key] = variable
            else:
                # Blank literals are present headers; env variables would be
                # skipped. A nonsecret invalid literal preserves absent headers.
                literal = '\r' if value is _MISSING else value
                for key in literal_names | {name}:
                    difference.setdefault('http_headers', {})[key] = literal
                variable = _bridge_variable(env, '', reserved)
                for key in env_names:
                    difference.setdefault('env_http_headers', {})[key] = variable
    if 'experimental_bearer_token' in difference:
        token = desired.get('experimental_bearer_token')
        if desired.get('env_key') is not None:
            difference.pop('experimental_bearer_token')  # Native env_key wins, including its error.
        elif isinstance(token, str) and token.strip():
            difference.pop('experimental_bearer_token')
            difference['env_key'] = _bridge_variable(env, token, reserved)
    return difference


def metadata_projection(config, provider, definition, static):
    baseline = copy.deepcopy(config.get('_helper_baseline', {}))
    policy = config.get('_requirements', {})
    baseline['model_provider'] = policy.get('modelProvider') or baseline.get('model_provider') or 'openai'
    desired = {key: config[key] for key in METADATA_KEYS
               if key != 'model_providers' and config.get(key) is not None}
    desired['model_provider'] = provider
    projection = _delta(baseline, desired)
    projection = {} if projection is _MISSING else projection
    projection = merge(config.get('_helper_discovery', {}), projection)
    for field in ('openai_base_url', 'chatgpt_base_url'):
        if field in projection:
            _verify_url_transport(field, projection[field])
    baseline_providers = {**baseline.get('model_providers', {}), **(policy.get('modelProviders') or {})}
    env = dict(os.environ)
    # Required policy definitions already remain native-file-backed. A profile
    # definition cannot override that exact replacement in the helper either.
    delta = _safe_provider_delta(provider, baseline_providers.get(provider, {}), definition,
                                static_catalog=static, configured_model=config.get('model') is not None, env=env)
    if delta:
        projection['model_providers'] = {provider: delta}
    return projection, env


def native_metadata(config, cwd):
    policy = config.get('_requirements', {})
    provider = policy.get('modelProvider') or config.get('model_provider') or 'openai'
    providers = {**config.get('model_providers', {}), **(policy.get('modelProviders') or {})}
    definition = providers.get(provider, {})
    if not isinstance(definition, dict):
        raise ValueError('Native selected provider definition has an unsupported shape')
    routing = ({key: definition[key] for key in ('model_catalog_url',) if key in definition}
               if provider == 'openai' else {} if provider in {'ollama', 'lmstudio'} else definition)
    static = bool(policy.get('modelCatalogJson') or config.get('model_catalog_json')) or (
        provider in {'amazon-bedrock', 'amazon-bedrock-runtime', 'gcp'} or
        routing.get('name') in {'Amazon Bedrock', 'Amazon Bedrock Runtime', 'Google Cloud Vertex AI'})
    originator = os.environ.get('CODEX_INTERNAL_ORIGINATOR_OVERRIDE')
    if not static and originator in {'codex_work_cca', 'codex_work_web', 'codex_work_desktop',
                                     'codex_work_mobile', 'chatgpt_cca'}:
        raise ValueError('Effective Codex metadata verification is unavailable for the selected '
                         'internal originator partition; use an ordinary Codex CLI environment '
                         'or an explicit native model_catalog_json snapshot')
    projection, child_env = metadata_projection(config, provider, definition, static)
    original_environment = provider_environment(definition, env=child_env)
    catalog = read_catalog(cwd, projection, env=child_env)
    # Pin only the metadata helper, not actual exec. Shared native list policy
    # sorts/auth-filters/default-marks this exact immutable raw catalogue.
    model = config.get('model')
    selection = 'configured'
    if model is None:
        # Standalone app-server does not enable CODEX_API_KEY, whereas exec and
        # debug models do. Avoid that differently authenticated helper entirely
        # when native policy accepts the key: apply the exact native picker
        # projection to the captured raw rows instead.
        allowed = policy.get('allowedLoginMethods')
        api_key = bool(os.environ.get('CODEX_API_KEY', '').strip()) and (
            config.get('forced_login_method') in (None, 'api') and
            (allowed is None or 'api' in allowed))
        workload = any(os.environ.get(key) is not None for key in
                       ('OPENAI_FEDERATION_RULE_ID', 'OPENAI_IDENTITY_TOKEN_FILE'))
        if api_key and not workload and routing.get('auth') is None:
            available = sorted(catalog['models'], key=lambda item: item.get('priority', 0))
            available = [item for item in available if item.get('supported_in_api', True)]
            chosen = next((item for item in available if item.get('visibility') == 'list'),
                          available[0] if available else None)
            row = {'model': chosen['slug']} if chosen else None
            selection = 'native_api_key_catalog_default'
        else:
            with tempfile.TemporaryDirectory(prefix='dk-codex-catalog-') as temporary:
                path = Path(temporary) / 'catalog.json'
                path.write_text(json.dumps(catalog))
                path.chmod(0o600)
                pinned = {**projection, 'model_catalog_json': str(path)}
                if fingerprint(read_catalog(cwd, pinned, env=child_env)) != fingerprint(catalog):
                    raise ValueError('Native metadata helper did not preserve the captured catalog snapshot')
                _, _, listing = read_config(cwd, pinned, models=True, env=child_env)
            if not isinstance(listing, dict) or not isinstance(listing.get('data'), list):
                raise ValueError('Native model/list returned an unsupported shape')
            rows = listing['data']
            if any(not isinstance(row, dict) or not isinstance(row.get('model'), str)
                   or not isinstance(row.get('isDefault'), bool) for row in rows):
                raise ValueError('Native model/list lacks model/default metadata')
            row = next((row for row in rows if row['isDefault']), rows[0] if rows else None)
            selection = 'native_catalog_default'
        if row is None:
            raise ValueError('Native catalog provides no selectable default model')
        model = row['model']
        if model not in {entry['slug'] for entry in catalog['models']}:
            raise ValueError('Native default selection does not match the captured catalog snapshot')
    if not isinstance(model, str) or not model:
        raise ValueError('Native selected model is not a nonempty identifier')
    route = 'static' if static else 'explicit_endpoint' if routing.get('model_catalog_url') else 'default'
    metadata, row_hash, origin = model_metadata(model, catalog, route)
    effective = effective_settings(config, model, provider, metadata)
    if fingerprint(read_catalog(cwd, projection, env=child_env)) != fingerprint(catalog):
        raise ValueError('Native active catalog changed during profile resolution; refresh preparation')
    if provider_environment(definition) != original_environment:
        raise ValueError('Codex governing environment changed during profile resolution; refresh preparation')
    definition_input = {'native_definition_version': config.get('_native_version'),
        'builtin_or_selected_id': provider, 'configured': definition,
        'openai_base_url': config.get('openai_base_url'),
        'auth': {key: config.get(key) for key in METADATA_KEYS if key not in {'model_providers', 'model_catalog_json'}},
        'requirements': policy}
    projected_input = copy.deepcopy(definition_input)
    projected_input['configured'] = _config_identity({'model_providers': {provider: definition}},
        Path(cwd), {})['model_providers'][provider]
    projected_input['auth'] = _config_identity(projected_input['auth'], Path(cwd), {})
    projected_input['requirements'] = _config_identity(policy, Path(cwd), {}, requirements=True)
    return {'_provider_comparison': {'exact': fingerprint(definition_input), 'comparison': fingerprint(projected_input)},
            'effective_settings': effective,
            'expected_observations': {'model': model, 'model_provider': provider,
                'model_reasoning_effort': config.get('model_reasoning_effort') or 'none',
                'model_context_window': effective['model_context_window'],
                'model_auto_compact_token_limit': effective['model_auto_compact_token_limit']},
            'catalog': {'sha256': fingerprint(catalog), 'selected_row_sha256': row_hash,
                        'metadata': metadata, 'origin': origin, 'route': route, 'selection': selection},
            'provider': {'id': provider, 'definition_sha256': fingerprint(definition_input),
                         'environment_sha256': original_environment}}


def resolve_profile(name, cwd=None):
    """Resolve selected native inputs and active metadata without a model turn.

    Metadata selection uses an immutable catalogue capture. Bounded rereads bind
    authentic validation, discovery and execution checks; they are not an atomic
    guarantee against inputs changing away and back between observations.
    """
    cwd = Path(cwd or Path.cwd()).resolve()
    before, config = selected_layers(name, cwd)
    validate_profile(name, cwd)
    native = native_metadata(config, cwd)
    after, _ = selected_layers(name, cwd)
    if before != after:
        raise ValueError('Codex governing inputs changed during profile resolution; refresh preparation')
    provider_comparison = native.pop('_provider_comparison', None)
    value = {**before, **native}
    value['identity_evidence']['provider'] = provider_comparison
    value['identity_evidence']['exact_sha256'] = fingerprint(
        {key: item for key, item in value.items() if key != 'identity_evidence'})
    return value


def verify_profile(resolved, cwd=None):
    binding = resolved['binding']
    if 'codex_profile' not in binding:
        return
    if resolved.get('executor') != 'codex':
        raise ValueError('codex_profile requires a native Codex executor')
    actual = resolve_profile(binding['codex_profile'], cwd)
    if actual != resolved.get('codex_execution'):
        raise ValueError('Codex execution settings changed for the selected profile; refresh preparation')


def observed_settings(stderr):
    """Only inspect the native CLI header, never prompt/model-authored text."""
    header = (stderr or '').split('\nuser\n', 1)[0]
    fields = {'model': 'model', 'provider': 'model_provider',
              'reasoning effort': 'model_reasoning_effort',
              'context window': 'model_context_window',
              'model context window': 'model_context_window',
              'auto compact token limit': 'model_auto_compact_token_limit'}
    observed = {}
    for label, field in fields.items():
        match = re.search(r'(?m)^' + re.escape(label) + r': ([^\r\n]+)\s*$', header)
        if match:
            value = match[1].strip()
            if field in {'model_context_window', 'model_auto_compact_token_limit'}:
                if not value.replace(',', '').isdigit():
                    continue
                value = int(value.replace(',', ''))
            observed[field] = value
    return observed


def verify_observed(expected, observed):
    projection = expected.get('expected_observations', expected.get('settings', {}))
    for field, value in observed.items():
        selected = projection.get(field)
        if field in projection and selected != value:
            raise ValueError(f'Codex execution setting mismatch for {field}: '
                             f'prepared {selected!r}, observed {value!r}')
