"""Optional local execution evidence; never changes a reviewer's JSON contract."""
import hashlib
import os
import sys
import time
from pathlib import Path
from shared.private_state import mutate

ENV = 'DK_REVIEW_STATS_TRACE'


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
            if telemetry_output and Path(telemetry_output).exists():
                event['output'] = Path(telemetry_output).read_text()
            mutate(Path(path), {'attempts': []}, lambda d: d['attempts'].append(event))
        except Exception as exc:
            print(f'review stats missing execution evidence ({path}): {exc}', file=sys.stderr)
