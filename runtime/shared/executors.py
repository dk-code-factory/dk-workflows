"""Host execution shared by review, preparation, Decisions and drafting."""
import base64
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event, RLock
from shared.bindings import current_host, resolve
from shared.diagnostics import Diagnostic, task
from shared.private_state import write
from shared.reviewer_telemetry import capture
from shared.workflow_storage import allocate


class ProviderExhausted(RuntimeError):
    """Explicit provider usage exhaustion; authentication failures remain blocking."""


_CANCELLATION = ContextVar('provider_cancellation', default=None)


class ProviderCancelled(RuntimeError):
    """The owning review was interrupted before transport completed."""


class ProviderCancellation:
    def __init__(self):
        self.lock = RLock()
        self.cancelled = Event()

    def cancel(self):
        with self.lock:
            self.cancelled.set()

    def check(self):
        if self.cancelled.is_set():
            raise ProviderCancelled('Review provider execution interrupted')


@contextmanager
def provider_cancellation():
    scope = ProviderCancellation()
    token = _CANCELLATION.set(scope)
    try:
        yield scope
    finally:
        _CANCELLATION.reset(token)


def run_provider(argv, **kwargs):
    """Abort only this invocation's owned transport, leaving lifecycle helpers alone."""
    scope = _CANCELLATION.get()
    if scope is None:
        return subprocess.run(argv, **kwargs)
    request = kwargs.pop('input', None)
    timeout = kwargs.pop('timeout', None)
    if kwargs.pop('capture_output', False):
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if request is not None:
        kwargs['stdin'] = subprocess.PIPE
    # Cancellation and spawn have one ordering; a late correction cannot escape.
    with scope.lock:
        scope.check()
        process = subprocess.Popen(argv, **kwargs)
    buffers = {}
    error = None
    def partial(stream):
        data = buffers.get(stream)
        return bytes(data) if data else None
    def output(stream):
        if stream is None:
            return None
        data = bytes(buffers[stream])
        if getattr(stream, 'encoding', None):
            return data.decode(stream.encoding, stream.errors).replace('\r\n', '\n').replace('\r', '\n')
        return data
    try:
        deadline = time.monotonic() + timeout if timeout is not None else None
        payload = request if request is not None else b''
        if request is not None and getattr(process.stdin, 'encoding', None):
            payload = request.encode(process.stdin.encoding, process.stdin.errors)
        payload, offset = memoryview(payload), 0
        with selectors.DefaultSelector() as selector:
            def close(stream):
                selector.unregister(stream)
                try:
                    stream.close()
                except BrokenPipeError:
                    pass
            for stream in (process.stdout, process.stderr):
                if stream:
                    buffers[stream] = bytearray()
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ)
            if process.stdin:
                if payload:
                    os.set_blocking(process.stdin.fileno(), False)
                    selector.register(process.stdin, selectors.EVENT_WRITE)
                else:
                    process.stdin.close()
            while selector.get_map() or process.poll() is None:
                scope.check()
                remaining = deadline - time.monotonic() if deadline is not None else .1
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout, output=partial(process.stdout),
                                                    stderr=partial(process.stderr))
                interval = min(.1, remaining)
                if not selector.get_map():
                    try:
                        process.wait(timeout=interval)
                    except subprocess.TimeoutExpired:
                        pass
                    continue
                for key, mask in selector.select(interval):
                    stream = key.fileobj
                    try:
                        if mask & selectors.EVENT_WRITE:
                            try:
                                offset += os.write(stream.fileno(), payload[offset:offset + 32768])
                            except BrokenPipeError:
                                close(stream)
                            else:
                                if offset == len(payload):
                                    close(stream)
                        else:
                            data = os.read(stream.fileno(), 32768)
                            if data:
                                buffers[stream].extend(data)
                            else:
                                close(stream)
                    except BlockingIOError:
                        pass
        return subprocess.CompletedProcess(argv, process.returncode,
                                           output(process.stdout), output(process.stderr))
    except BaseException as exc:
        error = exc
        if isinstance(exc, ProviderCancelled):
            exc.stdout = partial(process.stdout)
            exc.stderr = partial(process.stderr)
        raise
    finally:
        if process.poll() is None:
            process.kill()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                stream.close()
        process.wait()
        if isinstance(error, ProviderCancelled):
            error.returncode = process.returncode


