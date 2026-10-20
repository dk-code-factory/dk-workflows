"""Root-workflow CLI discovery shared by agents without caching workflow results."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from shared.private_state import mutate
from shared.repository_scope import inside_checkout


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def source(root, routes):
    paths = ['scripts/dk.py', '.codex-plugin/plugin.json', *[row[0] for row in routes.values()]]
    files = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths}
    return {'version': json.loads((root / '.codex-plugin/plugin.json').read_text())['version'],
            'digest': digest(files)}


def checked_path(path, root, cwd):
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise ValueError('CLI reference path must be absolute')
    path = path.resolve()
    if path.is_relative_to(root.resolve()) or inside_checkout(path, cwd):
        raise ValueError('CLI reference must stay outside the repository and plugin')
    return path


def validate(record, identity):
    if (not isinstance(record, dict) or record.get('schema_version') != 1
            or record.get('source') != identity or not isinstance(record.get('help'), dict)):
        raise ValueError('CLI reference is stale or invalid; create a reference for the current plugin version')


def create(root, routes, cwd, workflow, path=None):
    if not workflow:
        raise ValueError('reference requires --workflow-id or DK_UPDATE_PARENT')
    identity = source(root, routes)
    if path is None:
        base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
        if not base.is_absolute():
            raise ValueError('XDG_STATE_HOME must be absolute')
        path = base / 'dk-code-factory/cli-references' / digest(workflow) / (identity['digest'] + '.json')
    path = checked_path(path, root, cwd)
    record = {'schema_version': 1, 'source': identity, 'workflow': workflow,
              'commands': {name: {'script': row[0], 'cwd': row[1], 'pr': row[2], 'repo': row[3]}
                           for name, row in routes.items()}, 'help': {}}

    def ensure(value):
        validate(value, identity)
        if value.get('workflow') != workflow:
            raise ValueError('CLI reference belongs to a different root workflow')
    mutate(path, record, ensure)
    return {'reference': str(path), 'source': identity, 'command_count': len(routes)}


def help_result(path, root, routes, command, arguments, argv, cwd, env):
    """Only successful help discovery is reusable, never operational output."""
    path = checked_path(path, root, cwd)
    identity = source(root, routes)
    if not path.is_file():
        raise ValueError('CLI reference missing; run dk.py reference once for this root workflow')
    key = digest([command, arguments])

    def lookup(value):
        validate(value, identity)
        cached = key in value['help']
        if not cached:
            process = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
            if process.returncode:
                raise ValueError(process.stderr.strip() or 'help discovery failed; result was not cached')
            value['help'][key] = {'query': [command, *arguments], 'stdout': process.stdout,
                                  'stderr': process.stderr}
        return {'reference': str(path), 'section': '/help/' + key, 'cached': cached,
                'query': [command, *arguments]}
    return mutate(path, {}, lookup)
