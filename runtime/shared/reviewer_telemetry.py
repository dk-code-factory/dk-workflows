"""Optional local execution evidence; never changes a reviewer's JSON contract."""
import hashlib
import json
import os
import re
import sys
import time
import subprocess
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from shared.private_state import mutate
from shared import workflow_usage

ENV = 'DK_REVIEW_STATS_TRACE'
_INHERIT = object()
_TRACE = ContextVar('dk_review_stats_trace', default=_INHERIT)


def trace_path():
    value = _TRACE.get()
    return os.environ.get(ENV) if value is _INHERIT else value


@contextmanager
def select_trace(path):
    token = _TRACE.set(path)
    try:
        yield
    finally:
        _TRACE.reset(token)


def output_text(value):
    # TimeoutExpired may expose bytes even when subprocess.run used text=True.
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value


def codex_usage(stdout):
    """Read the fresh exec root turn only, never tool output or inherited usage.

    --json emits turn.completed usage alongside tool/agent events. A fresh exec
    has one requested root turn; duplicate deliveries replace that observation.
    Older CLIs can omit optional reasoning/cache fields without inventing zeros.
    """
    root, found = None, None
    for line in (output_text(stdout) or '').splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict) or event.get('inherited'):
            continue
        if event.get('type') == 'thread.started' and root is None:
            root = event.get('thread_id')
        if event.get('type') != 'turn.completed' or event.get('parent_thread_id'):
            continue
        if event.get('thread_id') and root and event['thread_id'] != root:
            continue
        raw = event.get('usage')
        if isinstance(raw, dict) and any(type(raw.get(k)) is int for k in ('input_tokens', 'output_tokens', 'total_tokens')):
            found = {**raw, 'source': 'codex_cli_json'}
    return found


def provider_metadata(result, provider):
    """Read inclusive CLI totals once; never sum nested model or agent usage."""
    model, usage = getattr(result, 'model', None), getattr(result, 'usage', None)
    if provider in ('claude', 'anthropic'):
        stdout = output_text(getattr(result, 'stdout', None)) or ''
        try:
            envelope = json.loads(stdout)
        except (ValueError, TypeError):
            envelope = None
            # Required native-pass evidence uses verbose stream JSON. Only the
            # root result carries the inclusive totals; never sum child events.
            try:
                rows = [json.loads(line) for line in stdout.splitlines() if line.strip()]
                results = [row for row in rows if isinstance(row, dict) and row.get('type') == 'result']
                if len(results) == 1:
                    envelope = results[0]
            except (ValueError, TypeError, AttributeError):
                pass
        if isinstance(envelope, dict) and isinstance(envelope.get('usage'), dict):
            if usage is None:
                usage = dict(envelope['usage'])
                if isinstance(envelope.get('modelUsage'), dict):
                    usage['model_usage'] = envelope['modelUsage']
                if isinstance(envelope.get('total_cost_usd'), (int, float)):
                    usage['total_cost_usd'] = envelope['total_cost_usd']
            if model is None and isinstance(envelope.get('modelUsage'), dict):
                model = ', '.join(sorted(envelope['modelUsage'])) or None
    elif provider in ('codex', 'openai'):
        if usage is None:
            usage = codex_usage(getattr(result, 'stdout', None))
        stderr = output_text(getattr(result, 'stderr', None)) or ''
        if isinstance(stderr, str):
            # The summary has no input/output/cache breakdown. Do not invent one.
            totals = re.findall(r'(?m)^tokens used\r?\n([0-9][0-9,]*)\s*$', stderr)
            if usage is None and totals:
                usage = {'total_tokens': int(totals[-1].replace(',', '')),
                         'source': 'codex_cli_summary'}
            header = stderr.split('\nuser\n', 1)[0]
            match = re.search(r'(?m)^model: (\S+)\s*$', header)
            if model is None and match:
                model = match[1]
    return model, usage


def transport(result):
    """Keep external command errors/output without counting inclusive execution again."""
    path = trace_path()
    if path:
        try:
            event = {key: getattr(result, key, None) for key in ('stdout', 'stderr', 'returncode')}
            mutate(Path(path), {'attempts': []}, lambda d: d.setdefault('transports', []).append(event))
        except Exception as exc:
            print(f'review stats missing transport evidence ({path}): {exc}', file=sys.stderr)


