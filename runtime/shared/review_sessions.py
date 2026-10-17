"""Durable review sessions for standalone reviews and workflow callers."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import uuid
from urllib.parse import urlparse

from shared.private_state import root, read, mutate
from shared import review_coverage as coverage


def directory(pr_url):
    url = urlparse(pr_url)
    parts = url.path.strip('/').split('/')
    if (url.scheme != 'https' or not url.hostname or len(parts) != 4
            or parts[2] != 'pull' or not parts[3].isdigit()):
        raise ValueError('expected a PR URL')
    return root('/'.join([url.hostname, *parts[:2]]).lower()) / 'review-sessions'


def outside(path, context):
    return coverage.outside(path, context)


def identity(context, prepared, reviewers, scope='', rules=''):
    return {'pr_url': context['url'], 'head': context['head_sha'],
        'base': prepared['identity']['base_sha'], 'preparation_id': prepared['preparation_id'],
        'tree': coverage.git(context['checkout'], 'rev-parse', f"{context['head_sha']}^{{tree}}"),
        'scope': coverage.scope(prepared), 'scope_text': coverage.digest(scope),
        'rules_text': coverage.digest(rules), 'reviewers': list(reviewers)}


class Session:
    def __init__(self, context, prepared, reviewers, *, path=None, resume=False, scope='', rules=''):
        self.context, self.prepared = context, prepared
        if resume and path is None:
            raise ValueError('resuming requires --session-file')
        self.path = outside(path or directory(context['url']) / (uuid.uuid4().hex + '.json'), context)
        self.execution_lock = self.path.with_name(self.path.name + '.execution.lock')
        self.validate_storage()
        self.identity = identity(context, prepared, reviewers, scope, rules)
        default = {'session_version': 1, 'id': uuid.uuid4().hex, 'identity': self.identity,
                   'status': 'active', 'reviews': {}, 'results': []}
        def initialize(old):
            if resume:
                if (old.get('session_version') != 1 or old.get('identity') != self.identity
                        or old.get('status') not in {'active', 'incomplete'}):
                    raise ValueError('review session is missing, finished or has changed evidence; start fresh')
            elif old != default:
                raise ValueError('session file already exists; explicitly resume interrupted work')
            return old['id']
        self.id = mutate(self.path, {} if resume else default, initialize)

    def validate_storage(self):
        if self.path.name.endswith('.lock'):
            raise ValueError('review session filenames ending in .lock are reserved for locks')
        coverage.storage_path(self.path, self.context)
        outside(self.execution_lock, self.context)
        paths = [self.path, self.path.with_name(self.path.name + '.lock'), self.execution_lock]
        for index, path in enumerate(paths):
            for other in paths[:index]:
                if (path.resolve() == other.resolve()
                        or (path.exists() and other.exists() and path.samefile(other))):
                    raise ValueError('review session and its locks must be distinct files')
        for path in paths[1:]:
            if path.exists() and path.stat().st_size:
                raise ValueError('review session lock destination contains existing data')
        for name in ('review-checkpoints', 'review-results'):
            outside(self.path.parent / name, self.context)

    def update(self, operation):
        self.validate_storage()
        def guarded(data):
            if data.get('id') != self.id or data.get('identity') != self.identity:
                raise ValueError('review session changed during execution')
            return operation(data)
        return mutate(self.path, {}, guarded)

    @contextmanager
    def execution(self):
        self.validate_storage()
        with self.execution_lock.open('w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError('review session is already executing') from exc
            def begin(data):
                if data['status'] not in {'active', 'incomplete'}:
                    raise ValueError('review session finished; start a fresh review')
                data['status'] = 'active'
                data['invocations'] = data.get('invocations', 0) + 1
                self.invocation = data['invocations']
            self.update(begin)
            try:
                yield self
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def cached(self, reviewer):
        def lookup(data):
            item = data['reviews'].get(reviewer, {})
            if item.get('status') != 'completed':
                return None
            saved = read(Path(item['checkpoint']['path']), {})
            if coverage.digest(saved) != item['checkpoint']['sha256']:
                raise ValueError('review checkpoint changed')
            if (saved.get('checkpoint_version') != 1 or saved.get('reviewer') != reviewer
                    or saved.get('status') != 'completed' or saved.get('session_id') != self.id
                    or saved.get('session_identity') != self.identity):
                raise ValueError('review checkpoint does not match this session and reviewer')
            return saved['result']
        return self.update(lookup)

    def record(self, reviewer, status, result=None, reason=None):
        self.validate_storage()
        if reviewer not in self.identity['reviewers']:
            raise ValueError('reviewer is outside this session')
        saved = {'checkpoint_version': 1, 'session_id': self.id, 'session_identity': self.identity,
                 'reviewer': reviewer, 'status': status, 'result': result, 'reason': reason}
        fingerprint = coverage.digest(saved)
        path = coverage.storage_path(
            self.path.parent / 'review-checkpoints' / (fingerprint + '.json'), self.context)
        def immutable(old):
            if old != saved:
                raise ValueError('review checkpoint changed')
        mutate(path, saved, immutable)
        def save(data):
            # Coordinator failures can happen before cache lookup. They must not
            # replace a completed reviewer's authoritative checkpoint or history.
            if status == 'failed' and data['reviews'].get(reviewer, {}).get('status') == 'completed':
                return
            item = {'status': status, 'checkpoint': {'path': str(path), 'sha256': fingerprint}}
            data.setdefault('review_history', []).append({'reviewer': reviewer, **item})
            data['reviews'][reviewer] = item
        self.update(save)

    def finish(self, result):
        self.validate_storage()
        item = coverage.retain(self.path, self.context, self.prepared, result, self.id)
        def save(data):
            data['results'].append(item)
            data['status'] = 'completed' if result['complete'] else 'incomplete'
        self.update(save)
        return item


def result_artifact(session_path, item):
    session = read(Path(session_path), {})
    if (session.get('session_version') != 1 or not session.get('results')
            or session['results'][-1] != item):
        raise ValueError('result is not the latest registered result of this review session')
    artifact = coverage.load(item)
    if (artifact.get('version') != 2
            or artifact['session_id'] != session.get('id')
            or artifact['session_identity'] != session.get('identity')
            or session.get('status') != ('completed' if artifact['result']['complete'] else 'incomplete')):
        raise ValueError('result is not the latest registered result of this review session')
    return artifact
