"""Host execution shared by review, preparation, Decisions and drafting."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from shared.bindings import current_host, resolve
from shared.reviewer_telemetry import capture


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


def execute(prompt, checkout, *, resolved=None, attempt=0, timeout=600):
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
            raise RuntimeError((proc.stderr or proc.stdout).strip() or f'{host} execution failed')
        if host == 'codex':
            if not output.is_file():
                raise RuntimeError('Codex did not produce a final response')
            return output.read_text()
        envelope = json.loads(proc.stdout)
        if envelope.get('is_error') or envelope.get('subtype') != 'success':
            raise RuntimeError(envelope.get('result') or 'Claude execution incomplete')
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
        proc = subprocess.run(argv, input=prompt, cwd=checkout, text=True, capture_output=True, env=env, timeout=600)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or 'Command provider failed')
        return proc.stdout
    return execute(prompt, checkout, resolved=resolved, attempt=attempt)
