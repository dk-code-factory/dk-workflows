"""Strict GitHub reads and single-dispatch writes for publication helpers."""
import json
import re
import subprocess
from urllib.parse import urlparse

from shared.github_reads import run


class ResponseError(ValueError):
    def __init__(self, value):
        self.response = value
        super().__init__('GitHub returned GraphQL errors: ' + json.dumps(value['errors']))


class MalformedResponse(ValueError):
    def __init__(self, output):
        self.response = {'stdout': output}
        super().__init__('GitHub returned malformed JSON; complete response is retained')


def identity(pr):
    parsed = urlparse(pr)
    parts = parsed.path.strip('/').split('/')
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port or parsed.query or parsed.fragment or len(parts) != 4
            or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', p) or p in {'.', '..'} for p in parts[:2])
            or parts[2] != 'pull' or not parts[3].isdecimal() or int(parts[3]) < 1):
        raise ValueError('a full PR URL with canonical HTTPS identity is required')
    return parsed.hostname, '/'.join(parts[:2]), int(parts[3])


def gh(argv, *, payload=None):
    options = {'capture_output': True, 'text': True, 'check': True}
    if payload is not None:
        options['input'] = json.dumps(payload)
    result = run(['gh', *argv], **options)
    try:
        value = json.loads(result.stdout) if result.stdout.strip() else None
    except (ValueError, RecursionError) as exc:
        raise MalformedResponse(result.stdout) from exc
    if isinstance(value, dict) and value.get('errors'):
        raise ResponseError(value)
    return value


def api(host, endpoint, *, payload=None, method=None):
    argv = ['api', '--hostname', host, endpoint]
    if method:
        argv += ['--method', method]
    if payload is not None:
        argv += ['--input', '-']
    return gh(argv, payload=payload)


def pages(host, endpoint, collection=None, *, counted=True):
    """Fetch every REST page, rejecting malformed/overlapping partial snapshots."""
    # gh follows server Link headers (also for endpoints returning all records
    # without pagination parameters). Nonzero exit rejects any partial stdout.
    responses = gh(['api', '--hostname', host, endpoint, '--paginate', '--slurp'])
    if not isinstance(responses, list) or not responses:
        raise ValueError('incomplete paginated GitHub response')
    result, seen, totals = [], set(), set()
    for values in responses:
        if collection:
            if not isinstance(values, dict) or counted and type(values.get('total_count')) is not int:
                raise ValueError('incomplete paginated GitHub collection')
            total = values.get('total_count')
            if counted:
                totals.add(total)
            values = values.get(collection)
        if not isinstance(values, list):
            raise ValueError('incomplete paginated GitHub response')
        for value in values:
            if not isinstance(value, dict) or not value.get('id') or value['id'] in seen:
                raise ValueError('malformed or overlapping GitHub pages')
            seen.add(value['id'])
            result.append(value)
    if collection and counted and totals != {len(result)}:
        raise ValueError('partial or changing GitHub collection')
    return result


def pr_snapshot(pr):
    host, repo, number = identity(pr)
    value = api(host, f'repos/{repo}/pulls/{number}')
    if (not isinstance(value, dict) or not isinstance(value.get('html_url'), str) or value['html_url'].lower() != pr.lower()
            or not isinstance(value.get('head'), dict) or not value['head'].get('sha')
            or not isinstance(value.get('base'), dict) or not value['base'].get('sha')):
        raise ValueError('incomplete PR snapshot')
    if any(not isinstance(value[side]['sha'], str) or not re.fullmatch(r'[0-9a-f]{40}', value[side]['sha'])
           for side in ('head', 'base')):
        raise ValueError('invalid PR head/base SHA')
    return value
