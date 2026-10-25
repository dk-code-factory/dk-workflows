"""Typed terminal results for the stable CLI; native helper contracts stay intact."""
import json
import os
from pathlib import Path
import subprocess
import uuid

from shared.repository_scope import inside_checkout


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
    if not isinstance(artifact, dict) or artifact.get('schema_version') != 1 or 'data' not in artifact:
        raise ValueError('expected a terminal helper result artifact')
    value = artifact['data']
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
    reason = None
    if command == 'ci-wait' and value.get('result') in {
            'failure', 'timeout', 'no_checks', 'head_changed', 'pending'}:
        reason = 'ci_' + value['result']
    elif command == 'completion' and isinstance(value.get('gate'), str) and value['gate'] != 'clean':
        reason = 'completion_' + value['gate']
    elif command == 'replies' and value.get('action') in {'stop', 'reconcile'}:
        reason = 'reply_' + value['action']
    elif command == 'decisions':
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
    return 'success', None, None


def receipt(outcome, error_code, next_action, artifact, code):
    return {'schema_version': 1, 'outcome': outcome, 'error_code': error_code,
            'retryable': False, 'next_action': next_action, 'artifact': artifact,
            'exit_code': code}


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
        self.streams = {}
        created = []
        try:
            for key, target in [('result', self.path),
                                ('stdout', self.path.with_name(self.path.name + '.stdout')),
                                ('stderr', self.path.with_name(self.path.name + '.stderr'))]:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created.append(target)
                self.streams[key] = os.fdopen(fd, 'w', encoding='utf-8')
            self.save({'schema_version': 1, 'outcome': 'running', 'artifact': str(self.path)})
        except BaseException:
            self.close()
            for target in created:
                target.unlink(missing_ok=True)
            raise

    def close(self):
        for stream in self.streams.values():
            stream.close()

    def save(self, value):
        stream = self.streams['result']
        stream.seek(0)
        json.dump(value, stream, ensure_ascii=False)
        stream.write('\n')
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())

    def finish(self, command, code, *, error=None, interrupted=False):
        for key in ('stdout', 'stderr'):
            self.streams[key].flush()
        output = self.path.with_name(self.path.name + '.stdout').read_text(encoding='utf-8', errors='replace')
        try:
            data = json.loads(output)
        except ValueError:
            data = None
        outcome, error_code, action = classification(command, code, data)
        if error:
            outcome, error_code, action = 'error', 'invocation_error', 'Inspect retained diagnostics before resuming.'
        if interrupted:
            outcome, error_code, action = 'interrupted', 'interrupted', 'Reconcile the retained operation before resuming.'
        result = receipt(outcome, error_code, action, str(self.path), code)
        self.save({**result, 'command': command, 'data': data, 'message': error,
                   'stdout': str(self.path.with_name(self.path.name + '.stdout')),
                   'stderr': str(self.path.with_name(self.path.name + '.stderr'))})
        self.terminal = result
        self.close()
        return result

    def run(self, command, argv, cwd, env):
        try:
            code = subprocess.run(argv, cwd=cwd, env=env, stdout=self.streams['stdout'],
                                  stderr=self.streams['stderr']).returncode
            code = code if code >= 0 else 128 - code
            return self.finish(command, code, interrupted=code >= 128), code
        except KeyboardInterrupt:
            return self.finish(command, 130, interrupted=True), 130
        except OSError as exc:
            return self.finish(command, 2, error=str(exc)), 2
