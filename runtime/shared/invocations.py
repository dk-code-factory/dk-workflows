"""Short lifecycle entry/exit with durable, authority-free invocation receipts."""
import json
import os
import sys
from pathlib import Path

from shared import workflow_storage, cli_reference
from shared.private_state import write
from shared.runtime_bundles import updater, verify
from shared.diagnostics import Diagnostic, command


def load(path):
    value = json.loads(Path(path).read_text())
    if (not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('kind') != 'dk-invocation'
            or value.get('host') not in ('codex', 'claude')
            or not all(isinstance(value.get(k), str) and value[k] for k in
                       ('runtime_root', 'token', 'skill', 'runtime_digest'))
            or type(value.get('managed')) is not bool):
        raise ValueError('invalid invocation receipt')
    root = Path(value['runtime_root'])
    if not root.is_absolute():
        raise ValueError('invocation runtime must be absolute')
    if verify(root) != value['runtime_digest']:
        raise ValueError('invocation runtime changed; use its retained runtime')
    return value


def begin(root, routes, host, skill, cwd, parent=None, *, managed_receipt=None):
    if not (root / host / 'skills' / skill / 'SKILL.md').is_file():
        raise ValueError('unknown packaged skill: ' + skill)
    if os.environ.get('DK_MANAGED_INVOCATION') and managed_receipt is None:
        value = load(os.environ['DK_MANAGED_INVOCATION'])
        if value['host'] == host and value['skill'] == skill and value['managed']:
            return value
    # Reserve storage first: an unavailable receipt location must not leak entry.
    directory = workflow_storage.allocate(repository=cwd)
    api = updater()
    managed = managed_receipt or json.loads(os.environ.get('DK_UPDATE_MANAGED_RECEIPT', 'null'))
    if managed is not None and not isinstance(managed, dict):
        raise ValueError('invalid executor-managed lifecycle receipt')
    identity = json.loads((root / f'.{host}-plugin/plugin.json').read_text())
    reusable = bool(managed and managed.get('identity', {}).get('name') == identity['name']
                    and managed.get('identity', {}).get('version') == identity['version']
                    and managed.get('identity', {}).get('host') == host)
    if managed_receipt is not None:
        reusable = bool(managed.get('identity', {}).get('name') == identity['name']
                        and managed.get('identity', {}).get('version') == identity['version'])
    # Older managed receipts put host only on the executor environment.
    if managed and managed_receipt is None and 'host' not in managed.get('identity', {}):
        reusable = bool(managed.get('identity', {}).get('name') == identity['name']
                        and managed.get('identity', {}).get('version') == identity['version']
                        and os.environ.get('DK_HOST') == host)
    entry = managed if reusable else api.lifecycle(root, host, 'entry', parent=parent)
    if not entry.get('token') or not entry.get('runtime_root') or entry.get('action') == 'stop':
        raise ValueError('lifecycle entry did not retain a usable runtime')
    retained = Path(entry['runtime_root'])
    value = {'schema_version': 1, 'kind': 'dk-invocation', 'skill': skill,
             'host': host, 'token': entry['token'], 'runtime_root': str(retained),
             'runtime_digest': verify(retained), 'managed': reusable, 'lifecycle': entry}
    # Keep the token recoverable even if reference creation fails.
    path = directory / 'invocation.json'
    value.update(reference=None, invocation=str(path))
    try:
        write(path, value)
        reference = cli_reference.create(retained, routes, cwd, entry['token'])
        value.update(reference=reference['reference'])
        write(path, value)
    except Exception as exc:
        raise Diagnostic('invocation_storage', 'Lifecycle entered but invocation setup failed: ' + str(exc),
            command([sys.executable, str(retained / 'scripts/update.py'), 'complete', '--host', host,
                     '--token', entry['token']], path), outcome='error') from exc
    return value


def end(path, reason=None):
    value = load(path)
    if value['managed']:
        return {'status': 'executor_managed', 'token': value['token'],
                'message': 'The host executor owns lifecycle completion.'}
    return updater().lifecycle(Path(value['runtime_root']), value['host'], 'complete',
                               token=value['token'], reason=reason)
