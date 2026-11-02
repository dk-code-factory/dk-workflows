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
import signal
import subprocess
import tempfile
import time
import tomllib

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
        return '{' + ','.join(json.dumps(key) + '=' + toml_value(item)
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


def stop_native(process):
    """Drain this isolated native helper and any credential-command descendants."""
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    finally:
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def run_native(argv, cwd, stdout):
    process = subprocess.Popen(argv, cwd=cwd, stdout=stdout, stderr=subprocess.DEVNULL,
                               start_new_session=(os.name == 'posix'))
    try:
        returncode = process.wait(timeout=30)
        return subprocess.CompletedProcess([], returncode)
    finally:
        stop_native(process)


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


def read_config(cwd, overrides=None, *, models=False, requirements=False):
    """Ask the native loader for enabled layers and their precedence/origins."""
    deadline = time.monotonic() + 30
    process = subprocess.Popen(['codex', *config_flags(overrides or {}), 'app-server'], cwd=cwd, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=(os.name == 'posix'))
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
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError('Codex config/read returned a non-object event')
                        if event.get('id') == identity:
                            if 'error' in event:
                                raise ValueError('Codex native metadata request failed: ' + method)
                            if 'result' not in event:
                                raise ValueError('Codex returned a malformed native metadata response')
                            return event['result']
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ValueError('Codex config/read timed out after 30 seconds')
                    block = os.read(process.stdout.fileno(), 65536)
                    if not block:
                        raise ValueError('Codex native metadata ended without a response')
                    buffered.extend(block)
                    if len(buffered) > 16 * 1024 * 1024:
                        raise ValueError('Codex config/read response exceeds 16 MiB')
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
    users = [i for i, layer in enumerate(layers)
             if isinstance(layer, dict) and layer.get('name', {}).get('type') == 'user']
    if len(users) != 1:
        raise ValueError('Codex config/read returned ambiguous user/profile layers')
    index = users[0]
    for i, layer in enumerate(layers):
        if (not isinstance(layer, dict) or not isinstance(layer.get('name'), dict)
                or not isinstance(layer.get('config'), dict) or not isinstance(layer.get('version'), str)):
            raise ValueError('Codex config/read returned an unsupported layer shape')
        allowed = HIGHER if i < index else LOWER if i > index else {'user'}
        if layer['name'].get('type') not in allowed:
            raise ValueError('Codex config/read returned unsupported layer precedence')
    base = layers[index]
    if (Path(base['name'].get('file', '')).resolve() != (home / 'config.toml').resolve()
            or base['name'].get('profile') is not None or base.get('disabledReason')):
        raise ValueError('Codex config/read did not resolve the expected base user configuration')
    # Native normalizes each file before merging. Preserve lexical selected
    # pathname semantics even when the profile itself is a symlink.
    for field in ('model_catalog_json', 'model_instructions_file'):
        overridden = any(not layer.get('disabledReason') and field in layer['config']
                         and layer['name']['type'] != 'sessionFlags' for layer in layers[:index])
        if field in profile and not overridden:
            profile[field] = str(native_path(profile[field], path.parent))
    merged, origins = {}, {}
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
    merged['_requirements'] = policy
    merged['_native_version'] = version.group(0).strip('/ ')
    settings = {field: merged.get(field) for field in SETTINGS}
    if policy.get('modelCatalogJson') is not None:
        settings['model_catalog_json'] = policy['modelCatalogJson']
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
    return {'profile': name, 'profile_source': source, 'codex_version': version.group(0).strip('/ '),
            'settings': settings, 'origins': {field: origins.get(field) for field in SETTINGS},
            'setting_sources': references, 'layers': provenance,
            'requirements_sha256': fingerprint(policy)}, merged


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
        # models-manager/src/model_info.rs::model_info_from_slug (94-145).
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


def read_catalog(cwd, projection):
    # Never retain native diagnostics or secret-bearing override arguments.
    with tempfile.TemporaryFile() as output:
        try:
            result = run_native(['codex', *config_flags(projection), 'debug', 'models'], cwd, output)
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


def provider_environment(definition):
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
    return fingerprint({name: os.environ.get(name) for name in sorted(names)})


def native_metadata(config, cwd):
    projection = {key: config[key] for key in METADATA_KEYS if config.get(key) is not None}
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
    catalog = read_catalog(cwd, projection)
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
                if fingerprint(read_catalog(cwd, pinned)) != fingerprint(catalog):
                    raise ValueError('Native metadata helper did not preserve the captured catalog snapshot')
                _, _, listing = read_config(cwd, pinned, models=True)
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
    if fingerprint(read_catalog(cwd, projection)) != fingerprint(catalog):
        raise ValueError('Native active catalog changed during profile resolution; refresh preparation')
    return {'effective_settings': effective,
            'expected_observations': {'model': model, 'model_provider': provider,
                'model_reasoning_effort': config.get('model_reasoning_effort') or 'none',
                'model_context_window': effective['model_context_window'],
                'model_auto_compact_token_limit': effective['model_auto_compact_token_limit']},
            'catalog': {'sha256': fingerprint(catalog), 'selected_row_sha256': row_hash,
                        'metadata': metadata, 'origin': origin, 'route': route, 'selection': selection},
            'provider': {'id': provider, 'definition_sha256': fingerprint({
                'native_definition_version': config.get('_native_version'),
                'builtin_or_selected_id': provider, 'configured': definition,
                'openai_base_url': config.get('openai_base_url'),
                'auth': {key: config.get(key) for key in METADATA_KEYS if key not in {
                    'model_providers', 'model_catalog_json'}},
                'requirements': policy}), 'environment_sha256': provider_environment(definition)}}


def resolve_profile(name, cwd=None):
    """Resolve selected native inputs and active metadata without a model turn.

    Metadata selection uses an immutable catalogue capture. Bounded rereads bind
    authentic validation, discovery and execution checks; they are not an atomic
    guarantee against inputs changing away and back between observations.
    """
    cwd = Path(cwd or Path.cwd()).absolute()
    before, config = selected_layers(name, cwd)
    validate_profile(name, cwd)
    native = native_metadata(config, cwd)
    after, _ = selected_layers(name, cwd)
    if before != after:
        raise ValueError('Codex governing inputs changed during profile resolution; refresh preparation')
    return {**before, **native}


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
