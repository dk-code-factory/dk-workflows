"""Durable intent and receipts; publishers own validation and live reconciliation."""
import hashlib
import fcntl
import json
import subprocess
from pathlib import Path
import uuid

from shared import workflow_storage
from shared.private_state import read, write
from shared.diagnostics import Diagnostic, command, task


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest()


def prepare(kind, target, expected, approved, reconcile_argv, *, repository=None, directory=None):
    directory = workflow_storage.allocate(repository=repository) if directory is None else workflow_storage.owned(directory)
    path = directory / (kind + '-' + uuid.uuid4().hex + '.json')
    request = {'kind': kind, 'target': target, 'expected': expected, 'approved': approved}
    value = {'schema_version': 1, 'operation_id': uuid.uuid4().hex,
             'request': request, 'request_hash': digest(request), 'status': 'prepared',
             'reconcile_argv': [str(x) for x in reconcile_argv] + ['--operation', str(path)]}
    write(path, value)
    return {'operation': str(path), **value}


def load(path, kind=None):
    path = Path(path).resolve()
    workflow_storage.owned(path)
    value = read(path, None)
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or not isinstance(value.get('operation_id'), str) or not value['operation_id']
            or not isinstance(value.get('request'), dict) or set(value['request']) != {'kind', 'target', 'expected', 'approved'}
            or not isinstance(value['request']['expected'], dict) or not isinstance(value['request']['approved'], dict)
            or not isinstance(value.get('reconcile_argv'), list) or not value['reconcile_argv']
            or not all(isinstance(v, str) for v in value['reconcile_argv'])
            or value.get('request_hash') != digest(value.get('request'))
            or kind and value['request']['kind'] != kind
            or value.get('status') not in {'prepared', 'pending', 'verified', 'not_applied'}):
        raise ValueError('invalid or changed prepared publication')
    if ('intent' in value and (value['status'] == 'prepared' or value['intent'] != {
            'operation_id': value['operation_id'], 'request_hash': value['request_hash']})):
        raise ValueError('prepared publication intent changed; retain recovery evidence')
    if value['status'] == 'pending' and 'intent' not in value:
        raise ValueError('pending publication intent is missing')
    return value


def require_target(path, kind, pr):
    value = load(path, kind)
    if pr is not None and value['request']['target'].lower() != pr.lower():
        raise ValueError('common PR context conflicts with the retained publication target')
    return value


def execute(path, kind, *, check, send, observe, reconcile=False):
    """Never retry a mutation. An intent without a verified receipt only permits reads.

    observe returns verified evidence, or raises. check is called even for receipt
    reuse; it must enforce the publisher's current readiness independently of history.
    """
    path = Path(path).resolve()
    load(path, kind)

    def operate(value):
        if value['request_hash'] != digest(value['request']):
            raise ValueError('prepared publication changed')
        def pending(exc):
            return Diagnostic('publication_uncertain',
                'Publication requires read-only reconciliation: ' + str(exc),
                command(value['reconcile_argv'], path), dispatched=True)

        def not_ready(exc):
            return Diagnostic('publication_not_ready',
                'Publication receipt is retained; current readiness failed: ' + str(exc),
                task('Refresh current evidence and satisfy the current workflow gate. Do not repeat the publication.', path),
                dispatched=True)

        def retain_failure(exc):
            if not any(hasattr(exc, key) for key in ('response', 'stdout', 'stderr')):
                return
            failure = {'message': str(exc), 'stdout': getattr(exc, 'stdout', None),
                       'stderr': getattr(exc, 'stderr', None), 'response': getattr(exc, 'response', None)}
            value['failure'] = failure
            if 'response' not in value and failure['response'] is not None:
                value['response'] = failure['response']
            try:
                write(path, value)
            except OSError:
                pass  # Keep the original failure and existing durable intent.

        if value['status'] in {'pending', 'verified'} or reconcile:
            receipt_saved = value['status'] == 'verified'
            try:
                evidence = observe(value)
                value.update(status='verified', receipt=evidence)
                write(path, value)
                receipt_saved = True
                # Receipt reconciliation and present readiness are distinct. A
                # changed governing source cannot erase a confirmed mutation.
                check(value, True)
            except Exception as exc:
                retain_failure(exc)
                raise (not_ready(exc) if receipt_saved else pending(exc)) from exc
            return {'operation': str(path), 'status': 'verified', 'reused': True,
                    'receipt': value['receipt']}
        if value['status'] == 'not_applied':
            raise Diagnostic('fresh_preparation_required', 'Prepare and review a new publication.',
                             command(value['reconcile_argv'], path))
        receipt_saved = False
        try:
            check(value, False)
        except Diagnostic:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise Diagnostic('evidence_unavailable', str(exc),
                task('Restore required evidence, read access and writable private storage before publication.', path),
                outcome='error') from exc
        except Exception as exc:
            raise Diagnostic('publication_not_ready', str(exc),
                task('Refresh current evidence and prepare the publication again.', path)) from exc
        value['status'] = 'pending'
        value['intent'] = {'operation_id': value['operation_id'], 'request_hash': value['request_hash']}
        # A failure here prevents dispatch. Never write "prepared" after dispatch.
        try:
            write(path, value)
        except OSError as exc:
            raise Diagnostic('artifact_storage', 'Intent persistence failed before dispatch: ' + str(exc),
                             task('Restore writable private storage and inspect the retained operation.', path),
                             outcome='error') from exc
        try:
            response = send(value)
            value['response'] = response
            write(path, value)
            evidence = observe(value)
            value.update(status='verified', receipt=evidence)
            write(path, value)
            receipt_saved = True
            check(value, True)
        except Exception as exc:
            retain_failure(exc)
            raise (not_ready(exc) if receipt_saved else pending(exc)) from exc
        return {'operation': str(path), 'status': 'verified', 'reused': False,
                'receipt': value['receipt']}

    # The per-operation lock protects the same operation's intent. It is not a
    # PR reservation and makes no claim about other work on this PR.
    with path.with_name(path.name + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return operate(load(path, kind))
