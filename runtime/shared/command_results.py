"""Typed terminal results for the stable CLI; native helper contracts stay intact."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from shared.repository_scope import inside_checkout
from shared.diagnostics import command as command_action
from shared.private_state import write


def default_path():
    base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    if not base.is_absolute():
        raise ValueError('XDG_STATE_HOME must be absolute')
    return base / 'dk-code-factory/helper-results' / (uuid.uuid4().hex + '.json')


def inspect_result(path, section=None, since=0, limit=20):
    """Page a retained native result without returning its entire history."""
    if since < 0 or not 1 <= limit <= 100:
        raise ValueError('--since must be nonnegative and --limit must be between 1 and 100')
    path = Path(path).expanduser().resolve()
    artifact = json.loads(path.read_text())
    if (not isinstance(artifact, dict) or artifact.get('schema_version') != 1
            or 'data' not in artifact and artifact.get('outcome') != 'running'):
        raise ValueError('expected a terminal helper result artifact')
    value = artifact.get('data', artifact)
    if section:
        if not section.startswith('/'):
            raise ValueError('--section must be a JSON pointer, for example /threads')
        for token in section[1:].split('/'):
            token = token.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list):
                if not token.isdecimal():
                    raise ValueError('array sections require a numeric index')
                value = value[int(token)]
            elif isinstance(value, dict):
                value = value[token]
            else:
                raise ValueError('section traverses a scalar value')

    def preview(item):
        if isinstance(item, (dict, list)):
            return {'type': 'object' if isinstance(item, dict) else 'array', 'count': len(item)}
        return item[:1024] + ('… [read artifact for full text]' if len(item) > 1024 else '') if isinstance(item, str) else item

    output = {'artifact': str(path), 'outcome': artifact['outcome'], 'section': section or '',
              'since': since, 'next': None}
    if isinstance(value, (dict, list)):
        entries = list(value.items()) if isinstance(value, dict) else list(enumerate(value))
        page = [{'key': preview(str(key)), 'value': preview(item)} for key, item in entries[since:since + limit]]
        output['count'] = len(entries)
        while page and len(json.dumps(page, ensure_ascii=False)) > 12000:
            page.pop()
        output['items'] = page
        if since + len(page) < len(entries):
            output['next'] = since + len(page)
    else:
        output['value'] = preview(value)
    return output


def classification(command, code, data):
    """Only documented structured outcomes may turn a failed call into a block."""
    value = data if isinstance(data, dict) else {}
    objects = {'edit-pr', 'post-review', 'publication-actions', 'branch-publish', 'run-state',
               'decisions', 'ci-wait', 'completion', 'replies', 'conversation', 'inline-feedback'}
    if not code and command in objects and not isinstance(data, dict):
        return 'error', 'malformed_output', 'Inspect retained native output before resuming.'
    diagnostic = value.get('diagnostic')
    if isinstance(diagnostic, dict) and isinstance(diagnostic.get('code'), str):
        outcome = diagnostic.get('outcome', 'blocked')
        return (outcome if outcome in {'blocked', 'error', 'interrupted'} else 'error',
                diagnostic['code'], 'Follow the structured recovery action.')
    reason = None
    if command == 'ci-wait' and value.get('result') in {
            'failure', 'timeout', 'no_checks', 'head_changed', 'pending'}:
        reason = 'ci_' + value['result']
    elif command == 'completion' and isinstance(value.get('gate'), str) and value['gate'] != 'clean':
        reason = 'completion_' + value['gate']
    elif command == 'replies' and value.get('action') in {'stop', 'reconcile'}:
        reason = 'reply_' + value['action']
    elif command == 'decisions':
        error = value.get('error') if isinstance(value.get('error'), dict) else {}
        if value.get('status') == 'error' and error.get('code') == 'metadata_conflict':
            return 'blocked', 'metadata_conflict', error.get('next_action', 'Reconcile the retained proposal explicitly.')
        payload = value.get('data')
        if isinstance(payload, dict) and payload.get('readiness') in {'needs_decision', 'sync_pending'}:
            reason = payload['readiness']
        elif value.get('status') == 'pending':
            reason = 'publication_pending'
    elif command == 'reviewers' and value.get('complete') is False:
        reason = 'review_incomplete'
    elif command == 'run-state' and value.get('status') == 'blocked':
        reason = 'review_reconciliation_blocked'
    elif command == 'update' and value.get('action') == 'stop':
        return 'error', 'runtime_unavailable', 'Restore the runtime before continuing.'
    if reason and code in (0, 1):
        return 'blocked', reason, 'Inspect the result artifact and satisfy the reported workflow gate.'
    if value.get('status') == 'error' or value.get('result') == 'error':
        return 'error', 'helper_error', 'Inspect retained diagnostics; correct the cause before resuming.'
    if code:
        return 'error', 'helper_failed', 'Inspect retained diagnostics; reconcile any uncertain mutation before retrying.'
    if data is None and command in {'edit-pr', 'post-review', 'publication-actions', 'branch-publish',
                                   'run-state', 'decisions', 'reviewers', 'ci-wait', 'completion',
                                   'replies', 'conversation', 'inline-feedback'}:
        return 'error', 'malformed_output', 'Inspect retained native output before resuming.'
    return 'success', None, None


def receipt(outcome, error_code, next_action, artifact, code, *, message=None, action=None):
    return {'schema_version': 1, 'outcome': outcome, 'error_code': error_code,
            'retryable': False, 'next_action': next_action, 'artifact': artifact,
            'exit_code': code, 'message': str(message or next_action or '')[:512], 'action': action}


class ResultFile:
    """Reserve private diagnostics before launching a potentially mutating helper."""
    def __init__(self, path, cwd, plugin_root):
        path = Path(path).expanduser()
        if not path.is_absolute():
            raise ValueError('--result-file must be absolute')
        self.path = path.resolve()
        if self.path.is_relative_to(plugin_root.resolve()) or inside_checkout(self.path, cwd):
            raise ValueError('--result-file must stay outside the plugin and repository')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.dispatched = False
        self.exit_code = None
        self.streams = {}
        created = []
        try:
            for key, target in [('result', self.path),
                                ('stdout', self.path.with_name(self.path.name + '.stdout')),
                                ('stderr', self.path.with_name(self.path.name + '.stderr'))]:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created.append(target)
                try:
                    self.streams[key] = os.fdopen(fd, 'w', encoding='utf-8', errors='backslashreplace')
                except BaseException:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    raise
            self.save({'schema_version': 1, 'outcome': 'running', 'artifact': str(self.path)})
        except BaseException:
            self.close()
            for target in created:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass  # Cleanup must not replace the reservation failure.
            raise

    def close(self):
        for stream in self.streams.values():
            try:
                stream.close()
            except OSError:
                pass  # Flush/fsync errors are reported at the owning write site.

    def save(self, value):
        write(self.path, value)

    def storage_failure(self, exc, code=2, *, original=None, action=None):
        message = (str(original) + '; ' if original else '') + 'Artifact persistence failed: ' + str(exc)
        return {**receipt('error', 'artifact_storage',
            'Inspect retained files and reconcile executed operations before resuming.', str(self.path), code,
            message=message, action=action or {'kind': 'agent_task',
                'task': 'Inspect retained operation records and reconcile publication before resuming.',
                'artifacts': [str(self.path)]}), 'dispatched': self.dispatched}

    def finish_safely(self, command, code, *, error=None):
        try:
            return self.finish(command, code, error=error)
        except OSError as exc:
            return self.storage_failure(exc, code, original=error)

    def finish(self, command, code, *, error=None, interrupted=False):
        for key in ('stdout', 'stderr'):
            self.streams[key].flush()
            os.fsync(self.streams[key].fileno())
        output = self.path.with_name(self.path.name + '.stdout').read_text(encoding='utf-8', errors='replace')
        try:
            data = json.loads(output)
        except (ValueError, RecursionError):
            data = None
        outcome, error_code, action = classification(command, code, data)
        if error:
            outcome, error_code, action = 'error', 'invocation_error', 'Inspect retained diagnostics before resuming.'
        if interrupted:
            outcome, error_code, action = 'interrupted', 'interrupted', 'Reconcile the retained operation before resuming.'
        diagnostic = data.get('diagnostic') if isinstance(data, dict) else None
        diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
        if not diagnostic and isinstance(data, dict) and isinstance(data.get('error'), dict):
            diagnostic = data['error']
        structured_action = diagnostic.get('action')
        if not structured_action and isinstance(data, dict) and isinstance(data.get('action'), dict):
            structured_action = data['action']
        if outcome != 'success' and not structured_action:
            structured_action = command_action([sys.executable, str(Path(__file__).parents[2] / 'scripts/dk.py'),
                'result', '--artifact', str(self.path)], self.path)
        result = receipt(outcome, error_code, action, str(self.path), code,
                         message=error or diagnostic.get('message') or (data.get('message') if isinstance(data, dict) else None),
                         action=structured_action)
        self.save({**result, 'command': command, 'data': data, 'message': error or diagnostic.get('message'),
                   'dispatched': self.dispatched,
                   'stdout': str(self.path.with_name(self.path.name + '.stdout')),
                   'stderr': str(self.path.with_name(self.path.name + '.stderr'))})
        self.terminal = result
        self.close()
        return result

    def run(self, command, argv, cwd, env):
        try:
            self.save({'schema_version': 1, 'outcome': 'running', 'artifact': str(self.path),
                       'command': command, 'argv': argv, 'cwd': str(cwd), 'dispatch_started': True})
            self.dispatched = True
            code = subprocess.run(argv, cwd=cwd, env=env, stdout=self.streams['stdout'],
                                  stderr=self.streams['stderr']).returncode
            code = code if code >= 0 else 128 - code
            self.exit_code = code
            interpreted = None if any(arg in ('--help', '-h') for arg in argv[2:]) else command
            return self.finish(interpreted, code, interrupted=code >= 128), code
        except KeyboardInterrupt:
            self.exit_code = 130
            return self.finish(command, 130, interrupted=True), 130
        except OSError as exc:
            if self.exit_code is not None:
                raise  # Finalization failure must preserve the native status.
            self.dispatched = False
            try:
                return self.finish(command, 2, error=str(exc)), 2
            except OSError as persistence:
                raise exc from persistence
