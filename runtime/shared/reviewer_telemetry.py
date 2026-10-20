"""Optional local execution evidence; never changes a reviewer's JSON contract."""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from shared.private_state import mutate

ENV = 'DK_REVIEW_STATS_TRACE'


def output_text(value):
    # TimeoutExpired may expose bytes even when subprocess.run used text=True.
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value


def provider_metadata(result, provider):
    """Read inclusive CLI totals once; never sum nested model or agent usage."""
    model, usage = getattr(result, 'model', None), getattr(result, 'usage', None)
    if provider in ('claude', 'anthropic'):
        try:
            envelope = json.loads(output_text(getattr(result, 'stdout', None)) or '')
        except (ValueError, TypeError):
            envelope = None
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
    path = os.environ.get(ENV)
    if path:
        try:
            event = {key: getattr(result, key, None) for key in ('stdout', 'stderr', 'returncode')}
            mutate(Path(path), {'attempts': []}, lambda d: d.setdefault('transports', []).append(event))
        except Exception as exc:
            print(f'review stats missing transport evidence ({path}): {exc}', file=sys.stderr)


def capture(function, *args, telemetry_prompt, telemetry_attempt, telemetry_provider=None,
            telemetry_output=None, **kwargs):
    path = os.environ.get(ENV)
    if not path:
        return function(*args, **kwargs)
    start = time.monotonic()
    result = None
    error = None
    failure = None
    # The configured adapter owns this trace. Its provider process may launch
    # native subreviewers with inclusive totals; do not instrument those again.
    os.environ.pop(ENV, None)
    try:
        if kwargs.get('env') is not None:
            kwargs['env'] = dict(kwargs['env'])
            kwargs['env'].pop(ENV, None)
        result = function(*args, **kwargs)
        return result
    except Exception as exc:
        error = str(exc)
        failure = exc
        raise
    finally:
        os.environ[ENV] = path
        evidence = result if result is not None else failure
        event = {'attempt': telemetry_attempt, 'duration_seconds': time.monotonic() - start,
                 'prompt_fingerprint': hashlib.sha256(telemetry_prompt.encode()).hexdigest(),
                 'prompt': telemetry_prompt, 'provider': telemetry_provider,
                 'model': getattr(result, 'model', None), 'usage': getattr(result, 'usage', None),
                 'stdout': output_text(getattr(evidence, 'stdout', None)),
                 'stderr': output_text(getattr(evidence, 'stderr', None)),
                 'returncode': getattr(result, 'returncode', None), 'error': error,
                 'error_type': type(failure).__name__ if failure else None,
                 'timeout_seconds': getattr(failure, 'timeout', None)}
        try:
            event['model'], event['usage'] = provider_metadata(evidence, telemetry_provider)
            if telemetry_output and Path(telemetry_output).exists():
                event['output'] = Path(telemetry_output).read_text(encoding='utf-8', errors='replace')
            mutate(Path(path), {'attempts': []}, lambda d: d['attempts'].append(event))
        except Exception as exc:
            print(f'review stats missing execution evidence ({path}): {exc}', file=sys.stderr)