def provider_timeout():
    """Read the per-invocation provider deadline in seconds."""
    value = os.environ.get('DK_PROVIDER_TIMEOUT_SECONDS', '2400')
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


MAX_PROVIDER_ERROR_CHARS = 8192


def check_prompt_size(prompt, limit):
    size = len(json.dumps(prompt, ensure_ascii=True))
    if size > limit:
        raise ValueError(f'Provider request too large ({size} serialized characters; '
                         f'limit {limit}). Retain evidence in files and '
                         'supply paths with bounded reading instructions before retrying.')


def redact_provider_input(detail, prompt, *, whole_lines=False):
    if prompt:
        # Text-mode subprocess capture normalizes CRLF and lone CR to LF.
        for text in dict.fromkeys((prompt, prompt.replace('\r\n', '\n').replace('\r', '\n'))):
            for echoed in (text, json.dumps(text, ensure_ascii=False)[1:-1],
                           json.dumps(text, ensure_ascii=True)[1:-1]):
                if whole_lines:
                    start = '' if echoed.startswith(('\r', '\n')) else '^'
                    end = '' if echoed.endswith(('\r', '\n')) else r'(?=\r?$)'
                    detail = re.sub(start + re.escape(echoed) + end,
                                    '\n[provider input omitted]\n', detail, flags=re.M)
                else:
                    detail = detail.replace(echoed, '[provider input omitted]')
    return detail


def provider_failure(detail, prompt=None, fallback='Provider execution failed'):
    detail = redact_provider_input(detail, prompt)
    authentication = re.search(r'authentication failed|not authenticated|unauthorized|invalid credentials|'
                               r'(?:invalid|expired|revoked) (?:oauth |access |auth(?:entication)? )?token', detail, re.I)
    exhausted = re.search(r'usage limit (?:reached|exceeded)|(?:hit|reached) your (?:session )?limit|'
                         r'(?:out of|no|insufficient) (?:credits|tokens)|tokens? exhausted', detail, re.I)
    detail = detail.strip() or fallback
    if len(detail) > MAX_PROVIDER_ERROR_CHARS:
        half = (MAX_PROVIDER_ERROR_CHARS - 40) // 2
        detail = detail[:half] + '\n[provider output truncated]\n' + detail[-half:]
    if exhausted and not authentication:
        raise ProviderExhausted(detail)
    raise RuntimeError(detail)


def managed_update(resolved, env, phase, token=None):
    """Run owned lifecycle bookkeeping outside the read-only model sandbox.

    External providers have no updater obligation and are never probed for scripts.
    Standalone plugins still ship and execute their own self-contained updater.
    """
    try:
        receipt = update_process(resolved, env, phase, token)
    except Exception as exc:
        if resolved and resolved.get('bundle_digest'):
            raise
        print('Plugin lifecycle unavailable: ' + str(exc), file=sys.stderr)
        return None
    if receipt and receipt.get('action') == 'stop':
        raise RuntimeError(receipt.get('message') or 'Required plugin runtime unavailable')
    return receipt


