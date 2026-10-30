#!/usr/bin/env python3
"""Standalone Codex startup hook; safe to copy outside the versioned plugin cache."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys


LOOKUP_COMMAND = ['codex', 'plugin', 'list', '--json']
LOOKUP_TIMEOUT = 90
REFRESH_TIMEOUT = 180


def positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError('timeout must be positive and finite')
    return seconds


def run(command, timeout, env):
    with subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, start_new_session=True) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except BaseException:
            # The refresh helper launches summarizers; killing only it leaves them running.
            # Signal the group even if its leader exited while a child still holds a pipe.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            raise
        if proc.returncode:
            raise ValueError(f'exit {proc.returncode}: ' + (stderr.strip() or stdout.strip()))
        return stdout


def installation(registry, plugin_id, env):
    parts = plugin_id.split('@')
    if (len(parts) != 2 or parts[0] != 'dk-workflows'
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', parts[1])):
        raise ValueError('expected dk-workflows@MARKETPLACE plugin identity')
    if not isinstance(registry, dict) or not isinstance(registry.get('installed'), list):
        raise ValueError('plugin registry must contain an installed list')
    records = [row for row in registry['installed']
               if isinstance(row, dict) and row.get('pluginId') == plugin_id]
    if len(records) != 1:
        raise ValueError(f'expected one registry entry for {plugin_id}, found {len(records)}')
    record = records[0]
    if (record.get('name') != parts[0] or record.get('marketplaceName') != parts[1]
            or record.get('installed', True) is not True or record.get('enabled', True) is not True):
        raise ValueError(f'{plugin_id} is disabled, uninstalled, or has inconsistent identity')
    version = record.get('version')
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('registry version must be X.Y.Z')
    home = Path(env.get('CODEX_HOME') or Path.home() / '.codex')
    registered_path = record.get('installedPath') or record.get('installPath')
    if registered_path is not None and not isinstance(registered_path, str):
        raise ValueError('registered plugin path must be a string')
    root = Path(registered_path or home / 'plugins/cache' / parts[1] / parts[0] / version)
    if not root.is_absolute():
        raise ValueError('registered plugin path must be absolute')
    root = root.resolve()
    manifest = json.loads((root / '.codex-plugin/plugin.json').read_text())
    if not isinstance(manifest, dict) or (manifest.get('name'), manifest.get('version')) != (parts[0], version):
        raise ValueError('installed manifest name/version does not match the registry')
    helper = root / 'runtime/dk-config/scripts/repo_rules.py'
    if not helper.is_file() or not helper.resolve().is_relative_to(root):
        raise ValueError('installed rule-refresh helper is missing or escapes the plugin')
    return helper


def hook(payload, args, env):
    phase = 'Hook input'
    command = None
    timeout = None
    try:
        if (not isinstance(payload, dict) or not isinstance(payload.get('cwd', '.'), str)
                or not isinstance(payload.get('hook_event_name', 'SessionStart'), str)):
            raise ValueError('expected a JSON object with string cwd and hook_event_name')
        phase, command, timeout = 'Plugin lookup', LOOKUP_COMMAND, args.lookup_timeout
        registry = json.loads(run(command, timeout, env))
        phase, command, timeout = 'Plugin validation', None, None
        helper = installation(registry, args.plugin_id, env)
        phase = 'Repository rule refresh'
        command = [sys.executable, str(helper), '--cwd', payload.get('cwd', '.'), 'refresh']
        timeout = args.refresh_timeout
        context = run(command, timeout, dict(env, DK_HOST='codex'))
        return {'hookSpecificOutput': {
            'hookEventName': payload.get('hook_event_name', 'SessionStart'),
            'additionalContext': context.strip(),
        }}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        detail = f'command timed out after {timeout:g}s' if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
        refresh_started = phase == 'Repository rule refresh' and not isinstance(exc, OSError)
        state = 'started' if refresh_started else 'did not start'
        diagnostic = f'{phase} failed; rule refresh {state}. {detail}'
        if command:
            diagnostic += f' Command: {shlex.join(command)}. Timeout: {timeout:g}s.'
        if phase == 'Repository rule refresh':
            recovery = 'Run $dk-workflows:dk-config refresh in the repository; inspect the refresh error first.'
        else:
            recovery = (f'Run {shlex.join(LOOKUP_COMMAND)} and verify {args.plugin_id} is enabled and its '
                        'installed manifest matches the registry version; then rerun this hook. '
                        'For a lookup timeout, increase --lookup-timeout and the outer hook timeout together.')
        return {'systemMessage': diagnostic + ' Recovery: ' + recovery}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin-id', default='dk-workflows@dk-workflows')
    parser.add_argument('--lookup-timeout', type=positive_seconds, default=LOOKUP_TIMEOUT)
    parser.add_argument('--refresh-timeout', type=positive_seconds, default=REFRESH_TIMEOUT)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        print(json.dumps({'systemMessage': f'Hook input failed; rule refresh did not start. {exc}'}))
        return
    print(json.dumps(hook(payload, args, dict(os.environ))))


def terminate(signum, frame):
    """Unwind process-group cleanup when the hook runner cancels this adapter."""
    raise SystemExit(128 + signum)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, terminate)
    main()
