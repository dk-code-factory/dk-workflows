"""Host execution shared by review, preparation, Decisions and drafting."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from shared.bindings import current_host, resolve
from shared.reviewer_telemetry import capture


class ProviderExhausted(RuntimeError):
    """Explicit provider usage exhaustion; authentication failures remain blocking."""


def provider_timeout():
    """Read the per-invocation provider deadline in seconds."""
    value = os.environ.get('DK_PROVIDER_TIMEOUT_SECONDS', '1200')
    try:
        seconds = int(value)
    except ValueError:
        raise ValueError('DK_PROVIDER_TIMEOUT_SECONDS must be a positive integer') from None
    if seconds <= 0:
        raise ValueError('DK_PROVIDER_TIMEOUT_SECONDS must be a positive integer')
    return seconds


def execute_json(prompt, checkout, *, validate=None, executor=None, correction_instructions=''):
    """Correct model JSON once, without retrying provider failures or coercing data."""
    original_prompt = prompt
    for attempt in range(2):
        raw = (executor or execute)(prompt, checkout, attempt=attempt)
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError('expected one JSON object')
            if validate is not None:
                validate(result)
            return result
        except (ValueError, RuntimeError) as exc:
            if attempt:
                raise ValueError('Model JSON invalid after one correction: ' + str(exc)) from exc
            prompt = (original_prompt + '\n\nCorrection: ' + str(exc) +
                      '\nReturn the complete result as exactly one JSON object without Markdown fences. '
                      'Follow the declared field types and preserve the same evidence and constraints. '
                      'Do not invent missing evidence or grant publication authority. ' + correction_instructions +
                      '\nPrior response:\n' + raw)


def provider_failure(detail):
    authentication = re.search(r'authentication failed|not authenticated|unauthorized|invalid credentials|'
                               r'(?:invalid|expired|revoked) (?:oauth |access |auth(?:entication)? )?token', detail, re.I)
    exhausted = re.search(r'usage limit (?:reached|exceeded)|(?:hit|reached) your (?:session )?limit|'
                         r'(?:out of|no|insufficient) (?:credits|tokens)|tokens? exhausted', detail, re.I)
    if exhausted and not authentication:
        raise ProviderExhausted(detail)
    raise RuntimeError(detail)


def managed_update(resolved, env, phase, token=None):
    """Run owned lifecycle bookkeeping outside the read-only model sandbox.

    External providers have no updater obligation and are never probed for scripts.
    Standalone plugins still ship and execute their own self-contained updater.
    """
    try:
        return update_process(resolved, env, phase, token)
    except Exception as exc:
        print('Plugin lifecycle unavailable: ' + str(exc), file=sys.stderr)
        return None


def update_process(resolved, env, phase, token=None):
    if not resolved:
        return None
    root = Path(resolved['root'])
    manifest = json.loads((root / '.codex-plugin/plugin.json').read_text()) if (root / '.codex-plugin/plugin.json').is_file() else {}
    if manifest.get('repository') != 'https://github.com/dk-code-factory/' + manifest.get('name', ''):
        return None
    script = root / 'scripts/update.py'
    if not script.is_file():
        return None
    argv = [sys.executable, str(script), phase, '--host', resolved['executor']]
    if token:
        argv += ['--token', token]
    proc = subprocess.run(argv, text=True, capture_output=True, env=env, timeout=120)
    try:
        receipt = json.loads(proc.stdout)
    except ValueError:
        print('Plugin lifecycle unavailable: ' + (proc.stderr.strip() or 'invalid response'), file=sys.stderr)
        return None
    if receipt.get('message'):
        print(receipt['message'], file=sys.stderr)
    return receipt


def execute(prompt, checkout, *, resolved=None, attempt=0, timeout=None):
    timeout = provider_timeout() if timeout is None else timeout
    host = resolved['executor'] if resolved else current_host()
    if host not in ('codex', 'claude'):
        raise ValueError('Model execution requires a native host')
    env = dict(os.environ, DK_HOST=host)
    stack = json.loads(env.get('DK_BINDING_STACK', '[]'))
    if resolved:
        identity = resolved['binding']['skill']
        if identity in stack:
            raise ValueError('Recursive provider invocation: ' + identity)
        stack.append(identity)
        env['DK_BINDING_STACK'] = json.dumps(stack)
        prompt = (f'Apply the installed procedure at {resolved["procedure"]}. '
                  f'Its plugin root is {resolved["root"]}. Read linked resources.\n\n' + prompt)
    receipt = managed_update(resolved, env, 'entry')
    token = receipt.get('token') if receipt else None
    if token:
        env['DK_UPDATE_PARENT'] = token
        env['DK_UPDATE_MANAGED_RECEIPT'] = json.dumps(receipt)
        prompt = ('The host executor has called this plugin\'s lifecycle entry and will call completion '
                  'after your final response. Use DK_UPDATE_MANAGED_RECEIPT; do not duplicate those calls.\n' + prompt)
    try:
        return execute_process(prompt, checkout, host, env, resolved, attempt, timeout)
    finally:
        if token:
            managed_update(resolved, env, 'complete', token)


def execute_process(prompt, checkout, host, env, resolved, attempt, timeout):
    with tempfile.TemporaryDirectory(prefix='dk-provider-') as temp:
        output = Path(temp) / 'result.txt'
        if host == 'codex':
            argv = ['codex', 'exec', '--ephemeral', '--sandbox', 'read-only', '--skip-git-repo-check',
                    '-C', str(checkout), '-o', str(output), '-']
        else:
            argv = ['claude', '--permission-mode', 'plan', '--no-session-persistence',
                    '--output-format', 'json', '--disallowedTools', 'Edit,Write,NotebookEdit', '-p']
            if resolved:
                argv += ['--plugin-dir', resolved['root']]
        proc = capture(subprocess.run, argv, input=prompt, text=True, capture_output=True,
                       cwd=checkout, env=env, timeout=timeout, telemetry_prompt=prompt,
                       telemetry_attempt=attempt, telemetry_provider=host, telemetry_output=output)
        if proc.returncode:
            provider_failure('\n'.join(filter(None, (proc.stderr, proc.stdout))).strip() or f'{host} execution failed')
        if host == 'codex':
            if not output.is_file():
                raise RuntimeError('Codex did not produce a final response')
            return output.read_text()
        envelope = json.loads(proc.stdout)
        if envelope.get('is_error') or envelope.get('subtype') != 'success':
            provider_failure(envelope.get('result') or json.dumps(envelope))
        result = envelope.get('result')
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError('Claude did not produce a final response')
        return result


def invoke(binding, handoff_path, checkout, prompt, *, attempt=0):
    resolved = resolve(binding)
    if resolved['executor'] == 'command':
        argv = [part.replace('{handoff}', str(handoff_path)) for part in resolved['binding']['command']]
        stack = json.loads(os.environ.get('DK_BINDING_STACK', '[]'))
        if resolved['identity'] in stack:
            raise ValueError('Recursive command provider invocation')
        env = dict(os.environ, DK_BINDING_STACK=json.dumps([*stack, resolved['identity']]),
                   DK_PROVIDER_ATTEMPT=str(attempt))
        # The immutable evidence handoff stays in argv. stdin carries the output
        # instructions and, on retry, the validation error and prior response.
        proc = subprocess.run(argv, input=prompt, cwd=checkout, text=True, capture_output=True,
                              env=env, timeout=provider_timeout())
        if proc.returncode:
            provider_failure('\n'.join(filter(None, (proc.stderr, proc.stdout))).strip() or 'Command provider failed')
        return proc.stdout
    return execute(prompt, checkout, resolved=resolved, attempt=attempt)