def update_process(resolved, env, phase, token=None):
    if not resolved:
        return None
    root = Path(resolved['root'])
    manifest = json.loads((root / '.codex-plugin/plugin.json').read_text()) if (root / '.codex-plugin/plugin.json').is_file() else {}
    if manifest.get('repository') != 'https://github.com/dk-code-factory/' + manifest.get('name', ''):
        return None
    if resolved.get('bundle_digest'):
        from shared.runtime_bundles import updater, verify
        if verify(root) != resolved['bundle_digest']:
            raise ValueError('retained provider runtime changed')
        receipt = updater().lifecycle(root, resolved['executor'], phase, token=token,
            parent=env.get('DK_UPDATE_PARENT'), origin_root=resolved.get('origin_root'))
        if receipt.get('message'):
            print(receipt['message'], file=sys.stderr)
        return receipt
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
    if _CANCELLATION.get() is not None:
        _CANCELLATION.get().check()
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
        from shared.runtime_bundles import retained_binding
        resolved = retained_binding(resolved)
        stack.append(identity)
        env['DK_BINDING_STACK'] = json.dumps(stack)
        prompt = (f'Apply the installed procedure at {resolved["procedure"]}. '
                  f'Its plugin root is {resolved["root"]}. Read linked resources required by your '
                  'assigned task or current stage. Load recovery and exceptional-state procedures '
                  'only when the procedure or a reported diagnostic requires them.\n\n' + prompt)
    receipt = managed_update(resolved, env, 'entry')
    if receipt and receipt.get('action') == 'stop':
        raise RuntimeError(receipt.get('message') or 'Required plugin runtime unavailable')
    token = receipt.get('token') if receipt else None
    if token:
        env['DK_UPDATE_PARENT'] = token
        env['DK_UPDATE_MANAGED_RECEIPT'] = json.dumps(receipt)
    managed_context = ''
    if token:
        managed_context = ('The host executor has called this plugin\'s lifecycle entry and will call completion '
                           'after your final response. Use DK_UPDATE_MANAGED_RECEIPT; do not duplicate those calls.\n')
    try:
        if token and resolved and receipt.get('identity', {}).get('name') == 'dk-workflows':
            # The read-only provider can reuse this complete begin receipt without
            # attempting lifecycle/storage writes from its sandbox.
            import importlib.util
            from shared.invocations import begin
            root = Path(receipt['runtime_root'])
            spec = importlib.util.spec_from_file_location('managed_dk_cli', root / 'scripts/dk.py')
            cli = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cli)
            entry = begin(root, cli.ROUTES, host, resolved['binding']['skill'].split(':')[-1],
                          checkout, managed_receipt=receipt)
            env['DK_MANAGED_INVOCATION'] = entry['invocation']
            managed_context = (
                'The host executor owns lifecycle entry and completion for this DK workflow. '
                f'DK_MANAGED_INVOCATION is {entry["invocation"]}; use that path as INVOCATION_FILE '
                f'and {entry["runtime_root"]} as PLUGIN_ROOT. The skill\'s begin/end commands reuse '
                'this executor-managed invocation; do not duplicate lifecycle calls with update.py '
                'entry or complete. Pass --invocation INVOCATION_FILE before routed helpers. '
                'A nested native skill uses this invocation\'s token as its parent and retains its '
                'own returned or executor-managed invocation.\n')
        return execute_process(managed_context + prompt, checkout, host, env, resolved, attempt, timeout)
    finally:
        if token:
            original = sys.exception()
            try:
                managed_update(resolved, env, 'complete', token)
            except Exception as exc:
                if not isinstance(original, Diagnostic) or original.code != 'provider_output_failed':
                    raise
                original.args = (str(original) + '; lifecycle completion also failed: ' + str(exc),)


def codex_write_failed(stderr, output, prompt):
    """Match the CLI diagnostic, not quoted examples or errors for other paths."""
    paths = {str(output), json.dumps(str(output), ensure_ascii=False),
             json.dumps(str(output), ensure_ascii=True)}
    detail = redact_provider_input(stderr, prompt, whole_lines=True)
    return any(line.startswith(f'Failed to write last message file {path}: ')
               for line in detail.splitlines() for path in paths)


def output_failure(output, proc, checkout, argv, attempt, timeout, reason):
    """Retain transport evidence independently of optional reviewer telemetry."""
    response, read_error, permission_error = None, None, None
    try:
        output.chmod(0o600)
    except OSError as exc:
        permission_error = str(exc)
    try:
        response = base64.b64encode(output.read_bytes()).decode('ascii')
    except OSError as exc:
        read_error = str(exc)
    evidence = allocate(repository=checkout) / 'provider-output-failure.json'
    write(evidence, {'provider': 'codex', 'argv': argv, 'checkout': str(checkout),
                     'attempt': attempt, 'timeout_seconds': timeout, 'output_path': str(output),
                     'reason': reason, 'returncode': proc.returncode,
                     'stdout': proc.stdout, 'stderr': proc.stderr,
                     'response_base64': response, 'response_read_error': read_error,
                     'response_permission_error': permission_error})
    return evidence


