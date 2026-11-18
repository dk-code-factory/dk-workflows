"""Canonical task identities, JSON hashes and private file primitives."""
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import json
import re
import subprocess
from shared.private_state import write as write_private

CONTRACT = 2

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def task_key(subject, task, skill, *, options=None):
    from alternatives_compat import storage_identity
    task, skill = storage_identity(task, skill, options)
    return digest({**subject, 'task': task, 'skill': skill})


def has_output_content(original):
    return bool(original.decode('utf-8', errors='surrogateescape').strip())


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    """Durable replace; a failed write never clears an existing claim."""
    write_private(path, value, ensure_ascii=True)


def git(checkout, *args):
    return subprocess.check_output(['git', '-C', str(checkout), *args], stderr=subprocess.PIPE)


def identity(repository, pr):
    repository = repository.lower().removeprefix('remote:').removesuffix('.git')
    if not re.fullmatch(r'[a-z0-9.-]+/[a-z0-9_.-]+/[a-z0-9_.-]+', repository):
        raise ValueError('repository must be HOST/OWNER/REPO')
    url = urlsplit(pr)
    parts = url.path.rstrip('/').split('/')
    if (url.scheme != 'https' or url.username or url.password or url.query or url.fragment
            or len(parts) != 5 or parts[3] != 'pull' or not parts[4].isdigit()
            or int(parts[4]) <= 0 or '/'.join([url.netloc.lower(), parts[1].lower(), parts[2].lower()]) != repository):
        raise ValueError('PR URL must identify the canonical repository')
    return {'repository': repository, 'pr': f'https://{repository}/pull/{int(parts[4])}'}
