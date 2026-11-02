"""Passive, private workflow attribution. These records never authorize work."""
import argparse
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import usage
from shared.private_state import read, write

VERSION = 1
ENV = 'DK_WORKFLOW_USAGE'
LOCK_TIMEOUT_SECONDS = 0.25
ACTIVE = ContextVar('workflow_usage_context', default=None)
_ID = re.compile(r'[a-f0-9]{32}')
STAGES = {
    'dk-pr-review': 'review', 'dk-pr-iter': 'coordination',
    'dk-pr-address-reviews': 'fixing', 'dk-pr-reply-review': 'replies',
    'dk-pr-split-commits': 'cleanup', 'dk-pr-decisions': 'preparation',
    'dk-architecture-review': 'review', 'dk-architecture-enforcer': 'preparation',
}


def now():
    return datetime.now(timezone.utc).isoformat()


def home(repository=None):
    base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    if not base.is_absolute():
        raise ValueError('XDG_STATE_HOME must be absolute')
    target = (base / 'dk-code-factory/workflow-usage').resolve()
    from shared.repository_scope import inside_checkout
    if (target.is_relative_to(Path(__file__).resolve().parents[2])
            or inside_checkout(target, repository or Path.cwd())):
        raise ValueError('workflow usage must stay outside the plugin and repository')
    return target


def token_path(token, repository=None):
    return home(repository) / 'tokens' / (hashlib.sha256(token.encode()).hexdigest() + '.json')


def checked(value, repository=None):
    if (not isinstance(value, dict) or value.get('version') != VERSION
            or not isinstance(value.get('workflow_id'), str) or not _ID.fullmatch(value['workflow_id'])
            or not isinstance(value.get('invocation_id'), str) or not value['invocation_id']
            or value.get('path') != str(home(repository) / (value['workflow_id'] + '.json'))):
        raise ValueError('invalid workflow usage context')
    return value


def warning(exc):
    print('Workflow usage evidence unavailable: ' + str(exc), file=sys.stderr)


def context(env=None):
    active = ACTIVE.get()
    if env is None and active is not None:
        return active
    env = os.environ if env is None else env
    value = None
    try:
        if env.get(ENV):
            value = checked(json.loads(env[ENV]))
        elif env.get('DK_UPDATE_PARENT'):
            stored = read(token_path(env['DK_UPDATE_PARENT']), None)
            value = checked(stored) if stored else None
        if active is not None and value is not None and active['workflow_id'] == value['workflow_id']:
            value = {**value, **{key: active[key] for key in ('stage', 'role', 'pinned_evidence') if key in active}}
        return value or active
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        if env.get(ENV):
            warning(exc)
    return active


def save(ctx, event):
    checked(ctx)
    path = Path(ctx['path'])
    # Telemetry cannot wait on a stuck writer. Missing evidence is preferable to
    # delaying a workflow behind an analytics-only lock.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(str(path) + '.lock', os.O_CREAT | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(min(0.005, max(0, deadline - time.monotonic())))
        data = read(path, {'version': VERSION, 'workflow_id': ctx['workflow_id'], 'events': []})
        if data.get('version') != VERSION or data.get('workflow_id') != ctx['workflow_id']:
            raise ValueError('workflow usage identity changed')
        data['events'].append(event)
        write(path, data)


def observe(kind, *, context=None, status=None, evidence=None, **fields):
    """Append an observation; absent telemetry and storage failures are nonblocking."""
    ctx = context if context is not None else globals()['context']()
    if ctx is None:
        return None
    event = {'id': uuid.uuid4().hex, 'kind': kind, 'observed_at': now(),
             'workflow_id': ctx['workflow_id'], 'invocation_id': ctx['invocation_id'],
             'parent_invocation_id': ctx.get('parent_invocation_id'),
             'parent_execution_id': ctx.get('parent_execution_id'),
             'stage': ctx.get('stage'), 'role': ctx.get('role'),
             'status': status or 'unknown', 'source': 'dk_runtime', 'evidence': evidence, **fields}
    try:
        save(ctx, event)
        return event
    except Exception as exc:
        warning(exc)
        return None


