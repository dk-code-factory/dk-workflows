"""Private Codex profile provenance, resolved without launching a model turn."""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import tempfile
import time
import tomllib

# Config/read reports unresolved CLI/model defaults as null. Preserve that fact;
# never substitute a model choice or derive a context threshold ourselves.
SETTINGS = ('model', 'model_provider', 'model_reasoning_effort', 'model_reasoning_summary',
            'model_verbosity', 'model_context_window', 'model_auto_compact_token_limit',
            'model_auto_compact_token_limit_scope', 'model_max_output_tokens',
            'model_supports_reasoning_summaries', 'model_instructions_file',
            'model_catalog_json', 'compact_prompt')
LOWER = {'packagedDefaults', 'mdm', 'system', 'enterpriseManaged'}
HIGHER = {'project', 'sessionFlags', 'legacyManagedConfigTomlFromFile',
          'legacyManagedConfigTomlFromMdm'}


def read_config(cwd):
    """Ask the native loader for enabled layers and their precedence/origins."""
    deadline = time.monotonic() + 30
    with tempfile.TemporaryFile() as diagnostics:
        process = subprocess.Popen(['codex', 'app-server'], cwd=cwd, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=diagnostics)
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
                                    raise ValueError('Codex config/read failed: ' +
                                                     str(event['error'].get('message', event['error'])))
                                return event['result']
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise ValueError('Codex config/read timed out after 30 seconds')
                        block = os.read(process.stdout.fileno(), 65536)
                        if not block:
                            diagnostics.seek(0)
                            raise ValueError('Codex config/read ended without a response: ' +
                                             diagnostics.read(4096).decode(errors='replace'))
                        buffered.extend(block)
                        if len(buffered) > 16 * 1024 * 1024:
                            raise ValueError('Codex config/read response exceeds 16 MiB')
                initialized = request(1, 'initialize', {
                    'clientInfo': {'name': 'dk_workflows_profile', 'version': '1'},
                    'capabilities': {'experimentalApi': True}})
                if not isinstance(initialized, dict):
                    raise ValueError('Codex initialize returned an unsupported response')
                process.stdin.write(b'{"method":"initialized","params":{}}\n')
                process.stdin.flush()
                result = request(2, 'config/read', {'cwd': str(cwd), 'includeLayers': True})
                return result, initialized.get('userAgent')
        finally:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass  # Preserve the original startup/protocol failure.
            process.stdout.close()
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


def resolve_profile(name, cwd=None):
    """Insert the documented profile-v2 user layer into native resolved layers.

    App-server does not accept --profile. Its config/read response supplies the
    actual trusted project layers and precedence, rather than us searching for
    project config files or assuming they are enabled. Profile-v2 is loaded after
    the base user config and before project/session/legacy managed overrides.
    """
    if (not isinstance(name, str) or not name.strip() or name in {'.', '..'}
            or any(char in name for char in ('/', '\\', '\0'))):
        raise ValueError('codex_profile must name a profile in CODEX_HOME')
    home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).resolve()
    path = home / (name + '.config.toml')
    try:
        content = path.read_bytes()
        profile = tomllib.loads(content.decode())
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f'Cannot read selected Codex profile {name!r} at {path}: {exc}') from exc
    # These fields can change which project layers the native loader trusts or
    # discovers. A base config/read cannot prove their selected-profile result.
    if {'projects', 'project_root_markers', 'profiles', 'profile'} & profile.keys():
        raise ValueError('Codex profile changes configuration-layer selection; effective settings '
                         'cannot be verified through config/read')
    cwd = Path(cwd or Path.cwd()).resolve()
    result, agent = read_config(cwd)
    if (not isinstance(result, dict) or not isinstance(result.get('config'), dict)
            or not isinstance(result.get('origins'), dict) or not isinstance(result.get('layers'), list)):
        raise ValueError('Codex config/read did not return effective settings and layers')
    # The profile-v2 layer format was introduced in 0.134. Earlier native loaders
    # must not interpret a separate file as an unsupported legacy profile table.
    version = re.search(r'(?:/|\s)(\d+)\.(\d+)\.(\d+)', agent if isinstance(agent, str) else '')
    if not version or tuple(map(int, version.groups())) < (0, 134, 0):
        raise ValueError('codex_profile requires Codex 0.134+ profile-v2 config/read support')
    layers = result['layers']
    user = [i for i, layer in enumerate(layers)
            if isinstance(layer, dict) and layer.get('name', {}).get('type') == 'user']
    if len(user) != 1:
        raise ValueError('Codex config/read returned ambiguous user/profile layers')
    index = user[0]
    for i, layer in enumerate(layers):
        if (not isinstance(layer, dict) or not isinstance(layer.get('name'), dict)
                or not isinstance(layer.get('config'), dict) or not isinstance(layer.get('version'), str)):
            raise ValueError('Codex config/read returned an unsupported layer shape')
        kind = layer['name'].get('type')
        allowed = HIGHER if i < index else LOWER if i > index else {'user'}
        if kind not in allowed:
            raise ValueError('Codex config/read returned unsupported layer precedence: ' + str(kind))
    base = layers[index]
    if (Path(base['name'].get('file', '')).resolve() != (home / 'config.toml').resolve()
            or base['name'].get('profile') is not None or base.get('disabledReason')):
        raise ValueError('Codex config/read did not resolve the expected base user configuration')
    settings = {field: result['config'].get(field) for field in SETTINGS}
    origins = {field: result['origins'].get(field) for field in SETTINGS}
    source = {'name': {'type': 'user', 'file': str(path), 'profile': name},
              'version': 'sha256:' + hashlib.sha256(content).hexdigest()}
    for field in SETTINGS:
        if field not in profile:
            continue
        # config/read layers are highest-first. Disabled untrusted project layers
        # remain evidence, but cannot override the selected profile.
        overrides = [layer for layer in layers[:index]
                     if not layer.get('disabledReason') and field in layer['config']]
        if not overrides:
            settings[field], origins[field] = profile[field], source
    # TOML keys governing these settings are scalars; don't silently accept a
    # table/array and claim it describes an effective model configuration.
    for field, value in settings.items():
        if value is not None and not isinstance(value, (str, int, bool)):
            raise ValueError('Unsupported Codex execution setting: ' + field)
    references = {}
    for field in ('model_catalog_json', 'model_instructions_file'):
        value = settings[field]
        if value is None:
            continue
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f'Codex {field} requires an absolute path to verify governing file contents')
        target = Path(value)
        try:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            real = str(target.resolve(strict=True))
        except OSError as exc:
            raise ValueError(f'Cannot read governing Codex {field} file {target}: {exc}') from exc
        references[field] = {'path': value, 'resolved_path': real, 'sha256': digest}
    return {'profile': name, 'profile_source': source, 'codex_version': version.group(0).strip('/ '),
            'settings': settings, 'origins': origins, 'setting_sources': references,
            'unresolved_defaults': [field for field, value in settings.items() if value is None]}


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
    for field, value in observed.items():
        selected = expected['settings'].get(field)
        if selected is not None and selected != value:
            raise ValueError(f'Codex execution setting mismatch for {field}: '
                             f'prepared {selected!r}, observed {value!r}')
