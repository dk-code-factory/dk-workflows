"""Decisions-owned private storage. Explicit stores never fall back to other roots."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import urlparse

from shared.repository_scope import inside_checkout


def identity(pr_url):
    url = urlparse(pr_url or '')
    match = re.fullmatch(r'/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?', url.path)
    if (url.scheme != 'https' or not url.hostname or not match or url.query or url.fragment
            or url.netloc != url.hostname or not re.fullmatch(r'[A-Za-z0-9.-]+', url.hostname)
            or any(part in ('.', '..') for part in match.groups()[:2])):
        raise ValueError('A full HTTPS --pr URL is required for cached commands')
    return Path(url.hostname, match[1], match[2], 'pr-' + match[3] + '.json')


def validate_location(path):
    path = Path(path).resolve()
    check_access(path)
    # Check both the caller's checkout (including shared Git metadata) and the
    # destination's checkout, even when called from an unrelated directory.
    existing = path
    while not existing.exists():
        existing = existing.parent
    if not existing.is_dir():
        existing = existing.parent
    if inside_checkout(path, Path.cwd()) or inside_checkout(path, existing):
        raise RuntimeError('Decisions store must be outside the code repository; '
                           'set DK_PR_DECISIONS_HOME to an absolute private directory')
    return path


def root():
    if 'DK_PR_DECISIONS_HOME' in os.environ:
        value = os.environ['DK_PR_DECISIONS_HOME']
        if not value or not Path(value).is_absolute():
            raise ValueError('DK_PR_DECISIONS_HOME must be a nonempty absolute directory; '
                             'unset it to use the default Decisions store')
        path = Path(value)
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
        if not base.is_absolute():
            raise ValueError('XDG_DATA_HOME must be an absolute path')
        path = base / 'dk-pr-decisions'
    path = validate_location(path)
    check_access(path, directory=True)
    return path


def check_access(path, *, directory=False):
    existing = path
    while not existing.exists():
        existing = existing.parent
    if (existing == path and directory and not existing.is_dir()) or (
            existing != path and not existing.is_dir()):
        raise ValueError(f'Decisions store path is not a directory: {existing}; '
                         'configure DK_PR_DECISIONS_HOME as a private directory')
    locations = [existing] if existing.is_dir() else [existing, existing.parent]
    for location in locations:
        access = os.R_OK | os.W_OK | (os.X_OK if location.is_dir() else 0)
        # Mode checks also diagnose inaccessible stores when run by a privileged host.
        mode = location.stat().st_mode
        if not os.access(location, access) or not mode & 0o444 or not mode & 0o222 or (
                location.is_dir() and not mode & 0o111):
            raise ValueError(f'Decisions store is inaccessible: {location}; grant read/write '
                             'and directory traversal access to DK_PR_DECISIONS_HOME')


def cache_path(pr_url):
    relative = identity(pr_url)
    base = root()
    path = validate_location(base / relative)
    if not path.is_relative_to(base):
        raise ValueError('Decisions record escapes the configured root through a symlink; '
                         'restore the host/repository hierarchy in DK_PR_DECISIONS_HOME')
    check_access(path)
    return path


def read(path, pr_url, *, missing=False):
    if not path.exists():
        if missing:
            return {}
        raise ValueError(f'No cached decisions at {path}; run scan first in this store')
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(state, dict) or not isinstance(state.get('pr'), dict):
            raise ValueError('expected a Decisions record with PR metadata')
        if not {'decisions', 'comments', 'threads', 'summaries'}.issubset(state):
            raise ValueError('Decisions record is missing its discussion snapshot')
        if state['pr'].get('url', '').rstrip('/') != pr_url.rstrip('/'):
            raise ValueError('Cached PR URL mismatch')
        pr = state['pr']
        if (not isinstance(pr.get('headRefOid'), str) or not pr['headRefOid']
                or not isinstance(pr.get('number'), int) or pr['number'] < 1
                or (pr.get('body') is not None and not isinstance(pr['body'], str))):
            raise ValueError('invalid cached PR snapshot')
        if state.get('version', 2) not in (1, 2):
            raise ValueError('unsupported Decisions cache version')
        for key in ('decisions', 'manual_decisions', 'replacements', 'publication_history'):
            if key in state and not isinstance(state[key], list):
                raise ValueError(f'{key} must be an array')
        for key in ('comments', 'threads', 'summaries', 'decision_sources', 'saved_decisions',
                    'manual_decision_sources', 'code_suggestions', 'description_suggestions'):
            if key in state and not isinstance(state[key], dict):
                raise ValueError(f'{key} must be an object')
        for key in ('decisions', 'manual_decisions'):
            for item in state.get(key, []):
                validate_choice(item)
        for item in state.get('comments', {}).values():
            if not isinstance(item, dict) or not isinstance(item.get('url'), str):
                raise ValueError('invalid cached comment')
        for item in state.get('threads', {}).values():
            if (not isinstance(item, dict) or not isinstance(item.get('comment_ids'), list)
                    or not isinstance(item.get('resolved'), bool)
                    or any(not isinstance(cid, str) or cid not in state.get('comments', {})
                           for cid in item['comment_ids'])):
                raise ValueError('invalid cached thread')
        for item in state.get('decision_sources', {}).values():
            if (not isinstance(item, dict)
                    or item.get('source_type') not in ('description', 'discussion', 'code', 'user', 'local')
                    or (item.get('source') is not None and not isinstance(item['source'], str))):
                raise ValueError('invalid cached agreement source')
        for field in ('code_suggestions', 'description_suggestions'):
            suggestions = state.get(field, {})
            if not isinstance(suggestions.get('items', []), list):
                raise ValueError('invalid cached suggestions')
            for item in suggestions.get('items', []):
                validate_choice(item)
        receipts = list(state.get('publication_history', []))
        if state.get('publication_intent') is not None:
            receipts.append(state['publication_intent'])
        for receipt in receipts:
            if (not isinstance(receipt, dict) or not isinstance(receipt.get('id'), str)
                    or receipt.get('status') not in ('pending', 'verified', 'not_applied')
                    or not isinstance(receipt.get('body'), str)
                    or not isinstance(receipt.get('snapshot'), dict)
                    or not isinstance(receipt['snapshot'].get('headRefOid'), str)
                    or 'body' not in receipt['snapshot']
                    or (receipt['snapshot']['body'] is not None
                        and not isinstance(receipt['snapshot']['body'], str))):
                raise ValueError('invalid cached publication receipt')
        for key, item in state.get('saved_decisions', {}).items():
            validate_record(item)
            if key != item['id']:
                raise ValueError('saved decision ID mismatch')
            if item['source_type'] == 'discussion' and item['source']:
                evidence = json.loads(item['source'])
                if not isinstance(evidence, dict) or not isinstance(evidence.get('comments'), list):
                    raise ValueError('invalid saved discussion evidence')
                if any(not isinstance(c, dict) or not isinstance(c.get('url'), str)
                       for c in evidence['comments']):
                    raise ValueError('invalid saved discussion comment')
        for replacement in state.get('replacements', []):
            if (not isinstance(replacement, dict) or not isinstance(replacement.get('new_id'), str)
                    or 'raw' not in replacement
                    or (replacement['raw'] is not None and not isinstance(replacement['raw'], str))):
                raise ValueError('invalid cached replacement')
            validate_record(replacement.get('old'))
        return state
    except (ValueError, TypeError, AttributeError, KeyError, OSError, RecursionError) as exc:
        raise ValueError(f'Cannot read Decisions store record {path}: {exc}; '
                         'restore a valid backup or reconcile the record before retrying; '
                         'no fallback store is used') from exc


def validate_choice(item):
    if (not isinstance(item, dict) or not isinstance(item.get('text'), str) or not item['text'].strip()
            or not isinstance(item.get('references'), list)
            or any(not isinstance(ref, str) or not ref for ref in item['references'])):
        raise ValueError('invalid cached decision text or references')


def validate_record(item):
    validate_choice(item)
    if (item.get('id') != hashlib.sha256(item['text'].encode()).hexdigest()[:20]
            or item.get('source_type') not in ('description', 'discussion', 'code', 'user', 'local')
            or item.get('status') not in ('active', 'superseded')
            or item.get('publication_status') not in ('published', 'unpublished')
            or 'source' not in item or (item['source'] is not None and not isinstance(item['source'], str))
            or 'superseded_by' not in item
            or (item['status'] == 'active' and item['superseded_by'] is not None)
            or (item['status'] == 'superseded' and not isinstance(item['superseded_by'], str))):
        raise ValueError('invalid cached decision record')


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_directory(path):
    if path.exists():
        if not path.is_dir():
            raise ValueError(f'Decisions store path is not a directory: {path}')
        return
    private_directory(path.parent)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        if not path.is_dir():
            raise
    sync_directory(path.parent)


def atomic_write(path, content):
    path = validate_location(Path(path))
    check_access(path)
    private_directory(path.parent)
    fd, name = tempfile.mkstemp(prefix='.decisions-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


_locks = {}
_guard = threading.Lock()
_held = threading.local()


@contextmanager
def lock(pr_url):
    path = cache_path(pr_url)
    key = (os.getpid(), str(path))
    with _guard:
        mutex = _locks.setdefault(key, threading.RLock())
    with mutex:
        held = getattr(_held, 'paths', set())
        if key in held:
            yield path
            return
        private_directory(path.parent)
        fd = os.open(path.with_suffix('.lock'), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            _held.paths = held | {key}
            yield path
        finally:
            _held.paths = held
            os.close(fd)  # The stable lock inode is never unlinked.