def register(invocation, cwd, parent=None):
    """Link an actual lifecycle token to its observed parent; never fabricate usage."""
    try:
        base = home(cwd)
        existing = read(token_path(invocation['token'], cwd), None)
        if existing:
            return checked(existing, cwd)
        previous = read(token_path(parent, cwd), None) if parent else None
        inherited = globals()['context']()
        if previous is None and inherited is not None:
            previous = inherited
        if previous is not None:
            checked(previous, cwd)
        workflow_id = previous['workflow_id'] if previous else uuid.uuid4().hex
        stage = STAGES.get(invocation['skill'], 'coordination')
        ctx = {'version': VERSION, 'workflow_id': workflow_id,
               'path': str(base / (workflow_id + '.json')),
               'invocation_id': invocation['token'],
               'parent_invocation_id': parent or (previous['invocation_id'] if previous else None),
               'parent_execution_id': (inherited or {}).get('parent_execution_id'),
               'stage': stage, 'role': invocation['skill']}
        identity = invocation['lifecycle'].get('identity', {})
        repository = None
        try:
            from shared.repository_scope import repository as identify
            repository = identify(cwd)
        except (OSError, ValueError, RuntimeError):
            pass
        ctx.update(repository=repository, runtime_digest=invocation['runtime_digest'],
                   plugin=identity.get('name'), plugin_version=identity.get('version'))
        event = {'id': 'lifecycle-' + invocation['token'], 'kind': 'invocation',
                 'status': 'running', 'started_at': now(), 'observed_at': now(),
                 'workflow_id': workflow_id, 'invocation_id': invocation['token'],
                 'parent_invocation_id': ctx['parent_invocation_id'],
                 'parent_execution_id': ctx['parent_execution_id'], 'stage': stage,
                 'role': invocation['skill'], 'source': 'dk_lifecycle',
                 'host': invocation['host'], 'repository': repository,
                 'runtime_digest': invocation['runtime_digest'],
                 'plugin_version': identity.get('version'), 'plugin': identity.get('name'),
                 'runtime_root': invocation['runtime_root'],
                 'invocation_receipt': invocation['invocation'],
                 'host_usage': None, 'host_model': None, 'host_effort': None,
                 'compactions': None, 'coordination_calls': None}
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        save(ctx, event)
        index = token_path(invocation['token'], cwd)
        index.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write(index, ctx)
        return ctx
    except Exception as exc:
        warning(exc)
        return None


def close(invocation, reason=None):
    try:
        ctx = read(token_path(invocation['token']), None)
        if ctx:
            observe('invocation_end', context=checked(ctx), status='closed',
                    evidence={'reason': reason, 'source': 'lifecycle_completion'},
                    # Lifecycle exit does not establish engineering completion.
                    workflow_outcome=None)
    except Exception as exc:
        warning(exc)


def stage(command, arguments):
    if command in {'reviewers', 'validate-review'}:
        return 'review'
    if command in {'review-context', 'architecture', 'preflight'} or command == 'decisions' and 'prepare' in arguments:
        return 'preparation'
    if command in {'replies', 'reply-batch', 'completion-threads'}:
        return 'replies'
    if command in {'split', 'branch-publish'}:
        return 'cleanup'
    if command == 'ci-wait' or command == 'flaky-tests':
        return 'ci'
    if command == 'completion' or command == 'run-state' and any(x in arguments for x in ('complete', 'finish-run', 'completion-verify')):
        return 'completion'
    if command == 'run-state' and 'work-gate' in arguments:
        return 'fixing'
    return 'coordination'


def input_evidence(arguments, cwd):
    """Retain hashes of supplied pinned artifacts, without copying their bodies."""
    result = []
    accepted = {'--context', '--preparation', '--preparation-file', '--decisions-file',
                '--handoff', '--handoff-file'}
    try:
        for index, argument in enumerate(arguments):
            key, separator, value = argument.partition('=')
            if key not in accepted:
                continue
            if not separator and index + 1 < len(arguments):
                value = arguments[index + 1]
            path = Path(value)
            path = path if path.is_absolute() else Path(cwd) / path
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(65536), b''):
                    digest.update(block)
            result.append({'argument': key, 'path': str(path.resolve()), 'sha256': digest.hexdigest()})
    except (OSError, ValueError) as exc:
        warning(exc)
    return result


@contextmanager
def helper(command, arguments, cwd, env, *, artifact=None):
    """Observe one dispatched helper and propagate attribution to its providers."""
    ctx = globals()['context'](env)
    event = {'env': env, 'status': 'returned', 'artifact': str(artifact) if artifact else None}
    start = time.monotonic()
    if ctx is not None:
        ctx = {**ctx, 'stage': stage(command, arguments), 'role': command,
               'helper_id': uuid.uuid4().hex, 'pinned_evidence': input_evidence(arguments, cwd)}
        event['env'] = dict(env, **{ENV: json.dumps(ctx)})
        record = observe('helper', context=ctx, status='running', command=command,
                         started_at=now(), helper_id=ctx['helper_id'], evidence=ctx['pinned_evidence'],
                         source='stable_cli_dispatch', artifact=event['artifact'])
    else:
        record = None
    try:
        yield event
    except BaseException as exc:
        event['status'] = 'interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'failed'
        raise
    finally:
        if record:
            fields = {k: v for k, v in event.items() if k != 'env'}
            try:
                save(ctx, {**record, **fields, 'duration_seconds': time.monotonic() - start,
                           'observed_at': now()})
            except Exception as exc:
                warning(exc)