def execute_process(prompt, checkout, host, env, resolved, attempt, timeout):
    temp = tempfile.mkdtemp(prefix='dk-provider-')
    retain_temp = False
    try:
        output = Path(temp) / 'result.txt'
        if host == 'codex':
            argv = ['codex', 'exec', '--ephemeral', '--sandbox', 'read-only', '--skip-git-repo-check',
                    '-C', str(checkout), '-o', str(output), '-']
        else:
            argv = ['claude', '--permission-mode', 'plan', '--no-session-persistence',
                    '--output-format', 'json', '--disallowedTools', 'Edit,Write,NotebookEdit', '-p']
            if resolved:
                argv += ['--plugin-dir', resolved['root']]
        proc = capture(run_provider, argv, input=prompt, text=True, capture_output=True,
                       cwd=checkout, env=env, timeout=timeout, telemetry_prompt=prompt,
                       telemetry_attempt=attempt, telemetry_provider=host, telemetry_output=output)
        if proc.returncode:
            provider_failure('\n'.join(filter(None, (proc.stderr, proc.stdout))), prompt=prompt, fallback=f'{host} execution failed')
        if host == 'codex':
            if codex_write_failed(proc.stderr, output, prompt):
                reason = 'Codex failed to write the requested response file'
            else:
                try:
                    return output.read_text()
                except (OSError, UnicodeError) as exc:
                    reason = 'Codex response file could not be read: ' + str(exc)
            retain_temp = True
            try:
                evidence = output_failure(output, proc, checkout, argv, attempt, timeout, reason)
            except Exception as exc:
                evidence = Path(temp)
                reason += '; diagnostic persistence also failed: ' + str(exc)
            else:
                retain_temp = False
            raise Diagnostic('provider_output_failed', reason + f'. Retained evidence: {evidence}. '
                             'Repair storage capacity, quota or permissions before retrying manually.',
                             task('Inspect the retained evidence and repair storage capacity, quota or permissions. '
                                  'Use a writable private TMPDIR if needed, then retry manually.', evidence),
                             outcome='error')
        envelope = json.loads(proc.stdout)
        if envelope.get('is_error') or envelope.get('subtype') != 'success':
            provider_failure(envelope.get('result') or json.dumps(envelope), prompt=prompt, fallback='Claude execution failed')
        result = envelope.get('result')
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError('Claude did not produce a final response')
        return result
    finally:
        if not retain_temp:
            original = sys.exception()
            try:
                shutil.rmtree(temp)
            except OSError:
                if original is None:
                    raise


def invoke(binding, handoff_path, checkout, prompt, *, attempt=0):
    if _CANCELLATION.get() is not None:
        _CANCELLATION.get().check()
    from shared.runtime_bundles import retained_binding
    resolved = resolve(binding)
    if resolved['executor'] == 'command':
        stack = json.loads(os.environ.get('DK_BINDING_STACK', '[]'))
        if resolved['identity'] in stack:
            raise ValueError('Recursive command provider invocation')
        resolved = retained_binding(resolved)
        argv = [part.replace('{handoff}', str(handoff_path))
                for part in resolved.get('argv', resolved['binding']['command'])]
        env = dict(os.environ, DK_BINDING_STACK=json.dumps([*stack, resolved['identity']]),
                   DK_PROVIDER_ATTEMPT=str(attempt))
        # The immutable evidence handoff stays in argv. stdin carries the output
        # instructions and, on retry, the validation error and prior response.
        proc = capture(run_provider, argv, input=prompt, cwd=checkout, text=True,
                       capture_output=True, env=env, timeout=provider_timeout(),
                       telemetry_prompt=prompt, telemetry_attempt=attempt,
                       telemetry_provider='command')
        if proc.returncode:
            provider_failure('\n'.join(filter(None, (proc.stderr, proc.stdout))), prompt=prompt, fallback='Command provider failed')
        return proc.stdout
    return execute(prompt, checkout, resolved=resolved, attempt=attempt)
