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


def provider_metadata(result, provider):
    """Read inclusive CLI totals once; never sum nested model or agent usage."""
    model, usage = getattr(result, 'model', None), getattr(result, 'usage', None)
    if provider in ('claude', 'anthropic'):
        try:
            envelope = json.loads(getattr(result, 'stdout', None) or '')
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
        stderr = getattr(result, 'stderr', None) or ''
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
    # The configured adapter owns this trace. Its provider process may launch
    # native subreviewers with inclusive totals; do not instrument those again.
    os.environ.pop(ENV, None)
    try:
        result = function(*args, **kwargs)
        return result
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        os.environ[ENV] = path
        event = {'attempt': telemetry_attempt, 'duration_seconds': time.monotonic() - start,
                 'prompt_fingerprint': hashlib.sha256(telemetry_prompt.encode()).hexdigest(),
                 'prompt': telemetry_prompt, 'provider': telemetry_provider,
                 'model': getattr(result, 'model', None), 'usage': getattr(result, 'usage', None),
                 'stdout': getattr(result, 'stdout', None), 'stderr': getattr(result, 'stderr', None),
                 'returncode': getattr(result, 'returncode', None), 'error': error}
        try:
            event['model'], event['usage'] = provider_metadata(result, telemetry_provider)
            if telemetry_output and Path(telemetry_output).exists():
                event['output'] = Path(telemetry_output).read_text()
            mutate(Path(path), {'attempts': []}, lambda d: d['attempts'].append(event))
        except Exception as exc:
            print(f'review stats missing execution evidence ({path}): {exc}', file=sys.stderr)