@contextmanager
def role(name, evidence=None):
    ctx = context()
    if ctx is not None:
        ctx = {**ctx, 'role': name, 'stage': 'review', 'pinned_evidence': evidence}
    token = ACTIVE.set(ctx)
    try:
        yield ctx
    finally:
        ACTIVE.reset(token)


def execution(ctx, event):
    """Keep compact metadata rather than duplicate private prompts/transcripts."""
    if ctx is None:
        return
    try:
        fields = {key: event.get(key) for key in (
            *usage.EXECUTION_FIELDS, 'attempt', 'duration_seconds', 'returncode',
            'error_type', 'compactions', 'tool_output_bytes', 'transport_output_bytes',
            'settings', 'settings_source')}
        fields.update(stage=ctx.get('stage'), role=ctx.get('role'),
                      parent_execution_id=event.get('parent_execution_id') or ctx.get('parent_execution_id'),
                      repository=ctx.get('repository'), runtime_digest=ctx.get('runtime_digest'),
                      plugin_version=ctx.get('plugin_version'), helper_id=ctx.get('helper_id'))
        raw = event.get('usage')
        observe('execution', context=ctx, status=event.get('status', 'unknown'),
                evidence={'prompt_fingerprint': event.get('prompt_fingerprint'),
                          'usage_source': raw.get('source') if isinstance(raw, dict) else None,
                          'duration_source': 'monotonic_clock' if fields['duration_seconds'] is not None else None,
                          'transport_output_source': 'captured_stdout_stderr' if fields['transport_output_bytes'] is not None else None,
                          'compactions_source': 'provider_result' if fields['compactions'] is not None else None,
                          'tool_output_source': 'provider_result' if fields['tool_output_bytes'] is not None else None,
                          'pinned': ctx.get('pinned_evidence')}, **fields)
    except Exception as exc:
        warning(exc)


def query(path, groups=()):
    """Read evidence only. Completion/coverage and missing stages remain visible."""
    data = read(Path(path), None)
    if not isinstance(data, dict) or data.get('version') != VERSION or not isinstance(data.get('events'), list):
        raise ValueError('expected workflow usage v1 evidence')
    events = data['events']
    latest = {item['id']: item for item in events}
    invocations = [item for item in latest.values() if item['kind'] == 'invocation']
    closed = {item['invocation_id'] for item in events if item['kind'] == 'invocation_end'}
    executions = [item for item in events if item['kind'] == 'execution']
    # Each observed host invocation is an unknown usage component until the host
    # exposes evidence. A provider parent can cover it through exact ancestry.
    hosts = [{'execution_id': 'host-' + item['invocation_id'],
              'parent_execution_id': item.get('parent_execution_id'),
              'provider': item.get('host'), 'usage': item.get('host_usage'),
              'stage': item['stage'], 'role': item['role'], 'scope': 'execution'}
             for item in invocations]
    runs = [item for item in events if item['kind'] == 'run' and item.get('source') == 'run_state']
    coverages = [item for item in events if item['kind'] == 'review_coverage']
    run = runs[-1] if runs else None
    outcome = run['status'] if run and run['status'] in {'completed', 'limited'} else 'unknown'
    return {'version': VERSION, 'workflow_id': data['workflow_id'],
            'workflow_outcome': outcome, 'completed_workflows': int(outcome == 'completed'),
            'completion_source': run.get('source') if run else None,
            'lifecycle_closed': bool(invocations) and all(item['invocation_id'] in closed for item in invocations),
            'review_coverage': coverages[-1] if coverages else None,
            'usage': usage.report([*hosts, *executions], groups),
            'unknown_host_invocations': [item['invocation_id'] for item in invocations if item.get('host_usage') is None],
            'unobserved_stages': sorted(set(('preparation', 'coordination', 'review', 'fixing', 'replies', 'ci', 'cleanup', 'completion')) -
                                        {item.get('stage') for item in events}),
            'execution_statuses': {status: sum(item.get('status') == status for item in
                {e['execution_id']: e for e in executions}.values())
                for status in ('running', 'returned', 'failed', 'interrupted')},
            'checkpoint_reuses': sum(item['kind'] == 'checkpoint_reuse' for item in latest.values()),
            'events': events}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workflow', help='Private workflow usage JSON path; defaults to active invocation')
    parser.add_argument('--group-by', action='append', default=[])
    args = parser.parse_args()
    ctx = context()
    path = args.workflow or (ctx or {}).get('path')
    if not path:
        parser.error('supply --workflow or a lifecycle invocation context')
    try:
        print(json.dumps(query(path, args.group_by), sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