def capture(function, *args, telemetry_prompt, telemetry_attempt, telemetry_provider=None,
            telemetry_output=None, **kwargs):
    path = trace_path()
    workflow = workflow_usage.context(kwargs.get('env'))
    # Strip instrumentation only from the child environment. Process-wide
    # environment mutation would redirect concurrent reviewers' observations.
    if function is subprocess.run or kwargs.get('env') is not None:
        kwargs['env'] = dict(kwargs.get('env') if kwargs.get('env') is not None else os.environ)
        kwargs['env'].pop(ENV, None)
    if not path and workflow is None:
        return function(*args, **kwargs)
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    execution_id = str(uuid.uuid4())
    result = None
    error = None
    failure = None
    if workflow is not None:
        workflow_usage.execution(workflow, {'execution_id': execution_id,
            'started_at': started_at, 'observed_at': started_at, 'status': 'running',
            'attempt': telemetry_attempt, 'provider': telemetry_provider,
            'inclusive': True, 'scope': 'execution'})
        if kwargs.get('env') is not None:
            child_context = {**workflow, 'parent_execution_id': execution_id}
            kwargs['env'][workflow_usage.ENV] = json.dumps(child_context)
    # The configured adapter owns this trace. Its provider process may launch
    # native subreviewers with inclusive totals; do not instrument those again.
    token = _TRACE.set(None)
    try:
        result = function(*args, **kwargs)
        return result
    except BaseException as exc:
        error = str(exc)
        failure = exc
        raise
    finally:
        _TRACE.reset(token)
        evidence = result if result is not None else failure
        event = {'attempt': telemetry_attempt, 'execution_id': execution_id,
                 'started_at': started_at, 'stage': 'review', 'role': 'root',
                 'observed_at': datetime.now(timezone.utc).isoformat(),
                 'scope': 'execution', 'inclusive': True,
                 'prompt_bytes': len(telemetry_prompt.encode('utf-8', errors='replace')),
                 'duration_seconds': time.monotonic() - start,
                 'prompt_fingerprint': hashlib.sha256(telemetry_prompt.encode()).hexdigest(),
                 'prompt': telemetry_prompt, 'provider': telemetry_provider,
                 'model': getattr(result, 'model', None), 'usage': getattr(result, 'usage', None),
                 'stdout': output_text(getattr(evidence, 'stdout', None)),
                 'stderr': output_text(getattr(evidence, 'stderr', None)),
                 'returncode': getattr(evidence, 'returncode', None), 'error': error,
                 'error_type': type(failure).__name__ if failure else None,
                 'timeout_seconds': getattr(failure, 'timeout', None),
                 'status': ('interrupted' if isinstance(failure, (KeyboardInterrupt, SystemExit))
                            or type(failure).__name__ == 'ProviderCancelled' else
                            'failed' if failure or getattr(evidence, 'returncode', 0) else 'returned'),
                 'compactions': getattr(evidence, 'compactions', None),
                 'tool_output_bytes': getattr(evidence, 'tool_output_bytes', None),
                 'settings': getattr(evidence, 'settings', None),
                 'settings_source': getattr(evidence, 'settings_source', None),
                 'transport_output_bytes': sum(len((output_text(getattr(evidence, field, None)) or '').encode(
                     'utf-8', errors='replace')) for field in ('stdout', 'stderr'))
                    if any(getattr(evidence, field, None) is not None for field in ('stdout', 'stderr')) else None}
        try:
            event['model'], event['usage'] = provider_metadata(evidence, telemetry_provider)
            event['effort'] = getattr(evidence, 'effort', None)
            for key in ('response_id', 'tool_call_id', 'parent_execution_id'):
                value = getattr(evidence, key, None)
                if isinstance(value, str) and value:
                    event[key] = value
            if telemetry_provider in ('claude', 'anthropic'):
                try:
                    envelope = json.loads(event['stdout'] or '')
                except (ValueError, TypeError):
                    envelope = None
                if isinstance(envelope, dict) and isinstance(envelope.get('uuid'), str):
                    event['response_id'] = envelope['uuid']
            if event['effort'] is None and telemetry_provider in ('codex', 'openai'):
                header = (event['stderr'] or '').split('\nuser\n', 1)[0]
                match = re.search(r'(?m)^reasoning effort: (\S+)\s*$', header)
                if match:
                    event['effort'] = match[1]
            if telemetry_output and Path(telemetry_output).exists():
                event['output'] = Path(telemetry_output).read_text(encoding='utf-8', errors='replace')
            if path:
                mutate(Path(path), {'attempts': []}, lambda d: d['attempts'].append(event))
        except Exception as exc:
            print(f'review stats missing execution evidence ({path}): {exc}', file=sys.stderr)
        finally:
            workflow_usage.execution(workflow, event)
